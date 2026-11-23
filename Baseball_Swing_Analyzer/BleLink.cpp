#include "BleLink.h"

#include <BLEUtils.h>

#include "Config.h"
#include "Protocol.h"

#if defined(CONFIG_BLUEDROID_ENABLED)
#include <BLE2902.h>
#endif

namespace {

constexpr uint16_t MAX_CONSECUTIVE_FAILURES = 400;  // ~5 s of refused notifications

uint32_t crc32(const uint8_t *data, uint32_t len) {
  static const uint32_t table[16] = {
    0x00000000, 0x1DB71064, 0x3B6E20C8, 0x26D930AC, 0x76DC4190, 0x6B6B51F4, 0x4DB26158, 0x5005713C,
    0xEDB88320, 0xF00F9344, 0xD6D6A3E8, 0xCB61B38C, 0x9B64C2B0, 0x86D3D2D4, 0xA00AE278, 0xBDBDF21C,
  };
  uint32_t crc = 0xFFFFFFFFu;
  for (uint32_t i = 0; i < len; i++) {
    crc ^= data[i];
    crc = table[crc & 0x0F] ^ (crc >> 4);
    crc = table[crc & 0x0F] ^ (crc >> 4);
  }
  return ~crc;
}

inline void put16(uint8_t *p, uint16_t v) {
  p[0] = (uint8_t)(v & 0xFF);
  p[1] = (uint8_t)(v >> 8);
}

inline void put32(uint8_t *p, uint32_t v) {
  put16(p, (uint16_t)(v & 0xFFFF));
  put16(p + 2, (uint16_t)(v >> 16));
}

void addCccd(BLECharacteristic *characteristic) {
#if defined(CONFIG_BLUEDROID_ENABLED)
  characteristic->addDescriptor(new BLE2902());
#else
  (void)characteristic;  // NimBLE adds the descriptor for notify characteristics itself
#endif
}

}  // namespace

void BleLink::begin(const String &info) {
  _commands = xQueueCreate(8, sizeof(LinkCommand));

  BLEDevice::init(cfg::DEVICE_NAME);
  BLEDevice::setMTU(256);

  _server = BLEDevice::createServer();
  _server->setCallbacks(this);
  _server->advertiseOnDisconnect(true);

  BLEService *service = _server->createService(proto::SERVICE_UUID);

  _live = service->createCharacteristic(proto::LIVE_UUID, BLECharacteristic::PROPERTY_NOTIFY);
  addCccd(_live);

  _swing = service->createCharacteristic(proto::SWING_UUID, BLECharacteristic::PROPERTY_NOTIFY);
  _swing->setCallbacks(this);
  addCccd(_swing);

  _control = service->createCharacteristic(
    proto::CONTROL_UUID, BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR);
  _control->setCallbacks(this);

  _info = service->createCharacteristic(proto::INFO_UUID, BLECharacteristic::PROPERTY_READ);
  _info->setValue(info);

  service->start();

  BLEAdvertising *advertising = BLEDevice::getAdvertising();
  advertising->addServiceUUID(proto::SERVICE_UUID);
  advertising->setScanResponse(true);
  BLEDevice::startAdvertising();
}

void BleLink::setInfo(const String &info) {
  if (_info != nullptr) {
    _info->setValue(info);
  }
}

void BleLink::onConnect(BLEServer *) {
  _connected.store(true);
  _newConnection.store(true);
}

void BleLink::onDisconnect(BLEServer *) {
  _connected.store(false);
}

void BleLink::onWrite(BLECharacteristic *characteristic) {
  if (characteristic != _control || _commands == nullptr) {
    return;
  }
  const uint8_t *data = characteristic->getData();
  size_t len = characteristic->getLength();
  if (data == nullptr || len < 1) {
    return;
  }
  LinkCommand cmd{data[0], 0, 0};
  if (len >= 3) {
    cmd.arg0 = (uint16_t)(data[1] | (data[2] << 8));
  }
  if (len >= 5) {
    cmd.arg1 = (uint16_t)(data[3] | (data[4] << 8));
  }
  xQueueSend(_commands, &cmd, 0);
}

