#include "Imu.h"

#include "Config.h"

// Register addresses, bit positions and codes are from ST's lsm6dsv320x_reg.h.
namespace {

constexpr uint8_t REG_FIFO_CTRL1 = 0x07;
constexpr uint8_t REG_FIFO_CTRL2 = 0x08;
constexpr uint8_t REG_FIFO_CTRL3 = 0x09;
constexpr uint8_t REG_FIFO_CTRL4 = 0x0A;
constexpr uint8_t REG_COUNTER_BDR_REG1 = 0x0B;
constexpr uint8_t REG_WHO_AM_I = 0x0F;
constexpr uint8_t REG_CTRL1 = 0x10;
constexpr uint8_t REG_CTRL2 = 0x11;
constexpr uint8_t REG_CTRL3 = 0x12;
constexpr uint8_t REG_CTRL6 = 0x15;
constexpr uint8_t REG_CTRL8 = 0x17;
constexpr uint8_t REG_FIFO_STATUS1 = 0x1B;
constexpr uint8_t REG_CTRL1_XL_HG = 0x4E;
constexpr uint8_t REG_FIFO_DATA_OUT_TAG = 0x78;

constexpr uint8_t WHO_AM_I_LSM6DSV320X = 0x73;

constexpr uint8_t POWER_DOWN = 0x00;
constexpr uint8_t CTRL3_SW_RESET = 0x01;
constexpr uint8_t CTRL3_BDU_IF_INC = 0x44;
constexpr uint8_t CTRL1_XL_960HZ = 0x09;        // high-performance mode, ODR 960 Hz
constexpr uint8_t CTRL2_G_960HZ = 0x09;         // high-performance mode, ODR 960 Hz
constexpr uint8_t CTRL6_G_4000DPS = 0x05;
constexpr uint8_t CTRL8_XL_16G = 0x03;
constexpr uint8_t CTRL1_XL_HG_960HZ_128G = 0x22;  // ODR 960 Hz (0x4 << 3), FS +/-128 g (0x2)
constexpr uint8_t FIFO_BDR_960HZ_BOTH = 0x99;     // gyro and low-g accel batched at 960 Hz
constexpr uint8_t FIFO_XL_HG_BATCH_EN = 0x08;     // high-g accel batched at its own ODR
constexpr uint8_t FIFO_MODE_BYPASS = 0x00;
constexpr uint8_t FIFO_MODE_CONTINUOUS = 0x06;

constexpr uint8_t FIFO_STATUS2_OVERRUN = 0x48;  // FIFO_OVR_IA | FIFO_OVR_LATCHED
constexpr uint8_t FIFO_STATUS2_DIFF_MSB = 0x01;
constexpr uint8_t TAG_GYRO = 0x01;
constexpr uint8_t TAG_ACCEL = 0x02;
constexpr uint8_t TAG_ACCEL_HG = 0x1D;
constexpr size_t FIFO_WORD_BYTES = 7;
constexpr int WORDS_PER_SAMPLE = 3;

constexpr uint8_t HAVE_GYRO = 0x01;
constexpr uint8_t HAVE_ACCEL = 0x02;
constexpr uint8_t HAVE_ACCEL_HG = 0x04;
constexpr uint8_t HAVE_ALL = HAVE_GYRO | HAVE_ACCEL | HAVE_ACCEL_HG;
constexpr uint8_t NO_SLOT = 0xFF;

inline void le16x3(const uint8_t *p, int16_t out[3]) {
  for (int i = 0; i < 3; i++) {
    out[i] = (int16_t)((uint16_t)p[2 * i] | ((uint16_t)p[2 * i + 1] << 8));
  }
}

}  // namespace

