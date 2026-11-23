#include "Motion.h"

#include "Config.h"

namespace {

constexpr float KP_STARTUP = 20.0f;      // fast tilt convergence right after boot
constexpr float KP_RUNNING = 1.0f;
constexpr float STARTUP_SECONDS = 1.5f;
constexpr float ACCEL_GATE_MIN_G2 = 0.7225f;  // 0.85 g squared
constexpr float ACCEL_GATE_MAX_G2 = 1.3225f;  // 1.15 g squared
constexpr float GYRO_GATE_RAD2 = 12.18f;      // (200 dps in rad/s) squared

}  // namespace

void Ahrs::reset() {
  _q[0] = 1.0f;
  _q[1] = _q[2] = _q[3] = 0.0f;
  _elapsed = 0.0f;
}

void Ahrs::update(float gx, float gy, float gz, float ax, float ay, float az, float dt) {
  float q0 = _q[0], q1 = _q[1], q2 = _q[2], q3 = _q[3];

  float a2 = ax * ax + ay * ay + az * az;
  float w2 = gx * gx + gy * gy + gz * gz;
  if (a2 > ACCEL_GATE_MIN_G2 && a2 < ACCEL_GATE_MAX_G2 && w2 < GYRO_GATE_RAD2) {
    float inv = 1.0f / sqrtf(a2);
    ax *= inv;
    ay *= inv;
    az *= inv;
    // World "up" expressed in the body frame, from the current estimate.
    float vx = 2.0f * (q1 * q3 - q0 * q2);
    float vy = 2.0f * (q0 * q1 + q2 * q3);
    float vz = q0 * q0 - q1 * q1 - q2 * q2 + q3 * q3;
    float kp = _elapsed < STARTUP_SECONDS ? KP_STARTUP : KP_RUNNING;
    gx += kp * (ay * vz - az * vy);
    gy += kp * (az * vx - ax * vz);
    gz += kp * (ax * vy - ay * vx);
  }

  float hx = 0.5f * gx * dt, hy = 0.5f * gy * dt, hz = 0.5f * gz * dt;
  float n0 = q0 - q1 * hx - q2 * hy - q3 * hz;
  float n1 = q1 + q0 * hx + q2 * hz - q3 * hy;
  float n2 = q2 + q0 * hy - q1 * hz + q3 * hx;
  float n3 = q3 + q0 * hz + q1 * hy - q2 * hx;
  float norm = sqrtf(n0 * n0 + n1 * n1 + n2 * n2 + n3 * n3);
  if (norm > 0.0f) {
    float inv = 1.0f / norm;
    _q[0] = n0 * inv;
    _q[1] = n1 * inv;
    _q[2] = n2 * inv;
    _q[3] = n3 * inv;
  }
  if (_elapsed < STARTUP_SECONDS) {
    _elapsed += dt;
  }
}

void GyroCalibrator::restart() {
  _calibrated = false;
  clearBlock();
}

void GyroCalibrator::clearBlock() {
  _n = 0;
  for (int i = 0; i < 3; i++) {
    _sum[i] = 0;
    _gMin[i] = _aMin[i] = INT16_MAX;
    _gMax[i] = _aMax[i] = INT16_MIN;
  }
}

bool GyroCalibrator::push(const ImuSample &s) {
  if (_n == 0) {
    clearBlock();
  }
  for (int i = 0; i < 3; i++) {
    _sum[i] += s.g[i];
    _gMin[i] = min(_gMin[i], s.g[i]);
    _gMax[i] = max(_gMax[i], s.g[i]);
    _aMin[i] = min(_aMin[i], s.a[i]);
    _aMax[i] = max(_aMax[i], s.a[i]);
  }
  if (++_n < cfg::CAL_BLOCK_SAMPLES) {
    return false;
  }

  bool still = true;
  for (int i = 0; i < 3; i++) {
    still = still && (_gMax[i] - _gMin[i]) <= cfg::CAL_GYRO_RANGE_RAW;
    still = still && (_aMax[i] - _aMin[i]) <= cfg::CAL_ACCEL_RANGE_RAW;
  }
  if (still) {
    for (int i = 0; i < 3; i++) {
      float mean = (float)_sum[i] / (float)cfg::CAL_BLOCK_SAMPLES * cfg::GYRO_DPS_PER_LSB;
      _biasDps[i] = _calibrated ? 0.8f * _biasDps[i] + 0.2f * mean : mean;
    }
    _calibrated = true;
  }
  _n = 0;
  return still;
}
