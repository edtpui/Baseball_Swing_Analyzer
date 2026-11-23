"""Checks the reconstruction against swings whose true motion is known."""

import numpy as np
import pytest

from server import protocol
from server.processing import BatConfig, integrate_orientation, process_swing
from server.simulator import SwingParams, synthesize_swing

BAT = BatConfig()

CASES = [
    SwingParams(),
    SwingParams(peak_rate_dps=1500.0, rise_time_s=0.19, attack_angle_deg=-3.0, vertical_bat_angle_deg=-15.0),
    SwingParams(peak_rate_dps=2400.0, rise_time_s=0.14, attack_angle_deg=15.0, vertical_bat_angle_deg=-35.0),
    SwingParams(handedness="L", heading_deg=140.0),
    SwingParams(heading_deg=-75.0, impact=False),
]


@pytest.mark.parametrize("params", CASES)
def test_metrics_match_ground_truth(params):
    swing = synthesize_swing(params, np.random.default_rng(42))
    result = process_swing(swing.raw, BAT)
    m, truth = result["metrics"], swing.truth

    assert m["bat_speed_mps"] == pytest.approx(truth["bat_speed_mps"], rel=0.01)
    assert m["attack_angle_deg"] == pytest.approx(truth["attack_angle_deg"], abs=0.5)
    assert m["vertical_bat_angle_deg"] == pytest.approx(truth["vertical_bat_angle_deg"], abs=0.5)
    assert m["peak_hand_speed_mps"] == pytest.approx(truth["peak_hand_speed_mps"], rel=0.02)
    assert m["handedness"] == truth["handedness"]
    assert m["impact"] == params.impact
    assert m["peak_omega_dps"] == pytest.approx(params.peak_rate_dps, rel=0.01)
    assert 100.0 < m["time_to_contact_ms"] < 220.0
    # The fastest case exceeds 16 g at the knob; the high-g channel covers it.
    assert result["quality"]["accel_source"] == "fused"
    assert result["quality"]["confidence"] == "high"


def test_path_is_heading_normalised_and_anchored_at_contact():
    a = process_swing(synthesize_swing(SwingParams(heading_deg=0.0), np.random.default_rng(3)).raw, BAT)
    b = process_swing(synthesize_swing(SwingParams(heading_deg=123.0), np.random.default_rng(3)).raw, BAT)
    for result in (a, b):
        path = result["path"]
        c = path["contact_index"]
        assert path["knob"][c] == pytest.approx([0.0, 0.0, 0.0], abs=1e-6)
        assert len(path["knob"]) == len(path["dir"]) == len(result["series"]["t_ms"])
        assert result["series"]["t_ms"][c] == 0.0
        assert np.linalg.norm(path["dir"], axis=1) == pytest.approx(1.0, abs=1e-3)
    # The same swing faced a different way must come out as the same path.
    np.testing.assert_allclose(a["path"]["knob"], b["path"]["knob"], atol=0.01)
    np.testing.assert_allclose(a["path"]["dir"], b["path"]["dir"], atol=0.01)


def test_barrel_travels_along_plus_x_at_contact():
    result = process_swing(synthesize_swing(SwingParams(heading_deg=60.0), np.random.default_rng(5)).raw, BAT)
    path = result["path"]
    c = path["contact_index"]
    sweet = np.array(path["knob"]) + np.array(path["dir"]) * BAT.sweet_spot_from_knob_m
    travel = sweet[c] - sweet[c - 1]
    assert travel[0] > 0 and abs(travel[1]) < 0.1 * travel[0]


FAST = dict(peak_rate_dps=2400.0, hand_radius_m=0.45, hand_ratio=0.5)  # over 16 g at the knob


def test_capture_without_high_g_channel_still_matches_ground_truth():
    swing = synthesize_swing(SwingParams(has_hi_accel=False), np.random.default_rng(42))
    assert not swing.raw.has_hi_accel
    result = process_swing(swing.raw, BAT)
    assert result["quality"]["accel_source"] == "main"
    assert result["quality"]["confidence"] == "high"
    assert result["metrics"]["bat_speed_mps"] == pytest.approx(swing.truth["bat_speed_mps"], rel=0.01)


