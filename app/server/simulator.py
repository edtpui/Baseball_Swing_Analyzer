"""Synthetic sensor for developing and testing without hardware.

`synthesize_swing` builds a swing from a known kinematic model, runs it through
a model of the LSM6DSV320X (scale, bias, noise, quantisation, saturation) and
returns the raw capture together with the true answers. `SimulatedSensor`
replays such swings through the same packet framing the firmware uses, so the
whole receive path is exercised.
"""

from __future__ import annotations

import asyncio
import math
import random
from dataclasses import dataclass
from typing import Callable

import numpy as np

from . import protocol
from .processing import G

SAMPLE_RATE_HZ = 960.0
GYRO_DPS_PER_LSB = 0.140
ACCEL_G_PER_LSB = 0.000488
HI_ACCEL_G_PER_LSB = 0.003904  # high-g channel at +/-128 g
# The high-g channel shares the chip, axes and clock of the 16 g one. These
# figures for how it differs (gain, zero offset, noise) are assumptions, not
# datasheet values; they are deliberately on the pessimistic side.
HI_SCALE = 1.01
HI_OFFSET_G = np.array([0.6, -0.4, 0.8])
HI_NOISE_G = 0.15
OVERSAMPLE = 8
STILL_SAMPLES = 880
TAIL_SAMPLES = 480

PRE_SAMPLES = 720
POST_IMPACT_SAMPLES = 144
START_DPS = 500.0
END_DPS = 150.0
START_CONFIRM = 6
END_CONFIRM = 58
LIVE_HZ = 30.0
CHUNK_SIZE = 240


@dataclass(frozen=True)
class SwingParams:
    peak_rate_dps: float = 2100.0
    rise_time_s: float = 0.16
    attack_angle_deg: float = 8.0  # of the barrel's rotational velocity at contact
    vertical_bat_angle_deg: float = -25.0
    handedness: str = "R"
    hand_radius_m: float = 0.55
    hand_ratio: float = 0.33  # hand arc rate as a fraction of bat rotation rate
    heading_deg: float = 0.0  # which way the batter happens to be facing
    impact: bool = True
    bat_length_m: float = 0.838
    sweet_spot_from_tip_m: float = 0.152
    gyro_bias_dps: tuple[float, float, float] = (0.5, -0.3, 0.2)
    has_hi_accel: bool = True  # False imitates a capture from before the high-g channel existed


@dataclass
class SyntheticSwing:
    raw: protocol.RawSwing
    truth: dict
    quaternions: np.ndarray  # (N, 4) true orientation for every captured sample


def _rodrigues(axis: np.ndarray, angles: np.ndarray) -> np.ndarray:
    k = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    k2 = k @ k
    s = np.sin(angles)[:, None, None]
    c = (1 - np.cos(angles))[:, None, None]
    return np.eye(3)[None] + s * k[None] + c * k2[None]


