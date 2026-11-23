"""Rebuilds bat motion from one captured swing.

Frames: the sensor body frame has Z along the bat. The world frame has Z up.
Quaternions are body -> world, Hamilton convention, (w, x, y, z).

The reported path is rotated about the vertical so +X is the direction the
barrel is travelling at contact, and shifted so the knob is at the origin at
contact. That makes swings directly comparable with each other.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np

from .protocol import (
    SWING_FLAG_ACCEL_CLIPPED,
    SWING_FLAG_GYRO_CLIPPED,
    SWING_FLAG_HI_ACCEL_CLIPPED,
    SWING_FLAG_TIMED_OUT,
    SWING_FLAG_UNCALIBRATED,
    RawSwing,
)

G = 9.80665

ANCHOR_WINDOW_S = 0.04
ANCHOR_MAX_RATE = 1.0  # rad/s, mean over the window
ANCHOR_MAX_ACCEL_DEV = 0.08  # g, mean deviation of |a| from 1 g
SWING_START_FRACTION = 0.15  # of the rate at contact
LEAD_IN_S = 0.12
FOLLOW_THROUGH_S = 0.08
MAX_OUTPUT_POINTS = 260

# Merging the high-g accelerometer channel with the +/-16 g one.
HI_FIT_LIMIT_G = 12.0  # the two channels are compared wherever the 16 g one reads below this
HI_BLEND_START_G = 12.0  # below this the 16 g channel is used alone
HI_BLEND_FULL_G = 14.5  # above this the high-g channel is used alone
HI_FIT_MAX_RMS_G = 1.0  # largest disagreement, after removing the offset, still treated as noise
HI_MAX_OFFSET_G = 8.0  # a zero offset beyond this means the channel is not working
HI_FIT_MIN_SAMPLES = 50


@dataclass(frozen=True)
class BatConfig:
    length_m: float = 0.838  # 33 in
    sweet_spot_from_tip_m: float = 0.152  # 6 in
    grip_from_knob_m: float = 0.15
    sensor_faces_out: bool = True  # chip side of the board faces away from the bat

    @property
    def axis_body(self) -> np.ndarray:
        """Unit vector from knob to barrel in the sensor frame."""
        return np.array([0.0, 0.0, -1.0 if self.sensor_faces_out else 1.0])

    @property
    def sweet_spot_from_knob_m(self) -> float:
        return self.length_m - self.sweet_spot_from_tip_m


def quat_multiply(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return np.array(
        [
            aw * bw - ax * bx - ay * by - az * bz,
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
        ]
    )


def rotation_matrices(q: np.ndarray) -> np.ndarray:
    """(N, 4) quaternions -> (N, 3, 3) body-to-world matrices."""
    w, x, y, z = q[:, 0], q[:, 1], q[:, 2], q[:, 3]
    r = np.empty((q.shape[0], 3, 3))
    r[:, 0, 0] = 1 - 2 * (y * y + z * z)
    r[:, 0, 1] = 2 * (x * y - w * z)
    r[:, 0, 2] = 2 * (x * z + w * y)
    r[:, 1, 0] = 2 * (x * y + w * z)
    r[:, 1, 1] = 1 - 2 * (x * x + z * z)
    r[:, 1, 2] = 2 * (y * z - w * x)
    r[:, 2, 0] = 2 * (x * z - w * y)
    r[:, 2, 1] = 2 * (y * z + w * x)
    r[:, 2, 2] = 1 - 2 * (x * x + y * y)
    return r


def integrate_orientation(omega: np.ndarray, dt: float, q0: np.ndarray) -> np.ndarray:
    """Integrates body angular rate (rad/s) from q0 using the midpoint rate per step."""
    n = omega.shape[0]
    q = np.empty((n, 4))
    norm0 = np.linalg.norm(q0)
    q[0] = q0 / norm0 if norm0 > 1e-9 else np.array([1.0, 0.0, 0.0, 0.0])
    mid = 0.5 * (omega[:-1] + omega[1:])
    angle = np.linalg.norm(mid, axis=1) * dt
    half = 0.5 * angle
    # sin(half)/angle -> 0.5 as the angle goes to zero.
    scale = np.where(angle > 1e-12, np.sin(half) / np.maximum(angle, 1e-12), 0.5) * dt
    dq = np.column_stack([np.cos(half), mid * scale[:, None]])
    for k in range(n - 1):
        nxt = quat_multiply(q[k], dq[k])
        q[k + 1] = nxt / np.linalg.norm(nxt)
    return q


def _cumulative_trapezoid(x: np.ndarray, dt: float) -> np.ndarray:
    out = np.zeros_like(x)
    out[1:] = np.cumsum(0.5 * (x[1:] + x[:-1]), axis=0) * dt
    return out


def _find_anchor(omega: np.ndarray, acc: np.ndarray, trigger: int, fs: float) -> tuple[int, int, str]:
    """Picks the quietest moment before the swing: the bat is assumed at rest there.

    Returns (anchor index, half window, quality).
    """
    window = max(3, int(round(ANCHOR_WINDOW_S * fs)))
    half = window // 2
    if trigger < window + 1:
        return 0, 0, "none"
    rate = np.linalg.norm(omega[:trigger], axis=1)
    accel_dev = np.abs(np.linalg.norm(acc[:trigger], axis=1) / G - 1.0)
    kernel = np.ones(window) / window
    mean_rate = np.convolve(rate, kernel, mode="valid")
    mean_dev = np.convolve(accel_dev, kernel, mode="valid")
    centre = np.arange(mean_rate.shape[0]) + half
    # Prefer a quiet moment close to the swing: less time for drift to build.
    score = mean_rate + 3.0 * mean_dev + 0.3 * (trigger - centre) / fs
    best = int(np.argmin(score))
    good = mean_rate[best] < ANCHOR_MAX_RATE and mean_dev[best] < ANCHOR_MAX_ACCEL_DEV
    return int(centre[best]), half, "good" if good else "moving"


def _fuse_accel(raw: RawSwing, last_clean: int) -> tuple[np.ndarray, str]:
    """Returns acceleration in g, and where it came from.

    The sensor has two accelerometer channels on one chip: a precise one that
    stops at 16 g and a coarser high-g one. They share axes and sample timing,
    so the only thing to reconcile is the high-g channel's zero offset, which
    is measured from the part of the swing both can read. The high-g readings
    then take over where the 16 g channel runs out of range.

    Source is "main" (capture has no high-g channel), "fused", or "mismatch"
    (the channels disagreed, so the 16 g one is used alone).
    """
    low = raw.accel.astype(float) * raw.accel_g_per_lsb
    if not raw.has_hi_accel:
        return low, "main"

    high = raw.accel_hi.astype(float) * raw.hi_accel_g_per_lsb
    low_peak = np.abs(low).max(axis=1)
    usable = np.zeros(low.shape[0], dtype=bool)
    usable[: max(0, last_clean + 1)] = True
    usable &= low_peak < HI_FIT_LIMIT_G
    if int(usable.sum()) < HI_FIT_MIN_SAMPLES:
        return low, "mismatch"

    difference = low[usable] - high[usable]
    offset = difference.mean(axis=0)
    rms = float(np.sqrt(((difference - offset) ** 2).sum(axis=1).mean()))
    if rms > HI_FIT_MAX_RMS_G or float(np.abs(offset).max()) > HI_MAX_OFFSET_G:
        return low, "mismatch"

    weight = np.clip((low_peak - HI_BLEND_START_G) / (HI_BLEND_FULL_G - HI_BLEND_START_G), 0.0, 1.0)[:, None]
    return (1.0 - weight) * low + weight * (high + offset), "fused"


def _rounded(values: np.ndarray, digits: int) -> list:
    return np.round(values, digits).tolist()


def process_swing(raw: RawSwing, bat: BatConfig) -> dict:
    """Returns metrics, speed curves and the 3D path for one swing (SI units, degrees)."""
    n = raw.sample_count
    fs = raw.sample_rate_hz
    dt = 1.0 / fs
    if n < 8:
        raise ValueError(f"swing has only {n} samples")

    omega = np.deg2rad(raw.gyro.astype(float) * raw.gyro_dps_per_lsb - np.asarray(raw.gyro_bias_dps))
    trigger = min(raw.trigger_idx, n - 1)
    impact = raw.impact_idx > trigger
    accel_g, accel_source = _fuse_accel(raw, raw.impact_idx - 1 if impact else n - 1)
    acc = accel_g * G

    q = integrate_orientation(omega, dt, np.asarray(raw.q0, dtype=float))
    rot = rotation_matrices(q)

    anchor, half, anchor_quality = _find_anchor(omega, acc, trigger, fs)

    gravity = G
    if anchor_quality == "good":
        # At rest the accelerometer reads straight up; use that to level the
        # orientation and to cancel any scale error in the accelerometer.
        lo, hi = anchor - half, anchor + half + 1
        up_measured = np.einsum("nij,nj->ni", rot[lo:hi], acc[lo:hi]).mean(axis=0)
        magnitude = float(np.linalg.norm(up_measured))
        if magnitude > 0.5 * G:
            gravity = magnitude
            u = up_measured / magnitude
            axis = np.cross(u, [0.0, 0.0, 1.0])
            sin_a = float(np.linalg.norm(axis))
            if sin_a > 1e-9:
                angle = math.atan2(sin_a, float(u[2]))
                correction = np.concatenate([[math.cos(angle / 2)], axis / sin_a * math.sin(angle / 2)])
                q = np.array([quat_multiply(correction, qk) for qk in q])
                rot = rotation_matrices(q)

    acc_world = np.einsum("nij,nj->ni", rot, acc)
    acc_world[:, 2] -= gravity
    velocity = _cumulative_trapezoid(acc_world, dt)
    velocity -= velocity[anchor]
    position = _cumulative_trapezoid(velocity, dt)
    position -= position[anchor]

    axis_world = np.einsum("nij,j->ni", rot, bat.axis_body)
    omega_world = np.einsum("nij,nj->ni", rot, omega)
    perp_rate = np.hypot(omega[:, 0], omega[:, 1])

    def point_velocity(distance_m: float) -> np.ndarray:
        return velocity + np.cross(omega_world, axis_world * distance_m)

    sweet_velocity = point_velocity(bat.sweet_spot_from_knob_m)
    sweet_speed = np.linalg.norm(sweet_velocity, axis=1)
    grip_speed = np.linalg.norm(point_velocity(bat.grip_from_knob_m), axis=1)

    if impact:
        contact = raw.impact_idx - 1  # last clean sample before the strike
    else:
        contact = trigger + int(np.argmax(sweet_speed[trigger:]))

    start = contact
    threshold = SWING_START_FRACTION * perp_rate[contact]
    while start > 0 and perp_rate[start] > threshold:
        start -= 1

    # The strike corrupts the accelerometer, so the knob coasts at its contact
    # velocity for the short follow-through that is shown.
    after = np.arange(contact + 1, n)
    position[after] = position[contact] + velocity[contact] * ((after - contact) * dt)[:, None]

    contact_velocity = sweet_velocity[contact]
    contact_speed = float(sweet_speed[contact])
    yaw = math.atan2(float(contact_velocity[1]), float(contact_velocity[0]))
    cos_y, sin_y = math.cos(-yaw), math.sin(-yaw)
    heading = np.array([[cos_y, -sin_y, 0.0], [sin_y, cos_y, 0.0], [0.0, 0.0, 1.0]])
    position = (position - position[contact]) @ heading.T
    axis_world = axis_world @ heading.T

    swing = slice(start, contact + 1)
    attack_angle = math.degrees(math.asin(np.clip(contact_velocity[2] / max(contact_speed, 1e-9), -1.0, 1.0)))
    vertical_bat_angle = math.degrees(math.asin(np.clip(axis_world[contact, 2], -1.0, 1.0)))

    flags = raw.flags
    reasons = []
    if anchor_quality != "good":
        reasons.append("bat was not still before the swing" if anchor_quality == "moving" else "no lead-in captured")
    if accel_source == "fused":
        accel_lost = bool(flags & SWING_FLAG_HI_ACCEL_CLIPPED)
        if accel_lost:
            limit_g = 32767 * raw.hi_accel_g_per_lsb
            reasons.append(f"acceleration went past {limit_g:.0f} g before contact")
    else:
        accel_lost = bool(flags & SWING_FLAG_ACCEL_CLIPPED)
        if accel_source == "mismatch":
            reasons.append("the high-g readings disagreed with the 16 g ones, so only the 16 g ones were used")
        if accel_lost:
            reasons.append("accelerometer saturated before contact")
    if flags & SWING_FLAG_GYRO_CLIPPED:
        reasons.append("gyroscope saturated before contact")
    if flags & SWING_FLAG_UNCALIBRATED:
        reasons.append("gyro bias not calibrated")
    if flags & SWING_FLAG_TIMED_OUT:
        reasons.append("swing ran past the capture window")

    lead = int(round(LEAD_IN_S * fs))
    follow = int(round(FOLLOW_THROUGH_S * fs))
    first = max(0, start - lead)
    last = min(n - 1, contact + follow)
    step = max(1, math.ceil((last - first + 1) / MAX_OUTPUT_POINTS))
    before = (contact - first) // step
    idx = contact + np.arange(-before, (last - contact) // step + 1) * step

    return {
        "metrics": {
            "bat_speed_mps": round(contact_speed, 3),
            "peak_bat_speed_mps": round(float(sweet_speed[swing].max()), 3),
            "peak_hand_speed_mps": round(float(grip_speed[swing].max()), 3),
            "attack_angle_deg": round(attack_angle, 2),
            "vertical_bat_angle_deg": round(vertical_bat_angle, 2),
            "time_to_contact_ms": round((contact - start) * dt * 1000.0, 1),
            "peak_omega_dps": round(float(np.rad2deg(perp_rate[swing].max())), 1),
            "impact": bool(impact),
            "handedness": "R" if omega_world[contact, 2] > 0 else "L",
        },
        "quality": {
            "confidence": "reduced" if reasons else "high",
            "reasons": reasons,
            "anchor": anchor_quality,
            "accel_clipped": accel_lost,
            "accel_source": accel_source,
            "gyro_clipped": bool(flags & SWING_FLAG_GYRO_CLIPPED),
            "calibrated": not flags & SWING_FLAG_UNCALIBRATED,
        },
        "series": {
            "t_ms": _rounded((idx - contact) * dt * 1000.0, 2),
            "bat_speed_mps": _rounded(sweet_speed[idx], 3),
            "hand_speed_mps": _rounded(grip_speed[idx], 3),
            "omega_dps": _rounded(np.rad2deg(perp_rate[idx]), 1),
        },
        "path": {
            "knob": _rounded(position[idx], 4),
            "dir": _rounded(axis_world[idx], 4),
            "contact_index": int(before),
            "start_index": int(max(0, before - (contact - start) // step)),
        },
        "bat": asdict(bat),
        "sample_rate_hz": round(fs, 2),
        "sample_count": n,
    }