bool Imu::begin(TwoWire &wire) {
  _wire = &wire;
  uint8_t found = 0;
  for (uint8_t addr : {(uint8_t)0x6A, (uint8_t)0x6B}) {
    if (probe(addr)) {
      found = addr;
      break;
    }
  }
  _addr = found;
  if (_addr == 0) {
    return false;
  }

  // The chip may still be running from before a microcontroller reset; ST
  // requires all three sensors off before a software reset.
  bool ok = writeReg(REG_CTRL1, POWER_DOWN);
  ok = ok && writeReg(REG_CTRL2, POWER_DOWN);
  ok = ok && writeReg(REG_CTRL1_XL_HG, POWER_DOWN);
  ok = ok && writeReg(REG_CTRL3, CTRL3_SW_RESET);
  if (!ok) {
    return false;
  }
  bool resetDone = false;
  for (int i = 0; i < 50 && !resetDone; i++) {
    delay(2);
    uint8_t ctrl3 = 0;
    resetDone = readRegs(REG_CTRL3, &ctrl3, 1) && (ctrl3 & CTRL3_SW_RESET) == 0;
  }
  if (!resetDone) {
    return false;
  }

  ok = writeReg(REG_CTRL3, CTRL3_BDU_IF_INC);
  ok = ok && writeReg(REG_CTRL6, CTRL6_G_4000DPS);
  ok = ok && writeReg(REG_CTRL8, CTRL8_XL_16G);
  ok = ok && writeReg(REG_FIFO_CTRL1, 0x00);
  ok = ok && writeReg(REG_FIFO_CTRL2, 0x00);
  ok = ok && writeReg(REG_FIFO_CTRL3, FIFO_BDR_960HZ_BOTH);
  ok = ok && writeReg(REG_COUNTER_BDR_REG1, FIFO_XL_HG_BATCH_EN);
  // The high-g channel only runs alongside the low-g one in high-performance
  // mode, which is what CTRL1 selects.
  ok = ok && writeReg(REG_CTRL1, CTRL1_XL_960HZ);
  ok = ok && writeReg(REG_CTRL2, CTRL2_G_960HZ);
  ok = ok && writeReg(REG_CTRL1_XL_HG, CTRL1_XL_HG_960HZ_128G);
  ok = ok && restartFifo();
  return ok;
}

bool Imu::probe(uint8_t addr) {
  _addr = addr;
  uint8_t id = 0;
  return readRegs(REG_WHO_AM_I, &id, 1) && id == WHO_AM_I_LSM6DSV320X;
}

bool Imu::restartFifo() {
  _have = 0;
  _slot = NO_SLOT;
  return writeReg(REG_FIFO_CTRL4, FIFO_MODE_BYPASS) && writeReg(REG_FIFO_CTRL4, FIFO_MODE_CONTINUOUS);
}

bool Imu::takeOverrun() {
  bool was = _overrun;
  _overrun = false;
  return was;
}

int Imu::poll(ImuSample *out, int maxSamples) {
  uint8_t status[2];
  if (!readRegs(REG_FIFO_STATUS1, status, 2)) {
    return -1;
  }
  if (status[1] & FIFO_STATUS2_OVERRUN) {
    // Samples were lost, so anything still queued is no longer contiguous.
    _overrun = true;
    return restartFifo() ? 0 : -1;
  }

  int words = status[0] | ((status[1] & FIFO_STATUS2_DIFF_MSB) << 8);
  int produced = 0;
  uint8_t buf[cfg::FIFO_WORDS_PER_READ * FIFO_WORD_BYTES];

  while (words > 0 && produced < maxSamples) {
    int batch = min(words, (int)cfg::FIFO_WORDS_PER_READ);
    // Each sample needs three words; do not read more than `out` can hold.
    batch = min(batch, WORDS_PER_SAMPLE * (maxSamples - produced));
    if (!readRegs(REG_FIFO_DATA_OUT_TAG, buf, batch * FIFO_WORD_BYTES)) {
      return -1;
    }
    words -= batch;

    for (int i = 0; i < batch; i++) {
      const uint8_t *w = buf + i * FIFO_WORD_BYTES;
      const uint8_t tag = w[0] >> 3;
      // Words written at the same instant carry the same two-bit slot counter.
      const uint8_t slot = (w[0] >> 1) & 0x03;
      uint8_t channel;
      int16_t *dst;
      if (tag == TAG_GYRO) {
        channel = HAVE_GYRO;
        dst = _pending.g;
      } else if (tag == TAG_ACCEL) {
        channel = HAVE_ACCEL;
        dst = _pending.a;
      } else if (tag == TAG_ACCEL_HG) {
        channel = HAVE_ACCEL_HG;
        dst = _pending.h;
      } else {
        _tagErrors++;
        continue;
      }
      if (slot != _slot) {
        // A new instant began. An unfinished one is dropped, which only
        // happens while the three channels are starting up.
        _slot = slot;
        _have = 0;
      }
      le16x3(w + 1, dst);
      _have |= channel;
      if (_have == HAVE_ALL) {
        out[produced++] = _pending;
        _have = 0;
        _slot = NO_SLOT;
      }
    }
  }
  return produced;
}

bool Imu::writeReg(uint8_t reg, uint8_t value) {
  _wire->beginTransmission(_addr);
  _wire->write(reg);
  _wire->write(value);
  return _wire->endTransmission() == 0;
}

bool Imu::readRegs(uint8_t reg, uint8_t *buf, size_t len) {
  _wire->beginTransmission(_addr);
  _wire->write(reg);
  if (_wire->endTransmission(false) != 0) {
    return false;
  }
  if (_wire->requestFrom((uint8_t)_addr, len, true) != len) {
    return false;
  }
  return _wire->readBytes(buf, len) == len;
}
