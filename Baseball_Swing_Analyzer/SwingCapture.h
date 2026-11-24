#pragma once

#include <Arduino.h>

#include "Config.h"
#include "Imu.h"
#include "Protocol.h"

struct SwingResult {
  uint32_t startAbs;      // absolute index of the first sample in the window
  uint16_t count;
  uint16_t triggerIdx;    // all indices are relative to startAbs
  uint16_t peakIdx;
  int16_t impactIdx;      // -1 when no strike was seen
  uint16_t flags;         // proto::SWING_FLAG_*
  uint16_t swingSamples;  // trigger to end
  float peakOmegaDps;
  float peakAccelG;
};

// Keeps the last ~2.4 s of raw samples and orientation, and finds swings in
// the stream from the angular rate perpendicular to the bat's long axis.
class SwingCapture {
 public:
  void setThresholds(uint16_t startDps, uint16_t minPeakDps);

  // Forgets history after a gap in the sample stream, and re-arms only once
  // the bat has been quiet for REARM_QUIET_SAMPLES.
  void reset();

  // Stores one sample. Returns true when `result` describes a finished swing.
  bool push(const ImuSample &s, const float q[4], float perpDps, float accelG, SwingResult &result);

  proto::DeviceState state() const { return _state; }

  void copySamples(uint32_t startAbs, uint16_t count, uint8_t *dst) const;
  void quaternionAt(uint32_t abs, float q[4]) const;

 private:
  static constexpr uint32_t MASK = cfg::RING_SAMPLES - 1;

  ImuSample _ring[cfg::RING_SAMPLES];
  int16_t _qRing[cfg::RING_SAMPLES][4];

  proto::DeviceState _state = proto::STATE_IDLE;
  uint16_t _startDps = cfg::DEFAULT_START_DPS;
  uint16_t _minPeakDps = cfg::DEFAULT_MIN_PEAK_DPS;

  uint32_t _count = 0;      // samples pushed so far
  uint32_t _validFrom = 0;  // oldest sample that is contiguous with the newest
  bool _hasPrev = false;
  ImuSample _prev{};

  uint16_t _confirm = 0;
  uint16_t _below = 0;
  uint16_t _quiet = 0;
  uint32_t _triggerAbs = 0;
  uint32_t _peakAbs = 0;
  uint32_t _impactAbs = 0;
  bool _hasImpact = false;
  uint16_t _flags = 0;
  float _peakDps = 0.0f;
  float _peakAccelG = 0.0f;
};
