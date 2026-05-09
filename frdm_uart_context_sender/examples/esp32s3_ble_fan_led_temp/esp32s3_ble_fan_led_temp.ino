#include <BLEDevice.h>
#include <BLEUtils.h>
#include <BLEServer.h>
#include <BLE2902.h>

#include <MD_MAX72xx.h>
#include <SPI.h>

#include <OneWire.h>
#include <DallasTemperature.h>

// =======================
// Fan Module L9110S
// =======================
const int FAN_INA = 5;  // PWM
const int FAN_INB = 6;

// =======================
// DS18B20
// =======================
const int ONE_WIRE_BUS = 7;

OneWire oneWire(ONE_WIRE_BUS);
DallasTemperature sensors(&oneWire);

// =======================
// MAX7219 LED Matrix
// =======================
#define HARDWARE_TYPE MD_MAX72XX::FC16_HW
#define MAX_DEVICES 1

const int DATA_PIN = 11;  // DIN
const int CS_PIN   = 10;  // CS
const int CLK_PIN  = 12;  // CLK

MD_MAX72XX mx = MD_MAX72XX(HARDWARE_TYPE, DATA_PIN, CLK_PIN, CS_PIN, MAX_DEVICES);

// =======================
// BLE UUID
// =======================
#define DEVICE_NAME       "ESP32S3_FAN_LED_TEMP"
#define SERVICE_UUID      "12345678-1234-1234-1234-1234567890ab"
#define COMMAND_CHAR_UUID "12345678-1234-1234-1234-1234567890ac"
#define STATUS_CHAR_UUID  "12345678-1234-1234-1234-1234567890ad"

BLEServer *bleServer = nullptr;
BLECharacteristic *statusCharacteristic = nullptr;

bool bleConnected = false;
bool restartAdvertising = false;

// =======================
// State
// =======================
const int FAN_DEFAULT_SPEED = 180;
const int FAN_MIN_RUNNING_SPEED = 96;
const int FAN_START_KICK_SPEED = 255;
const unsigned long FAN_START_KICK_MS = 250;

bool fanOn = false;
int fanSpeed = FAN_DEFAULT_SPEED;  // 0~255
unsigned long fanKickUntil = 0;

bool ledOn = false;
int ledFrame = 0;

float currentTempC = -127.0;

unsigned long lastTempTime = 0;
unsigned long lastLedTime = 0;

const unsigned long TEMP_INTERVAL = 2000;
const unsigned long LED_FRAME_INTERVAL = 500;

// =======================
// Letter + Heart Patterns
// =======================
byte patternN[8] = {
  B10000001,
  B10000011,
  B10000101,
  B10001001,
  B10010001,
  B10100001,
  B11000001,
  B10000001
};

byte patternX[8] = {
  B10000001,
  B01000010,
  B00100100,
  B00011000,
  B00011000,
  B00100100,
  B01000010,
  B10000001
};

byte patternP[8] = {
  B00111111,
  B01000001,
  B01000001,
  B00111111,
  B00000001,
  B00000001,
  B00000001,
  B00000001
};

byte patternHeart[8] = {
  B00000000,
  B01100110,
  B11111111,
  B11111111,
  B11111111,
  B01111110,
  B00111100,
  B00011000
};

byte patternA[8] = {
  B00111100,
  B01000010,
  B10000001,
  B10000001,
  B11111111,
  B10000001,
  B10000001,
  B10000001
};

byte patternV[8] = {
  B10000001,
  B10000001,
  B10000001,
  B01000010,
  B01000010,
  B00100100,
  B00100100,
  B00011000
};

byte patternE[8] = {
  B11111111,
  B00000001,
  B00000001,
  B00111111,
  B00000001,
  B00000001,
  B00000001,
  B11111111
};

byte patternT[8] = {
  B11111111,
  B00011000,
  B00011000,
  B00011000,
  B00011000,
  B00011000,
  B00011000,
  B00011000
};

