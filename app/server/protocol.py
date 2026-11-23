"""Wire format shared with the firmware (Baseball_Swing_Analyzer/Protocol.h).

Everything is little-endian. Keep the two files in step.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

import numpy as np

PROTOCOL_VERSION = 2
DEVICE_NAME = "SwingSensor"

SERVICE_UUID = "b5a70001-6f1d-4c8e-9a53-2f1c7d4e8a10"
LIVE_UUID = "b5a70002-6f1d-4c8e-9a53-2f1c7d4e8a10"
SWING_UUID = "b5a70003-6f1d-4c8e-9a53-2f1c7d4e8a10"
CONTROL_UUID = "b5a70004-6f1d-4c8e-9a53-2f1c7d4e8a10"
INFO_UUID = "b5a70005-6f1d-4c8e-9a53-2f1c7d4e8a10"

PKT_LIVE = 0x01
PKT_SUMMARY = 0x02
PKT_XFER_START = 0xA0
PKT_XFER_DATA = 0xA1
PKT_XFER_END = 0xA2

CMD_RECALIBRATE = 0x01
CMD_SET_THRESHOLDS = 0x02
CMD_RESEND_SWING = 0x10

STATE_NAMES = {0: "idle", 1: "swinging", 2: "settling"}
LIVE_FLAG_CALIBRATED = 0x10
LIVE_FLAG_TRANSFER = 0x20
LIVE_FLAG_OVERRUN = 0x40
LIVE_FLAG_IMU_FAULT = 0x80

SWING_FLAG_ACCEL_CLIPPED = 0x0001
SWING_FLAG_GYRO_CLIPPED = 0x0002
SWING_FLAG_TIMED_OUT = 0x0004
SWING_FLAG_UNCALIBRATED = 0x0008
SWING_FLAG_HAS_HI_ACCEL = 0x0010
SWING_FLAG_HI_ACCEL_CLIPPED = 0x0020

_LIVE = struct.Struct("<BBH4hHHHH")
_SUMMARY = struct.Struct("<BBHHHH")
_HEADER = struct.Struct("<4sHHHHhHfff4f3ffff")
_HEADER_V1 = struct.Struct("<4sHHHHhHfff4f3fff")
_XFER_START = struct.Struct("<BHIHH")
_XFER_DATA_HEAD = struct.Struct("<BH")
_XFER_END = struct.Struct("<BHI")

assert _LIVE.size == 20
assert _SUMMARY.size == 10
assert _HEADER.size == 68
assert _HEADER_V1.size == 64

HEADER_BYTES = _HEADER.size
SAMPLE_BYTES = 18
MAGIC = b"SWG2"
# Captures stored by protocol version 1: no high-g accelerometer columns.
MAGIC_V1 = b"SWG1"
SAMPLE_BYTES_V1 = 12
MAX_SAMPLES = 1750


class ProtocolError(ValueError):
    """A packet or blob did not match the wire format."""


@dataclass(frozen=True)
class LivePacket:
    state: str
    calibrated: bool
    transferring: bool
    overrun: bool
    imu_fault: bool
    seq: int
    q: tuple[float, float, float, float]
    gyro_dps: float
    accel_g: float
    sample_rate_hz: float
    swing_count: int


@dataclass(frozen=True)
class SwingSummary:
    swing_id: int
    impact: bool
    accel_clipped: bool
    gyro_clipped: bool
    peak_omega_dps: float
    duration_ms: int
    peak_accel_g: float


@dataclass
class RawSwing:
    """One captured swing exactly as the sensor recorded it."""

    swing_id: int
    trigger_idx: int
    peak_idx: int
    impact_idx: int  # -1 when no ball strike was seen
    flags: int
    sample_rate_hz: float
    gyro_dps_per_lsb: float
    accel_g_per_lsb: float
    q0: tuple[float, float, float, float]
    gyro_bias_dps: tuple[float, float, float]
    peak_omega_dps: float
    peak_accel_g: float
    gyro: np.ndarray  # (N, 3) int16
    accel: np.ndarray  # (N, 3) int16
    # High-g accelerometer channel, same axes as `accel`. Zeros in version 1 captures.
    accel_hi: np.ndarray | None = None  # (N, 3) int16
    hi_accel_g_per_lsb: float = 0.0

    def __post_init__(self) -> None:
        if self.accel_hi is None:
            self.accel_hi = np.zeros_like(self.accel)

    @property
    def has_hi_accel(self) -> bool:
        return bool(self.flags & SWING_FLAG_HAS_HI_ACCEL) and self.hi_accel_g_per_lsb > 0

    @property
    def sample_count(self) -> int:
        return int(self.gyro.shape[0])


def parse_live(data: bytes) -> LivePacket:
    if len(data) != _LIVE.size or data[0] != PKT_LIVE:
        raise ProtocolError(f"not a live packet ({len(data)} bytes)")
    _, state, seq, qw, qx, qy, qz, gyro, accel_mg, odr_x10, swings = _LIVE.unpack(data)
    q = np.array([qw, qx, qy, qz], dtype=float) / 32767.0
    norm = float(np.linalg.norm(q))
    q = q / norm if norm > 1e-6 else np.array([1.0, 0.0, 0.0, 0.0])
    return LivePacket(
        state=STATE_NAMES.get(state & 0x0F, "unknown"),
        calibrated=bool(state & LIVE_FLAG_CALIBRATED),
        transferring=bool(state & LIVE_FLAG_TRANSFER),
        overrun=bool(state & LIVE_FLAG_OVERRUN),
        imu_fault=bool(state & LIVE_FLAG_IMU_FAULT),
        seq=seq,
        q=(float(q[0]), float(q[1]), float(q[2]), float(q[3])),
        gyro_dps=float(gyro),
        accel_g=accel_mg / 1000.0,
        sample_rate_hz=odr_x10 / 10.0,
        swing_count=swings,
    )


def build_live(
    *,
    state: int,
    flags: int,
    seq: int,
    q: tuple[float, float, float, float],
    gyro_dps: float,
    accel_g: float,
    sample_rate_hz: float,
    swing_count: int,
) -> bytes:
    qi = [int(round(max(-1.0, min(1.0, c)) * 32767)) for c in q]
    return _LIVE.pack(
        PKT_LIVE,
        (state & 0x0F) | flags,
        seq & 0xFFFF,
        *qi,
        int(min(65535, max(0, round(gyro_dps)))),
        int(min(65535, max(0, round(accel_g * 1000)))),
        int(round(sample_rate_hz * 10)),
        swing_count & 0xFFFF,
    )


def parse_summary(data: bytes) -> SwingSummary:
    if len(data) != _SUMMARY.size or data[0] != PKT_SUMMARY:
        raise ProtocolError(f"not a summary packet ({len(data)} bytes)")
    _, flags, swing_id, peak, duration, accel_cg = _SUMMARY.unpack(data)
    return SwingSummary(
        swing_id=swing_id,
        impact=bool(flags & 0x01),
        accel_clipped=bool(flags & 0x02),
        gyro_clipped=bool(flags & 0x04),
        peak_omega_dps=float(peak),
        duration_ms=duration,
        peak_accel_g=accel_cg / 100.0,
    )


def build_summary(swing: RawSwing, duration_ms: int) -> bytes:
    lost = SWING_FLAG_HI_ACCEL_CLIPPED if swing.has_hi_accel else SWING_FLAG_ACCEL_CLIPPED
    flags = (
        (0x01 if swing.impact_idx >= 0 else 0)
        | (0x02 if swing.flags & lost else 0)
        | (0x04 if swing.flags & SWING_FLAG_GYRO_CLIPPED else 0)
    )
    return _SUMMARY.pack(
        PKT_SUMMARY,
        flags,
        swing.swing_id & 0xFFFF,
        int(min(65535, round(swing.peak_omega_dps))),
        int(min(65535, duration_ms)),
        int(min(65535, round(swing.peak_accel_g * 100))),
    )


def parse_blob(blob: bytes) -> RawSwing:
    if len(blob) < _HEADER_V1.size:
        raise ProtocolError(f"swing blob too short ({len(blob)} bytes)")
    legacy = blob[:4] == MAGIC_V1
    header = _HEADER_V1 if legacy else _HEADER
    sample_bytes = SAMPLE_BYTES_V1 if legacy else SAMPLE_BYTES
    if len(blob) < header.size:
        raise ProtocolError(f"swing blob too short ({len(blob)} bytes)")
    fields = header.unpack_from(blob, 0)
    magic, swing_id, count, trigger, peak, impact, flags = fields[:7]
    rate, gyro_scale, accel_scale = fields[7:10]
    q0 = fields[10:14]
    bias = fields[14:17]
    peak_omega, peak_accel = fields[17:19]
    hi_scale = 0.0 if legacy else fields[19]
    if legacy:
        flags &= ~(SWING_FLAG_HAS_HI_ACCEL | SWING_FLAG_HI_ACCEL_CLIPPED)

    if magic not in (MAGIC, MAGIC_V1):
        raise ProtocolError(f"bad magic {magic!r}")
    if not 0 < count <= MAX_SAMPLES:
        raise ProtocolError(f"sample count {count} out of range")
    if len(blob) != header.size + count * sample_bytes:
        raise ProtocolError(f"blob is {len(blob)} bytes, expected {header.size + count * sample_bytes}")
    if not (trigger < count and peak < count and -1 <= impact < count):
        raise ProtocolError("swing indices outside the sample window")
    if not 100.0 < rate < 10000.0 or gyro_scale <= 0 or accel_scale <= 0:
        raise ProtocolError("implausible sample rate or scale factors")
    if not all(np.isfinite(fields[7:])) or hi_scale < 0:
        raise ProtocolError("non-finite value in swing header")

    samples = np.frombuffer(blob, dtype="<i2", offset=header.size).reshape(count, sample_bytes // 2)
    return RawSwing(
        swing_id=swing_id,
        trigger_idx=trigger,
        peak_idx=peak,
        impact_idx=impact,
        flags=flags,
        sample_rate_hz=float(rate),
        gyro_dps_per_lsb=float(gyro_scale),
        accel_g_per_lsb=float(accel_scale),
        q0=tuple(float(v) for v in q0),
        gyro_bias_dps=tuple(float(v) for v in bias),
        peak_omega_dps=float(peak_omega),
        peak_accel_g=float(peak_accel),
        gyro=samples[:, 0:3].copy(),
        accel=samples[:, 3:6].copy(),
        accel_hi=None if legacy else samples[:, 6:9].copy(),
        hi_accel_g_per_lsb=float(hi_scale),
    )


def build_blob(swing: RawSwing) -> bytes:
    header = _HEADER.pack(
        MAGIC,
        swing.swing_id & 0xFFFF,
        swing.sample_count,
        swing.trigger_idx,
        swing.peak_idx,
        swing.impact_idx,
        swing.flags,
        swing.sample_rate_hz,
        swing.gyro_dps_per_lsb,
        swing.accel_g_per_lsb,
        *swing.q0,
        *swing.gyro_bias_dps,
        swing.peak_omega_dps,
        swing.peak_accel_g,
        swing.hi_accel_g_per_lsb,
    )
    samples = np.hstack([swing.gyro, swing.accel, swing.accel_hi]).astype("<i2")
    return header + samples.tobytes()


def chunk_blob(blob: bytes, swing_id: int, chunk_size: int) -> list[bytes]:
    """Frames a blob the way the firmware does: START, DATA..., END."""
    count = (len(blob) + chunk_size - 1) // chunk_size
    packets = [_XFER_START.pack(PKT_XFER_START, swing_id & 0xFFFF, len(blob), chunk_size, count)]
    for i in range(count):
        packets.append(_XFER_DATA_HEAD.pack(PKT_XFER_DATA, i) + blob[i * chunk_size : (i + 1) * chunk_size])
    packets.append(_XFER_END.pack(PKT_XFER_END, swing_id & 0xFFFF, zlib.crc32(blob) & 0xFFFFFFFF))
    return packets


def command_recalibrate() -> bytes:
    return bytes([CMD_RECALIBRATE])


def command_resend() -> bytes:
    return bytes([CMD_RESEND_SWING])


def command_set_thresholds(start_dps: int, min_peak_dps: int) -> bytes:
    return struct.pack("<BHH", CMD_SET_THRESHOLDS, start_dps, min_peak_dps)


@dataclass(frozen=True)
class TransferResult:
    ok: bool
    swing_id: int
    blob: bytes = b""
    error: str = ""


class TransferAssembler:
    """Rebuilds a swing blob from START / DATA / END notifications."""

    def __init__(self) -> None:
        self._active = False
        self._swing_id = 0
        self._buffer = bytearray()
        self._chunk_size = 0
        self._chunk_count = 0
        self._received: set[int] = set()

    @property
    def active(self) -> bool:
        return self._active

    @property
    def swing_id(self) -> int:
        return self._swing_id

    def abort(self) -> None:
        self._active = False

    def feed(self, packet: bytes) -> TransferResult | None:
        """Returns a result when a transfer finishes, otherwise None."""
        if not packet:
            return None
        kind = packet[0]

        if kind == PKT_XFER_START:
            if len(packet) != _XFER_START.size:
                return None
            _, swing_id, total, chunk_size, chunk_count = _XFER_START.unpack(packet)
            max_len = HEADER_BYTES + MAX_SAMPLES * SAMPLE_BYTES
            if not (HEADER_BYTES < total <= max_len) or chunk_size == 0:
                return TransferResult(False, swing_id, error="START packet has an implausible length")
            if chunk_count != (total + chunk_size - 1) // chunk_size:
                return TransferResult(False, swing_id, error="START packet chunk count does not match its length")
            self._active = True
            self._swing_id = swing_id
            self._buffer = bytearray(total)
            self._chunk_size = chunk_size
            self._chunk_count = chunk_count
            self._received = set()
            return None

        if not self._active:
            return None

        if kind == PKT_XFER_DATA:
            if len(packet) <= _XFER_DATA_HEAD.size:
                return None
            _, index = _XFER_DATA_HEAD.unpack_from(packet, 0)
            payload = packet[_XFER_DATA_HEAD.size :]
            offset = index * self._chunk_size
            if index >= self._chunk_count or offset + len(payload) > len(self._buffer):
                return None
            self._buffer[offset : offset + len(payload)] = payload
            self._received.add(index)
            return None

        if kind == PKT_XFER_END:
            if len(packet) != _XFER_END.size:
                return None
            _, swing_id, crc = _XFER_END.unpack(packet)
            self._active = False
            if swing_id != self._swing_id:
                return TransferResult(False, swing_id, error="END packet is for a different swing")
            missing = self._chunk_count - len(self._received)
            if missing:
                return TransferResult(False, swing_id, error=f"{missing} of {self._chunk_count} chunks missing")
            blob = bytes(self._buffer)
            if zlib.crc32(blob) & 0xFFFFFFFF != crc:
                return TransferResult(False, swing_id, error="checksum mismatch")
            return TransferResult(True, swing_id, blob=blob)

        return None