def _quat_from_matrix(m: np.ndarray) -> np.ndarray:
    trace = m[0, 0] + m[1, 1] + m[2, 2]
    if trace > 0:
        s = math.sqrt(trace + 1.0) * 2
        q = [0.25 * s, (m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s]
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = math.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2
        q = [(m[2, 1] - m[1, 2]) / s, 0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s]
    elif m[1, 1] > m[2, 2]:
        s = math.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2
        q = [(m[0, 2] - m[2, 0]) / s, (m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s]
    else:
        s = math.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2
        q = [(m[1, 0] - m[0, 1]) / s, (m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s]
    q = np.array(q)
    return q / np.linalg.norm(q)


def synthesize_swing(params: SwingParams, rng: np.random.Generator, swing_id: int = 1) -> SyntheticSwing:
    fs = SAMPLE_RATE_HZ
    dt_fine = 1.0 / (fs * OVERSAMPLE)
    rise_samples = int(round(params.rise_time_s * fs))
    contact_sample = STILL_SAMPLES + rise_samples
    total_samples = contact_sample + TAIL_SAMPLES
    fine_count = total_samples * OVERSAMPLE + 1
    t = np.arange(fine_count) * dt_fine
    t_start = STILL_SAMPLES / fs
    t_contact = contact_sample / fs
    fall_time = 0.30

    up = np.clip((t - t_start) / (t_contact - t_start), 0.0, 1.0)
    down = np.clip((t - t_contact) / fall_time, 0.0, 1.0)
    peak = math.radians(params.peak_rate_dps)
    rate = np.where(t < t_contact, np.sin(0.5 * np.pi * up) ** 2, np.cos(0.5 * np.pi * down) ** 2) * peak
    contact_fine = contact_sample * OVERSAMPLE
    if params.impact:
        # The ball takes some of the bat's rotation. The step sits between two
        # sensor samples, so the contact sample itself is still clean.
        rate[contact_fine + OVERSAMPLE // 2 :] *= 0.88
    theta = np.concatenate([[0.0], np.cumsum(0.5 * (rate[1:] + rate[:-1])) * dt_fine])
    theta_c = theta[contact_fine]

    side = -1.0 if params.handedness == "R" else 1.0
    vba = math.radians(params.vertical_bat_angle_deg)
    u_c = np.array([0.0, side * math.cos(vba), math.sin(vba)])  # knob -> barrel at contact
    att = math.radians(params.attack_angle_deg)
    v_hat = np.array([math.cos(att), 0.0, math.sin(att)])
    v_hat -= v_hat.dot(u_c) * u_c
    v_hat /= np.linalg.norm(v_hat)
    axis = np.cross(u_c, v_hat)  # rotating about this drives the barrel along v_hat

    z_b = -u_c  # the chip faces away from the bat, so the barrel lies along -Z
    x_b = axis
    y_b = np.cross(z_b, x_b)
    r_contact = np.column_stack([x_b, y_b, z_b])
    rot = _rodrigues(axis, theta - theta_c) @ r_contact

    e1 = np.array([v_hat[0], v_hat[1], 0.0])
    e1 /= np.linalg.norm(e1)
    e2 = np.cross([0.0, 0.0, 1.0], e1) * -side
    phi = params.hand_ratio * (theta - theta_c)
    progress = np.clip(theta / theta_c, 0.0, 1.0)
    knob = (
        params.hand_radius_m * np.sin(phi)[:, None] * e1
        + params.hand_radius_m * (1 - np.cos(phi))[:, None] * e2
        + np.array([0.0, 0.0, -0.12])[None] * (progress**2 * (3 - 2 * progress))[:, None]
    )

    heading = math.radians(params.heading_deg)
    rz = np.array(
        [[math.cos(heading), -math.sin(heading), 0.0], [math.sin(heading), math.cos(heading), 0.0], [0.0, 0.0, 1.0]]
    )
    rot = rz[None] @ rot
    knob = knob @ rz.T

    accel_world = np.gradient(np.gradient(knob, dt_fine, axis=0), dt_fine, axis=0)
    accel_world[:, 2] += G
    force_body = np.einsum("nji,nj->ni", rot, accel_world)
    # Rotation is about a fixed axis that is the body X axis, so the gyro sees it on X only.
    omega_body = np.zeros((fine_count, 3))
    omega_body[:, 0] = rate

    sweet = params.bat_length_m - params.sweet_spot_from_tip_m
    barrel_dir = np.einsum("nij,j->ni", rot, np.array([0.0, 0.0, -1.0]))
    sweet_pos = knob + barrel_dir * sweet
    sweet_vel = np.gradient(sweet_pos, dt_fine, axis=0)
    grip_vel = np.gradient(knob + barrel_dir * 0.15, dt_fine, axis=0)

    coarse = slice(0, fine_count - 1, OVERSAMPLE)
    gyro_dps = np.rad2deg(omega_body[coarse]) + np.asarray(params.gyro_bias_dps)
    accel_g = force_body[coarse] / G
    count = gyro_dps.shape[0]
    hi_g = accel_g * HI_SCALE + HI_OFFSET_G

    impact_sample = contact_sample + 1
    if params.impact:
        spike = np.array([30.0, -45.0, 20.0])
        for offset, gain in enumerate((1.0, -0.6, 0.3)):
            accel_g[impact_sample + offset] += spike * gain
            hi_g[impact_sample + offset] += spike * gain

    gyro_raw = np.clip(np.round(gyro_dps / GYRO_DPS_PER_LSB + rng.normal(0, 0.7, (count, 3))), -32768, 32767)
    accel_raw = np.clip(np.round(accel_g / ACCEL_G_PER_LSB + rng.normal(0, 4.0, (count, 3))), -32768, 32767)
    gyro_raw = gyro_raw.astype(np.int16)
    accel_raw = accel_raw.astype(np.int16)
    hi_counts = np.round((hi_g + rng.normal(0, HI_NOISE_G, (count, 3))) / HI_ACCEL_G_PER_LSB)
    hi_raw = np.clip(hi_counts, -32768, 32767).astype(np.int16)
    if not params.has_hi_accel:
        hi_raw[:] = 0

    # Window selection mirrors SwingCapture.cpp.
    perp = np.hypot(gyro_dps[:, 0] - params.gyro_bias_dps[0], gyro_dps[:, 1] - params.gyro_bias_dps[1])
    above = perp >= START_DPS
    run = np.convolve(above.astype(int), np.ones(START_CONFIRM, dtype=int), mode="valid")
    confirmed = np.flatnonzero(run == START_CONFIRM)
    if confirmed.size == 0:
        raise ValueError("swing never crosses the start threshold")
    trigger = int(confirmed[0])
    start = max(0, trigger - PRE_SAMPLES)
    if params.impact:
        end = min(count - 1, impact_sample + POST_IMPACT_SAMPLES)
        peak_idx = trigger + int(np.argmax(perp[trigger:impact_sample]))
    else:
        below = np.convolve((perp < END_DPS).astype(int), np.ones(END_CONFIRM, dtype=int), mode="valid")
        ends = np.flatnonzero(below[trigger:] == END_CONFIRM)
        end = min(count - 1, trigger + int(ends[0]) + END_CONFIRM - 1) if ends.size else count - 1
        peak_idx = trigger + int(np.argmax(perp[trigger : end + 1]))

    clip = 32700
    pre_impact = slice(trigger, impact_sample if params.impact else end + 1)
    flags = 0
    if np.any(np.abs(accel_raw[pre_impact].astype(int)) >= clip):
        flags |= protocol.SWING_FLAG_ACCEL_CLIPPED
    if np.any(np.abs(gyro_raw[pre_impact].astype(int)) >= clip):
        flags |= protocol.SWING_FLAG_GYRO_CLIPPED
    if params.has_hi_accel:
        flags |= protocol.SWING_FLAG_HAS_HI_ACCEL
        if np.any(np.abs(hi_raw[pre_impact].astype(int)) >= clip):
            flags |= protocol.SWING_FLAG_HI_ACCEL_CLIPPED

    quaternions = np.array([_quat_from_matrix(m) for m in rot[coarse][start : end + 1]])
    raw = protocol.RawSwing(
        swing_id=swing_id,
        trigger_idx=trigger - start,
        peak_idx=peak_idx - start,
        impact_idx=impact_sample - start if params.impact else -1,
        flags=flags,
        sample_rate_hz=fs,
        gyro_dps_per_lsb=GYRO_DPS_PER_LSB,
        accel_g_per_lsb=ACCEL_G_PER_LSB,
        q0=tuple(float(v) for v in quaternions[0]),
        gyro_bias_dps=params.gyro_bias_dps,
        peak_omega_dps=float(perp[peak_idx]),
        peak_accel_g=float(np.linalg.norm(accel_g[trigger : end + 1], axis=1).max()),
        gyro=gyro_raw[start : end + 1],
        accel=accel_raw[start : end + 1],
        accel_hi=hi_raw[start : end + 1],
        hi_accel_g_per_lsb=HI_ACCEL_G_PER_LSB,
    )

    if params.impact:
        truth_fine = contact_fine
    else:
        truth_fine = int(np.argmax(np.linalg.norm(sweet_vel, axis=1)))
    v = sweet_vel[truth_fine]
    speed = float(np.linalg.norm(v))
    swing_fine = slice(STILL_SAMPLES * OVERSAMPLE, truth_fine + 1)
    truth = {
        "bat_speed_mps": speed,
        "attack_angle_deg": math.degrees(math.asin(v[2] / speed)),
        "vertical_bat_angle_deg": math.degrees(math.asin(barrel_dir[truth_fine, 2])),
        "peak_hand_speed_mps": float(np.linalg.norm(grip_vel[swing_fine], axis=1).max()),
        "handedness": params.handedness,
    }
    return SyntheticSwing(raw=raw, truth=truth, quaternions=quaternions)


def random_params(rng: random.Random) -> SwingParams:
    return SwingParams(
        peak_rate_dps=rng.uniform(1600.0, 2250.0),
        rise_time_s=rng.uniform(0.14, 0.19),
        attack_angle_deg=rng.uniform(-2.0, 16.0),
        vertical_bat_angle_deg=rng.uniform(-36.0, -14.0),
        heading_deg=rng.uniform(-180.0, 180.0),
        impact=rng.random() < 0.8,
    )


class SimulatedSensor:
    """Stands in for the BLE link. Emits the same bytes the firmware would."""

    def __init__(
        self,
        on_status: Callable[[dict], None],
        on_live: Callable[[bytes], None],
        on_swing_packet: Callable[[bytes], None],
        auto_interval_s: float | None = None,
        seed: int | None = None,
    ) -> None:
        self._on_status = on_status
        self._on_live = on_live
        self._on_swing_packet = on_swing_packet
        self._auto_interval = auto_interval_s
        self._random = random.Random(seed)
        self._rng = np.random.default_rng(seed)
        self._swing_requested = asyncio.Event()
        self._swing_count = 0
        self._seq = 0
        self._last_packets: list[bytes] = []
        self._calibrated_at = 0.0
        self._next: SyntheticSwing | None = None

    def request_swing(self) -> None:
        self._swing_requested.set()

    async def send_command(self, data: bytes) -> None:
        if not data:
            return
        if data[0] == protocol.CMD_RECALIBRATE:
            self._calibrated_at = asyncio.get_running_loop().time() + 1.0
        elif data[0] == protocol.CMD_RESEND_SWING and self._last_packets:
            for packet in self._last_packets:
                self._on_swing_packet(packet)
                await asyncio.sleep(0)

    def _live(self, q: np.ndarray, state: int, gyro_dps: float, accel_g: float, transferring: bool = False) -> None:
        flags = 0
        if asyncio.get_running_loop().time() >= self._calibrated_at:
            flags |= protocol.LIVE_FLAG_CALIBRATED
        if transferring:
            flags |= protocol.LIVE_FLAG_TRANSFER
        self._on_live(
            protocol.build_live(
                state=state,
                flags=flags,
                seq=self._seq,
                q=tuple(float(v) for v in q),
                gyro_dps=gyro_dps,
                accel_g=accel_g,
                sample_rate_hz=SAMPLE_RATE_HZ,
                swing_count=self._swing_count,
            )
        )
        self._seq += 1

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        self._on_status(
            {
                "state": "connected",
                "name": "SwingSensor (simulated)",
                "address": "simulated",
                "info": {"fw": "simulator", "proto": str(protocol.PROTOCOL_VERSION)},
            }
        )
        period = 1.0 / LIVE_HZ
        next_auto = loop.time() + self._auto_interval if self._auto_interval else None
        while True:
            if self._next is None:
                self._next = synthesize_swing(random_params(self._random), self._rng, self._swing_count + 1)
            stance = self._next.quaternions[0]
            # A resting batter is never perfectly still: add a slow wobble about the bat's own axis.
            wobble = 0.03 * math.sin(loop.time() * 1.7)
            dq = np.array([math.cos(wobble / 2), 0.0, 0.0, math.sin(wobble / 2)])
            w, x, y, z = stance
            q = np.array(
                [
                    w * dq[0] - z * dq[3],
                    x * dq[0] + y * dq[3],
                    y * dq[0] - x * dq[3],
                    z * dq[0] + w * dq[3],
                ]
            )
            self._live(q, 0, abs(wobble) * 60, 1.0)

            due = self._swing_requested.is_set() or (next_auto is not None and loop.time() >= next_auto)
            if due:
                self._swing_requested.clear()
                await self._play(self._next)
                self._next = None
                if self._auto_interval:
                    next_auto = loop.time() + self._auto_interval
            await asyncio.sleep(period)

    async def _play(self, swing: SyntheticSwing) -> None:
        raw = swing.raw
        fs = raw.sample_rate_hz
        self._swing_count += 1
        gyro_dps = np.linalg.norm(raw.gyro.astype(float) * raw.gyro_dps_per_lsb, axis=1)
        accel_g = np.linalg.norm(raw.accel.astype(float) * raw.accel_g_per_lsb, axis=1)
        step = max(1, int(round(fs / LIVE_HZ)))
        for i in range(max(0, raw.trigger_idx - step * 6), raw.sample_count, step):
            self._live(swing.quaternions[i], 1 if i >= raw.trigger_idx else 0, float(gyro_dps[i]), float(accel_g[i]))
            await asyncio.sleep(step / fs)

        duration_ms = int(round((raw.sample_count - raw.trigger_idx) * 1000.0 / fs))
        self._on_live(protocol.build_summary(raw, duration_ms))
        blob = protocol.build_blob(raw)
        self._last_packets = protocol.chunk_blob(blob, raw.swing_id, CHUNK_SIZE)
        for packet in self._last_packets:
            self._on_swing_packet(packet)
            await asyncio.sleep(0.003)
