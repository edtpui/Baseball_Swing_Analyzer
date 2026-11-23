#pragma once

#include <Arduino.h>

#include "Imu.h"

// Gyro-integrating orientation filter. Gravity only corrects tilt while the
// bat is close to still; during a swing the estimate is pure gyro integration.
// Quaternion is sensor body -> world (Z up), Hamilton convention, w first.
class Ahrs {
 public:
  void reset();
  // gyro in rad/s, accel in g, dt in seconds.
  void update(float gx, float gy, float gz, float ax, float ay, float az, float dt);
  const float *q() const { return _q; }

 private:
  float _q[4] = {1.0f, 0.0f, 0.0f, 0.0f};
  float _elapsed = 0.0f;
};

// Tracks gyro zero-rate offset from half-second blocks in which the sensor did not move.
class GyroCalibrator {
 public:
  void restart();
  // Returns true when the bias estimate was just updated.
  bool push(const ImuSample &s);
  bool calibrated() const { return _calibrated; }
  const float *biasDps() const { return _biasDps; }

 private:
  void clearBlock();

  bool _calibrated = false;
  float _biasDps[3] = {0.0f, 0.0f, 0.0f};
  uint16_t _n = 0;
  int32_t _sum[3] = {0, 0, 0};
  int16_t _gMin[3], _gMax[3], _aMin[3], _aMax[3];
};
