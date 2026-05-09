#include <BLEDevice.h>
#include <BLEUtils.h>
#include <BLEServer.h>
#include <BLE2902.h>

#define DEVICE_NAME       "ESP32S3_FAN_LED_TEMP"
#define SERVICE_UUID      "12345678-1234-1234-1234-1234567890ab"
#define COMMAND_CHAR_UUID "12345678-1234-1234-1234-1234567890ac"
#define STATUS_CHAR_UUID  "12345678-1234-1234-1234-1234567890ad"

BLEServer *server = nullptr;
BLECharacteristic *statusCharacteristic = nullptr;
bool connected = false;
bool restartAdvertising = false;
unsigned long lastStatusAt = 0;

class ServerCallback : public BLEServerCallbacks {
  void onConnect(BLEServer *server) {
    connected = true;
    Serial.println("BLE central connected");
  }

  void onDisconnect(BLEServer *server) {
    connected = false;
    restartAdvertising = true;
    Serial.println("BLE central disconnected");
  }
};

class CommandCallback : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic *characteristic) {
    String cmd = characteristic->getValue().c_str();
    cmd.trim();
    Serial.print("BLE Command: ");
    Serial.println(cmd);
    statusCharacteristic->setValue("TEMP:25.00,FAN:OFF,SPEED:0,LED:OFF");
    if (connected) {
      statusCharacteristic->notify();
    }
  }
};

void startAdvertising() {
  BLEAdvertising *advertising = BLEDevice::getAdvertising();

  advertising->stop();

  BLEAdvertisementData advData;
  advData.setFlags(0x06);
  advData.setName(DEVICE_NAME);

  BLEAdvertisementData scanData;
  scanData.setCompleteServices(BLEUUID(SERVICE_UUID));

  advertising->setAdvertisementData(advData);
  advertising->setScanResponseData(scanData);
  advertising->setScanResponse(true);
  advertising->setMinPreferred(0x06);
  advertising->setMinPreferred(0x12);
  BLEDevice::startAdvertising();

  Serial.println("BLE advertising started");
  Serial.print("Name: ");
  Serial.println(DEVICE_NAME);
  Serial.print("Service: ");
  Serial.println(SERVICE_UUID);
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println();
  Serial.println("Minimal ESP32-S3 BLE advertising test");

  BLEDevice::init(DEVICE_NAME);
  server = BLEDevice::createServer();
  server->setCallbacks(new ServerCallback());

  BLEService *service = server->createService(SERVICE_UUID);

  BLECharacteristic *commandCharacteristic = service->createCharacteristic(
    COMMAND_CHAR_UUID,
    BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR
  );
  commandCharacteristic->setCallbacks(new CommandCallback());

  statusCharacteristic = service->createCharacteristic(
    STATUS_CHAR_UUID,
    BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_NOTIFY
  );
  statusCharacteristic->addDescriptor(new BLE2902());
  statusCharacteristic->setValue("TEMP:25.00,FAN:OFF,SPEED:0,LED:OFF");

  service->start();
  startAdvertising();
}

void loop() {
  if (restartAdvertising) {
    restartAdvertising = false;
    delay(300);
    startAdvertising();
  }

  if (millis() - lastStatusAt >= 2000) {
    lastStatusAt = millis();
    const char *msg = "TEMP:25.00,FAN:OFF,SPEED:0,LED:OFF";
    statusCharacteristic->setValue(msg);
    if (connected) {
      statusCharacteristic->notify();
    }
    Serial.println(msg);
  }
}