// =======================
// Fan Control
// =======================
bool fanKickActive() {
  return fanKickUntil != 0 && (long)(millis() - fanKickUntil) < 0;
}

int effectiveFanDuty() {
  if (!fanOn || fanSpeed <= 0) return 0;

  int duty = constrain(fanSpeed, 0, 255);
  if (duty > 0 && duty < FAN_MIN_RUNNING_SPEED) {
    duty = FAN_MIN_RUNNING_SPEED;
  }
  if (fanKickActive()) {
    duty = max(duty, FAN_START_KICK_SPEED);
  }
  return constrain(duty, 0, 255);
}

void applyFan() {
  int duty = effectiveFanDuty();
  if (duty > 0) {
    digitalWrite(FAN_INB, LOW);
    ledcWrite(FAN_INA, duty);
  } else {
    ledcWrite(FAN_INA, 0);
    digitalWrite(FAN_INB, LOW);
  }
}

void setFan(bool on) {
  bool wasOn = fanOn;
  fanOn = on;
  if (fanOn) {
    if (fanSpeed <= 0) fanSpeed = FAN_DEFAULT_SPEED;
    if (fanSpeed < FAN_MIN_RUNNING_SPEED) fanSpeed = FAN_MIN_RUNNING_SPEED;
    if (!wasOn) fanKickUntil = millis() + FAN_START_KICK_MS;
  } else {
    fanKickUntil = 0;
  }
  applyFan();
}

void setFanSpeed(int speedValue) {
  int requestedSpeed = constrain(speedValue, 0, 255);
  bool wasOn = fanOn;

  if (requestedSpeed <= 0) {
    fanSpeed = 0;
    fanOn = false;
    fanKickUntil = 0;
  } else {
    fanSpeed = max(requestedSpeed, FAN_MIN_RUNNING_SPEED);
    fanOn = true;
    if (!wasOn) fanKickUntil = millis() + FAN_START_KICK_MS;
  }

  applyFan();
}

void updateFanKick() {
  if (fanKickUntil != 0 && !fanKickActive()) {
    fanKickUntil = 0;
    applyFan();
  }
}

// =======================
// LED Matrix Control
// =======================
void showPattern(byte pattern[8]) {
  mx.clear();

  for (int row = 0; row < 8; row++) {
    for (int col = 0; col < 8; col++) {
      bool pixelOn = bitRead(pattern[row], 7 - col);

      int newRow = col;
      int newCol = 7 - row;

      mx.setPoint(newRow, newCol, pixelOn);
    }
  }

  mx.update();
}

void clearMatrix() {
  mx.clear();
  mx.update();
}

void updateLedAnimation() {
  if (!ledOn) return;
  if (millis() - lastLedTime < LED_FRAME_INTERVAL) return;
  lastLedTime = millis();

  switch (ledFrame) {
    case 0: showPattern(patternN); break;
    case 1: showPattern(patternX); break;
    case 2: showPattern(patternP); break;
    case 3: showPattern(patternHeart); break;
    case 4: showPattern(patternA); break;
    case 5: showPattern(patternV); break;
    case 6: showPattern(patternN); break;
    case 7: showPattern(patternE); break;
    case 8: showPattern(patternT); break;
  }

  ledFrame++;
  if (ledFrame > 8) ledFrame = 0;
}

void setLed(bool on) {
  ledOn = on;
  if (ledOn) {
    ledFrame = 0;
    lastLedTime = 0;
  } else {
    clearMatrix();
  }
}

// =======================
// Temperature
// =======================
void readTemperature() {
  sensors.requestTemperatures();
  currentTempC = sensors.getTempCByIndex(0);
}

// =======================
// BLE Status
// =======================
String buildStatusMessage() {
  String msg = "";
  msg += "TEMP:";
  msg += String(currentTempC, 2);
  msg += ",FAN:";
  msg += fanOn ? "ON" : "OFF";
  msg += ",SPEED:";
  msg += String(fanSpeed);
  msg += ",LED:";
  msg += ledOn ? "ON" : "OFF";
  return msg;
}