void BleLink::onStatus(BLECharacteristic *, Status status, uint32_t) {
  if (status != SUCCESS_NOTIFY && status != SUCCESS_INDICATE) {
    _notifyFailed.store(true);
  }
}

bool BleLink::takeCommand(LinkCommand &out) {
  return _commands != nullptr && xQueueReceive(_commands, &out, 0) == pdTRUE;
}

void BleLink::sendLive(const uint8_t *data, size_t len) {
  if (!connected() || _live == nullptr) {
    return;
  }
  _live->setValue(data, len);
  _live->notify();
}

bool BleLink::notifySwing(const uint8_t *data, size_t len) {
  _notifyFailed.store(false);
  _swing->setValue(data, len);
  _swing->notify();
  return !_notifyFailed.load();
}

bool BleLink::startTransfer(const uint8_t *blob, uint32_t len, uint16_t swingId) {
  if (!connected() || blob == nullptr || len == 0) {
    return false;
  }
  uint16_t mtu = _server->getPeerMTU(_server->getConnId());
  if (mtu < 23) {
    mtu = 23;
  }
  uint16_t payload = (uint16_t)(mtu - 3 - proto::XFER_DATA_OVERHEAD);
  _chunkSize = min(payload, cfg::MAX_CHUNK_PAYLOAD);
  _chunkCount = (uint16_t)((len + _chunkSize - 1) / _chunkSize);
  _blob = blob;
  _blobLen = len;
  _blobCrc = crc32(blob, len);
  _swingId = swingId;
  _nextChunk = -1;
  _failures = 0;
  _waitUs = 0;
  _lastSendUs = micros();
  _xferActive = true;
  return true;
}

void BleLink::service() {
  if (_newConnection.exchange(false)) {
#if defined(CONFIG_NIMBLE_ENABLED)
    // Ask for a 7.5-15 ms connection interval so a swing transfers in about a second.
    _server->requestConnParams(_server->getConnId(), 6, 12, 0, 400);
#endif
  }
  if (!_xferActive) {
    return;
  }
  if (!connected()) {
    _xferActive = false;
    return;
  }
  uint32_t now = micros();
  if ((uint32_t)(now - _lastSendUs) < _waitUs) {
    return;
  }
  _lastSendUs = now;

  uint8_t packet[proto::XFER_DATA_OVERHEAD + cfg::MAX_CHUNK_PAYLOAD];
  size_t len = 0;
  if (_nextChunk < 0) {
    packet[0] = proto::PKT_XFER_START;
    put16(packet + 1, _swingId);
    put32(packet + 3, _blobLen);
    put16(packet + 7, _chunkSize);
    put16(packet + 9, _chunkCount);
    len = proto::XFER_START_BYTES;
  } else if (_nextChunk < (int32_t)_chunkCount) {
    uint32_t offset = (uint32_t)_nextChunk * _chunkSize;
    uint32_t n = min((uint32_t)_chunkSize, _blobLen - offset);
    packet[0] = proto::PKT_XFER_DATA;
    put16(packet + 1, (uint16_t)_nextChunk);
    memcpy(packet + proto::XFER_DATA_OVERHEAD, _blob + offset, n);
    len = proto::XFER_DATA_OVERHEAD + n;
  } else {
    packet[0] = proto::PKT_XFER_END;
    put16(packet + 1, _swingId);
    put32(packet + 3, _blobCrc);
    len = proto::XFER_END_BYTES;
  }

  if (!notifySwing(packet, len)) {
    // Usually the stack is out of buffers; back off and send the same chunk again.
    _waitUs = cfg::CHUNK_RETRY_US;
    if (++_failures >= MAX_CONSECUTIVE_FAILURES) {
      _xferActive = false;
    }
    return;
  }
  _failures = 0;
  _waitUs = cfg::CHUNK_INTERVAL_US;
  if (_nextChunk >= (int32_t)_chunkCount) {
    _xferActive = false;
  } else {
    _nextChunk++;
  }
}
