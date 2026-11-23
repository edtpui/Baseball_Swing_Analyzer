"""Web server: bridges the sensor link to browsers and serves the dashboard."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import protocol
from .processing import process_swing
from .storage import Settings, SettingsStore, SwingStore

log = logging.getLogger(__name__)

TRANSFER_STALL_S = 2.5
MAX_RESENDS = 2
CLIENT_QUEUE = 128


class SensorLink(Protocol):
    async def run(self) -> None: ...
    async def send_command(self, data: bytes) -> None: ...


@dataclass(frozen=True)
class ServerOptions:
    data_dir: Path
    web_dir: Path
    simulate: bool = False
    sim_interval_s: float | None = None
    sim_seed: int | None = None


class _Client:
    """One browser connection with its own bounded send queue."""

    def __init__(self, socket: WebSocket) -> None:
        self.socket = socket
        self.queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=CLIENT_QUEUE)

    def offer(self, message: dict, droppable: bool) -> None:
        if self.queue.full():
            if droppable:
                return
            # A browser that cannot keep up loses the oldest queued frame, never an event.
            self.queue.get_nowait()
        self.queue.put_nowait(message)


class Hub:
    """Owns the sensor link and fans decoded data out to connected browsers."""

    def __init__(self, options: ServerOptions) -> None:
        self.options = options
        self.settings = SettingsStore(options.data_dir / "settings.json")
        self.swings = SwingStore(options.data_dir / "swings")
        self.status: dict = {"state": "starting"}
        self.link: SensorLink | None = None
        self._clients: set[_Client] = set()
        self._assembler = protocol.TransferAssembler()
        self._last_swing_packet = 0.0
        self._resends: dict[int, int] = {}
        self._tasks: set[asyncio.Task] = set()

    # --- link callbacks (always called on the event loop) -----------------

    def on_status(self, status: dict) -> None:
        self.status = status
        self.broadcast({"type": "status", "status": status})
        if status.get("state") == "connected":
            self._spawn(self._push_thresholds())

    def on_live(self, data: bytes) -> None:
        try:
            if data and data[0] == protocol.PKT_SUMMARY:
                summary = protocol.parse_summary(data)
                self.broadcast({"type": "summary", "summary": summary.__dict__})
                return
            live = protocol.parse_live(data)
        except protocol.ProtocolError as exc:
            log.debug("dropped packet: %s", exc)
            return
        self.broadcast({"type": "live", "live": live.__dict__}, droppable=True)

    def on_swing_packet(self, data: bytes) -> None:
        self._last_swing_packet = asyncio.get_running_loop().time()
        result = self._assembler.feed(data)
        if result is None:
            return
        if result.ok:
            self._resends.pop(result.swing_id, None)
            self._spawn(self._finish_swing(result.swing_id, result.blob))
        else:
            self._spawn(self._retry_transfer(result.swing_id, result.error))

    # --- internals ---------------------------------------------------------

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def broadcast(self, message: dict, droppable: bool = False) -> None:
        for client in self._clients:
            client.offer(message, droppable)

    async def _push_thresholds(self) -> None:
        current = self.settings.current
        try:
            await self.send_command(protocol.command_set_thresholds(current.start_dps, current.min_peak_dps))
        except RuntimeError as exc:
            log.warning("could not send thresholds to the sensor: %s", exc)

    async def send_command(self, data: bytes) -> None:
        if self.link is None:
            raise RuntimeError("sensor link is not running")
        await self.link.send_command(data)

    async def _retry_transfer(self, swing_id: int, error: str) -> None:
        attempts = self._resends.get(swing_id, 0)
        if attempts >= MAX_RESENDS:
            self._resends.pop(swing_id, None)
            self.broadcast({"type": "swing_error", "swing_id": swing_id, "error": f"transfer failed: {error}"})
            return
        self._resends[swing_id] = attempts + 1
        log.warning("swing %d transfer failed (%s); asking the sensor to resend", swing_id, error)
        try:
            await self.send_command(protocol.command_resend())
        except RuntimeError as exc:
            self._resends.pop(swing_id, None)
            self.broadcast({"type": "swing_error", "swing_id": swing_id, "error": f"transfer failed: {exc}"})

    async def _finish_swing(self, swing_id: int, blob: bytes) -> None:
        try:
            raw = protocol.parse_blob(blob)
            result = await asyncio.to_thread(process_swing, raw, self.settings.current.bat())
        except (protocol.ProtocolError, ValueError) as exc:
            log.warning("swing %d could not be processed: %s", swing_id, exc)
            self.broadcast({"type": "swing_error", "swing_id": swing_id, "error": str(exc)})
            return
        record = self.swings.add(blob, result, swing_id)
        self.broadcast({"type": "swing", "swing": record})

    async def watch_transfers(self) -> None:
        """Asks for a resend when a transfer goes quiet before its END packet."""
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(0.5)
            if self._assembler.active and loop.time() - self._last_swing_packet > TRANSFER_STALL_S:
                swing_id = self._assembler.swing_id
                self._assembler.abort()
                await self._retry_transfer(swing_id, "transfer stalled")

    async def reprocess_all(self) -> int:
        bat = self.settings.current.bat()
        done = 0
        for summary in self.swings.list():
            swing_id = summary["id"]
            try:
                raw = protocol.parse_blob(self.swings.raw(swing_id))
                result = await asyncio.to_thread(process_swing, raw, bat)
            except (OSError, KeyError, protocol.ProtocolError, ValueError) as exc:
                log.warning("could not re-analyse swing %s: %s", swing_id, exc)
                continue
            self.swings.replace_result(swing_id, result)
            done += 1
        return done

    async def serve_client(self, socket: WebSocket) -> None:
        await socket.accept()
        client = _Client(socket)
        client.offer(self.hello(), droppable=False)
        self._clients.add(client)

        async def reader() -> None:
            # Browsers send nothing; reading is how a closed socket is noticed.
            while True:
                await socket.receive_text()

        read_task = asyncio.create_task(reader())
        try:
            while not read_task.done():
                get_task = asyncio.create_task(client.queue.get())
                done, _ = await asyncio.wait({get_task, read_task}, return_when=asyncio.FIRST_COMPLETED)
                if get_task in done:
                    await socket.send_json(get_task.result())
                else:
                    get_task.cancel()
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            self._clients.discard(client)
            if read_task.done() and not read_task.cancelled():
                read_task.exception()  # the disconnect; retrieved so asyncio does not log it
            else:
                read_task.cancel()

    def hello(self) -> dict:
        return {
            "type": "hello",
            "status": self.status,
            "settings": self.settings.current.model_dump(),
            "simulate": self.options.simulate,
        }


def _make_link(hub: Hub) -> SensorLink:
    if hub.options.simulate:
        from .simulator import SimulatedSensor

        return SimulatedSensor(
            hub.on_status,
            hub.on_live,
            hub.on_swing_packet,
            auto_interval_s=hub.options.sim_interval_s,
            seed=hub.options.sim_seed,
        )
    from .ble_client import BleSensorLink

    return BleSensorLink(hub.on_status, hub.on_live, hub.on_swing_packet)


def create_app(options: ServerOptions) -> FastAPI:
    hub = Hub(options)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        hub.link = _make_link(hub)
        tasks = [asyncio.create_task(hub.link.run()), asyncio.create_task(hub.watch_transfers())]
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    app = FastAPI(title="Swing Analyzer", lifespan=lifespan)
    app.state.hub = hub

    @app.get("/api/state")
    async def get_state() -> dict:
        return hub.hello()

    @app.get("/api/swings")
    async def list_swings() -> list[dict]:
        return hub.swings.list()

    @app.get("/api/swings/{swing_id}")
    async def get_swing(swing_id: str) -> dict:
        try:
            return hub.swings.get(swing_id)
        except KeyError:
            raise HTTPException(404, "swing not found") from None

    @app.delete("/api/swings/{swing_id}", status_code=204)
    async def delete_swing(swing_id: str) -> None:
        try:
            hub.swings.delete(swing_id)
        except KeyError:
            raise HTTPException(404, "swing not found") from None
        hub.broadcast({"type": "swings_changed"})

    @app.put("/api/settings")
    async def put_settings(new: Settings) -> dict:
        old = hub.settings.current
        hub.settings.save(new)
        hub.broadcast({"type": "settings", "settings": new.model_dump()})
        if (new.start_dps, new.min_peak_dps) != (old.start_dps, old.min_peak_dps):
            await hub._push_thresholds()
        if new.bat() != old.bat():
            await hub.reprocess_all()
            hub.broadcast({"type": "swings_changed"})
        return new.model_dump()

    @app.post("/api/device/recalibrate", status_code=202)
    async def recalibrate() -> dict:
        try:
            await hub.send_command(protocol.command_recalibrate())
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from None
        return {"ok": True}

    @app.post("/api/sim/swing", status_code=202)
    async def simulate_swing() -> dict:
        request = getattr(hub.link, "request_swing", None)
        if request is None:
            raise HTTPException(404, "the server is not running in simulation mode")
        request()
        return {"ok": True}

    @app.websocket("/ws")
    async def websocket(socket: WebSocket) -> None:
        await hub.serve_client(socket)

    if (options.web_dir / "index.html").exists():
        app.mount("/", StaticFiles(directory=options.web_dir, html=True), name="web")
    else:

        @app.get("/", response_class=PlainTextResponse)
        async def missing_dashboard() -> str:
            return "The dashboard has not been built yet. Run `npm install` then `npm run build` in app/web."

    return app
