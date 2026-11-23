#pragma once

#include <Arduino.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <atomic>

struct LinkCommand {
  uint8_t op;
  uint16_t arg0;
  uint16_t arg1;
};

// BLE peripheral: one notify stream for live data, one for swing transfers,
// and a write characteristic for commands from the laptop.
class BleLink : public BLEServerCallbacks, public BLECharacteristicCallbacks {
 public:
  void begin(const String &info);
  void setInfo(const String &info);

  bool connected() const { return _connected.load(); }

  // Sends one packet on the live characteristic. Dropped when not connected.
  void sendLive(const uint8_t *data, size_t len);

  // Starts streaming `blob` in chunks. The memory must stay valid and
  // unchanged until transferActive() goes false.
  bool startTransfer(const uint8_t *blob, uint32_t len, uint16_t swingId);
  bool transferActive() const { return _xferActive; }

  // Call every loop pass: sends at most one transfer chunk.
  void service();

  // Pops one command written by the laptop. Safe to call from loop().
  bool takeCommand(LinkCommand &out);

  // BLEServerCallbacks
  void onConnect(BLEServer *server) override;
  void onDisconnect(BLEServer *server) override;
  // BLECharacteristicCallbacks
  void onWrite(BLECharacteristic *characteristic) override;
  void onStatus(BLECharacteristic *characteristic, Status status, uint32_t code) override;

 private:
  bool notifySwing(const uint8_t *data, size_t len);

  BLEServer *_server = nullptr;
  BLECharacteristic *_live = nullptr;
  BLECharacteristic *_swing = nullptr;
  BLECharacteristic *_control = nullptr;
  BLECharacteristic *_info = nullptr;
  QueueHandle_t _commands = nullptr;

  std::atomic<bool> _connected{false};
  std::atomic<bool> _newConnection{false};
  std::atomic<bool> _notifyFailed{false};

  const uint8_t *_blob = nullptr;
  uint32_t _blobLen = 0;
  uint32_t _blobCrc = 0;
  uint16_t _swingId = 0;
  uint16_t _chunkSize = 0;
  uint16_t _chunkCount = 0;
  int32_t _nextChunk = 0;  // -1 = START pending, chunkCount = END pending
  bool _xferActive = false;
  uint16_t _failures = 0;
  uint32_t _lastSendUs = 0;
  uint32_t _waitUs = 0;
};
