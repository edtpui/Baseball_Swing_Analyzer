// Baseball swing sensor firmware.
//
// Board : Seeed XIAO ESP32-C3
// Sensor: ST LSM6DSV320X (STEVAL-MKI251A adapter) on I2C (SDA = D4, SCL = D5),
//         mounted flat on the bat knob so the sensor's Z axis lies along the
//         bat. One chip holds the gyro, a +/-16 g accelerometer and a high-g
//         accelerometer for the part of a hard swing past 16 g.
//
// The sensor streams orientation at ~30 Hz for the live view. When it sees a
// swing it sends a short summary at once, then the raw 960 Hz samples around
// the swing so the laptop can rebuild bat speed and swing path.

#include <Preferences.h>
#include <Wire.h>

#include "BleLink.h"
#include "Config.h"
#include "Imu.h"
#include "Motion.h"
#include "Protocol.h"
#include "SwingCapture.h"

namespace {

constexpr float DEG_TO_RAD_F = 0.01745329252f;
constexpr int POLL_BATCH = 48;
constexpr int MAX_BUS_ERRORS = 20;
constexpr uint32_t RATE_WINDOW_US = 10000000;
constexpr uint32_t IMU_RETRY_MS = 1000;
constexpr uint32_t FAULT_LIVE_INTERVAL_MS = 500;

constexpr uint16_t START_DPS_MIN = 200;
constexpr uint16_t START_DPS_MAX = 2000;
constexpr uint16_t MIN_PEAK_DPS_MAX = 3500;
constexpr uint16_t MIN_PEAK_MARGIN_DPS = 100;

Imu imu;
Ahrs ahrs;
GyroCalibrator calibrator;
SwingCapture capture;
BleLink ble;
Preferences prefs;

uint8_t swingBlob[sizeof(proto::SwingHeader) + cfg::MAX_WINDOW_SAMPLES * proto::SAMPLE_BYTES];
uint32_t swingBlobLen = 0;
uint16_t swingBlobId = 0;

uint16_t startDps = cfg::DEFAULT_START_DPS;
uint16_t minPeakDps = cfg::DEFAULT_MIN_PEAK_DPS;

bool imuOk = false;
int busErrors = 0;
uint32_t lastImuRetryMs = 0;

float odrHz = cfg::NOMINAL_ODR_HZ;
bool odrMeasured = false;
uint32_t rateWindowStartUs = 0;
uint32_t rateSamples = 0;
bool rateWindowDirty = false;

float lastGyroDps = 0.0f;
float lastAccelG = 0.0f;
uint16_t swingCount = 0;
uint16_t liveSeq = 0;
uint32_t lastLiveMs = 0;
bool overrunSinceLive = false;

uint32_t overruns = 0;
uint32_t totalSamples = 0;
uint32_t lastDiagMs = 0;

bool thresholdsValid(uint16_t start, uint16_t minPeak) {
  return start >= START_DPS_MIN && start <= START_DPS_MAX && minPeak >= start + MIN_PEAK_MARGIN_DPS &&
         minPeak <= MIN_PEAK_DPS_MAX;
}

String infoString() {
  String s;
  s.reserve(140);
  s += "fw=";
  s += cfg::FW_VERSION;
  s += ";proto=";
  s += proto::VERSION;
  s += ";odr=";
  s += String(odrHz, 1);
  s += ";gyro_fs=4000;accel_fs=16;hi_accel_fs=";
  s += cfg::HI_ACCEL_FS_G;
  s += ";start_dps=";
  s += startDps;
  s += ";min_peak_dps=";
  s += minPeakDps;
  s += ";imu=";
  s += imuOk ? 1 : 0;
  return s;
}

void loadSettings() {
  prefs.begin("swing", false);
  uint16_t start = prefs.getUShort("start", cfg::DEFAULT_START_DPS);
  uint16_t minPeak = prefs.getUShort("minpeak", cfg::DEFAULT_MIN_PEAK_DPS);
  if (thresholdsValid(start, minPeak)) {
    startDps = start;
    minPeakDps = minPeak;
  }
  capture.setThresholds(startDps, minPeakDps);
}

bool startImu() {
  if (!imu.begin(Wire)) {
    return false;
  }
  ahrs.reset();
  calibrator.restart();
  capture.reset();
  busErrors = 0;
  rateWindowStartUs = micros();
  rateSamples = 0;
  rateWindowDirty = false;
  Serial.printf("IMU ready at 0x%02X\n", imu.address());
  return true;
}

// The sensor's clock is only accurate to a few percent, so the real sample
// rate is measured against the ESP32's crystal over ten-second windows.
void updateSampleRate(int newSamples) {
  rateSamples += (uint32_t)newSamples;
  uint32_t elapsed = micros() - rateWindowStartUs;
  if (elapsed < RATE_WINDOW_US) {
    return;
  }
  if (!rateWindowDirty) {
    float measured = (float)rateSamples * 1e6f / (float)elapsed;
    if (measured > 0.9f * cfg::NOMINAL_ODR_HZ && measured < 1.1f * cfg::NOMINAL_ODR_HZ) {
      odrHz = odrMeasured ? 0.7f * odrHz + 0.3f * measured : measured;
      odrMeasured = true;
    }
  }
  rateWindowStartUs += elapsed;
  rateSamples = 0;
  rateWindowDirty = false;
}

void finishSwing(const SwingResult &r) {
  swingCount++;

  proto::SwingHeader header;
  memcpy(header.magic, "SWG2", 4);
  header.swingId = swingCount;
  header.sampleCount = r.count;
  header.triggerIdx = r.triggerIdx;
  header.peakIdx = r.peakIdx;
  header.impactIdx = r.impactIdx;
  header.flags = r.flags | (calibrator.calibrated() ? 0 : proto::SWING_FLAG_UNCALIBRATED);
  header.sampleRateHz = odrHz;
  header.gyroDpsPerLsb = cfg::GYRO_DPS_PER_LSB;
  header.accelGPerLsb = cfg::ACCEL_G_PER_LSB;
  float q0[4];
  capture.quaternionAt(r.startAbs, q0);
  memcpy(header.q0, q0, sizeof(q0));
  memcpy(header.gyroBiasDps, calibrator.biasDps(), sizeof(header.gyroBiasDps));
  header.peakOmegaDps = r.peakOmegaDps;
  header.peakAccelG = r.peakAccelG;
  header.hiAccelGPerLsb = cfg::HI_ACCEL_G_PER_LSB;

  memcpy(swingBlob, &header, sizeof(header));
  capture.copySamples(r.startAbs, r.count, swingBlob + sizeof(header));
  swingBlobLen = sizeof(header) + (uint32_t)r.count * proto::SAMPLE_BYTES;
  swingBlobId = swingCount;

  proto::SummaryPacket summary;
  summary.type = proto::PKT_SUMMARY;
  // Only the high-g channel's limit counts as lost acceleration.
  const bool accelLost = (r.flags & proto::SWING_FLAG_HI_ACCEL_CLIPPED) != 0;
  summary.flags = (r.impactIdx >= 0 ? 0x01 : 0) | (accelLost ? 0x02 : 0) |
                  ((r.flags & proto::SWING_FLAG_GYRO_CLIPPED) ? 0x04 : 0);
  summary.swingId = swingCount;
  summary.peakOmegaDps = (uint16_t)constrain(lroundf(r.peakOmegaDps), 0L, 65535L);
  summary.durationMs = (uint16_t)constrain(lroundf(r.swingSamples * 1000.0f / odrHz), 0L, 65535L);
  summary.peakAccelCg = (uint16_t)constrain(lroundf(r.peakAccelG * 100.0f), 0L, 65535L);
  ble.sendLive((const uint8_t *)&summary, sizeof(summary));

  // A swing that ends while the previous one is still transferring replaces it.
  ble.startTransfer(swingBlob, swingBlobLen, swingBlobId);

  Serial.printf("swing %u: peak %.0f dps, %u samples, impact %d, flags 0x%04X\n", swingCount, r.peakOmegaDps,
                r.count, r.impactIdx, header.flags);
}

void handleSample(const ImuSample &s) {
  const float *bias = calibrator.biasDps();
  float gx = s.g[0] * cfg::GYRO_DPS_PER_LSB - bias[0];
  float gy = s.g[1] * cfg::GYRO_DPS_PER_LSB - bias[1];
  float gz = s.g[2] * cfg::GYRO_DPS_PER_LSB - bias[2];
  float ax = s.a[0] * cfg::ACCEL_G_PER_LSB;
  float ay = s.a[1] * cfg::ACCEL_G_PER_LSB;
  float az = s.a[2] * cfg::ACCEL_G_PER_LSB;

  calibrator.push(s);
  ahrs.update(gx * DEG_TO_RAD_F, gy * DEG_TO_RAD_F, gz * DEG_TO_RAD_F, ax, ay, az, 1.0f / odrHz);

  // Sensor Z lies along the bat, so X and Y carry the swing rotation.
  float perp2 = gx * gx + gy * gy;
  float perpDps = sqrtf(perp2);
  lastGyroDps = sqrtf(perp2 + gz * gz);
  lastAccelG = sqrtf(ax * ax + ay * ay + az * az);
  if (max(max(abs(s.a[0]), abs(s.a[1])), abs(s.a[2])) >= cfg::ACCEL_HANDOVER_RAW) {
    // The low-g channel is at or near its limit: report from the high-g one.
    float hx = s.h[0] * cfg::HI_ACCEL_G_PER_LSB;
    float hy = s.h[1] * cfg::HI_ACCEL_G_PER_LSB;
    float hz = s.h[2] * cfg::HI_ACCEL_G_PER_LSB;
    lastAccelG = sqrtf(hx * hx + hy * hy + hz * hz);
  }

  SwingResult result;
  if (capture.push(s, ahrs.q(), perpDps, lastAccelG, result)) {
    finishSwing(result);
  }
}

void pollImu() {
  ImuSample batch[POLL_BATCH];
  int n = imu.poll(batch, POLL_BATCH);
  if (n < 0) {
    if (++busErrors >= MAX_BUS_ERRORS) {
      imuOk = false;
      lastImuRetryMs = millis();
      ble.setInfo(infoString());
      Serial.println("IMU stopped answering on I2C; retrying");
    }
    delay(1);
    return;
  }
  busErrors = 0;

  if (imu.takeOverrun()) {
    overruns++;
    overrunSinceLive = true;
    rateWindowDirty = true;
    capture.reset();
  }
  for (int i = 0; i < n; i++) {
    handleSample(batch[i]);
  }
  totalSamples += (uint32_t)n;
  updateSampleRate(n);
  if (n == 0) {
    delay(1);
  }
}

void retryImu() {
  uint32_t now = millis();
  if (now - lastImuRetryMs >= IMU_RETRY_MS) {
    lastImuRetryMs = now;
    imuOk = startImu();
    if (imuOk) {
      ble.setInfo(infoString());
    } else {
      Serial.println("LSM6DSV320X not found on I2C (checked 0x6A and 0x6B)");
    }
  }
  delay(5);
}

void handleCommands() {
  LinkCommand cmd;
  while (ble.takeCommand(cmd)) {
    switch (cmd.op) {
      case proto::CMD_RECALIBRATE:
        calibrator.restart();
        Serial.println("gyro recalibration requested; hold the bat still");
        break;
      case proto::CMD_SET_THRESHOLDS:
        if (thresholdsValid(cmd.arg0, cmd.arg1)) {
          startDps = cmd.arg0;
          minPeakDps = cmd.arg1;
          capture.setThresholds(startDps, minPeakDps);
          prefs.putUShort("start", startDps);
          prefs.putUShort("minpeak", minPeakDps);
          ble.setInfo(infoString());
          Serial.printf("thresholds: start %u dps, min peak %u dps\n", startDps, minPeakDps);
        } else {
          Serial.printf("rejected thresholds: start %u, min peak %u\n", cmd.arg0, cmd.arg1);
        }
        break;
      case proto::CMD_RESEND_SWING:
        if (swingBlobLen > 0 && !ble.transferActive()) {
          ble.startTransfer(swingBlob, swingBlobLen, swingBlobId);
        }
        break;
      default:
        Serial.printf("unknown command 0x%02X\n", cmd.op);
        break;
    }
  }
}

void sendLive() {
  uint32_t now = millis();
  uint32_t interval = imuOk ? cfg::LIVE_INTERVAL_MS : FAULT_LIVE_INTERVAL_MS;
  if (!ble.connected() || now - lastLiveMs < interval) {
    return;
  }
  lastLiveMs = now;

  proto::LivePacket p;
  p.type = proto::PKT_LIVE;
  p.state = (uint8_t)capture.state();
  if (calibrator.calibrated()) p.state |= proto::LIVE_FLAG_CALIBRATED;
  if (ble.transferActive()) p.state |= proto::LIVE_FLAG_TRANSFER;
  if (overrunSinceLive) p.state |= proto::LIVE_FLAG_OVERRUN;
  if (!imuOk) p.state |= proto::LIVE_FLAG_IMU_FAULT;
  overrunSinceLive = false;
  p.seq = liveSeq++;
  const float *q = ahrs.q();
  for (int i = 0; i < 4; i++) {
    p.q[i] = (int16_t)lroundf(constrain(q[i], -1.0f, 1.0f) * 32767.0f);
  }
  p.gyroDps = (uint16_t)constrain(lroundf(lastGyroDps), 0L, 65535L);
  p.accelMg = (uint16_t)constrain(lroundf(lastAccelG * 1000.0f), 0L, 65535L);
  p.odrX10 = (uint16_t)lroundf(odrHz * 10.0f);
  p.swingCount = swingCount;
  ble.sendLive((const uint8_t *)&p, sizeof(p));
}

void printDiagnostics() {
  uint32_t now = millis();
  if (now - lastDiagMs < cfg::DIAG_INTERVAL_MS) {
    return;
  }
  lastDiagMs = now;
  const float *bias = calibrator.biasDps();
  Serial.printf("odr %.1f Hz%s | samples %lu | overruns %lu | fifo tag errors %lu | bias %.2f %.2f %.2f dps%s | ble %s | swings %u\n",
                odrHz, odrMeasured ? "" : " (nominal)", (unsigned long)totalSamples, (unsigned long)overruns,
                (unsigned long)imu.tagErrors(), bias[0], bias[1], bias[2],
                calibrator.calibrated() ? "" : " (uncalibrated)",
                ble.connected() ? "connected" : "advertising", swingCount);
}

}  // namespace

void setup() {
  Serial.begin(115200);
#if ARDUINO_USB_CDC_ON_BOOT
  // Never block on USB serial when running from the battery with no host attached.
  Serial.setTxTimeoutMs(0);
#endif
  delay(200);
  Serial.printf("\nSwing sensor firmware %s\n", cfg::FW_VERSION);

  loadSettings();

  Wire.begin();
  Wire.setClock(cfg::I2C_HZ);
  imuOk = startImu();
  if (!imuOk) {
    Serial.println("LSM6DSV320X not found on I2C (checked 0x6A and 0x6B)");
  }

  ble.begin(infoString());
  Serial.printf("advertising as \"%s\"\n", cfg::DEVICE_NAME);
}

void loop() {
  if (imuOk) {
    pollImu();
  } else {
    retryImu();
  }
  handleCommands();
  ble.service();
  sendLive();
  printDiagnostics();
}
