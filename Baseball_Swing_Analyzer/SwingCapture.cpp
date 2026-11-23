#include "SwingCapture.h"

static_assert((cfg::RING_SAMPLES & (cfg::RING_SAMPLES - 1)) == 0, "RING_SAMPLES must be a power of two");
static_assert(cfg::MAX_WINDOW_SAMPLES < cfg::RING_SAMPLES, "window must fit in the ring");
static_assert(cfg::PRE_SAMPLES + cfg::MAX_SWING_SAMPLES + cfg::START_CONFIRM_SAMPLES < cfg::MAX_WINDOW_SAMPLES,
              "pre-roll plus the longest swing must fit in one window");

namespace {

inline bool clipped(const int16_t v[3]) {
  for (int i = 0; i < 3; i++) {
    if (v[i] >= cfg::CLIP_RAW || v[i] <= -cfg::CLIP_RAW) {
      return true;
    }
  }
  return false;
}

inline float stepNorm(const int16_t a[3], const int16_t b[3], float scale) {
  float dx = (float)(a[0] - b[0]), dy = (float)(a[1] - b[1]), dz = (float)(a[2] - b[2]);
  return sqrtf(dx * dx + dy * dy + dz * dz) * scale;
}

}  // namespace

void SwingCapture::setThresholds(uint16_t startDps, uint16_t minPeakDps) {
  _startDps = startDps;
  _minPeakDps = minPeakDps;
}

void SwingCapture::reset() {
  _state = proto::STATE_IDLE;
  _validFrom = _count;
  _hasPrev = false;
  _confirm = 0;
}

bool SwingCapture::push(const ImuSample &s, const float q[4], float perpDps, float accelG, SwingResult &result) {
  const uint32_t n = _count++;
  _ring[n & MASK] = s;
  for (int i = 0; i < 4; i++) {
    _qRing[n & MASK][i] = (int16_t)lroundf(constrain(q[i], -1.0f, 1.0f) * 32767.0f);
  }

  float accelStepG = 0.0f, gyroStepDps = 0.0f;
  if (_hasPrev) {
    accelStepG = stepNorm(s.a, _prev.a, cfg::ACCEL_G_PER_LSB);
    gyroStepDps = stepNorm(s.g, _prev.g, cfg::GYRO_DPS_PER_LSB);
  }
  _prev = s;
  _hasPrev = true;

  switch (_state) {
    case proto::STATE_IDLE:
      if (perpDps >= _startDps) {
        if (++_confirm >= cfg::START_CONFIRM_SAMPLES) {
          _state = proto::STATE_SWINGING;
          _triggerAbs = n - (cfg::START_CONFIRM_SAMPLES - 1);
          _peakAbs = n;
          _peakDps = perpDps;
          _peakAccelG = accelG;
          _hasImpact = false;
          _flags = 0;
          _below = 0;
        }
      } else {
        _confirm = 0;
      }
      return false;

    case proto::STATE_SWINGING: {
      if (!_hasImpact && perpDps > 0.5f * _minPeakDps &&
          (accelStepG > cfg::IMPACT_ACCEL_STEP_G || gyroStepDps > cfg::IMPACT_GYRO_STEP_DPS)) {
        _hasImpact = true;
        _impactAbs = n;
      }
      if (!_hasImpact) {
        // Peak and saturation are judged on the approach to the ball only;
        // the strike itself always saturates the low-g accelerometer.
        if (perpDps > _peakDps) {
          _peakDps = perpDps;
          _peakAbs = n;
        }
        if (clipped(s.a)) {
          _flags |= proto::SWING_FLAG_ACCEL_CLIPPED;
        }
        if (clipped(s.g)) {
          _flags |= proto::SWING_FLAG_GYRO_CLIPPED;
        }
        if (clipped(s.h)) {
          _flags |= proto::SWING_FLAG_HI_ACCEL_CLIPPED;
        }
      }
      _peakAccelG = max(_peakAccelG, accelG);
      _below = perpDps < cfg::END_DPS ? _below + 1 : 0;

      const uint32_t swingSamples = n - _triggerAbs;
      bool ended = false;
      if (_hasImpact) {
        ended = (n - _impactAbs) >= cfg::POST_IMPACT_SAMPLES;
      } else if (_below >= cfg::END_CONFIRM_SAMPLES) {
        ended = true;
      }
      if (!ended && swingSamples >= cfg::MAX_SWING_SAMPLES) {
        ended = true;
        _flags |= proto::SWING_FLAG_TIMED_OUT;
      }
      if (!ended) {
        return false;
      }

      _state = proto::STATE_SETTLING;
      _quiet = 0;
      if (_peakDps < _minPeakDps || swingSamples < cfg::MIN_SWING_SAMPLES) {
        return false;
      }

      uint32_t startAbs = _triggerAbs >= cfg::PRE_SAMPLES ? _triggerAbs - cfg::PRE_SAMPLES : 0;
      startAbs = max(startAbs, _validFrom);
      if (n - startAbs + 1 > cfg::MAX_WINDOW_SAMPLES) {
        startAbs = n + 1 - cfg::MAX_WINDOW_SAMPLES;
      }
      // The pre-roll can be cut short right after boot or a FIFO overrun, but
      // the trigger itself is always inside the window.
      startAbs = min(startAbs, _triggerAbs);

      result.startAbs = startAbs;
      result.count = (uint16_t)(n - startAbs + 1);
      result.triggerIdx = (uint16_t)(_triggerAbs - startAbs);
      result.peakIdx = (uint16_t)(_peakAbs - startAbs);
      result.impactIdx = _hasImpact ? (int16_t)(_impactAbs - startAbs) : -1;
      result.flags = _flags | proto::SWING_FLAG_HAS_HI_ACCEL;
      result.swingSamples = (uint16_t)swingSamples;
      result.peakOmegaDps = _peakDps;
      result.peakAccelG = _peakAccelG;
      return true;
    }

    case proto::STATE_SETTLING:
      if (perpDps < cfg::END_DPS) {
        if (++_quiet >= cfg::REARM_QUIET_SAMPLES) {
          _state = proto::STATE_IDLE;
          _confirm = 0;
        }
      } else {
        _quiet = 0;
      }
      return false;
  }
  return false;
}

void SwingCapture::copySamples(uint32_t startAbs, uint16_t count, uint8_t *dst) const {
  // ImuSample is laid out exactly like a wire sample.
  for (uint16_t i = 0; i < count; i++) {
    memcpy(dst, &_ring[(startAbs + i) & MASK], proto::SAMPLE_BYTES);
    dst += proto::SAMPLE_BYTES;
  }
}

void SwingCapture::quaternionAt(uint32_t abs, float q[4]) const {
  const int16_t *raw = _qRing[abs & MASK];
  float norm = 0.0f;
  for (int i = 0; i < 4; i++) {
    q[i] = (float)raw[i] / 32767.0f;
    norm += q[i] * q[i];
  }
  norm = sqrtf(norm);
  if (norm < 1e-6f) {
    q[0] = 1.0f;
    q[1] = q[2] = q[3] = 0.0f;
    return;
  }
  for (int i = 0; i < 4; i++) {
    q[i] /= norm;
  }
}