def test_high_g_channel_recovers_a_swing_the_16_g_channel_cannot_measure():
    alone = synthesize_swing(SwingParams(has_hi_accel=False, **FAST), np.random.default_rng(9))
    both = synthesize_swing(SwingParams(has_hi_accel=True, **FAST), np.random.default_rng(9))
    assert alone.raw.flags & protocol.SWING_FLAG_ACCEL_CLIPPED
    assert not both.raw.flags & protocol.SWING_FLAG_HI_ACCEL_CLIPPED
    truth = both.truth

    fused = process_swing(both.raw, BAT)
    assert fused["quality"]["accel_source"] == "fused"
    assert fused["quality"]["confidence"] == "high"
    assert fused["metrics"]["bat_speed_mps"] == pytest.approx(truth["bat_speed_mps"], rel=0.01)
    assert fused["metrics"]["attack_angle_deg"] == pytest.approx(truth["attack_angle_deg"], abs=0.5)
    assert fused["metrics"]["peak_hand_speed_mps"] == pytest.approx(truth["peak_hand_speed_mps"], rel=0.02)

    clipped = process_swing(alone.raw, BAT)
    error_alone = abs(clipped["metrics"]["peak_hand_speed_mps"] - truth["peak_hand_speed_mps"])
    error_fused = abs(fused["metrics"]["peak_hand_speed_mps"] - truth["peak_hand_speed_mps"])
    assert error_fused < error_alone


def test_acceleration_beyond_both_channels_is_flagged():
    raw = synthesize_swing(SwingParams(**FAST), np.random.default_rng(9)).raw
    raw.flags |= protocol.SWING_FLAG_HI_ACCEL_CLIPPED  # as the firmware reports past +/-128 g
    result = process_swing(raw, BAT)
    assert result["quality"]["confidence"] == "reduced"
    assert any("128 g" in reason for reason in result["quality"]["reasons"])


def test_disagreeing_channels_fall_back_to_the_16_g_one():
    raw = synthesize_swing(SwingParams(), np.random.default_rng(21)).raw
    raw.accel_hi = np.random.default_rng(1).integers(-9000, 9000, raw.accel_hi.shape).astype(np.int16)
    result = process_swing(raw, BAT)
    assert result["quality"]["accel_source"] == "mismatch"
    assert result["quality"]["confidence"] == "reduced"


def test_saturated_accelerometer_is_flagged():
    # Tight, fast hand path: more than 16 g at the knob before contact, and no high-g channel.
    params = SwingParams(peak_rate_dps=2400.0, hand_radius_m=0.30, hand_ratio=0.75, has_hi_accel=False)
    swing = synthesize_swing(params, np.random.default_rng(9))
    assert swing.raw.flags & protocol.SWING_FLAG_ACCEL_CLIPPED
    result = process_swing(swing.raw, BAT)
    assert result["quality"]["confidence"] == "reduced"
    assert result["quality"]["accel_clipped"]


def test_moving_lead_in_is_flagged():
    swing = synthesize_swing(SwingParams(), np.random.default_rng(11))
    raw = swing.raw
    # Shake the bat through the whole lead-in so there is no still moment to anchor on.
    t = np.arange(raw.trigger_idx) / raw.sample_rate_hz
    shake = (400.0 / raw.gyro_dps_per_lsb * np.sin(2 * np.pi * 6.0 * t)).astype(np.int16)
    raw.gyro[: raw.trigger_idx, 1] += shake
    result = process_swing(raw, BAT)
    assert result["quality"]["anchor"] == "moving"
    assert result["quality"]["confidence"] == "reduced"


def test_sensor_facing_setting_flips_the_bat():
    raw = synthesize_swing(SwingParams(), np.random.default_rng(13)).raw
    out = process_swing(raw, BatConfig(sensor_faces_out=True))
    flipped = process_swing(raw, BatConfig(sensor_faces_out=False))
    c = out["path"]["contact_index"]
    assert out["path"]["dir"][c][2] == pytest.approx(-flipped["path"]["dir"][c][2], abs=0.02)


def test_orientation_integration_is_exact_for_constant_rate():
    fs, rate = 960.0, np.deg2rad(2000.0)
    n = int(fs * 0.5) + 1
    omega = np.tile([0.0, 0.0, rate], (n, 1))
    q = integrate_orientation(omega, 1.0 / fs, np.array([1.0, 0.0, 0.0, 0.0]))
    angle = rate * (n - 1) / fs
    assert q[-1] == pytest.approx([np.cos(angle / 2), 0.0, 0.0, np.sin(angle / 2)], abs=1e-9)


def test_rejects_tiny_capture():
    raw = synthesize_swing(SwingParams(), np.random.default_rng(1)).raw
    raw.gyro, raw.accel, raw.accel_hi = raw.gyro[:4], raw.accel[:4], raw.accel_hi[:4]
    with pytest.raises(ValueError):
        process_swing(raw, BAT)
