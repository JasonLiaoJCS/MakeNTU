# Jetson Smart Home Dashboard API

Jetson 端 local dashboard service，給手機/網頁前端呼叫。網站只需要打 HTTP API；Jetson 負責讀寫本機狀態、proxy music/weather、讀 focus report、抓 camera frame，必要時再同步 UART 給 FRDM。

## Start

```bash
cd /home/asrlab-yian/MakeNTU
python3 smart_home_dashboard/server.py --host 0.0.0.0 --port 8789
```

手機或同網段裝置開：

```text
http://jetson-ip:8789/dashboard
```

預設不會開 FRDM UART，避免和 wake bridge 同時搶同一個 serial port。若 dashboard server 要直接同步 FRDM：

```bash
python3 smart_home_dashboard/server.py \
  --host 0.0.0.0 \
  --port 8789 \
  --frdm-uart-port auto
```

## Frontend API

### Whole Dashboard State

```http
GET /api/status
```

回傳 time / devices / sensors / todo / focus today / music / weather / system health。

### AI Trace

```http
GET /api/ai/trace?limit=20
```

回傳最近的使用者文字輸入與模型文字輸出，給網站 AI 分頁顯示。Wake Bridge 預設會寫 Jetson 本機：

```text
frdm_uart_context_sender/logs/ai_trace.jsonl
```

Dashboard 會優先讀這份 local trace；如果 Jetson 沒有該 log，才從 Windows AI server 的 `/health` 取 `last_debug`。可用環境變數或參數指定其他本地 log：

```bash
AI_DEBUG_LOG=/path/to/fast_chat_debug.jsonl \
python3 smart_home_dashboard/server.py --host 0.0.0.0 --port 8789
```

### Pet Camera

```http
GET /api/camera/latest
GET /api/camera/stream?fps=2
```

`latest` 回傳 JPEG；如果 Jetson 沒有 OpenCV 或 camera 不可用，會回 SVG placeholder。`stream` 是 MJPEG，前端可直接放到 `<img src="/api/camera/stream?fps=2">`。

### To-do

```http
GET  /api/todo
POST /api/todo
POST /api/todo/{id}/done
POST /api/todo/clear-completed
```

新增 body：

```json
{"text":"Write MakeNTU report"}
```

完成某項後 Jetson 會更新 JSON，再同步給 FRDM：

```text
Todo open_count,done_count
TodoItem slot,id,open,url_encoded_text
TodoEnd visible_count
```

### Focus Summaries

```http
GET /api/focus/summaries?range=today
GET /api/focus/summaries?range=week
GET /api/focus/summaries?range=month
GET /api/focus/summaries?range=all
GET /api/focus/session/{session_id}
```

資料來源：

```text
frdm_uart_context_sender/logs/focus_sessions/*/focus_summary.json
/tmp/focus_voice_test/*/focus_summary.json
```

Recent Sessions 的 title 會優先使用 `focus_summary.json.report_title`，格式和 Discord 第一行一致：

```text
專心報告：YYYY/MM/DD/HH 開始的專注時段
```

### Device Control

```http
GET  /api/devices
POST /api/devices/{device_id}/set
```

範例：

```json
{"state":"on","value":80}
```

目前是 Jetson local device registry，可讓前端 demo 電燈、風扇、冷氣狀態。若啟用 `--frdm-uart-port`，會額外送：

```text
Device device_id,state,value
```

### Sensors

```http
GET  /api/sensors
POST /api/sensors/{sensor_id}/update
```

範例：

```json
{"value":27.4,"unit":"C","online":true,"source":"frdm"}
```

這個 endpoint 先當作 Jetson/FRDM sensor hub 的共用資料入口。之後如果 FRDM 透過 Wi-Fi 或 UART 回傳溫濕度、光照、motion，也可以由 Jetson 寫進這裡，手機網站就會讀到同一份狀態。

### Music

```http
GET  /api/music/status
POST /api/music/control
```

範例：

```json
{"action":"play","query":"lofi study"}
```

