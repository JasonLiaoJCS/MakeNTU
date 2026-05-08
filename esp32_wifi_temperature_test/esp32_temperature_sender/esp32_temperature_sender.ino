/*
  ESP32 + DS18B20 WiFi temperature sender.

  Arduino IDE libraries:
    - OneWire
    - DallasTemperature

  Wiring:
    DS18B20 VCC  -> ESP32 3V3
    DS18B20 GND  -> ESP32 GND
    DS18B20 DATA -> ESP32 GPIO4
    4.7k resistor between DATA and 3V3

  Jetson receiver:
    python3 esp32_wifi_temperature_test/jetson_temperature_receiver.py
*/

#include <WiFi.h>
#include <HTTPClient.h>
#include <OneWire.h>
#include <DallasTemperature.h>

const char* WIFI_SSID = "ktphone";
const char* WIFI_PASSWORD = "ktktktkt";

// Use the Jetson LAN IP from `hostname -I`, not 127.0.0.1.
const char* JETSON_TEMPERATURE_URL = "http://10.47.235.158:8790/temperature";

const int ONE_WIRE_BUS = 4;
const unsigned long SEND_INTERVAL_MS = 5000;
const unsigned long WIFI_CONNECT_TIMEOUT_MS = 20000;

OneWire oneWire(ONE_WIRE_BUS);
DallasTemperature sensors(&oneWire);

unsigned long lastSendAt = 0;

void printWiFiStatus(wl_status_t status) {
  switch (status) {
    case WL_IDLE_STATUS:
      Serial.println("WL_IDLE_STATUS");
      break;
    case WL_NO_SSID_AVAIL:
      Serial.println("WL_NO_SSID_AVAIL");
      break;
    case WL_SCAN_COMPLETED:
      Serial.println("WL_SCAN_COMPLETED");
      break;
    case WL_CONNECTED:
      Serial.println("WL_CONNECTED");
      break;
    case WL_CONNECT_FAILED:
      Serial.println("WL_CONNECT_FAILED");
      break;
    case WL_CONNECTION_LOST:
      Serial.println("WL_CONNECTION_LOST");
      break;
    case WL_DISCONNECTED:
      Serial.println("WL_DISCONNECTED");
      break;
    default:
      Serial.println((int)status);
      break;
  }
}

bool connectWiFi() {
  if (WiFi.status() == WL_CONNECTED) {
    return true;
  }

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.disconnect(true);
  delay(300);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  Serial.print("WiFi SSID: ");
  Serial.println(WIFI_SSID);
  Serial.print("WiFi connecting");
  unsigned long startAt = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - startAt < WIFI_CONNECT_TIMEOUT_MS) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();

  if (WiFi.status() != WL_CONNECTED) {
    Serial.print("WiFi connect failed, status: ");
    printWiFiStatus(WiFi.status());
    Serial.println("Check hotspot is 2.4 GHz, SSID/password are correct, and hotspot allows devices.");
    return false;
  }

  Serial.print("ESP32 IP: ");
  Serial.println(WiFi.localIP());
  Serial.print("WiFi RSSI: ");
  Serial.println(WiFi.RSSI());
  return true;
}

bool sendTemperature(float temperatureC) {
  if (WiFi.status() != WL_CONNECTED) {
    return false;
  }

  HTTPClient http;
  http.begin(JETSON_TEMPERATURE_URL);
  http.addHeader("Content-Type", "application/json");

  String payload = "{\"temperature_c\":";
  payload += String(temperatureC, 1);
  payload += "}";

  int statusCode = http.POST(payload);
  http.end();

  Serial.print("Temperature: ");
  Serial.print(temperatureC, 1);
  Serial.print(" C, HTTP ");
  Serial.println(statusCode);

  return statusCode >= 200 && statusCode < 300;
}

void setup() {
  Serial.begin(115200);
  delay(1000);

  sensors.begin();
  sensors.setResolution(12);

  connectWiFi();
}

void loop() {
  if (!connectWiFi()) {
    delay(5000);
    return;
  }

  unsigned long now = millis();
  if (now - lastSendAt < SEND_INTERVAL_MS) {
    delay(50);
    return;
  }
  lastSendAt = now;

  sensors.requestTemperatures();
  float temperatureC = sensors.getTempCByIndex(0);

  if (temperatureC == DEVICE_DISCONNECTED_C) {
    Serial.println("Temperature sensor read failed.");
    return;
  }

  if (!sendTemperature(temperatureC)) {
    Serial.println("Temperature POST failed.");
  }
}
