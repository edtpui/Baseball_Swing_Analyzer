import numpy as np
import pytest

from server import protocol
from server.simulator import SwingParams, synthesize_swing


@pytest.fixture(scope="module")
def raw():
    return synthesize_swing(SwingParams(), np.random.default_rng(1), swing_id=7).raw


def test_blob_round_trip(raw):
    parsed = protocol.parse_blob(protocol.build_blob(raw))
    assert parsed.swing_id == 7
    assert parsed.trigger_idx == raw.trigger_idx
    assert parsed.impact_idx == raw.impact_idx
    assert parsed.sample_rate_hz == pytest.approx(raw.sample_rate_hz)
    assert parsed.q0 == pytest.approx(raw.q0, abs=1e-6)
    np.testing.assert_array_equal(parsed.gyro, raw.gyro)
    np.testing.assert_array_equal(parsed.accel, raw.accel)
    np.testing.assert_array_equal(parsed.accel_hi, raw.accel_hi)
    assert parsed.has_hi_accel and parsed.hi_accel_g_per_lsb == pytest.approx(raw.hi_accel_g_per_lsb)


def test_version_1_captures_still_parse(raw):
    # Swings stored before the second accelerometer existed: 64-byte header, six values per sample.
    v2 = protocol.build_blob(raw)
    header = b"SWG1" + v2[4:64]
    samples = np.hstack([raw.gyro, raw.accel]).astype("<i2").tobytes()
    parsed = protocol.parse_blob(header + samples)
    assert not parsed.has_hi_accel
    assert not parsed.accel_hi.any()
    np.testing.assert_array_equal(parsed.gyro, raw.gyro)
    np.testing.assert_array_equal(parsed.accel, raw.accel)
    assert parsed.sample_rate_hz == pytest.approx(raw.sample_rate_hz)


@pytest.mark.parametrize("chunk_size", [17, 240])
def test_transfer_reassembles(raw, chunk_size):
    blob = protocol.build_blob(raw)
    assembler = protocol.TransferAssembler()
    results = [assembler.feed(p) for p in protocol.chunk_blob(blob, raw.swing_id, chunk_size)]
    assert all(r is None for r in results[:-1])
    assert results[-1].ok and results[-1].blob == blob


def test_transfer_tolerates_duplicates_and_reordering(raw):
    blob = protocol.build_blob(raw)
    packets = protocol.chunk_blob(blob, raw.swing_id, 240)
    start, data, end = packets[0], packets[1:-1], packets[-1]
    shuffled = data[::-1] + data[:3]
    assembler = protocol.TransferAssembler()
    assembler.feed(start)
    for p in shuffled:
        assert assembler.feed(p) is None
    result = assembler.feed(end)
    assert result.ok and result.blob == blob


def test_transfer_reports_missing_chunk(raw):
    packets = protocol.chunk_blob(protocol.build_blob(raw), raw.swing_id, 240)
    del packets[5]
    assembler = protocol.TransferAssembler()
    result = [assembler.feed(p) for p in packets][-1]
    assert not result.ok and "missing" in result.error


def test_transfer_reports_corruption(raw):
    packets = protocol.chunk_blob(protocol.build_blob(raw), raw.swing_id, 240)
    damaged = bytearray(packets[4])
    damaged[10] ^= 0xFF
    packets[4] = bytes(damaged)
    assembler = protocol.TransferAssembler()
    result = [assembler.feed(p) for p in packets][-1]
    assert not result.ok and "checksum" in result.error


def test_parse_blob_rejects_bad_input(raw):
    blob = protocol.build_blob(raw)
    with pytest.raises(protocol.ProtocolError):
        protocol.parse_blob(blob[:-1])
    with pytest.raises(protocol.ProtocolError):
        protocol.parse_blob(b"XXXX" + blob[4:])
    with pytest.raises(protocol.ProtocolError):
        protocol.parse_blob(b"short")


def test_live_round_trip():
    q = (0.5, -0.5, 0.5, 0.5)
    data = protocol.build_live(
        state=1,
        flags=protocol.LIVE_FLAG_CALIBRATED,
        seq=65535,
        q=q,
        gyro_dps=1234.4,
        accel_g=2.5,
        sample_rate_hz=831.7,
        swing_count=12,
    )
    assert len(data) == 20
    live = protocol.parse_live(data)
    assert live.state == "swinging" and live.calibrated and not live.imu_fault
    assert live.q == pytest.approx(q, abs=1e-4)
    assert live.gyro_dps == 1234 and live.accel_g == pytest.approx(2.5)
    assert live.sample_rate_hz == pytest.approx(831.7) and live.swing_count == 12


def test_summary_round_trip(raw):
    summary = protocol.parse_summary(protocol.build_summary(raw, 312))
    assert summary.swing_id == raw.swing_id and summary.impact and summary.duration_ms == 312
    assert summary.peak_omega_dps == pytest.approx(raw.peak_omega_dps, abs=1)


def test_threshold_command_layout():
    assert protocol.command_set_thresholds(500, 900) == bytes([0x02, 0xF4, 0x01, 0x84, 0x03])
