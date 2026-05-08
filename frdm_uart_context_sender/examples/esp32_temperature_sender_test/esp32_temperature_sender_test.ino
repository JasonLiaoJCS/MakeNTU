/*
  ESP32-S3-N16R8 + DS18B20 WiFi temperature sender test.

  Purpose:
    Read DS18B20 on GPIO4 and POST only the temperature value to a Jetson
    standalone receiver.

  Arduino IDE libraries:
    - OneWire
    - DallasTemperature

  Jetson receiver:
    python3 frdm_uart_context_sender/test_esp32_temperature_receiver.py \
      --host 0.0.0.0 \
      --port 8790 \
      --path /temperature

  Important:
    JETSON_TEMPERATURE_URL must use the Jetson LAN IP, not 127.0.0.1.
*/

#include <WiFi.h>
#include <HTTPClient.h>
#include <OneWire.h>
#include <DallasTemperature.h>

#define ONE_WIRE_BUS 4

const char* WIFI_SSID = "YOUR_WIFI_SSID";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";

// Replace 192.168.1.23 with the Jetson LAN IP from: hostname -I
const char* JETSON_TEMPERATURE_URL = "http://192.168.1.23:8790/temperature";

const unsigned long POST_INTERVAL_MS = 5000;

OneWire oneWire(ONE_WIRE_BUS);
DallasTemperature sensors(&oneWire);

unsigned long lastPostAt = 0;

void connectWiFi() {
  if (WiFi.status() == WL_CONNECTED) {
    return;
  }

  Serial.print("Connecting to WiFi: ");
  Serial.println(WIFI_SSID);

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println();
  Serial.println("WiFi connected.");
  Serial.print("ESP32 IP: ");
  Serial.println(WiFi.localIP());
}

bool postTemperature(float temperatureC) {
  if (WiFi.status() != WL_CONNECTED) {
    return false;
  }

  HTTPClient http;
  http.begin(JETSON_TEMPERATURE_URL);
  http.addHeader("Content-Type", "application/json");

  String payload = "{";
  payload += "\"ok\":true,";
  payload += "\"temperature_c\":";
  payload += String(temperatureC, 1);
  payload += "}";

  int statusCode = http.POST(payload);
  String response = http.getString();
  http.end();

  Serial.print("Temperature: ");
  Serial.print(temperatureC, 1);
  Serial.print(" C | HTTP ");
  Serial.print(statusCode);
  Serial.print(" | ");
  Serial.println(response);

  return statusCode >= 200 && statusCode < 300;
}

void setup() {
  Serial.begin(115200);
  delay(1000);

  Serial.println();
  Serial.println("ESP32-S3 DS18B20 WiFi sender test");
  Serial.println("DS18B20 DATA/OUT pin: GPIO4");

  sensors.begin();
  sensors.setResolution(12);

  connectWiFi();
}

void loop() {
  connectWiFi();

  unsigned long now = millis();
  if (now - lastPostAt < POST_INTERVAL_MS) {
    delay(50);
    return;
  }
  lastPostAt = now;

  sensors.requestTemperatures();
  float tempC = sensors.getTempCByIndex(0);

  if (tempC == DEVICE_DISCONNECTED_C) {
    Serial.println("ERROR: DS18B20 disconnected or read failed.");
    return;
  }

  if (tempC < -55.0 || tempC > 125.0) {
    Serial.print("ERROR: temperature out of DS18B20 range: ");
    Serial.println(tempC);
    return;
  }

  if (!postTemperature(tempC)) {
    Serial.println("WARNING: failed to POST temperature to Jetson.");
  }
}