void notifyStatus() {
  if (statusCharacteristic == nullptr) return;

  String msg = buildStatusMessage();
  statusCharacteristic->setValue(msg.c_str());
  if (bleConnected) {
    statusCharacteristic->notify();
  }
  Serial.println(msg);
}

// =======================
// BLE Command Handler
// =======================
void handleCommand(String cmd) {
  cmd.trim();
  cmd.toUpperCase();

  Serial.print("BLE Command: ");
  Serial.println(cmd);

  if (cmd == "FAN_ON") {
    setFan(true);
  } else if (cmd == "FAN_OFF") {
    setFan(false);
  } else if (cmd == "FAN_TOGGLE") {
    setFan(!fanOn);
  } else if (cmd.startsWith("FAN_SPEED:")) {
    int value = cmd.substring(10).toInt();
    setFanSpeed(value);
  } else if (cmd == "LED_ON") {
    setLed(true);
  } else if (cmd == "LED_OFF") {
    setLed(false);
  } else if (cmd == "LED_TOGGLE") {
    setLed(!ledOn);
  } else if (cmd == "TEMP?") {
    readTemperature();
  } else if (cmd == "ALL_OFF") {
    setFan(false);
    setLed(false);
  }

  notifyStatus();
}

class CommandCallback : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic *characteristic) {
    String cmd = characteristic->getValue().c_str();
    handleCommand(cmd);
  }
};

class ServerCallback : public BLEServerCallbacks {
  void onConnect(BLEServer *server) {
    bleConnected = true;
    Serial.println("BLE central connected");
  }

  void onDisconnect(BLEServer *server) {
    bleConnected = false;
    restartAdvertising = true;
    Serial.println("BLE central disconnected; advertising will restart");
  }
};

void startBleAdvertising() {
  BLEAdvertising *advertising = BLEDevice::getAdvertising();

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
  advertising->start();

  Serial.println("BLE advertising started");
  Serial.print("BLE Device name: ");
  Serial.println(DEVICE_NAME);
  Serial.print("BLE Service UUID: ");
  Serial.println(SERVICE_UUID);
}

// =======================
// Setup
// =======================
void setup() {
  Serial.begin(115200);
  delay(1000);

  pinMode(FAN_INB, OUTPUT);
  digitalWrite(FAN_INB, LOW);

  ledcAttach(FAN_INA, 20000, 8);
  setFan(false);

  mx.begin();
  mx.control(MD_MAX72XX::INTENSITY, 8);
  clearMatrix();

  sensors.begin();
  readTemperature();

  BLEDevice::init(DEVICE_NAME);
  bleServer = BLEDevice::createServer();
  bleServer->setCallbacks(new ServerCallback());

  BLEService *service = bleServer->createService(SERVICE_UUID);

  BLECharacteristic *commandCharacteristic = service->createCharacteristic(
    COMMAND_CHAR_UUID,
    BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR
  );

  statusCharacteristic = service->createCharacteristic(
    STATUS_CHAR_UUID,
    BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_NOTIFY
  );
  statusCharacteristic->addDescriptor(new BLE2902());

  commandCharacteristic->setCallbacks(new CommandCallback());

  service->start();
  startBleAdvertising();

  Serial.println("System Ready");
  Serial.println("BLE Commands:");
  Serial.println("FAN_ON / FAN_OFF / FAN_TOGGLE");
  Serial.println("FAN_SPEED:0~255");
  Serial.println("LED_ON / LED_OFF / LED_TOGGLE");
  Serial.println("TEMP?");
  Serial.println("ALL_OFF");

  notifyStatus();
}

// =======================
// Loop
// =======================
void loop() {
  if (restartAdvertising) {
    restartAdvertising = false;
    delay(200);
    startBleAdvertising();
  }

  updateFanKick();
  updateLedAnimation();

  if (millis() - lastTempTime >= TEMP_INTERVAL) {
    lastTempTime = millis();
    readTemperature();
    notifyStatus();
  }
}