Dashboard server 會轉送到既有 music sidecar：

```text
http://127.0.0.1:8788/music
```

若 music sidecar 使用 `--backend mpv`，Dashboard 會優先顯示 mpv 從 YouTube/yt-dlp 取得的 `media-title`，而不是使用者語音抽出的 query。`browser` 模式只開搜尋頁，無法可靠知道 YouTube 實際播放 title。

### Weather

```http
GET  /api/weather?location=Taipei
POST /api/weather
```

範例：

```json
{"location":"Taipei","text":"Taipei weather"}
```

Dashboard server 會轉送到既有 weather sidecar：

```text
http://127.0.0.1:8788/weather
```

### Events

```http
GET /api/events
```

回傳 dashboard 操作紀錄，例如 todo add/done、device set、music/weather calls。

### FRDM Power Cycle

```http
POST /api/frdm/power-cycle
```

網站 Maintenance 的 `Power Cycle` 按鈕會請 Jetson 斷開供給 FRDM 的電源，等待一小段時間後再恢復。這不是 UART reset，所以 FRDM firmware 當機、UART parser 停掉時也比較有機會救回來。

預設模式是 `usb-host`，會 unbind/bind Jetson xUSB controller：

```bash
python3 smart_home_dashboard/server.py \
  --host 0.0.0.0 \
  --port 8789 \
  --no-frdm-uart \
  --frdm-power-cycle-mode usb-host
```

預設 controller 是 `3610000.usb`，等同於 Jetson 端執行：

```text
echo 3610000.usb > /sys/bus/platform/drivers/tegra-xusb/unbind
sleep 2
echo 3610000.usb > /sys/bus/platform/drivers/tegra-xusb/bind
```

注意：這會重置該 USB controller，上面如果同時接 camera、UACDemo audio、或其他 USB 裝置，會短暫斷線再重連。Dashboard process 需要 root 權限，或讓 `sudo -n` 能寫入上面的 sysfs 路徑。

如果網站顯示 `sudo: a password is required`，代表 Dashboard 不能在 HTTP request 裡輸入密碼。正式 demo 建議用 `visudo` 加一條只允許寫入 xUSB bind/unbind 的 NOPASSWD 規則：

```bash
sudo visudo -f /etc/sudoers.d/makentu-frdm-power
```

內容把 `<jetson-user>` 換成 Jetson 登入帳號：

```text
<jetson-user> ALL=(root) NOPASSWD: /usr/bin/tee /sys/bus/platform/drivers/tegra-xusb/unbind, /usr/bin/tee /sys/bus/platform/drivers/tegra-xusb/bind
```

存檔後檢查：

```bash
sudo visudo -cf /etc/sudoers.d/makentu-frdm-power
```

臨時測試也可以直接用 root 跑 Dashboard：

```bash
sudo python3 smart_home_dashboard/server.py --host 0.0.0.0 --port 8789 --no-frdm-uart
```

如果要更精準只斷 FRDM，建議把 FRDM 電源接到獨立 USB hub port、relay、load switch、或 Jetson GPIO 控制的 power switch，然後改用：

```bash
--frdm-power-cycle-mode uhubctl --frdm-uhubctl-location <hub-location> --frdm-uhubctl-port <port>
```

或：

```bash
--frdm-power-cycle-mode script --frdm-power-cycle-script /path/to/power_cycle_frdm.sh
```

舊的 `POST /api/frdm/reset` 仍保留成相容 alias，但現在也會執行同一個 power-cycle 流程。

## Files

```text
smart_home_dashboard/server.py      # API server
smart_home_dashboard/static/        # phone/web dashboard UI
smart_home_dashboard/data/          # devices/events local state
smart_home_dashboard/data/sensors.json
frdm_uart_context_sender/logs/todo_list.json
frdm_uart_context_sender/logs/ai_trace.jsonl
```

`todo_list.json` 和 wake bridge/focus mode 共用，所以語音新增待辦、FRDM checkbox、手機網站完成待辦會看到同一份資料。
