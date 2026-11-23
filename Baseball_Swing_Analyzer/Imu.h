#pragma once

#include <Arduino.h>
#include <Wire.h>

// One instant from the sensor, raw counts. The layout is the wire format of a
// swing sample (Protocol.h): nine little-endian int16 with no padding.
struct ImuSample {
  int16_t g[3];  // gyro
  int16_t a[3];  // low-g accelerometer, +/-16 g
  int16_t h[3];  // high-g accelerometer, +/-128 g, same axes
};
static_assert(sizeof(ImuSample) == 18, "ImuSample must match the 18-byte wire sample");

// LSM6DSV320X driver: 960 Hz, +/-4000 dps, +/-16 g and +/-128 g, read through
// the sensor's FIFO so samples stay evenly spaced even when the radio stalls
// the main loop. All three channels share the chip's clock, so one gyro, one
// low-g and one high-g word make up each sample.
class Imu {
 public:
  bool begin(TwoWire &wire);

  // Drains up to maxSamples complete samples from the FIFO.
  // Returns the number written to `out`, or -1 on an I2C failure.
  int poll(ImuSample *out, int maxSamples);

  // True once after the FIFO overflowed; the FIFO has already been restarted.
  bool takeOverrun();

  uint8_t address() const { return _addr; }
  uint32_t tagErrors() const { return _tagErrors; }

 private:
  bool probe(uint8_t addr);
  bool writeReg(uint8_t reg, uint8_t value);
  bool readRegs(uint8_t reg, uint8_t *buf, size_t len);
  bool restartFifo();

  TwoWire *_wire = nullptr;
  uint8_t _addr = 0;
  uint8_t _have = 0;     // which channels of _pending have arrived
  uint8_t _slot = 0xFF;  // FIFO slot counter the pending words belong to
  bool _overrun = false;
  uint32_t _tagErrors = 0;
  ImuSample _pending{};
};
