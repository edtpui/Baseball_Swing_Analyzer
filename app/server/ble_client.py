"""Bluetooth LE link to the swing sensor, with automatic reconnect."""

from __future__ import annotations

import asyncio
import logging
from typing import Callable

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from bleak.exc import BleakError

from . import protocol

log = logging.getLogger(__name__)

SCAN_TIMEOUT_S = 8.0
RETRY_DELAY_S = 2.0


class LinkUnavailable(RuntimeError):
    """A command was sent while no sensor is connected."""


def parse_info(data: bytes) -> dict[str, str]:
    """The INFO characteristic is `key=value;key=value` text."""
    info: dict[str, str] = {}
    for part in data.decode("ascii", errors="replace").split(";"):
        key, sep, value = part.partition("=")
        if sep:
            info[key.strip()] = value.strip()
    return info


def _matches(device: BLEDevice, advertisement: AdvertisementData) -> bool:
    if protocol.SERVICE_UUID in (u.lower() for u in advertisement.service_uuids):
        return True
    return (advertisement.local_name or device.name or "") == protocol.DEVICE_NAME


class BleSensorLink:
    def __init__(
        self,
        on_status: Callable[[dict], None],
        on_live: Callable[[bytes], None],
        on_swing_packet: Callable[[bytes], None],
    ) -> None:
        self._on_status = on_status
        self._on_live = on_live
        self._on_swing_packet = on_swing_packet
        self._client: BleakClient | None = None

    async def send_command(self, data: bytes) -> None:
        client = self._client
        if client is None or not client.is_connected:
            raise LinkUnavailable("sensor is not connected")
        try:
            await client.write_gatt_char(protocol.CONTROL_UUID, data, response=True)
        except (BleakError, OSError, asyncio.TimeoutError) as exc:
            raise LinkUnavailable(f"command failed: {exc}") from exc

    async def run(self) -> None:
        while True:
            error: str | None = None
            try:
                self._on_status({"state": "scanning"})
                device = await BleakScanner.find_device_by_filter(_matches, timeout=SCAN_TIMEOUT_S)
                if device is None:
                    continue
                await self._session(device)
            except asyncio.CancelledError:
                raise
            except (BleakError, OSError, asyncio.TimeoutError) as exc:
                error = str(exc) or type(exc).__name__
                log.warning("bluetooth link error: %s", error)
            finally:
                self._client = None
            self._on_status({"state": "disconnected", "error": error})
            await asyncio.sleep(RETRY_DELAY_S)

    async def _session(self, device: BLEDevice) -> None:
        loop = asyncio.get_running_loop()
        disconnected = asyncio.Event()
        name = device.name or protocol.DEVICE_NAME
        self._on_status({"state": "connecting", "name": name, "address": device.address})

        def on_disconnect(_: BleakClient) -> None:
            loop.call_soon_threadsafe(disconnected.set)

        async with BleakClient(device, disconnected_callback=on_disconnect) as client:
            self._client = client
            info = parse_info(bytes(await client.read_gatt_char(protocol.INFO_UUID)))
            if info.get("proto") != str(protocol.PROTOCOL_VERSION):
                raise BleakError(
                    f"sensor speaks protocol {info.get('proto', '?')}, this app needs {protocol.PROTOCOL_VERSION}"
                )
            await client.start_notify(protocol.LIVE_UUID, lambda _, data: self._on_live(bytes(data)))
            await client.start_notify(protocol.SWING_UUID, lambda _, data: self._on_swing_packet(bytes(data)))
            self._on_status(
                {"state": "connected", "name": name, "address": device.address, "info": info, "mtu": client.mtu_size}
            )
            await disconnected.wait()
