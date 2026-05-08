# ESP32 WiFi Temperature Test

這是一組獨立測試程式，只驗證：

```text
DS18B20 -> ESP32 -> WiFi -> Jetson
```

Jetson 端只會顯示收到的溫度，例如：

```text
25.4 C
25.5 C
25.4 C
```

## 1. DS18B20 接線

```text
DS18B20 VCC  -> ESP32 3V3
DS18B20 GND  -> ESP32 GND
DS18B20 DATA -> ESP32 GPIO4
4.7k resistor between DATA and 3V3
```

如果你想換腳位，改 `esp32_temperature_sender/esp32_temperature_sender.ino` 裡的 `ONE_WIRE_BUS`。

## 2. Jetson 端啟動接收器

先查 Jetson 在區網內的 IP：

```bash
hostname -I
```

啟動接收器：

```bash
python3 esp32_wifi_temperature_test/jetson_temperature_receiver.py --host 0.0.0.0 --port 8790
```

可以先用 Jetson 本機測試：

```bash
curl "http://127.0.0.1:8790/temperature?temperature_c=25.4"
```

終端機應該會多印一行：

```text
25.4 C
```

## 3. ESP32 端上傳程式

用 Arduino IDE 開啟：

```text
esp32_wifi_temperature_test/esp32_temperature_sender/esp32_temperature_sender.ino
```

安裝 Arduino libraries：

```text
OneWire
DallasTemperature
```

修改這三個設定：

```cpp
const char* WIFI_SSID = "YOUR_WIFI_SSID";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
const char* JETSON_TEMPERATURE_URL = "http://JETSON_LAN_IP:8790/temperature";
```

`JETSON_LAN_IP` 要填 `hostname -I` 查到的 Jetson IP，不要填 `127.0.0.1`。

上傳到 ESP32 後，Jetson 終端機會每 5 秒看到一次溫度。

## 4. 常見問題

如果 Jetson 沒收到：

```text
1. ESP32 和 Jetson 要在同一個 WiFi/LAN。
2. ESP32 程式裡的 Jetson IP 不能是 127.0.0.1。
3. Jetson 防火牆或路由器不能阻擋 ESP32 連到 port 8790。
4. 如果主程式已經佔用 8790，就把 Jetson 指令和 ESP32 URL 一起改成 8791。
5. DS18B20 DATA 要有 4.7k pull-up 到 3V3。
```
