#pragma once

#include <Arduino.h>

// Wire format shared with the laptop app (app/server/protocol.py).
// Everything is little-endian. Keep the two files in step.

namespace proto {

constexpr uint8_t VERSION = 2;

constexpr char SERVICE_UUID[] = "b5a70001-6f1d-4c8e-9a53-2f1c7d4e8a10";
constexpr char LIVE_UUID[] = "b5a70002-6f1d-4c8e-9a53-2f1c7d4e8a10";     // notify
constexpr char SWING_UUID[] = "b5a70003-6f1d-4c8e-9a53-2f1c7d4e8a10";    // notify
constexpr char CONTROL_UUID[] = "b5a70004-6f1d-4c8e-9a53-2f1c7d4e8a10";  // write
constexpr char INFO_UUID[] = "b5a70005-6f1d-4c8e-9a53-2f1c7d4e8a10";     // read

enum PacketType : uint8_t {
  PKT_LIVE = 0x01,
  PKT_SUMMARY = 0x02,
  PKT_XFER_START = 0xA0,
  PKT_XFER_DATA = 0xA1,
  PKT_XFER_END = 0xA2,
};

enum Command : uint8_t {
  CMD_RECALIBRATE = 0x01,     // no payload
  CMD_SET_THRESHOLDS = 0x02,  // u16 startDps, u16 minPeakDps
  CMD_RESEND_SWING = 0x10,    // no payload
};

// Low nibble of LivePacket::state.
enum DeviceState : uint8_t {
  STATE_IDLE = 0,
  STATE_SWINGING = 1,
  STATE_SETTLING = 2,
};

constexpr uint8_t LIVE_FLAG_CALIBRATED = 0x10;
constexpr uint8_t LIVE_FLAG_TRANSFER = 0x20;
constexpr uint8_t LIVE_FLAG_OVERRUN = 0x40;
constexpr uint8_t LIVE_FLAG_IMU_FAULT = 0x80;

constexpr uint16_t SWING_FLAG_ACCEL_CLIPPED = 0x0001;
constexpr uint16_t SWING_FLAG_GYRO_CLIPPED = 0x0002;
constexpr uint16_t SWING_FLAG_TIMED_OUT = 0x0004;
constexpr uint16_t SWING_FLAG_UNCALIBRATED = 0x0008;
constexpr uint16_t SWING_FLAG_HAS_HI_ACCEL = 0x0010;      // every sample carries a high-g reading
constexpr uint16_t SWING_FLAG_HI_ACCEL_CLIPPED = 0x0020;  // even the high-g channel saturated

// Sent on the LIVE characteristic at ~30 Hz. Fits the default 23-byte MTU.
struct __attribute__((packed)) LivePacket {
  uint8_t type;        // PKT_LIVE
  uint8_t state;       // DeviceState | LIVE_FLAG_*
  uint16_t seq;
  int16_t q[4];        // w, x, y, z scaled by 32767 (sensor body -> world, Z up)
  uint16_t gyroDps;    // total angular rate magnitude
  uint16_t accelMg;    // specific-force magnitude
  uint16_t odrX10;     // measured sample rate, Hz * 10
  uint16_t swingCount;
};
static_assert(sizeof(LivePacket) == 20, "LivePacket must be 20 bytes");

// Sent on the LIVE characteristic the moment a swing ends, ahead of the raw data.
struct __attribute__((packed)) SummaryPacket {
  uint8_t type;           // PKT_SUMMARY
  uint8_t flags;          // bit0 impact detected, bit1 acceleration beyond what was measurable, bit2 gyro clipped
  uint16_t swingId;
  uint16_t peakOmegaDps;  // peak rate perpendicular to the bat axis
  uint16_t durationMs;
  uint16_t peakAccelCg;   // centi-g
};
static_assert(sizeof(SummaryPacket) == 10, "SummaryPacket must be 10 bytes");

// Start of the swing blob. Followed by sampleCount records of nine int16:
// gx, gy, gz, ax, ay, az, hx, hy, hz (raw sensor counts, bias not removed).
// a is the +/-16 g accelerometer channel and h the high-g one, in the same axes.
// This firmware always sets SWING_FLAG_HAS_HI_ACCEL.
struct __attribute__((packed)) SwingHeader {
  char magic[4];          // "SWG2"
  uint16_t swingId;
  uint16_t sampleCount;
  uint16_t triggerIdx;
  uint16_t peakIdx;
  int16_t impactIdx;      // -1 when no ball strike was seen
  uint16_t flags;         // SWING_FLAG_*
  float sampleRateHz;
  float gyroDpsPerLsb;
  float accelGPerLsb;
  float q0[4];            // orientation at sample 0 (w, x, y, z)
  float gyroBiasDps[3];
  float peakOmegaDps;
  float peakAccelG;
  float hiAccelGPerLsb;
};
static_assert(sizeof(SwingHeader) == 68, "SwingHeader must be 68 bytes");

constexpr size_t SAMPLE_BYTES = 18;

// Chunk framing on the SWING characteristic:
//   START: [0xA0][u16 swingId][u32 totalLen][u16 chunkSize][u16 chunkCount]
//   DATA : [0xA1][u16 chunkIndex][payload]
//   END  : [0xA2][u16 swingId][u32 crc32 of the whole blob]
constexpr size_t XFER_START_BYTES = 11;
constexpr size_t XFER_DATA_OVERHEAD = 3;
constexpr size_t XFER_END_BYTES = 7;

}  // namespace proto
