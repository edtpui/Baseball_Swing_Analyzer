#pragma once

#include <Arduino.h>

// Build-time configuration for the bat-knob swing sensor.
// Hardware: Seeed XIAO ESP32-C3 + ST LSM6DSV320X (STEVAL-MKI251A adapter) on
// I2C (SDA = D4, SCL = D5).

namespace cfg {

constexpr char DEVICE_NAME[] = "SwingSensor";
constexpr char FW_VERSION[] = "2.0.0";

// --- IMU ---------------------------------------------------------------
constexpr uint32_t I2C_HZ = 400000;
constexpr float NOMINAL_ODR_HZ = 960.0f;
constexpr float GYRO_DPS_PER_LSB = 0.140f;       // +/-4000 dps full scale
constexpr float ACCEL_G_PER_LSB = 0.000488f;     // +/-16 g full scale (low-g channel)
constexpr float HI_ACCEL_G_PER_LSB = 0.003904f;  // +/-128 g full scale (high-g channel)
constexpr uint16_t HI_ACCEL_FS_G = 128;
constexpr int16_t CLIP_RAW = 32700;              // |raw| at or above this is treated as saturated
// Above this the low-g channel is close to its limit and the high-g one is read instead.
constexpr int16_t ACCEL_HANDOVER_RAW = 28000;    // ~13.7 g

// FIFO words fetched per I2C transaction (7 bytes each). The sensor wraps the
// read pointer from 0x7E back to 0x78, so several words fit in one read.
// Set to 1 if the diagnostics line ever reports FIFO tag errors.
constexpr uint8_t FIFO_WORDS_PER_READ = 16;

// --- Capture window ------------------------------------------------------
constexpr uint16_t PRE_SAMPLES = 720;         // 0.75 s kept before the trigger
constexpr uint16_t MAX_SWING_SAMPLES = 960;   // 1.0 s hard limit on one swing
constexpr uint16_t POST_IMPACT_SAMPLES = 144; // 0.15 s kept after contact
constexpr uint16_t MAX_WINDOW_SAMPLES = 1750;
constexpr uint16_t RING_SAMPLES = 2048;       // power of two, > MAX_WINDOW_SAMPLES

// --- Swing detection (angular rate perpendicular to the bat axis) ---------
constexpr uint16_t DEFAULT_START_DPS = 500;
constexpr uint16_t DEFAULT_MIN_PEAK_DPS = 900;
constexpr uint16_t END_DPS = 150;
constexpr uint8_t START_CONFIRM_SAMPLES = 6;  // 6 ms above the start threshold
constexpr uint16_t END_CONFIRM_SAMPLES = 58;  // 60 ms below the end threshold
constexpr uint16_t MIN_SWING_SAMPLES = 48;    // 50 ms
constexpr uint16_t REARM_QUIET_SAMPLES = 96;  // 100 ms of quiet before the next swing

// A ball strike shows up as a step between two consecutive samples.
constexpr float IMPACT_ACCEL_STEP_G = 10.0f;
constexpr float IMPACT_GYRO_STEP_DPS = 250.0f;

// --- Gyro bias calibration ------------------------------------------------
constexpr uint16_t CAL_BLOCK_SAMPLES = 480;   // 0.5 s
constexpr int16_t CAL_GYRO_RANGE_RAW = 14;    // ~2 dps peak-to-peak
constexpr int16_t CAL_ACCEL_RANGE_RAW = 102;  // ~0.05 g peak-to-peak

// --- Link ----------------------------------------------------------------
constexpr uint32_t LIVE_INTERVAL_MS = 33;     // ~30 Hz orientation stream
constexpr uint16_t MAX_CHUNK_PAYLOAD = 240;
constexpr uint32_t CHUNK_INTERVAL_US = 3000;
constexpr uint32_t CHUNK_RETRY_US = 12000;

constexpr uint32_t DIAG_INTERVAL_MS = 5000;

}  // namespace cfg
