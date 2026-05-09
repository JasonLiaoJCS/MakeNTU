# Quick Start: Wake Bridge Full Demo + Vision + FRDM UART + Focus + To-Do + Music + Weather + Dashboard

這份文件是現場 demo 操作手冊。每次要從零啟動，先看 **0. 一頁照貼版**，照順序貼 Terminal 1/2/4/5/3 即可。Terminal 4 是 Jetson 本地工具視窗，負責音樂 `/music` 與天氣 `/weather`；Terminal 5 是手機/網站 dashboard；Terminal 3 是 Wake Bridge，正式語音 demo 最後啟動。

最短使用方式：

```text
Terminal 1 on Windows : desktop_fast_chat_server.py
Terminal 2 on Jetson  : jetson_piper_tts.server
Terminal 4 on Jetson  : music_web_player.py   # local /music + /weather tools
Terminal 5 on Jetson  : smart_home_dashboard/server.py   # phone/web dashboard
Terminal 3 on Jetson  : wake_voice_chat_frdm_bridge.py
```

請盡量整段複製，不要手打尾端參數。最常見打錯是把下面兩個參數黏在一起：

```text
錯誤：--uart-debug\terval 0.75
正確：--tts-poll-interval 0.75 \
      --tts-debug \
      --uart-debug
```

現在建議 Terminal 3 直接用啟動腳本，避免 `source` 錯 venv 或尾端參數貼壞：

```bash
cd /home/asrlab-yian/MakeNTU
./frdm_uart_context_sender/run_wake_bridge_full_demo.sh
```

如果 FRDM firmware 還沒加入 Time / Weather / Todo / Health dashboard parser，log 出現 `FRDM UART RX: No such command exist.` 不會影響語音、TTS、表情和頭部馬達；要暫時關掉那些 dashboard UART 噪音，可用：

```bash
./frdm_uart_context_sender/run_wake_bridge_full_demo.sh --no-startup-time --no-startup-weather --no-dashboard-uart
```

目前預設：

```text
Windows Tailscale : 100.108.141.26
Jetson Tailscale  : 100.110.90.72
Windows server    : http://100.108.141.26:8766/voice-chat
Jetson TTS        : http://127.0.0.1:8777
Text/Vision model : qwen35-fast:latest
Wake word         : Hey Jarvis
FRDM baudrate     : 115200, CRLF
Local tool server : http://127.0.0.1:8788
Dashboard server  : http://jetson-ip:8789/dashboard
Weather source    : Open-Meteo, default location=Taipei
Startup weather   : enabled, sends Weather daily + Weather current before Normal
ESP32-S3 BLE     : fan + LED + DS18B20 status, device ESP32S3_FAN_LED_TEMP
Local temperature : preferred from ESP32 BLE notify; legacy HTTP push receiver still available
```

如果 Tailscale IP 變了，所有指令裡的 IP 要一起改。

目前完整功能穩定版參數：

```text
noisy_room=on
wake_threshold=0.75
wake_volume_min=500
wake_volume_ratio=1.35
wake_volume_window_seconds=1.0
standby_progress_interval=1.5
volume_min=1100
speech_start_margin=750
speech_start_ratio=1.45
silence_margin=900
silence_noise_ratio=1.30
recording_beep=1500Hz, 220ms, volume=0.35
max_speech_seconds=5
max_recording_seconds=7
audio_read_timeout=0.75
camera=auto 320x240 jpeg_quality=70
image_capture=speech_end
conversation_mode=on
boot_normal_delay=2.0s
focus_work_mode=on, interval=60s, manual stop
todo_list=on, local JSON at frdm_uart_context_sender/logs/todo_list.json
focus_report=focus_summary.json + focus_report.md, optional Discord webhook
focus_report_title=專心報告：YYYY/MM/DD/HH 開始的專注時段
tts_poll_interval=0.75
music_backend=mpv
music_wake_pause_timeout=0.6
weather_default_location=Taipei
smart_home_dashboard=on, Jetson :8789, phone/web controls Jetson state + ESP32 BLE appliances
esp32_ble=on by default in run_wake_bridge_full_demo.sh
esp32_dashboard_api=Wake Bridge local :8791 for Dashboard -> ESP32 BLE control
dashboard_wake_monitor=Wake Bridge writes logs/wake_status.json for volume/wake score/listening health
esp32_temperature_mode=push receiver still available, but BLE notify is preferred for the fan/LED/temp board
head_pitch=65..90..115 (down..center..up)
head_yaw=0..90..180 (right..center..left)
head_motor=enabled in Terminal 3 full demo, disable only for FRDM parser debugging
head_motion=pose-driven cute motion, large yaw/pitch targets with longer holds
```

這組偏向 demo 穩定與低延遲：現場吵也不會一輪卡太久，USB mic 停吐 audio chunk 時也會自動退出當輪。

現場音量安全檔位：

```text
目前建議值     : DEFAULT_VOLUME_GAIN=4.8, --tts-volume-gain 4.8
旁邊的人會嚇到 : 降到 3.6，並把 --beep-volume 降到 0.25
現場太吵聽不清 : 先升到 6.0，不要直接跳 8.0
USB sink 建議   : PulseAudio UACDemo 約 70%，ALSA PCM 70%
```

這裡的 `4.8` 是固定絕對增益，不是每次啟動再乘一次。它是前一版 `2.4` 的 `2x`，用來補回現場聽起來依然偏小的 TTS 音量。

新開 Terminal 會透過 `~/.bashrc` 自動呼叫 `frdm_uart_context_sender/set_uacdemo_volume.sh --wait 1`，把 UACDemo 的 PulseAudio 與 ALSA PCM 音量拉回絕對 `70%`。開機/登入時也有已啟用 linger 的 `makentu-uacdemo-volume.service` 和 `~/.config/autostart/makentu-uacdemo-volume.desktop` 會等待 USB speaker 出現後套用同一份絕對音量。臨時要改可以在開 Terminal 前設定 `MAKE_NTU_UACDEMO_PCM_VOLUME` / `MAKE_NTU_UACDEMO_PULSE_VOLUME`，例如 `MAKE_NTU_UACDEMO_PCM_VOLUME=60% MAKE_NTU_UACDEMO_PULSE_VOLUME=60% bash`。

新開 Terminal 也會把 demo 裝置和音量環境變數固定成 auto/keyword/absolute：`AUDIO_DEVICE=auto:UACDemo`、`MIC_DEVICE_KEYWORD=UACDemo`、`SPEAKER_DEVICE_KEYWORD=UACDemo`、`WAKE_CAMERA_ID=auto`、`FOCUS_CAMERA_ID=auto`、`FOCUS_UART_PORT=auto`、`TTS_VOLUME_GAIN=4.8`、`DEFAULT_VOLUME_GAIN=4.8`。如果真的要手動覆蓋，用 `MAKE_NTU_AUDIO_DEVICE` / `MAKE_NTU_WAKE_CAMERA_ID` / `MAKE_NTU_FOCUS_UART_PORT` / `MAKE_NTU_TTS_VOLUME_GAIN` 這類 `MAKE_NTU_*` 變數。

正式 demo 最省事的做法是先跑自動偵測，再用包好的啟動腳本：

```bash
cd /home/asrlab-yian/MakeNTU
bash frdm_uart_context_sender/auto_demo_devices.sh
./frdm_uart_context_sender/run_wake_bridge_full_demo.sh
```

`auto_demo_devices.sh` 會等 UACDemo 揚聲器、UACDemo 麥克風、FRDM UART、相機重新枚舉完成，並順手套用 UACDemo 70% 音量。`run_wake_bridge_full_demo.sh` 也會在每次啟動前再跑一次偵測，所以 USB 重插後通常只要重開 Terminal 3。

音量相關修改後：

```text
改 jetson_piper_tts/.env DEFAULT_VOLUME_GAIN -> 重啟 Terminal 2
改 Terminal 3 --tts-volume-gain              -> 重啟 Terminal 3
只改 beep 音量                                -> 重啟 Terminal 3
```

本次版本更新後一定要重啟：

```text
Windows Terminal 1  -> 必須重啟，讓 emotion alias / local fallback 生效
Jetson Terminal 2   -> 必須重啟，讓 TTS volume_gain API 生效
Jetson Terminal 3   -> 必須重啟，讓 Speaking 0-5、音樂誤判保護、tts-volume-gain 生效
Jetson Terminal 4   -> music/weather tool 若已正常可不用重啟；若點歌誤判仍怪，重啟它
Jetson Terminal 5   -> dashboard server 若前端/API 有改，重啟它
```

現場最快恢復順序：

```text
1. Windows Terminal 1 還活著嗎？先看 /health。
2. Jetson Terminal 2 TTS 還活著嗎？先看 /health。
3. 停掉舊 bridge：pkill -9 -f wake_voice_chat_frdm_bridge.py
4. Dashboard 不動可先不用重開；若網站怪怪的，重貼 Terminal 5。
5. 重貼 Terminal 3 正式完整模式。
6. 若找不到 USB，跑 ./recover_demo_usb.sh，然後重開 Terminal 2 和 Terminal 3。
```

FRDM UART 狀態機速查：

```text
bridge startup        -> wait 2s -> Time <payload> -> Weather daily <payload> -> Weather current <payload> -> Normal 0 0
Hey Jarvis detected   -> Thinking 0 0
AI/TTS starts         -> Speaking <0..5>
TTS speaking          -> MotorYawPitch <yaw> <pitch>
follow-up listening   -> Thinking 0 0
掰掰/拜拜/再見        -> Normal 0 0, then wake-only standby
睡覺/休息/晚安        -> Sleep 0 0, then wake-only standby
播放/繼續音樂         -> Music 0 0, then wake-only standby
暫停/停止音樂         -> Normal 0 0, then wake-only standby
專注/專心/工作模式    -> Focus 0 0, then wake-only standby
回來/回到正常         -> Normal 0 0
```

`Speaking` 是單參數，不能送尾巴的 `0`。這張表必須跟 FRDM `SpeakingGui()` 的 `switch (value)` 完全一致：

```text
neutral   -> Speaking 0
concerned -> Speaking 1
angry     -> Speaking 2
sad       -> Speaking 3
happy     -> Speaking 4
curious   -> Speaking 5   # FRDM 沒有獨立 curious 臉，借用 confused 臉
excited   -> Speaking 4   # FRDM 沒有獨立 excited 臉，借用 happy 臉
confused  -> Speaking 5
sleepy    -> Speaking 3   # FRDM 沒有獨立 sleepy 臉，借用 sad 臉
```

注意：這裡的 emotion 是機器人自己的表情反應，不是使用者的情緒分類。使用者罵髒話、生氣或責備時，機器人預設會送 `concerned -> Speaking 1`，代表冷靜關心，不會自動把使用者的怒氣鏡像成 angry 臉。只有機器人自己的回覆真的在嚴肅設界線或表達不悅時，才會送 `angry -> Speaking 2`。

常見同義情緒也會自動轉成目前 FRDM 支援的 speaking code：

```text
操你媽 / 生氣 / 火大 / 不爽  -> concerned -> Speaking 1   # robot does not mirror user anger
sad / 難過 / 沮喪             -> sad       -> Speaking 3
anxious / worried / 急 / 擔心 -> concerned -> Speaking 1
surprised / amazed / 興奮     -> excited   -> Speaking 4
unsure / puzzled / 困惑       -> confused  -> Speaking 5
tired / drowsy / 想睡         -> sleepy    -> Speaking 3
```

如果 log 顯示 `FRDM UART RX: Speaking 4` 但 FRDM 印出 `emotion: neutral`，代表 FRDM firmware 的 `SpeakingGui(char *pValue)` 沒解析到參數，不是 UART 沒送到。請把 FRDM 端 `sscanf(pValue, "%u", &value)` 換成 repo 裡的 patch：

```text
emotion_robot_controller/frdm_firmware/patches/speaking_gui_emotion_fix.c
```

如果你聽到「聲音超小」這類音量抱怨，程式不應再把 `聽到聲音` 誤判成點歌；只有明確「播放、放歌、我想聽歌、我想聽告白氣球」才會走 Music。

Startup Weather UART：

```text
Jetson startup weather lookup -> Weather daily,19,23,76,53
Jetson startup current lookup -> Weather current,20,20,0,3
Jetson + ESP32 local temp     -> Weather daily,19,23,76,53,254
Jetson + ESP32 local temp     -> Weather current,20,20,0,3,254
Jetson + ESP32 live room temp -> TempRoom 254      # every 10s, 254 = 25.4 C

格式：Weather kind,low_or_temp,high_or_temp,rain_percent,open_meteo_weather_code[,local_temp_c_x10]
current 例：Weather current,20,20,0,3
daily   例：Weather daily,19,23,76,53
local   例：Weather current,20,20,0,3,254  # 254 = 25.4 C
room    例：TempRoom 254                   # 254 = 25.4 C
```

Jetson 會用既有 `/weather` 工具查 Open-Meteo，並在 Terminal 3 有收到 ESP32/DS18B20 溫度時把它加成 Weather 第 6 欄。除此之外，Terminal 3 也會每 10 秒送一次 `TempRoom <攝氏x10>`，讓 FRDM 不用等下一次天氣查詢也能更新室內溫度畫面。FRDM 端 Weather parser 需要接受 5 欄或 6 欄，室內溫度頁則需要解析 `TempRoom 254` 這種單參數命令：

```text
emotion_robot_controller/frdm_firmware/patches/weather_uart_sleep_screen.c
```

FRDM 端 `TempRoom` parser 最小規格：

```text
UART line : TempRoom 254
meaning   : 25.4 C
range     : -550..1250
action    : update room-temp value only; do not switch screen
```

建議在 FRDM 端保存：

```c
int roomTempCx10 = 0;
bool roomTempValid = false;
```

收到 `TempRoom 254` 後顯示 `25.4 C`；還沒收到任何 `TempRoom` 前顯示 `--.- C`。

## 0. 一頁照貼版

### 0.0 啟動順序

```text
0. Windows refresh    : scp 最新 desktop_fast_chat_server.py, needed after server code changes
1. Windows Terminal 1 : ASR + qwen35-fast server
2. Jetson Terminal 2  : Piper TTS
3. Jetson Terminal 4  : Music Web Player, optional but recommended
4. Jetson Terminal 5  : Smart Home Dashboard, phone/web UI
5. Jetson Terminal 3  : Wake Bridge, 最後啟動
```

現場有兩種 UART owner 模式：

```text
語音桌寵 demo       : Terminal 3 Wake Bridge owns FRDM UART；Terminal 5 用 --no-frdm-uart
手機智慧家庭 HMI demo: Terminal 5 Dashboard owns FRDM UART；不要同時跑 Terminal 3 搶 UART
```

這份 Quick Start 預設走「語音桌寵 demo」，所以 Terminal 5 不碰 FRDM UART，只負責手機/網站 API。

Wake Bridge 啟動後會進入常駐 standby。之後只需要說：

```text
Hey Jarvis，講個笑話
Hey Jarvis，我現在是什麼表情
Hey Jarvis，我想要聽告白氣球
Hey Jarvis，暫停音樂
Hey Jarvis，繼續播放音樂
Hey Jarvis，換成七里香
Hey Jarvis，所在地天氣如何
Hey Jarvis，明天下午三點台北天氣如何
Hey Jarvis，今天會下雨嗎
Hey Jarvis，新增待辦 寫報告
Hey Jarvis，列出待辦
Hey Jarvis，完成待辦 1
Hey Jarvis，開始專心工作
Hey Jarvis，開始工作 25 分鐘 寫報告
Hey Jarvis，結束工作
```

音樂播放中只要偵測到 `Hey Jarvis`，Wake Bridge 會先暫停音樂，再開始錄你的下一句。
如果你接著問一般問題或控制 FRDM，音樂會維持暫停；要恢復請說 `Hey Jarvis，繼續播放音樂`。

### 0.1 先清掉舊 Wake Bridge

Jetson：

```bash
pkill -9 -f wake_voice_chat_frdm_bridge.py 2>/dev/null || true
```

如果你剛重插 USB speaker，Terminal 2 TTS 也建議重開，讓它重新抓 `UACDemo`。

### 0.2 Windows Refresh / SCP 同步

只要我有改過 Windows server bundle、`debug_version` 不對、或你不確定 Windows 是不是最新，就先在 Windows PowerShell 跑這段。這段會先釋放 `8766`，再從 Jetson scp 最新 server 檔案。

Windows PowerShell：

```powershell
$ErrorActionPreference = "Continue"

$port = 8766
$owners = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -Unique
foreach ($ownerPid in $owners) {
  if ($ownerPid -and $ownerPid -ne 0) {
    Stop-Process -Id $ownerPid -Force -ErrorAction SilentlyContinue
  }
}

New-Item -ItemType Directory -Force "$env:USERPROFILE\Desktop\windows_desktop_server_bundle" | Out-Null

scp asrlab-yian@100.110.90.72:/home/asrlab-yian/MakeNTU/emotion_robot_controller/voice_stt_remote/windows_desktop_server_bundle/desktop_fast_chat_server.py "$env:USERPROFILE\Desktop\windows_desktop_server_bundle\desktop_fast_chat_server.py"

cd "$env:USERPROFILE\Desktop\windows_desktop_server_bundle"
.\.venv\Scripts\Activate.ps1
python desktop_fast_chat_server.py --self-test
```

成功要看到：

```text
desktop_fast_chat_server self-test OK
```

如果 scp 連不到 Jetson，先在 Jetson 確認：

```bash
tailscale ip -4
```

然後把 PowerShell 指令裡的 `100.110.90.72` 換成 Jetson 目前的 Tailscale IP。

### 0.3 Terminal 1: Windows ASR/Ollama Server

Windows PowerShell，開著不要關：

```powershell
try {
  Invoke-RestMethod http://127.0.0.1:11434/api/tags | Out-Null
} catch {
  Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Minimized
  Start-Sleep -Seconds 3
}

ollama pull qwen35-fast:latest

cd "$env:USERPROFILE\Desktop\windows_desktop_server_bundle"
.\.venv\Scripts\Activate.ps1

python desktop_fast_chat_server.py `
  --host 0.0.0.0 `
  --port 8766 `
  --ollama-model qwen35-fast:latest `
  --vision-model qwen35-fast:latest `
  --no-think
```

### 0.4 Terminal 2: Jetson Piper TTS

Jetson，開著不要關：

```bash
cd /home/asrlab-yian/MakeNTU/jetson_piper_tts
source .venv/bin/activate

pkill -f 'jetson_piper_tts.server' 2>/dev/null || true

python -m jetson_piper_tts.server \
  --host 0.0.0.0 \
  --port 8777 \
  --no-warmup
```

TTS `.env` 必須是：

```text
AUDIO_DEVICE=auto:UACDemo
DEFAULT_VOLUME_GAIN=4.8
ENABLE_STREAM_PLAYBACK=true
```

`4.8` 是目前現場固定音量。若仍偏大，降到 `3.6`；若太小，再試 `6.0`。不要直接跳回 `8.0`。

### 0.5 Terminal 4: Jetson Local Tool Server

這個 terminal 建議開著看 log。它同時提供：

```text
/music    點歌、暫停、繼續、停止音樂
/weather  用 Open-Meteo 查所在地、明天、特定時間、指定城市天氣
```

若忘了開，Wake Bridge 點歌或問天氣時會嘗試自動啟動，但沒有這個視窗就比較難看錯誤。正式 demo 建議先手動開著。

```bash
cd /home/asrlab-yian/MakeNTU/music_web_player
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate

python3 music_web_player.py \
  --server \
  --host 127.0.0.1 \
  --port 8788 \
  --backend mpv \
  --mpv-audio-device auto \
  --mpv-volume 125 \
  --mpv-volume-max 200 \
  --mpv-ready-timeout 1.5 \
  --weather-default-location Taipei \
  --weather-timeout 4.5
```

`mpv` 會真的播放第一個搜尋結果，並支援 pause/resume 保留播放位置；`browser` 只開搜尋頁，不保證播放，也不能可靠暫停/繼續。
`--mpv-audio-device auto` 會優先找 `UACDemo` USB 音效輸出，避免 mpv 播了但聲音跑到 Jetson 預設音源。
`--mpv-volume 125` 只調整音樂本身，不會影響 Piper TTS；`--mpv-volume-max 200` 讓 mpv 接受大於 100 的音量設定，避免你把音樂調高卻被 mpv 內部上限截掉。
天氣走 Open-Meteo，不需要 API key。`--weather-default-location Taipei` 是「所在地、這裡、附近、here」的預設位置；如果 demo 場地在新竹，可改成 `Hsinchu`。`--weather-timeout 4.5` 會讓外部 Open-Meteo 查詢在合理時間內回覆，並搭配本機 cache 避免 dashboard/status 被天氣查詢卡住。

ESP32-S3 + DS18B20 本地溫度不在 Terminal 4。現在主線是 Terminal 3 透過 BLE 訂閱 ESP32-S3 狀態 notify；同一塊 ESP32 也控制風扇和 MAX7219 LED。舊版 WiFi push 溫度仍保留給只做溫度板的情境：ESP32 和 Jetson 在同一個 LAN，ESP32 定期 POST 到 Jetson：

```text
POST http://JETSON_LAN_IP:8790/temperature
{"ok":true,"temperature_c":25.4}
```

如果還在用舊 HTTP push，Jetson 的 LAN IP 用這個看，ESP32 程式裡要填這個 IP，不要填 `127.0.0.1`：

```bash
hostname -I
```

Terminal 4 health 可用這個看：

```bash
curl http://127.0.0.1:8788/health
```

重要欄位：

```text
backend=mpv        -> 正式播放模式
active=true        -> 目前有 mpv process
paused=true        -> 音樂暫停中，可以 resume
requested_mpv_volume=125 -> 啟動時要求的音樂音量
requested_mpv_volume_max=200 -> 啟動時要求的 mpv 上限
mpv_volume=125     -> 程式設定的音樂音量
mpv_volume_max=200 -> mpv --volume-max ceiling
mpv_actual_volume=125 -> mpv IPC 回報的實際音量，播放中才會有
mpv_effective_volume=125 -> 播放中用實際音量，閒置時用設定音量
mpv_volume_clamped=false -> true 代表要求值被上限截斷
last_query=...     -> 上一次點的歌
title=...          -> mpv 從 YouTube/yt-dlp 取得的實際 media title
ipc_path=/tmp/...  -> mpv IPC socket，pause/resume 需要它
weather_available=true -> /weather 已載入
weather_default_location=Taipei -> 所在地天氣預設城市
```

手動測天氣：

```bash
curl -X POST http://127.0.0.1:8788/weather \
  -H "Content-Type: application/json" \
  -d '{"text":"明天下午三點所在地天氣如何","default_location":"Taipei"}'
```

### 0.6 Terminal 5: Jetson Smart Home Dashboard

這個 terminal 提供手機/網站 dashboard：

```text
/dashboard             手機/網頁 UI
/api/status            time / devices / sensors / ESP32 temp / wake monitor / todo / focus / music / weather / health
/api/camera/latest     pet camera snapshot
/api/camera/stream     MJPEG pet camera stream
/api/devices           電器控制狀態，LED / fan 會轉送 Wake Bridge :8791
/api/todo              和 Wake Bridge 共用同一份 todo JSON
/api/focus/summaries   專心紀錄圖像化資料
/api/ai/trace          使用者文字輸入 / 模型文字輸出
/api/frdm/power-cycle  斷開/恢復 Jetson 供給 FRDM 的電源
/api/events            dashboard 操作紀錄
```

Terminal 3 Wake Bridge 會把每輪使用者 transcript / model reply 寫到：

```text
frdm_uart_context_sender/logs/ai_trace.jsonl
```

Terminal 5 的 AI 分頁和 `/api/ai/trace` 會優先讀這份本機 trace；如果還沒有語音 turn，才會退回 Windows AI `/health` 的最近 debug。

Terminal 3 Wake Bridge 也會每秒左右更新：

```text
frdm_uart_context_sender/logs/wake_status.json
```

Dashboard 的 Live tab 會用它顯示 `Wake Listen`、`Volume`、`Wake Score`。System Health 裡原本的 `TTS` 指示已改成 `Wake Listen`，代表目前 wake bridge 的監聽迴圈是不是還活著；TTS server health 仍保留在 `/api/status.health.details.tts` 裡。

Live tab 的 Smart Appliances 區塊走這條路：

```text
Local Temperature <- Wake Bridge ESP32 BLE status notify
LED Light         -> Dashboard :8789 -> Wake Bridge :8791 -> ESP32 BLE LED_ON/OFF
Fan Speed         -> Dashboard :8789 -> Wake Bridge :8791 -> ESP32 BLE FAN_SPEED
```

所以要讓網站上的 LED/風扇真的動，Terminal 3 必須開著並啟用 ESP32 BLE。`run_wake_bridge_full_demo.sh` 已經會啟用主線 ESP32 BLE 設定；若手動下指令，要包含 `--esp32-ble`、ESP32 address/adapter，且不要加 `--no-esp32-dashboard-control`。

語音桌寵 demo 預設不要讓 Dashboard 搶 FRDM UART，所以用 `--no-frdm-uart`：

```bash
cd /home/asrlab-yian/MakeNTU
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate

python3 smart_home_dashboard/server.py \
  --host 0.0.0.0 \
  --port 8789 \
  --no-frdm-uart
```

網站 Maintenance 的 `Power Cycle` 按鈕不走 UART。預設 `--frdm-power-cycle-mode usb-host` 會讓 Jetson unbind/bind `3610000.usb`，把 USB-powered FRDM 斷電後再重啟。這可以和語音 demo 同時使用，但同一個 USB controller 上的 camera / UACDemo audio / 其他 USB 裝置會短暫斷線再重連。

這個動作需要 Dashboard 有權限寫入：

```text
/sys/bus/platform/drivers/tegra-xusb/unbind
/sys/bus/platform/drivers/tegra-xusb/bind
```

實機 demo 可以用 root 跑 Dashboard，或設定 passwordless sudo 讓 Dashboard 用 `sudo -n tee` 寫入這兩個 sysfs 檔案。

如果網站跳 `FRDM power cycle failed: sudo: a password is required`，代表 web request 不能互動輸入 sudo 密碼。新版 Dashboard 會用 `sudo -n tee` 寫入 xUSB bind/unbind；正式 demo 建議用 `visudo` 加 NOPASSWD：

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

臨時測試可以直接用 root 跑 Dashboard：

```bash
sudo python3 smart_home_dashboard/server.py --host 0.0.0.0 --port 8789 --no-frdm-uart
```

如果要只斷 FRDM、不影響其他 USB 裝置，建議用獨立 hub port / relay / load switch，並改成：

```bash
python3 smart_home_dashboard/server.py \
  --host 0.0.0.0 \
  --port 8789 \
  --no-frdm-uart \
  --frdm-power-cycle-mode script \
  --frdm-power-cycle-script /path/to/power_cycle_frdm.sh
```

手機或同網段電腦打開：

```text
http://JETSON_LAN_IP:8789/dashboard
```

Jetson LAN IP 用這個看：

```bash
hostname -I
```

Dashboard health / smoke test：

```bash
curl http://127.0.0.1:8789/api/status
curl http://127.0.0.1:8789/api/devices
curl http://127.0.0.1:8789/api/focus/summaries?range=today
```

如果是「手機智慧家庭 HMI demo」，要讓網站按鈕也同步 FRDM 面板，才改成：

```bash
python3 smart_home_dashboard/server.py \
  --host 0.0.0.0 \
  --port 8789 \
  --frdm-uart-port auto
```

這個模式不要同時跑 Terminal 3 Wake Bridge，避免兩個 process 搶同一條 UART。

### 0.7 Terminal 3: Jetson Wake Bridge

這個最後啟動。請整段複製，不要手打最後幾行。要真的送 Discord，先在同一個 terminal 設：

```bash
export DISCORD_WEBHOOK_URL='https://discord.com/api/webhooks/...'
```

沒有設定 webhook 時，專心報告仍會寫到本機檔案，只是不會送出通知。
如果手動用 Python 測 webhook，request 要帶 `User-Agent`，不然可能被 Discord 前面的 Cloudflare 擋成 `403 error code: 1010`：

```bash
python3 - <<'PY'
import os, json, urllib.request, urllib.error

url = os.environ["DISCORD_WEBHOOK_URL"].strip()
payload = json.dumps({"content": "Jetson Discord webhook test."}).encode("utf-8")
req = urllib.request.Request(
    url,
    data=payload,
    headers={
        "Content-Type": "application/json",
        "User-Agent": "DiscordBot (https://github.com/asrlab-yian/MakeNTU, 0.1)",
    },
    method="POST",
)

try:
    with urllib.request.urlopen(req, timeout=8) as r:
        print("status:", r.status)
        print(r.read().decode("utf-8", errors="replace"))
except urllib.error.HTTPError as e:
    print("HTTP status:", e.code)
    print(e.read().decode("utf-8", errors="replace"))
PY
```

推薦直接跑腳本；它會自動進 `/home/asrlab-yian/MakeNTU`、啟用 `emotion_robot_controller/.venv`，並帶完整穩定參數：

```bash
cd /home/asrlab-yian/MakeNTU
./frdm_uart_context_sender/run_wake_bridge_full_demo.sh
```

ESP32 BLE 建議用掃描到的 MAC 啟動，最穩：

```bash
ESP32_BLE_ADDRESS=78:E3:6D:18:94:6A \
ESP32_BLE_ADAPTER=hci0 \
./frdm_uart_context_sender/run_wake_bridge_full_demo.sh
```

如果你要 Jetson 開機就自動啟動 Wake Bridge 並自動連 ESP32 BLE，安裝 user systemd service：

```bash
cd /home/asrlab-yian/MakeNTU
./frdm_uart_context_sender/install_wake_bridge_service.sh \
  --address 78:E3:6D:18:94:6A \
  --adapter hci0

systemctl --user start makentu-wake-bridge.service
journalctl --user -u makentu-wake-bridge.service -f
```

之後重開機也會自動啟動。BLE 中途斷線時，程式會持續重新 scan/connect；如果整個 bridge 程式掛掉，systemd 會用 `Restart=always` 拉起來。要改 MAC、Tailscale IP 或風扇最低 PWM，編輯：

```bash
nano ~/.config/makentu/wake-bridge.env
systemctl --user restart makentu-wake-bridge.service
```

如果 BLE 斷線時你說「開風扇 / 關風扇 / 調高風扇」，Jetson 會直接語音提醒「目前沒有連上 ESP32-S3 藍芽、正在重新連線」，並把這次指令先排進佇列；ESP32-S3 重新連上後會自動送出。如果 BLE 連線中且最新 ESP32 狀態已經是 `FAN:OFF`，你又說「關風扇」，Jetson 會直接說電風扇明明已經是關的，不會再重複送 `FAN_OFF`。

語音控制 ESP32 是本地控制，不會先丟給一般 AI route。`關風扇 / 全部關掉 / 關 LED` 會先送 BLE 指令，再用 Piper 說一小句確認，最後送 `Normal 0 0` 並退出 conversation follow-up。這些確認句強制 `head_motion=none`；Terminal 3 看到 `speaking head motion skipped: none` 是正確的，代表有講話但不轉頭，避免「沒聲音或已關閉後馬達還在動」。

需要調風扇最低起轉 duty 時：

```bash
ESP32_BLE_ADDRESS=78:E3:6D:18:94:6A \
ESP32_BLE_ADAPTER=hci0 \
FAN_MIN_PWM=120 \
./frdm_uart_context_sender/run_wake_bridge_full_demo.sh
```

暫時不接 ESP32 時：

```bash
ESP32_BLE=0 ./frdm_uart_context_sender/run_wake_bridge_full_demo.sh
```

如果 Windows Tailscale IP 變了，不用改檔案，這樣覆蓋：

```bash
SERVER_URL=http://NEW_WINDOWS_IP:8766/voice-chat \
FOCUS_SERVER_URL=http://NEW_WINDOWS_IP:8766/focus-check \
./frdm_uart_context_sender/run_wake_bridge_full_demo.sh
```

```bash
cd /home/asrlab-yian/MakeNTU
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate

python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --mic-keyword UACDemo \
  --beep-keyword UACDemo \
  --beep-player auto \
  --noisy-room \
  --tts-volume-gain 4.8 \
  --beep-volume 0.35 \
  --uart-port auto \
  --uart-baudrate 115200 \
  --frdm-uart-tx-timeout 0.45 \
  --frdm-uart-failure-threshold 2 \
  --frdm-uart-circuit-breaker-sec 4.0 \
  --enable-head-motor \
  --boot-normal-delay 2.0 \
  --device-ready-timeout 30 \
  --wake-threshold 0.75 \
  --wake-volume-min 500 \
  --volume-min 1100 \
  --speech-start-margin 750 \
  --silence-duration 1.2 \
  --silence-margin 900 \
  --max-speech-seconds 5 \
  --max-recording-seconds 7 \
  --audio-read-timeout 0.75 \
  --recording-progress-interval 1.0 \
  --conversation-mode \
  --turn-listen-timeout 8 \
  --session-idle-timeout 30 \
  --max-session-turns 20 \
  --camera-id auto \
  --camera-width 320 \
  --camera-height 240 \
  --camera-jpeg-quality 70 \
  --camera-latest-timeout 1.0 \
  --camera-frame-max-age 2.0 \
  --focus-script /home/asrlab-yian/MakeNTU/frdm_uart_context_sender/focus_work_mode.py \
  --focus-server-url http://100.108.141.26:8766/focus-check \
  --focus-interval-sec 60 \
  --focus-first-sample-delay-sec -1 \
  --focus-duration-min 0 \
  --focus-log-root /tmp/focus_voice_test \
  --focus-alert-threshold 1 \
  --focus-alert-cooldown-sec 90 \
  --fan-device-id desk_fan \
  --fan-speed-max 100 \
  --esp32-ble \
  --esp32-ble-adapter hci0 \
  --esp32-ble-scan-duplicates \
  --esp32-ble-min-fan-pwm 96 \
  --esp32-dashboard-host 127.0.0.1 \
  --esp32-dashboard-port 8791 \
  --fan-duplicate-suppress-sec 2.0 \
  --todo-list-path /home/asrlab-yian/MakeNTU/frdm_uart_context_sender/logs/todo_list.json \
  --wake-status-path /home/asrlab-yian/MakeNTU/frdm_uart_context_sender/logs/wake_status.json \
  --focus-notify-mode discord \
  --focus-discord-webhook-url "$DISCORD_WEBHOOK_URL" \
  --music-backend mpv \
  --music-mpv-audio-device auto \
  --music-mpv-volume 125 \
  --music-mpv-volume-max 200 \
  --music-mpv-ready-timeout 1.5 \
  --music-timeout 5 \
  --music-wake-pause-timeout 0.25 \
  --music-wake-beep-settle 0.05 \
  --post-music-standby-cooldown 0.8 \
  --music-debug \
  --weather-default-location Taipei \
  --weather-timeout 6 \
  --weather-api-timeout 4.5 \
  --weather-debug \
  --esp32-temperature-mode push \
  --esp32-temperature-host 0.0.0.0 \
  --esp32-temperature-port 8790 \
  --esp32-temperature-path /temperature \
  --temp-room-uart-interval-sec 10 \
  --temp-room-uart-max-age-sec 30 \
  --motor-step-delay 0.55 \
  --motor-smooth-step-deg 120 \
  --motor-speaking-step-delay 0.72 \
  --motor-speaking-smooth-step-deg 120 \
  --motor-reset-repeats 1 \
  --motor-reset-delay 0.35 \
  --motor-stop-timeout 6 \
  --motor-join-timeout 6 \
  --device-preflight-verbose \
  --tts-poll-interval 0.75 \
  --tts-start-poll-interval 0.12 \
  --tts-speaking-start-timeout 1.2 \
  --tts-speaking-require-audio \
  --tts-debug \
  --uart-debug
```

這是 Wake Bridge 完整功能版：wake word、連續對話、speech-end 拍照、FRDM UART、Piper TTS、To-Do、Music、Weather、Focus Work Mode、ESP32 BLE、Dashboard ESP32 control API 都會開。第一次說 `Hey Jarvis` 後會進入 conversation mode，後續 follow-up 不用重複喚醒詞；說 `byebye / 掰掰 / 拜拜 / 再見`、叫它睡覺、音樂控制結束、或 focus mode 指令處理完後，會回到只聽喚醒詞的 standby。

啟動成功時 Terminal 3 會看到：

```text
ESP32 dashboard control: http://127.0.0.1:8791/api/esp32/status
```

Terminal 5 Dashboard 會用這個 local API 轉送 LED/風扇控制，並讀 `logs/wake_status.json` 顯示監聽狀態、volume、wake score。

Terminal 3 預設就是完整功能版，包含頭部馬達；啟動指令內已經有 `--enable-head-motor`。畫面同步採嚴格模式：文字回覆已經回來但喇叭尚未出聲時，FRDM 仍停在 `Thinking`；只有 TTS `/health` 回報 `audio.playing=true` 時才送 `Speaking <emotion>` 並開始 `MotorYawPitch <yaw> <pitch>` speaking motion。TTS job 結束後才停止 speaking motion，並切到下一個 Normal / Music / Focus / Sleep 畫面。新版馬達策略是「少量大動作 + 到位停留」，不是密集小步進。

如果 FRDM parser 還在 debug、暫時不想讓馬達動，把指令中的 `--enable-head-motor \` 改成：

```bash
--disable-head-motor \
```

啟動成功時要看到：

```text
Head motor motion: enabled=True
```

如果只想一問一答、每次都要重新說 `Hey Jarvis`，把 `--conversation-mode`、`--turn-listen-timeout`、`--session-idle-timeout`、`--max-session-turns` 這四行拿掉。

如果想要更激進低延遲，可以額外加 `--ultra-response`；如果講話中間常停頓被太早切句，改用比較保守的 `--turbo-response`。`fast_reply / num_predict` 需要 Windows Terminal 1 也使用最新版 `desktop_fast_chat_server.py` 並重啟；如果 Windows 還是舊 server，只會套用 Jetson 端的錄音/TTS/camera 加速。

現場吵雜版已加 `--noisy-room`：speech/silence gate 會比安靜室內更嚴格。正式腳本另外固定 `--beep-volume 0.35`，避免提示音太刺耳。TTS 回覆使用固定 `--tts-volume-gain 4.8`；若仍偏大降到 `3.6`，仍太小再回到 `6.0`。只想先測 beep 音量可跑：

```bash
python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py --beep-keyword UACDemo --noisy-room --beep-volume 0.35 --test-beep
```

如果現場背景音量平均約 `10000`、你講話約 `19000`，`--noisy-room` 會自動抓大約：

```text
wake accept volume >= 13500
speech start       >= 14500
speech end/silence <= 13000
```

所以背景聲不會太容易觸發 speech，而你講話到 19000 仍會開始錄；講完掉回背景音附近時也比較能結束。

如果你看到 `wake=0.98` 但同一行 `volume` 很低而被忽略，通常是 openWakeWord score 延遲：真正說 `Hey Jarvis` 的高音量發生在前幾個 audio chunk。新版會用最近 1 秒的 `recent_peak` 來通過 wake 音量 gate。standby 音量也改成摘要 log；想完全關掉 standby 音量列可加：

```text
--standby-progress-interval 0
```

每次準備收音前都會先 beep；程式判定你講完時會再 beep 一聲，並在那一刻抓照片，跟該輪語音一起送到 Windows server。beep 預設用 `--beep-player auto`，會優先走 `paplay` / PulseAudio 的 UACDemo sink，不再用 `sounddevice` 直接開 ALSA output，避免 PortAudio 在音樂播放/暫停交界時 assertion abort。播音樂後下一次 `Hey Jarvis` 會先快速 pause 音樂、短暫等 0.05 秒，再播開始收音 beep；為了讓嗶聲快，wake 當下不再先送 `Music paused` dashboard UART。音樂被 pause 後會重設錄音 gate，避免剛剛音樂音量把 speech/silence 門檻墊太高，導致後續回應變慢。說 `byebye / 掰掰 / 拜拜 / 再見` 後會送 `Normal 0 0`；說「去睡覺吧 / 休息一下」會送 `Sleep 0 0`。兩者都會回到只聽喚醒詞的 standby；這之後你講一般話不會送 ASR/Ollama，下一次必須重新說 `Hey Jarvis`。

音樂控制也會自動結束 conversation mode：播音樂或繼續播放後會送 `Music 0 0` 並回到 wake-only standby；暫停或停止會先處理 mpv，再用 Piper 說一小句確認，最後送 `Normal 0 0` 並回到 wake-only standby。這些本地控制確認不會啟動頭部馬達，避免沒有實際說話時馬達還在動。所以下次要暫停、停止或換歌，都必須先說 `Hey Jarvis`。音樂 mpv 預設音量已調成 `125`，不影響 Piper TTS 的說話音量；覺得太大或太小就用 `MUSIC_MPV_VOLUME=95 ./frdm_uart_context_sender/run_wake_bridge_full_demo.sh` 覆蓋。

音樂控制的預期 log：

```text
停止/暫停音樂:
Music stop/pause handled before TTS; local confirmation will be spoken.
FRDM UART TX: Speaking 0
speaking head motion skipped: none
FRDM UART TX: Normal 0 0

繼續/播放音樂:
TTS finished
Music tool: action=resume/play
FRDM UART TX: Music 0 0
```

如果停止時剛好沒有 mpv process，Piper 會說「音樂現在沒有在播放。」這也會回 `Normal 0 0` 並回到 wake-only standby，不會再靜音略過。

音樂正在播放時，Wake Bridge 預設維持 `5aae453` 版本的喚醒行為：仍用一般 `--wake-threshold 0.75`，所以可以直接喊 `Hey Jarvis` 讓它 pause 音樂並進入收音。若現場喇叭真的一直誤觸，再額外加 `--music-wake-guard --music-wake-threshold 0.9 --music-wake-confirm-chunks 1`。

Focus Work Mode 指令也會自動結束 conversation mode，避免進入工作模式後還一直收 follow-up。Wake Bridge 會先講完進入專注模式的回覆、切到 Focus 畫面，最後才打開 focus 子程序的 activation gate；gate 開啟前子程序不會取樣、不會罵人、不會插隊 TTS。`--focus-first-sample-delay-sec -1` 代表第一張工作狀態照片等同 `--focus-interval-sec 60` 秒後再拍，之後每 60 秒取樣一次；照片預設只存在記憶體，判斷完就丟掉。`--focus-duration-min 0` 代表不自動結束，要再說「結束工作 / 停止專心 / 下班」才會停。

Wake Bridge 啟動 focus mode 時，FRDM 會先維持 `Thinking`；等 TTS `/health` 回報 audio.playing，才送 `Speaking <emotion>` 並開始頭部動作。TTS/頭部動作結束後才送 `Focus 0 0`。背景的 `focus_work_mode.py` 會被 UART gate 擋住，在 Speaking 期間完全不送 `Focus active/focused/idle` 或 `Thinking`；Wake Bridge 送完 `Focus 0 0` 後才打開 gate，讓背景程序繼續同步 `Focus ...` dashboard raw data。如果是設定分鐘數自動結束，子程序最後仍會送 `Normal 0 0`。

Focus 取樣判斷為 `focused` 時，背景程序會保持安靜，不再重送 `Focus focused,...`，倒數也會照原本時間繼續。若達到 `--focus-alert-threshold 1` 次判斷為 `distracted / phone / away / sleeping`，背景程序會等 TTS audio.playing 後才送 `Speaking 2`，用 TTS 嚴厲提醒回到工作、跑一段 `MotorYawPitch` 警告動作，然後回到 `Focus <state>,<remaining>,<streak>`；有設定自動結束時間時，倒數會從該次分心重新計時。預設兩次 spoken alert 至少間隔 `--focus-alert-cooldown-sec 90` 秒，避免每張照片都罵一次；倒數重設仍會照 confirmed distraction 執行。

FRDM 觸控回傳現在由 Wake Bridge 的單一 UART bus 常駐監聽，不再靠短輪詢。也就是 Speaking/TTS/頭部馬達動作、Hey Jarvis standby、conversation follow-up 期間，Jetson 都會持續讀 FRDM 回送行。FRDM 風扇 UI 建議送：

```text
Fan 1,50         # 開，風速 50%
Fan 0,0          # 關
EVT,Fan,1,100    # 也支援 EVT 前綴
FanSpeed 75      # 只改風速百分比；大於 0 會視為開
```

Wake Bridge 會把它轉成 `desk_fan` dashboard 狀態，預設 POST 到 `http://127.0.0.1:8789/api/devices/desk_fan/set`，並在啟用 `--esp32-ble` 時把 0-100 風速百分比轉成 ESP32-S3 的 0-255 PWM 指令；非零低速會至少送 `FAN_MIN_PWM=96` / `--esp32-ble-min-fan-pwm 96`，避免小風扇顯示 ON 但 duty 太低起轉不了。若風扇還是卡住，可先試 `FAN_MIN_PWM=120`。相同狀態會用 `--fan-duplicate-suppress-sec 2.0` 去重，避免 FRDM slider 持續回報 `FanSpeed 100` 時洗 terminal 和 dashboard。若要真的控制 Jetson GPIO/PWM/relay，另外加 `--fan-control-command "/path/to/fan_control.sh {power} {speed} {percent}"`；環境變數也會帶 `FAN_POWER`、`FAN_SPEED`、`FAN_PERCENT`、`FAN_STATE`。Focus 子程序的 UART 也改走 parent UART proxy，所以不會和主程式搶 `/dev/ttyACM0`。

如果 FRDM CDC 一時卡住，正式指令現在會用 `--frdm-uart-tx-timeout 0.45` 快速失敗，不會再每個 `Thinking/Speaking/MotorYawPitch` 卡約 2 秒；連續失敗 2 次後會暫停 TX 4 秒但保持 RX 監聽，所以觸控事件恢復後仍能進來。看到 `FRDM UART bus temporarily bypassing TX` 時，先檢查 FRDM firmware 是否正在讀 UART、MCU-LINK USB-C 是否穩定，必要時重插 FRDM 或加 `--no-frdm-uart-bus` 暫時退回舊 per-command TX。

Wake Bridge 的 device preflight 會清掉真的 `mpv/aplay/ffplay` audio process，但不會再因為 Terminal 4 指令裡有 `--backend mpv` 就誤殺 `music_web_player.py`。如果你看到 Terminal 4 印 `Music web player stopped.`，代表它真的收到 SIGINT/SIGTERM；先用 `curl http://127.0.0.1:8788/health` 確認，沒活著再重開 Terminal 4。

Focus 結束時會寫 `focus_summary.json` 和 `focus_report.md`，內容會整合專注時間、分心時間、專注分數、建議，以及這段期間完成/剩下的 To-Do。報告標題會使用「專心報告：YYYY/MM/DD/HH 開始的專注時段」，同時寫進 `focus_summary.json` 的 `report_title`，Discord 第一行也會使用同一個標題。若有設定 `DISCORD_WEBHOOK_URL`，會透過 Discord webhook 送一則短摘要；沒設 webhook 時只會留下檔案。

To-Do List 是本機 JSON 功能，不需要 Terminal 4 或 Windows server 額外支援。說「新增待辦 寫報告」「列出待辦」「完成待辦 1」會直接更新 `frdm_uart_context_sender/logs/todo_list.json`；它不會啟動/停止 focus mode，focus mode 執行中仍可先記明確的待辦。

不要把 `--conversation-mode` 和 `--no-wake-word` 一起用；程式會拒絕啟動，避免結束後仍然不用喚醒詞就錄音。

### 0.8 ESP32-S3 BLE 風扇 / LED / 溫度快速流程

正式 demo 現在預設啟用 ESP32 BLE。ESP32-S3 端請燒完整硬體版，不是只廣播的 minimal test：

```text
frdm_uart_context_sender/examples/esp32s3_ble_fan_led_temp/esp32s3_ble_fan_led_temp.ino
```

硬體接線速查：

```text
L9110S fan INA -> GPIO5 PWM
L9110S fan INB -> GPIO6 LOW
L9110S VCC     -> 5V
L9110S GND     -> Jetson/ESP32/馬達電源共地

MAX7219 DIN -> GPIO11
MAX7219 CS  -> GPIO10
MAX7219 CLK -> GPIO12

DS18B20 DATA -> GPIO7
DS18B20 VCC  -> 3.3V
DS18B20 GND  -> GND
DATA 和 3.3V 之間 4.7k pull-up
```

BLE 參數：

```text
Name        : ESP32S3_FAN_LED_TEMP
Service UUID: 12345678-1234-1234-1234-1234567890ab
Command UUID: 12345678-1234-1234-1234-1234567890ac
Status UUID : 12345678-1234-1234-1234-1234567890ad
Status      : TEMP:24.12,FAN:ON,SPEED:96,LED:OFF
```

第一次先掃描，抓到 MAC 後固定用 `ESP32_BLE_ADDRESS`，不要依賴名稱，因為 Jetson/BlueZ 有時會看到 `(unnamed)`：

```bash
cd /home/asrlab-yian/MakeNTU
source emotion_robot_controller/.venv/bin/activate

python3 frdm_uart_context_sender/esp32s3_ble_fan_led_controller.py \
  --scan-only \
  --scan-target-only \
  --adapter hci0 \
  --scan-duplicates \
  --scan-timeout 30
```

看到這種就算成功：

```text
* 78:E3:6D:18:94:6A    (unnamed) rssi=-95 uuids=12345678-1234-1234-1234-1234567890ab
```

Standalone CLI 先測硬體：

```bash
python3 frdm_uart_context_sender/esp32s3_ble_fan_led_controller.py \
  --address 78:E3:6D:18:94:6A \
  --adapter hci0 \
  --scan-duplicates \
  --no-tts-reminder
```

進入 `ble>` 後依序測：

```text
TEMP?
LED_ON
LED_OFF
FAN_SPEED:255
PERCENT:50
FAN_OFF
```

`FAN_SPEED:255` 是硬體最大速測試。如果 255 都不轉，先查 5V、共地、L9110S 輸出、馬達接線，不要先改程式。

FRDM 手動模式送的是 0-100 百分比，Jetson 會轉成 0-255 PWM。為了避免小風扇低 duty 不起轉，非零值預設至少送 `FAN_MIN_PWM=96`：

```text
FanSpeed 0   -> FAN_OFF
FanSpeed 16  -> FAN_SPEED:96
FanSpeed 50  -> FAN_SPEED:128
FanSpeed 75  -> FAN_SPEED:191
FanSpeed 100 -> FAN_SPEED:255
```

如果 `FanSpeed 16` 還是卡住，可把最低起轉 PWM 調高：

```bash
ESP32_BLE_ADDRESS=78:E3:6D:18:94:6A \
ESP32_BLE_ADAPTER=hci0 \
FAN_MIN_PWM=120 \
./frdm_uart_context_sender/run_wake_bridge_full_demo.sh
```

正式啟動建議：

```bash
ESP32_BLE_ADDRESS=78:E3:6D:18:94:6A \
ESP32_BLE_ADAPTER=hci0 \
./frdm_uart_context_sender/run_wake_bridge_full_demo.sh
```

可以講的語音：

```text
Hey Jarvis，幫我開風扇
Hey Jarvis，關風扇
Hey Jarvis，風扇轉快一點
Hey Jarvis，風扇轉慢一點
Hey Jarvis，LED on
Hey Jarvis，LED off
```

ESP32 溫度 notify 也會變成 local temperature，天氣 UART 有新鮮溫度時會補第 6 欄，例如 `254 = 25.4 C`。同一個溫度還會每 10 秒送 `TempRoom 254` 給 FRDM，讓室內溫度畫面可以持續更新；頻率用 `TEMP_ROOM_UART_INTERVAL_SEC` 或 `--temp-room-uart-interval-sec` 調整。溫度高於 `--esp32-ble-passive-threshold` 預設 25 度時，Wake Bridge 會被動提醒是否要開風扇；使用者可以再用語音或 FRDM 觸控開啟。

只測 ESP32 BLE + FRDM 溫度回傳、不跑完整 Hey Jarvis 時，可用 standalone controller：

```bash
python3 frdm_uart_context_sender/esp32s3_ble_fan_led_controller.py \
  --address 78:E3:6D:18:94:6A \
  --adapter hci0 \
  --scan-duplicates \
  --frdm-uart-port auto \
  --frdm-temp-room-interval-sec 10 \
  --no-tts-reminder
```

這個 standalone 測試會直接開 FRDM UART；跑它之前先停 Terminal 3 Wake Bridge，避免兩個 process 搶同一個 `/dev/ttyACM*`。

應看到：

```text
[12:00:01] ESP32 status: TEMP=25.43 C, FAN=OFF, SPEED=0, LED=OFF
FRDM UART TX: TempRoom 254
```

### 0.9 啟動成功最小判斷

Terminal 3 看到這些就可以開始說 `Hey Jarvis`：

```text
Client version: wake_voice_chat_frdm_bridge_vision_conversation_motor_natural_v6
Server health: debug_version=13, chat_ready=True, asr_loaded=True
TTS health: ready=True
Selected input device ... by keyword 'UACDemo'
Selected beep output device ... by keyword 'UACDemo'
Camera ready in continuous warm-reader mode
Camera warm reader opened camera 0
Focus work mode: enabled, script=/home/asrlab-yian/MakeNTU/frdm_uart_context_sender/focus_work_mode.py, interval=60s, duration_default=0min, notify=discord
To-do list: enabled, path=/home/asrlab-yian/MakeNTU/frdm_uart_context_sender/logs/todo_list.json
Music tool: http://127.0.0.1:8788/music, backend=mpv->mpv, autostart=True, pause_on_wake=True, beep_settle=0.18s, post_music_cooldown=0.8s
Weather tool: http://127.0.0.1:8788/weather, default_location=Taipei, source=Open-Meteo
ESP32-S3 BLE fan/LED/temp: enabled, name=ESP32S3_FAN_LED_TEMP, address=78:E3:6D:18:94:6A, voice_control=True, frdm_relay=True, min_pwm=96, passive_reminder=True (>25 C)
BLE: connected. Subscribing status notify...
Weather local temperature: push receiver http://0.0.0.0:8790/temperature
FRDM room temperature UART: TempRoom every 10s, max_age=30s, payload=Celsius*10
Head motor motion: enabled=True, smooth_step=120deg, step_delay=0.55s, speaking_step_delay=0.72s, speaking_smooth_step=120deg, reset_repeats=1, reset_delay=0.35s, read_ms=35, stop_timeout=6s, join_timeout=6s
Boot screen settle: waiting 2s, then sending startup dashboard data and Normal.
ESP32 temperature receiver: http://0.0.0.0:8790/temperature
Weather UART sent: Weather daily,19,23,76,53,254 (local=25.4 C)    # 如果 ESP32 已先送溫度
Weather UART sent: Weather current,20,20,0,3,254 (local=25.4 C)   # startup 也會送 current
TempRoom UART sent: TempRoom 254 (25.4 C, age=0.5s)               # 之後每 10 秒一次
FRDM UART TX: Normal 0 0
Listening for wake word 'hey_jarvis'
```

目前馬達 UART 是絕對角度，不是相對位移：

```text
MotorPitch 65   -> 低頭極限
MotorPitch 90   -> 中間
MotorPitch 115  -> 抬頭極限

MotorYaw 0      -> 右轉極限
MotorYaw 90     -> 中間
MotorYaw 180    -> 左轉極限
```

所有 head motion 結束都會回 `Yaw 90 / Pitch 90`。一般單軸馬達 UART 只送一個角度參數：`MotorPitch 90`、`MotorYaw 90`；新的同步馬達指令會送兩個數值：`MotorYawPitch 120 90`。目前頭部動作表會優先使用 `MotorYawPitch`，讓 yaw/pitch 同時到位。

新版動作表把 yaw 當成表情的一部分，不再只有俯仰。左右跨側動作也不會插入 `Yaw 90` 中間停頓；例如從右看改成左看，會直接送下一個左側 `MotorYawPitch` 目標，讓伺服自己連續轉過去。

如果 TX 是正確的 `MotorPitch 90`，但 RX 變成：

```text
FRDM UART RX: Motor Pitch = 537190203
FRDM UART RX: Motor Yaw = 537190201
```

這不是角度，是 FRDM 端沒有成功把 `char *pValue` 轉成 `90`，或 `sscanf` 失敗後用了未初始化的 `value`。先停馬達測試，修 FRDM firmware：`MotorControlPitch(char *pValue)` / `MotorControlYaw(char *pValue)` 裡要把 `value` 初始化、檢查 `sscanf` 回傳值，再 clamp 到 Pitch `65..115`、Yaw `0..180`。Terminal 3 預設會送頭部馬達；如果啟用後仍看到超出範圍的 ACK，當次程序會停送後續馬達指令，避免頭被錯誤值推到極限。需要暫時關閉馬達時，把 `--enable-head-motor` 改成 `--disable-head-motor`。

### 0.10 直接測 FRDM 頭部馬達

如果頭沒有連續動作、角度不對、或沒有回正，先不要跑完整 Hey Jarvis 流程，直接測 UART 馬達。這個模式不會開麥克風、相機、TTS、Windows server，只會碰 FRDM UART。

如果 FRDM echo 有收到 `MotorYaw 90`，但 handler 印 `Motor Yaw = 0`，代表 UART 本身有送到，問題在 FRDM 端 handler 沒有從 `pValue` parse 到角度。先在 FRDM 裡印：

```c
PRINTF("Motor Yaw raw pValue = [%s]\r\n", pValue ? pValue : "(null)");
```

如果 raw pValue 是 `[MotorYaw 90]`，handler 不能只用 `sscanf(pValue, "%d", &value)`，要改成同時支援「純參數」和「整行命令」的 parser。

新的同步頭部指令要在 FRDM command table 加：

```c
{ "MotorYawPitch", "<yaw> <pitch>", "control yaw and pitch together", MotorControlYawPitch },
```

參考 patch：

```text
emotion_robot_controller/frdm_firmware/patches/motor_yaw_pitch_parser.c
```

先 dry-run 看全部 motion 會送什麼：

```bash
cd /home/asrlab-yian/MakeNTU
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate

python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py \
  --test-head-motion all \
  --uart-dry-run \
  --uart-debug \
  --motor-step-delay 0.01 \
  --motor-reset-delay 0.01 \
  --test-head-gap 0
```

你應該會看到類似：

```text
Motor settings: pitch=65..90..115 (down..center..up), yaw=0..90..180 (right..center..left), smooth_step=120deg
Testing head motion: nod
head motion keyframes: MotorYawPitch:yaw=72,pitch=100 -> MotorYawPitch:yaw=108,pitch=65 -> MotorYawPitch:yaw=72,pitch=108 -> MotorYawPitch:yaw=90,pitch=90
head motion expanded: MotorYawPitch:yaw=72,pitch=100 -> MotorYawPitch:yaw=108,pitch=65 -> MotorYawPitch:yaw=72,pitch=108 -> MotorYawPitch:yaw=90,pitch=90
head motion reset skipped: already centered
FRDM UART dry-run TX: MotorYawPitch 72 100
FRDM UART dry-run TX: MotorYawPitch 108 65
```

再測「情緒會不會自動對應頭部動作」：

```bash
python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py \
  --test-head-emotion all \
  --uart-dry-run \
  --uart-debug \
  --motor-step-delay 0.01 \
  --motor-reset-delay 0.01 \
  --test-head-gap 0
```

情緒 fallback 對應表：

```text
neutral   -> Speaking 0 -> none
concerned -> Speaking 1 -> concerned_tilt  # 右下 -> 左下 -> 右下小回收
angry     -> Speaking 2 -> firm_shake      # 右上極限 -> 左上極限 -> 右/左斜切
sad       -> Speaking 3 -> sad_droop       # 右下 -> 左低頭 -> 右低頭
happy     -> Speaking 4 -> happy_bounce    # 右上跳 -> 左上跳 -> 右下蓄力 -> 左上
curious   -> Speaking 5 -> curious_peek    # 右上探看 -> 直接左上探看
excited   -> Speaking 4 -> excited_bounce  # 大幅右上 -> 大幅左上 -> 斜向回彈
confused  -> Speaking 5 -> confused_tilt   # 右上疑惑 -> 左下疑惑 -> 右上/左下
sleepy    -> Speaking 3 -> sleepy_drop     # 右側下垂 -> 左側低頭 -> 右側沉下
surprised / amazed       -> excited
anxious / worried / 急   -> concerned
操你媽 / 生氣 / 不爽     -> concerned
tired / drowsy           -> sleepy
unsure / puzzled         -> confused
```

FRDM firmware 修好、手動確認 `MotorPitch 90` 會回 `Motor Pitch = 90` 之後，才實機測「講話期間循環動作」：

```bash
python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py \
  --uart-port auto \
  --uart-debug \
  --enable-head-motor \
  --test-speaking-head-motion happy_bounce \
  --test-speaking-seconds 6 \
  --motor-speaking-step-delay 0.72 \
  --motor-speaking-smooth-step-deg 120 \
  --motor-reset-repeats 1 \
  --motor-reset-delay 0.35
```

FRDM ACK 正常後，再實機測一次性 motion table：

```bash
python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py \
  --uart-port auto \
  --uart-debug \
  --enable-head-motor \
  --test-head-motion all \
  --motor-step-delay 0.55 \
  --motor-smooth-step-deg 120 \
  --motor-reset-repeats 1 \
  --motor-reset-delay 0.35
```

現場調參：

```text
看起來太碎、一直抖          -> --motor-smooth-step-deg 120
想要同側/俯仰稍微有過渡      -> --motor-smooth-step-deg 60
講話時動作太快              -> --motor-speaking-step-delay 0.9
講話時動作太慢              -> --motor-speaking-step-delay 0.55
一次性測試動作太快          -> --motor-step-delay 0.75
一次性測試太慢              -> --motor-step-delay 0.35
偶爾沒有回正                -> --motor-reset-repeats 2
回正指令太密或 FRDM 吃不穩   -> --motor-reset-delay 0.45
TTS 結束後太早切下一個畫面      -> --motor-join-timeout 8
```

若看到 `No UART serial device is visible`，代表 FRDM 沒接上或 `/dev/ttyACM0` 消失，先跑：

```bash
python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py --list-uarts
./frdm_uart_context_sender/recover_demo_usb.sh
```

如果啟動時 30 秒內都沒有 `/dev/serial/by-id/*`、`/dev/ttyACM*`、`/dev/ttyUSB*`，Wake Bridge 會先進入 FRDM UART auto-recovery：語音/相機/TTS 仍可跑，UART bus 會在背景安靜等待 FRDM debug USB。插回 FRDM 後會看到 `FRDM UART bus opened ...`，之後新的畫面/馬達/dashboard 指令會自動恢復送 UART，不需要重啟 Terminal 3。只有明確加 `--no-uart` 時，這一輪才會完全不送 FRDM UART。

如果現場 demo 一定要 FRDM，有缺 UART 就不要繼續啟動，指令尾端加：

```text
--require-uart
```

### 0.11 一輪互動正常 log

```text
Wake detected
Music wake pause: ok=True action=pause ...
Recording beep played.
FRDM UART TX: Thinking 0 0
Recording progress: phase=speech ...
POST audio+image ...
AI control: ...
FRDM UART TX: Speaking <emotion_code>
TTS started
TTS finished
Music tool: ok=True handled=True action=play query=...   # 只有點歌才會有
Weather tool: ok=True handled=True location=...          # 只有問天氣才會有
FRDM UART TX: Thinking 0 0 或 Normal/Music/Focus/Sleep
Listening for wake word 'hey_jarvis'
```

### 0.12 最常用恢復指令

Jetson 找不到 UACDemo/camera/FRDM：

```bash
cd /home/asrlab-yian/MakeNTU/frdm_uart_context_sender
./recover_demo_usb.sh
```

舊 bridge 卡住或不能 Ctrl+C：

```bash
pkill -9 -f wake_voice_chat_frdm_bridge.py
```

Windows server health：

```powershell
curl.exe http://127.0.0.1:8766/health
curl.exe http://127.0.0.1:11434/api/tags
tailscale ip -4
```

Jetson health：

```bash
curl http://127.0.0.1:8777/health
curl http://127.0.0.1:8788/health
python3 /home/asrlab-yian/MakeNTU/frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py --list-mics
```

## 1. 詳細必跑指令總覽

### 1.1 如果已經開過舊程式

Jetson 只需要保留 TTS server，舊 wake bridge 可以先停掉：

```bash
pkill -9 -f wake_voice_chat_frdm_bridge.py 2>/dev/null || true
```

如果 TTS server 已經在 Terminal 2 正常跑，可以不用重開。若你剛重插 USB speaker，建議照 Terminal 2 指令重開 TTS，讓它重新抓 `UACDemo` speaker。

### 1.2 Windows Server 有改過才同步

在 Windows PowerShell 執行。這段會先停掉占用 `8766` 的舊 server，再 scp Jetson 上的最新版 bundle 檔案到 Windows Desktop：

```powershell
$ErrorActionPreference = "Continue"

$port = 8766
$owners = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -Unique
foreach ($ownerPid in $owners) {
  if ($ownerPid -and $ownerPid -ne 0) {
    Stop-Process -Id $ownerPid -Force -ErrorAction SilentlyContinue
  }
}

New-Item -ItemType Directory -Force "$env:USERPROFILE\Desktop\windows_desktop_server_bundle" | Out-Null

scp asrlab-yian@100.110.90.72:/home/asrlab-yian/MakeNTU/emotion_robot_controller/voice_stt_remote/windows_desktop_server_bundle/desktop_fast_chat_server.py "$env:USERPROFILE\Desktop\windows_desktop_server_bundle\desktop_fast_chat_server.py"

cd "$env:USERPROFILE\Desktop\windows_desktop_server_bundle"
.\.venv\Scripts\Activate.ps1
python desktop_fast_chat_server.py --self-test
```

同步後一定要重啟 Windows server，health 要看到 `debug_version: 13`。

### 1.3 軟體 Self-Test

這兩個檢查不需要麥克風、相機、FRDM 或 Ollama。剛改過程式時先跑。

Jetson：

```bash
cd /home/asrlab-yian/MakeNTU
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate
python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py --self-test
python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py --test-head-emotion all --uart-dry-run --uart-debug --motor-step-delay 0.01 --motor-reset-delay 0.01 --test-head-gap 0
```

Windows PowerShell：

```powershell
cd "$env:USERPROFILE\Desktop\windows_desktop_server_bundle"
.\.venv\Scripts\Activate.ps1
python desktop_fast_chat_server.py --self-test
```

成功會看到：

```text
wake bridge self-test OK
desktop_fast_chat_server self-test OK
```

### 1.4 Terminal 1: Windows ASR/Ollama Server

在 Windows PowerShell 開著，不要關：

```powershell
try {
  Invoke-RestMethod http://127.0.0.1:11434/api/tags | Out-Null
} catch {
  Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Minimized
  Start-Sleep -Seconds 3
}

ollama pull qwen35-fast:latest

cd "$env:USERPROFILE\Desktop\windows_desktop_server_bundle"
.\.venv\Scripts\Activate.ps1

python desktop_fast_chat_server.py `
  --host 0.0.0.0 `
  --port 8766 `
  --ollama-model qwen35-fast:latest `
  --vision-model qwen35-fast:latest `
  --no-think
```

### 1.5 Terminal 2: Jetson Piper TTS

在 Jetson 開著，不要關：

```bash
cd /home/asrlab-yian/MakeNTU/jetson_piper_tts
source .venv/bin/activate

pkill -f 'jetson_piper_tts.server' 2>/dev/null || true

python -m jetson_piper_tts.server \
  --host 0.0.0.0 \
  --port 8777 \
  --no-warmup
```

TTS `.env` 必須用可重插的設定：

```text
AUDIO_DEVICE=auto:UACDemo
DEFAULT_VOLUME_GAIN=4.8
ENABLE_STREAM_PLAYBACK=true
```

### 1.6 Terminal 4: Local Music + Weather Tool Optional

新版 Wake Bridge 偵測到點歌或天氣問題時會自動嘗試啟動 `music_web_player.py`，所以 Terminal 4 不是必開；但正式 demo 建議開著，方便看 `/music`、`/weather` log。

要真的直接播放聲音，用 `mpv` backend；天氣預設所在地用 `--weather-default-location` 設定：

```bash
cd /home/asrlab-yian/MakeNTU/music_web_player
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate

python3 music_web_player.py \
  --server \
  --host 127.0.0.1 \
  --port 8788 \
  --backend mpv \
  --mpv-audio-device auto \
  --mpv-volume 125 \
  --mpv-volume-max 200 \
  --mpv-ready-timeout 1.5 \
  --weather-default-location Taipei \
  --weather-timeout 4.5
```

如果 YouTube 要登入，先在 Jetson 瀏覽器登入 Premium，然後多加 `--mpv-ytdl-cookies-from-browser firefox` 或 `--mpv-ytdl-cookies-from-browser chrome`。如果 Jetson 沒有瀏覽器，就從已登入的電腦匯出 cookies 檔，再多加 `--mpv-ytdl-cookies "$HOME/.config/makentu/youtube_cookies.txt"`。不要把 Google 帳密寫進程式或指令。
如果你不開 Terminal 4、交給 Wake Bridge 自動啟動 sidecar，改用環境變數：

```bash
export MUSIC_MPV_YTDL_COOKIES_FROM_BROWSER=firefox
# 或
export MUSIC_MPV_YTDL_COOKIES="$HOME/.config/makentu/youtube_cookies.txt"
```

只想打開搜尋頁，用 `--backend browser`。注意 browser 模式不保證自動播放。
只想改所在地，例如 demo 在新竹：

```bash
python3 music_web_player.py --server --host 127.0.0.1 --port 8788 --backend mpv --mpv-audio-device auto --mpv-volume 125 --mpv-volume-max 200 --mpv-ready-timeout 1.5 --weather-default-location Hsinchu
```

### 1.7 Terminal 5: Smart Home Dashboard Optional

手機/網站 dashboard 可以和語音 demo 同時開。語音 demo 時讓 Terminal 3 擁有 FRDM UART，所以 Terminal 5 使用 `--no-frdm-uart`：

```bash
cd /home/asrlab-yian/MakeNTU
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate

python3 smart_home_dashboard/server.py \
  --host 0.0.0.0 \
  --port 8789 \
  --no-frdm-uart
```

手機開：

```text
http://JETSON_LAN_IP:8789/dashboard
```

如果這場 demo 是 phone-first smart home HMI，不跑 Wake Bridge，才可以讓 Dashboard 擁有 FRDM UART：

```bash
python3 smart_home_dashboard/server.py --host 0.0.0.0 --port 8789 --frdm-uart-port auto
```

### 1.8 Terminal 3: Jetson Wake Bridge

正式完整模式：

請整段複製貼上，尤其最後三行不要手打錯。

```bash
cd /home/asrlab-yian/MakeNTU
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate

python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --mic-keyword UACDemo \
  --beep-keyword UACDemo \
  --beep-player auto \
  --noisy-room \
  --tts-volume-gain 4.8 \
  --beep-volume 0.35 \
  --uart-port auto \
  --uart-baudrate 115200 \
  --frdm-uart-tx-timeout 0.45 \
  --frdm-uart-failure-threshold 2 \
  --frdm-uart-circuit-breaker-sec 4.0 \
  --enable-head-motor \
  --boot-normal-delay 2.0 \
  --device-ready-timeout 30 \
  --wake-threshold 0.75 \
  --wake-volume-min 500 \
  --volume-min 1100 \
  --speech-start-margin 750 \
  --silence-duration 1.2 \
  --silence-margin 900 \
  --max-speech-seconds 5 \
  --max-recording-seconds 7 \
  --audio-read-timeout 0.75 \
  --recording-progress-interval 1.0 \
  --conversation-mode \
  --turn-listen-timeout 8 \
  --session-idle-timeout 30 \
  --max-session-turns 20 \
  --camera-id auto \
  --camera-width 320 \
  --camera-height 240 \
  --camera-jpeg-quality 70 \
  --camera-latest-timeout 1.0 \
  --camera-frame-max-age 2.0 \
  --focus-script /home/asrlab-yian/MakeNTU/frdm_uart_context_sender/focus_work_mode.py \
  --focus-server-url http://100.108.141.26:8766/focus-check \
  --focus-interval-sec 60 \
  --focus-first-sample-delay-sec -1 \
  --focus-duration-min 0 \
  --focus-log-root /tmp/focus_voice_test \
  --focus-alert-threshold 1 \
  --focus-alert-cooldown-sec 90 \
  --fan-device-id desk_fan \
  --fan-speed-max 100 \
  --esp32-ble \
  --esp32-ble-adapter hci0 \
  --esp32-ble-scan-duplicates \
  --esp32-ble-min-fan-pwm 96 \
  --esp32-dashboard-host 127.0.0.1 \
  --esp32-dashboard-port 8791 \
  --fan-duplicate-suppress-sec 2.0 \
  --todo-list-path /home/asrlab-yian/MakeNTU/frdm_uart_context_sender/logs/todo_list.json \
  --wake-status-path /home/asrlab-yian/MakeNTU/frdm_uart_context_sender/logs/wake_status.json \
  --focus-notify-mode discord \
  --focus-discord-webhook-url "$DISCORD_WEBHOOK_URL" \
  --music-backend mpv \
  --music-mpv-audio-device auto \
  --music-mpv-volume 125 \
  --music-mpv-volume-max 200 \
  --music-mpv-ready-timeout 1.5 \
  --music-timeout 5 \
  --music-wake-pause-timeout 0.25 \
  --music-wake-beep-settle 0.05 \
  --post-music-standby-cooldown 0.8 \
  --music-debug \
  --weather-default-location Taipei \
  --weather-timeout 6 \
  --weather-api-timeout 4.5 \
  --weather-debug \
  --esp32-temperature-mode push \
  --esp32-temperature-host 0.0.0.0 \
  --esp32-temperature-port 8790 \
  --esp32-temperature-path /temperature \
  --temp-room-uart-interval-sec 10 \
  --temp-room-uart-max-age-sec 30 \
  --motor-step-delay 0.55 \
  --motor-smooth-step-deg 120 \
  --motor-speaking-step-delay 0.72 \
  --motor-speaking-smooth-step-deg 120 \
  --motor-reset-repeats 1 \
  --motor-reset-delay 0.35 \
  --motor-stop-timeout 6 \
  --motor-join-timeout 6 \
  --device-preflight-verbose \
  --tts-poll-interval 0.75 \
  --tts-start-poll-interval 0.12 \
  --tts-speaking-start-timeout 1.2 \
  --tts-speaking-require-audio \
  --tts-debug \
  --uart-debug
```

正式指令請不要加固定 index：

```text
不要加 --device 25
不要加 --beep-device 24
不要加 --camera-id 0
不要加 --uart-port /dev/ttyACM0
```

重插 USB 後這些數字會變。正式 demo 用 `UACDemo` keyword 和 `auto`。
bridge 每次啟動會先做 device preflight：停掉舊 wake bridge、camera 測試程式、`aplay` / `mpv` 等占用 demo 裝置的 process；如果 UACDemo/camera/FRDM 整個不見，會重置 Jetson USB host，然後重新掃 mic/camera/speaker/FRDM。Piper TTS server 會被保留，因為它本來就負責 speaker 播放。
USB reset 後有時 ALSA 已經看到 UACDemo，但 sounddevice 還沒刷新；bridge 會再等最多 `--device-ready-timeout 12` 秒，直到 UACDemo input/output 真的可用。
相機預設會開 continuous warm reader，程式啟動後背景持續更新最新 JPEG；這顆 Global Shutter Camera 第一次吐 frame 可能要 5 到 7 秒，所以 bridge 剛啟動後請等看到 `Camera warm reader opened camera 0.`，再開始測視覺問題。
音樂預設接 Music Web Player，但不會改掉主流程：每輪仍然先聽 `Hey Jarvis`、錄音、送 AI、TTS、UART。只有 transcript 被判斷成點歌、暫停、停止或換歌時，才會呼叫 `http://127.0.0.1:8788/music`。
偵測到 `Hey Jarvis` 的瞬間，bridge 會先用很短 timeout 對 Music Player 送 `pause`，讓音樂不要被錄進麥克風；如果 Music Player 沒開，這一步會安靜跳過，不會自動啟動 sidecar。
點歌或繼續播放時會在 TTS 確認句說完後呼叫 Music Player。如果 Terminal 4 沒開，bridge 會在點歌時自動嘗試啟動 sidecar。正式要真的出聲並支援 pause/resume，用 `--music-backend mpv`；`browser` 只開搜尋頁，不保證播放。
天氣也走同一個 Terminal 4 local tool server，但 endpoint 是 `http://127.0.0.1:8788/weather`。Wake Bridge 會先用 rule-based intent 判斷 transcript 是否在問天氣；只有問天氣時才呼叫 `/weather`，一般聊天、FRDM 控制、vision 問題不會查天氣。天氣資料由 Jetson 端直接連 Open-Meteo 查詢，不經 Windows Ollama，不需要 API key。

本地溫度走 ESP32-S3，不走 Terminal 4。現在主線是 BLE notify：同一塊 ESP32-S3 會回傳 `TEMP:...,FAN:...,SPEED:...,LED:...`，Wake Bridge 會把最新 `TEMP` 當成 Weather UART 的 local temperature。Terminal 3 啟用 `--esp32-ble` 後應看到：

```text
ESP32-S3 BLE fan/LED/temp: enabled
BLE: connected. Subscribing status notify...
ESP32 status: TEMP=24.12 C, FAN=ON, SPEED=96, LED=OFF
```

舊 HTTP push 溫度路徑仍可用於只做 DS18B20 的 ESP32。Terminal 3 加上 `--esp32-temperature-mode push` 後會開：

```text
http://JETSON_LAN_IP:8790/temperature
```

HTTP 版 ESP32 每幾秒 POST 一次：

```json
{"ok":true,"temperature_c":25.4}
```

之後 Weather UART 會把 Open-Meteo 資料和本地溫度合併：

```text
Weather daily,23,29,40,61,254
Weather current,20,20,0,3,254
```

其中第 6 欄 `254` 是 `25.4 C`。如果 ESP32 還沒送或資料超過 `--esp32-temperature-max-age-sec`，就退回舊的 5 欄格式。
天氣回答會覆蓋桌機 AI 的一般回答，避免模型亂猜天氣；FRDM 會用 `curious/curious_peek` 的控制資料，最後仍回 Normal 或 Sleep。

錄音使用 adaptive recording gate。程式會在 wake 前估計環境底噪，wake 後自動算：

```text
speech_start_threshold = max(volume_min, noise_floor + speech_start_margin)
silence_threshold      = max(volume_min, noise_floor + silence_margin, peak_volume * silence_peak_ratio)
```

所以吵的環境不會只因背景音量高於 `--volume-min 1100` 就一路錄到最長秒數。
正式預設 `max_speech_seconds=5`，避免真的開始說話後錄太久；`max_recording_seconds=7` 是從 wake 被接受開始算的硬上限，就算現場太吵、speech start 判斷怪掉，也會退出回 standby；`audio_read_timeout=0.75` 是 USB mic watchdog，若 mic stream 停止吐 audio chunk，Python 不會卡在 blocking read；`recording_progress_interval=1.0` 會每秒印一行錄音狀態；`tts_poll_interval=0.75` 會降低 TTS `/queue` 查詢頻率，避免 Terminal 2 被 log 洗版。

錄音 log 快速判讀：

```text
phase=waiting_speech -> wake 成功，但你講話音量還沒高過 start threshold
phase=speech         -> 已開始錄音，等 silence 或 max_speech 結束
Max recording...     -> 硬上限保護觸發，這是正常防卡死
audio chunk warning  -> USB mic 暫時沒吐資料，watchdog 會讓當輪退出
Standby audio...     -> 還在等 Hey Jarvis；recent_peak 是最近 1 秒音量峰值
```

## 2. 啟動成功要看到

Windows server health：

```text
debug_version: 13
chat_ready   : True
asr_loaded   : True
ollama_model : qwen35-fast:latest
vision       : enabled=True model=qwen35-fast:latest
```

Jetson TTS health：

```text
ready : True
audio : aplay device=plughw:CARD=UACDemo...
```

Jetson bridge：

```text
Device preflight: releasing stale demo device owners.
Device preflight: target devices look free.
Client version: wake_voice_chat_frdm_bridge_vision_conversation_motor_natural_v6
Selected input device ... by keyword 'UACDemo'.
Selected beep output device ... by keyword 'UACDemo'.
Camera ready in continuous warm-reader mode.
Camera warm reader opened camera 0.
Adaptive recording gate: on, noise_p75, speech_margin=750, speech_ratio=1.45, silence_margin=900, silence_noise_ratio=1.3, peak_ratio=0.35
Audio read watchdog: callback queue, timeout=0.75s, progress_interval=1s
Music tool: http://127.0.0.1:8788/music, backend=mpv->mpv, autostart=True, pause_on_wake=True, beep_settle=0.18s, post_music_cooldown=0.8s
Weather tool: http://127.0.0.1:8788/weather, default_location=Taipei, source=Open-Meteo
ESP32-S3 BLE fan/LED/temp: enabled, name=ESP32S3_FAN_LED_TEMP, address=..., voice_control=True, frdm_relay=True, min_pwm=96, passive_reminder=True (>25 C)
BLE: connected. Subscribing status notify...
ESP32 status: TEMP=24.12 C, FAN=OFF, SPEED=180, LED=OFF
Head motor motion: enabled=True, smooth_step=120deg, step_delay=0.55s, speaking_step_delay=0.72s, speaking_smooth_step=120deg, reset_repeats=1, reset_delay=0.35s, read_ms=35, stop_timeout=6s, join_timeout=6s
TTS queue polling: every 0.75s, start_poll=0.12s, speaking_start_timeout=1.2s, speaking_requires_audio=True, playback_timeout=45s
USB auto-discovery: mic=keyword 'UACDemo'; beep=keyword 'UACDemo'; camera=auto; FRDM UART=auto
Listening for wake word 'hey_jarvis'
```

一次互動的正常時序：

```text
Wake detected
Music wake pause: ok=True action=pause stopped=True post_ms=...
Recording thresholds: noise_floor=..., speech_start_threshold=..., silence_base_threshold=..., adaptive=on
Recording beep played.
FRDM UART TX: Thinking 0 0
Recording. Speak now
Recording progress: phase=...
POST audio+image ...
AI control:
  persistent_state : unchanged / normal / sleep
  screen_mode      : unchanged / normal / sleep / music / focus / thinking
  emotion          : neutral / concerned / angry / sad / happy / curious / excited / confused / sleepy
  head_motion      : none / nod / double_nod / look_around / shake / gentle_nod / sleepy_drop / happy_bounce / excited_bounce / curious_peek / concerned_tilt / sad_droop / confused_tilt / firm_shake
TTS started
TTS playback observed: TTS audio player reports output
FRDM UART TX: Speaking 0..5     # 0 neutral, 1 concerned, 2 angry, 3 sad, 4 happy, 5 confused
head motion started
TTS finished
Music tool: ok=True handled=True action=play query=...
FRDM UART TX: Thinking 0 0      # 連續對話等下一句；或 Normal/Music/Focus/Sleep
```

音樂互動的預期：

```text
Hey Jarvis，我想要聽告白氣球
-> 先 TTS 回覆「好呀，我來幫你放...」
-> TTS 結束後 Music tool action=play query=告白氣球
-> bridge 回 standby，繼續聽下一次 Hey Jarvis

Hey Jarvis，暫停音樂
-> wake 當下先 pause 一次
-> transcript 判斷 action=pause，保留目前播放位置
-> 先處理 mpv pause，再用 Piper 說「好，我先暫停音樂。」
-> FRDM UART TX: Normal 0 0，回 wake-only standby

Hey Jarvis，繼續播放音樂
-> wake 當下仍會先 pause，避免錄音收到音樂
-> transcript 判斷 action=resume
-> TTS 說完確認句後，mpv 從暫停位置繼續播放
-> FRDM UART TX: Music 0 0，回 wake-only standby

Hey Jarvis，換成七里香
-> 新的 play 會讓 mpv 停掉上一首，再播新的搜尋結果

Hey Jarvis，停止音樂
-> 先處理 mpv stop，再用 Piper 說「好，我把音樂關掉了。」
-> 如果本來沒有音樂，Piper 說「音樂現在沒有在播放。」
-> FRDM UART TX: Normal 0 0，回 wake-only standby
```

音樂 action 對照：

```text
play   : 搜尋並播放新歌；會取代上一首
pause  : 暫停目前 mpv；保留播放位置
resume : 從 pause 的位置繼續
stop   : 結束 mpv process；不能從同一秒 resume
```

TTS 只會唸 `reply`。`control` 是內部 JSON，不會被唸出來。

## 3. 立刻測

先測 ESP32 BLE，不用叫醒詞也能從 standalone CLI 確認硬體：

```bash
cd /home/asrlab-yian/MakeNTU
source emotion_robot_controller/.venv/bin/activate

python3 frdm_uart_context_sender/esp32s3_ble_fan_led_controller.py \
  --address 78:E3:6D:18:94:6A \
  --adapter hci0 \
  --scan-duplicates \
  --no-tts-reminder
```

在 `ble>` 輸入：

```text
TEMP?            -> 應看到 TEMP=...
LED_ON           -> MAX7219 應亮動畫
LED_OFF          -> MAX7219 應熄
FAN_SPEED:255    -> 風扇最大速，硬體必須會轉
PERCENT:50       -> 轉成 FAN_SPEED:128
FAN_OFF          -> 風扇停止
```

再測 FRDM 手動模式：

```text
FRDM slider 16%  -> Terminal 3 應出現 Fanspeed 16，BLE TX: FAN_SPEED:96
FRDM slider 50%  -> BLE TX: FAN_SPEED:128
FRDM off         -> BLE TX: FAN_OFF
```

如果 log 有 `ESP32 status: FAN=ON,SPEED=96` 但風扇不轉，先在 CLI 送 `FAN_SPEED:255` 分辨是軟體低速問題還是 L9110S/馬達供電問題。

先測 FRDM 狀態機，這組不用看回答內容，只看 Terminal 3 UART log：

```text
Hey Jarvis，講個笑話
-> Thinking 0 0
-> Speaking 0 或 Speaking 4
-> TTS 結束後回 Thinking 0 0 等 follow-up

掰掰
-> Normal 0 0
-> 回到 wake-only standby

Hey Jarvis，去睡覺吧
-> Thinking 0 0
-> Speaking 3
-> Sleep 0 0
-> 回到 wake-only standby

Hey Jarvis，回來
-> Thinking 0 0
-> Speaking 4 或 Speaking 0
-> Normal 0 0

Hey Jarvis，我想聽告白氣球
-> Speaking 4
-> Music 0 0

Hey Jarvis，開始專心工作
-> Speaking 4 或 Speaking 5
-> Focus 0 0

Hey Jarvis，太酷了我超期待
-> Speaking 4

Hey Jarvis，這個結果怪怪的我看不懂
-> Speaking 5

Hey Jarvis，我好睏想睡
-> Speaking 3

Hey Jarvis，我操你媽的
-> Speaking 1

Hey Jarvis，為什麼沒聲音，聲音超小
-> 不應出現 Music routing action=play
```

純聊天，不應走 vision：

```text
Hey Jarvis，講個笑話。
Hey Jarvis，今天幾號？
Hey Jarvis，幫我開電風扇。
```

預期：

```text
vision_intent=False
used_vision=False
TTS 有聲音
FRDM UART 有 TX/RX
```

看圖，應走 vision：

```text
Hey Jarvis，我現在是什麼表情？
Hey Jarvis，我手上拿什麼？
Hey Jarvis，桌上有什麼？
Hey Jarvis，螢幕上寫什麼？
Hey Jarvis，這是什麼顏色？
Hey Jarvis，check my posture.
```

預期 Jetson log：

```text
Vision routing:
  vision_intent    : True
  vision_reason    : keyword:... or pattern:...
  used_vision       : True
  image_received    : True
  image_size_bytes  : ...
  vision_model      : qwen35-fast:latest
```

預期 Windows server log：

```text
voice-chat xxxx: transcript='我現在是什麼表情'
voice-chat xxxx: normalized_transcript='我現在是什麼表情'
voice-chat xxxx: vision_intent=True reason=pattern:zh_self_expression ...
voice-chat xxxx: calling vision model=qwen35-fast:latest image_bytes=...
```

睡覺：

```text
Hey Jarvis，去睡覺吧。
```

預期：

```text
emotion=sleepy
head_motion=sleepy_drop
最後 FRDM UART TX: Sleep 0 0
conversation mode 結束，回到 wake-only standby
```

起床：

```text
Hey Jarvis，起床，回來。
```

預期：

```text
persistent_state=normal
emotion=happy 或 neutral
最後 FRDM UART TX: Normal 0 0
```

音樂：

```text
Hey Jarvis，我想要聽告白氣球。
Hey Jarvis，暫停音樂。
Hey Jarvis，繼續播放音樂。
Hey Jarvis，換成七里香。
Hey Jarvis，停止音樂。
```

預期：

```text
點歌/換歌      -> TTS 結束後 Music tool action=play query=...
               -> ipc_ready=True 代表後續 pause/resume 可用
暫停音樂       -> Music tool action=pause paused=True
繼續播放音樂   -> TTS 結束後 Music tool action=resume resumed=True
停止音樂       -> Music tool action=stop stopped=True
Hey Jarvis wake -> 先 Music wake pause，等 0.18s，再 beep 並開始 recording；講完時再 beep + 抓圖
```

手動測 Music Player 本身：

```bash
curl http://127.0.0.1:8788/health

curl -X POST http://127.0.0.1:8788/music \
  -H "Content-Type: application/json" \
  -d '{"action":"play","query":"告白氣球","backend":"mpv"}'

curl -X POST http://127.0.0.1:8788/music \
  -H "Content-Type: application/json" \
  -d '{"action":"pause"}'

curl -X POST http://127.0.0.1:8788/music \
  -H "Content-Type: application/json" \
  -d '{"action":"resume"}'
```

天氣：

```text
Hey Jarvis，所在地天氣如何。
Hey Jarvis，明天天氣如何。
Hey Jarvis，明天下午三點台北天氣如何。
Hey Jarvis，今天會下雨嗎？
Hey Jarvis，明天要帶傘嗎？
Hey Jarvis，weather in Tokyo tomorrow.
```

預期：

```text
Weather routing: intent=True location=...
Weather tool: ok=True handled=True source=open-meteo
Weather UART sent: Weather current,20,20,0,3,254 (local=25.4 C)  # 問現在/所在地天氣，有 ESP32 溫度時
Weather UART sent: Weather daily,23,29,40,61,254 (local=25.4 C)   # 問明天/今天整日預報，有 ESP32 溫度時
Reply: <城市><時間>大約 ... °C，... 降雨機率 ...
AI control: emotion=curious, head_motion=curious_peek
FRDM UART TX: Speaking 5
最後 FRDM UART TX: Thinking 0 0 或 Normal/Music/Focus/Sleep
```

手動測 Weather Tool 本身：

```bash
curl -X POST http://127.0.0.1:8788/weather \
  -H "Content-Type: application/json" \
  -d '{"text":"所在地天氣如何","default_location":"Taipei"}'

curl -X POST http://127.0.0.1:8788/weather \
  -H "Content-Type: application/json" \
  -d '{"text":"明天下午三點台北天氣如何","default_location":"Taipei"}'

curl -X POST http://127.0.0.1:8788/weather \
  -H "Content-Type: application/json" \
  -d '{"text":"weather in Tokyo tomorrow","default_location":"Taipei"}'
```

## 4. 快速 Health Check

Windows PowerShell：

```powershell
curl.exe http://127.0.0.1:8766/health
curl.exe http://127.0.0.1:11434/api/tags
tailscale ip -4
```

Jetson：

```bash
curl http://127.0.0.1:8777/health
curl http://127.0.0.1:8788/health
curl -X POST http://127.0.0.1:8788/weather -H "Content-Type: application/json" -d '{"text":"所在地天氣如何","default_location":"Taipei"}'

cd /home/asrlab-yian/MakeNTU
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate
python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py --list-mics
python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py --list-uarts
lsusb
ls -l /dev/video* /dev/ttyACM* 2>/dev/null
```

Terminal 3 已啟動且使用 ESP32 push mode 時，可以手動打一筆本地溫度進 receiver：

```bash
curl -X POST http://127.0.0.1:8790/temperature \
  -H "Content-Type: application/json" \
  -d '{"ok":true,"temperature_c":25.4}'
```

`--list-mics` 要看到 UACDemo input，例如：

```text
UACDemoV1.0: USB Audio (hw:3,0)
```

TTS health 要看到：

```text
audio.configured_device: auto:UACDemo
audio.device           : plughw:CARD=UACDemo...
```

## 5. USB 重插與 Recovery

正式 bridge 會自動重新找：

```text
mic       : --mic-keyword UACDemo
beep      : --beep-keyword UACDemo
TTS audio : AUDIO_DEVICE=auto:UACDemo
camera    : --camera-id auto
FRDM      : --uart-port auto
```

如果 `lsusb` 只剩 root hub，或看不到 UACDemo / camera / FRDM，直接跑：

```bash
cd /home/asrlab-yian/MakeNTU/frdm_uart_context_sender
./recover_demo_usb.sh
```

成功時會看到：

```text
Jieli Technology UACDemoV1.0
Global Shutter Camera
NXP Semiconductors MCU-LINK FRDM-MCXN947
/dev/video0 /dev/video1
/dev/ttyACM0
```

如果 bridge 卡住或 Ctrl+C 停不了：

```bash
pkill -9 -f wake_voice_chat_frdm_bridge.py
```

USB recovery 之後，建議重啟 Terminal 2 TTS，讓 TTS 重新抓 speaker：

```bash
cd /home/asrlab-yian/MakeNTU/jetson_piper_tts
source .venv/bin/activate
pkill -f 'jetson_piper_tts.server' 2>/dev/null || true
python -m jetson_piper_tts.server --host 0.0.0.0 --port 8777 --no-warmup
```

## 6. 只測純語音

如果 camera 不穩，先用這個確認 wake / ASR / TTS / UART：

```bash
cd /home/asrlab-yian/MakeNTU
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate

python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --mic-keyword UACDemo \
  --uart-port auto \
  --no-camera \
  --no-beep \
  --wake-threshold 0.75 \
  --wake-volume-min 500 \
  --volume-min 1100 \
  --speech-start-margin 750 \
  --silence-duration 1.2 \
  --silence-margin 900 \
  --max-speech-seconds 5 \
  --max-recording-seconds 7 \
  --audio-read-timeout 0.75 \
  --recording-progress-interval 1.0 \
  --tts-poll-interval 0.75 \
  --tts-debug \
  --uart-debug
```

## 7. Force Vision / No Vision

強制每次有圖就看圖，用來測 camera -> server -> qwen35-fast vision：

```bash
python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --mic-keyword UACDemo \
  --uart-port auto \
  --force-vision \
  --wake-threshold 0.75 \
  --wake-volume-min 500 \
  --volume-min 1100 \
  --speech-start-margin 750 \
  --silence-duration 1.2 \
  --silence-margin 900 \
  --max-speech-seconds 5 \
  --max-recording-seconds 7 \
  --audio-read-timeout 0.75 \
  --recording-progress-interval 1.0 \
  --camera-id auto \
  --camera-width 320 \
  --camera-height 240 \
  --camera-jpeg-quality 70 \
  --tts-poll-interval 0.75 \
  --tts-debug \
  --uart-debug
```

完全關閉 camera 和 vision：

```bash
python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --mic-keyword UACDemo \
  --uart-port auto \
  --no-vision \
  --wake-threshold 0.75 \
  --wake-volume-min 500 \
  --volume-min 1100 \
  --speech-start-margin 750 \
  --silence-duration 1.2 \
  --silence-margin 900 \
  --max-speech-seconds 5 \
  --max-recording-seconds 7 \
  --audio-read-timeout 0.75 \
  --recording-progress-interval 1.0 \
  --tts-poll-interval 0.75 \
  --tts-debug \
  --uart-debug
```

優先順序：

```text
--no-vision > --force-vision > auto detect_vision_intent
```

## 8. 常見問題速查

### No microphone matching UACDemo

代表 Jetson 現在沒有看到 UACDemo input。先看：

```bash
lsusb
python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py --list-mics
```

如果 `lsusb` 看不到 UACDemo，跑：

```bash
cd /home/asrlab-yian/MakeNTU/frdm_uart_context_sender
./recover_demo_usb.sh
```

不要用 `--allow-default-mic` 做正式 demo，會錄到 Jetson APE 或錯的 default。

### 想先看 preflight 會殺誰

正式 bridge 預設會清掉舊 wake bridge、camera 測試、`aplay` / `mpv` 等占用 demo 裝置的 process。如果想先確認清單，不真的 kill：

```bash
python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py \
  --mic-keyword UACDemo \
  --uart-port auto \
  --device-preflight-only \
  --device-preflight-dry-run \
  --device-preflight-verbose
```

如果你正在用 `mpv` 播音樂，不想 bridge 啟動時停掉它，加：

```text
--device-preflight-keep-music
```

如果你不想讓 bridge 自動 reset USB host，加：

```text
--no-usb-reset-if-missing
```

### Recording 後看起來卡住

先看 `phase`，不要只看 `Recording. Speak now` 那行。

如果看到：

```text
Recording progress: phase=waiting_speech, elapsed=..., volume=..., start_threshold=..., wake_timeout_in=...
```

代表 wake 已成功，但聲音還沒高過「環境底噪 + margin」。如果你有講話但一直停在 `phase=waiting_speech`，可以先靠近 mic，或降低：

```text
--speech-start-margin 250
```

如果是相反問題：錄音一直停不下來，把 silence margin 再加大：

```text
--silence-margin 1000
```

或縮短保險上限：

```text
--max-speech-seconds 4
```

如果它連 `Speech started` 都沒進入、或你只想保證整輪 wake 後一定退出，縮短硬上限：

```text
--max-recording-seconds 7
```

如果畫面完全停在 `Recording. Speak now...`，代表 USB mic stream 可能停止吐 chunk。新版會用 callback watchdog 退出；可縮短：

```text
--audio-read-timeout 0.75
```

現場很吵時優先直接加：

```text
--noisy-room
```

如果還需要手動細調，再用這組覆蓋值：

```text
--speech-start-margin 750 --silence-margin 900 --max-speech-seconds 5 --max-recording-seconds 7 --audio-read-timeout 0.75 --recording-progress-interval 1.0
```

如果背景約 10000、講話約 19000，先用 `--noisy-room` 不要手動降門檻。若變成「叫醒後你說話也不開始錄」，優先把 `--speech-start-ratio` 從 1.45 降到 1.35；還不行才把 `--speech-start-margin` 從 750 降回 550、300 或 250。

錄音調參表：

```text
漏收你的聲音              -> --speech-start-margin 250
背景音太容易觸發 speech   -> --noisy-room，或 --speech-start-ratio 1.55
speech 後一直停不下來     -> --silence-noise-ratio 1.4
想更快送出                -> --max-speech-seconds 4
整輪 wake 後不想等太久    -> --max-recording-seconds 6
完全沒有 Recording progress -> --audio-read-timeout 0.75，並重開 bridge
```

### Wake 被 ignore

如果看到：

```text
Low-volume wake-like score ignored ...
```

正式建議：

```text
--wake-volume-ratio 1.25
```

仍漏叫可加 `--wake-volume-window-seconds 1.5`；如果還是不行再降 `--wake-volume-min`，但誤觸發會變多。

### Camera timeout

不會讓主流程 crash，會 fallback 純語音。先查：

```bash
lsusb
ls -l /dev/video*
./recover_demo_usb.sh
```

正式低延遲設定：

```text
--camera-width 320 --camera-height 240 --camera-jpeg-quality 70
```

如果看到 `image_received=False`，先確認 Jetson 真的有看到相機：

```bash
lsusb | grep -E 'Global Shutter|Camera'
ls -l /dev/video*
```

如果沒有 `/dev/video0`，跑 `./recover_demo_usb.sh`。如果有 `/dev/video0` 但剛啟動就問視覺問題，等 5 到 7 秒讓 warm reader 先抓到第一張 frame。

### Windows health timeout

Jetson：

```bash
curl -v --connect-timeout 5 http://100.108.141.26:8766/health
```

Windows：

```powershell
curl.exe http://127.0.0.1:8766/health
tailscale ip -4
```

如果 Windows IP 變了，改 Jetson `--server-url`。

### Ollama WinError 10061 / connection refused

如果 Jetson response 裡看到：

```text
Ollama request failed: <urlopen error [WinError 10061] ...>
```

代表 Windows server 有開，但 Windows 本機 Ollama 沒有在 `127.0.0.1:11434` 接 request。到 Windows PowerShell 跑：

```powershell
curl.exe http://127.0.0.1:11434/api/tags
Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Minimized
ollama pull qwen35-fast:latest
```

再重啟 Terminal 1。

### debug_version 不是 13

重新 scp 同步，關掉舊 server，重開 Terminal 1。

### Weather tool 沒反應或回答連不到

先確認 Terminal 4 是新版，`/health` 要看到 `weather_available=true`：

```bash
curl http://127.0.0.1:8788/health
```

如果沒有 `weather_available`，代表 Terminal 4 還是舊 process，重開：

```bash
pkill -f 'music_web_player.py'

cd /home/asrlab-yian/MakeNTU/music_web_player
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate
python3 music_web_player.py --server --host 127.0.0.1 --port 8788 --backend mpv --mpv-audio-device auto --mpv-volume 125 --mpv-volume-max 200 --mpv-ready-timeout 1.5 --weather-default-location Taipei --weather-timeout 4.5
```

手動測：

```bash
curl -X POST http://127.0.0.1:8788/weather \
  -H "Content-Type: application/json" \
  -d '{"text":"明天天氣如何","default_location":"Taipei"}'
```

如果回 `Open-Meteo` 相關錯誤，通常是 Jetson 當下沒有外網。音樂也需要外網，所以一起檢查：

```bash
ping -c 2 open-meteo.com
ping -c 2 youtube.com
```

如果 Wake Bridge log 沒有 `Weather routing`，代表 transcript 沒被判斷成天氣問題。請先用明確句測：

```text
Hey Jarvis，明天下午三點所在地天氣如何。
Hey Jarvis，今天會下雨嗎？
```

### ESP32 BLE / 本地溫度沒有合併到 Weather UART

現在優先看 BLE notify。Terminal 3 啟動時應看到：

```text
ESP32-S3 BLE fan/LED/temp: enabled
BLE: connected. Subscribing status notify...
ESP32 status: TEMP=24.12 C, FAN=..., SPEED=..., LED=...
```

如果沒有 BLE status，先用 scan-only 找 ESP32：

```bash
cd /home/asrlab-yian/MakeNTU
source emotion_robot_controller/.venv/bin/activate

python3 frdm_uart_context_sender/esp32s3_ble_fan_led_controller.py \
  --scan-only \
  --scan-target-only \
  --adapter hci0 \
  --scan-duplicates \
  --scan-timeout 30
```

看到 service UUID 後，用 MAC 啟動：

```bash
ESP32_BLE_ADDRESS=78:E3:6D:18:94:6A \
ESP32_BLE_ADAPTER=hci0 \
./frdm_uart_context_sender/run_wake_bridge_full_demo.sh
```

如果 BLE status 有溫度，但 Weather UART 還是 5 欄，通常是溫度資料太舊或天氣尚未重新查詢。再問一次：

```text
Hey Jarvis，現在天氣如何？
```

應看到：

```text
Weather UART sent: Weather current,20,20,0,3,241 (local=24.1 C)
```

舊 HTTP push 溫度路徑仍可用，以下是相容測試。

先確認 Terminal 3 啟動參數有：

```text
--esp32-temperature-mode push
--esp32-temperature-host 0.0.0.0
--esp32-temperature-port 8790
--esp32-temperature-path /temperature
```

Terminal 3 啟動時應看到：

```text
ESP32 temperature receiver: http://0.0.0.0:8790/temperature
Weather local temperature: push receiver http://0.0.0.0:8790/temperature
```

Jetson 本機先手動 POST：

```bash
curl -X POST http://127.0.0.1:8790/temperature \
  -H "Content-Type: application/json" \
  -d '{"ok":true,"temperature_c":25.4}'
```

再問一次天氣或重啟 Terminal 3，應看到：

```text
Weather UART sent: Weather daily,19,23,76,53,254 (local=25.4 C)
Weather UART sent: Weather current,20,20,0,3,254 (local=25.4 C)
```

如果 Jetson 本機可 POST，但 ESP32 不行，檢查 ESP32 程式裡的目標 IP 必須是 Jetson 的 LAN IP，不是 `127.0.0.1`。用 `hostname -I` 看 Jetson IP。ESP32 和 Jetson 也要在同一個 WiFi/LAN，且網路不能隔離 client-to-client traffic。

如果 FRDM 收到 `Weather ... ,254` 但畫面沒有本地溫度，代表 FRDM firmware 還只 parse 5 欄；要把 `WeatherGui` / `ParseWeatherPayload` 改成 5 欄和 6 欄都接受，並把第 6 欄 `local_temp_c_x10` 顯示成 `25.4 C`。

### ESP32 BLE 掃得到手機但 Jetson 掃不到

手機掃得到代表 ESP32 在廣播；Jetson 掃不到多半是 BlueZ adapter/cache/scan response 問題。先不要用名字掃，改用 service UUID：

```bash
python3 frdm_uart_context_sender/esp32s3_ble_fan_led_controller.py \
  --scan-only \
  --scan-target-only \
  --adapter hci0 \
  --scan-duplicates \
  --scan-timeout 30
```

如果看到 `(unnamed)` 但有 UUID：

```text
* 78:E3:6D:18:94:6A    (unnamed) rssi=-95 uuids=12345678-1234-1234-1234-1234567890ab
```

直接用 MAC：

```bash
python3 frdm_uart_context_sender/esp32s3_ble_fan_led_controller.py \
  --address 78:E3:6D:18:94:6A \
  --adapter hci0 \
  --scan-duplicates \
  --no-tts-reminder
```

如果出現 `Device with address ... was not found`，通常是 BlueZ cache 沒建好該 device object。先跑上面的 scan-only，再立刻 connect。還是不行就重啟 bluetooth：

```bash
pkill -f esp32s3_ble_fan_led_controller.py || true
pkill -f bluetoothctl || true
sudo systemctl restart bluetooth
sleep 3
sudo rfkill unblock bluetooth
bluetoothctl show
```

確認：

```text
Powered: yes
Discovering: no
Soft blocked: no
Hard blocked: no
```

另外手機 BLE app 連著 ESP32 時，Jetson 可能連不上；先把手機斷線。

### ESP32 回 FAN=ON 但風扇不轉

先分辨是「指令問題」還是「硬體問題」：

```text
ble> FAN_SPEED:255
```

如果 255 不轉，檢查硬體：

```text
L9110S VCC 是 5V，不是 3.3V
ESP32 GND / L9110S GND / 馬達電源 GND 共地
INA 接 GPIO5
INB 接 GPIO6
馬達接 L9110S output，不是接 INA/INB
ESP32 full sketch 真的有重新燒錄，不是 minimal advertise sketch
```

如果 255 會轉、但 FRDM `Fanspeed 16` 不轉，代表低 duty 起轉不足。現在 Jetson 和 ESP32 都預設非零最低 PWM 96；可再調高：

```bash
ESP32_BLE_ADDRESS=78:E3:6D:18:94:6A \
ESP32_BLE_ADAPTER=hci0 \
FAN_MIN_PWM=120 \
./frdm_uart_context_sender/run_wake_bridge_full_demo.sh
```

ESP32 full sketch 端也有 `FAN_MIN_RUNNING_SPEED=96` 和 250ms 的 `FAN_START_KICK_SPEED=255`，如果改 sketch 常數要重新燒錄。

### fan dashboard sync failed

這行不影響 BLE：

```text
WARNING: fan dashboard sync failed: <urlopen error [Errno 111] Connection refused>
```

只是 Terminal 5 dashboard server 沒開，Wake Bridge POST `desk_fan` dashboard 狀態失敗。處理方式二選一：

```bash
# 開 dashboard
python3 smart_home_dashboard/server.py --host 0.0.0.0 --port 8789 --no-frdm-uart

# 或正式 demo 不同步 dashboard
./frdm_uart_context_sender/run_wake_bridge_full_demo.sh --no-fan-dashboard-sync
```

### TTS ready 但沒聲音

```bash
curl http://127.0.0.1:8777/health
aplay -l
cat /home/asrlab-yian/MakeNTU/jetson_piper_tts/.env
```

確認：

```text
AUDIO_DEVICE=auto:UACDemo
DEFAULT_VOLUME_GAIN=4.8
```

改 `.env` 或 USB recovery 後重開 TTS server。

如果 Terminal 3 出現 `WARNING: TTS speak failed: <urlopen error [Errno 111] Connection refused>`，代表 Terminal 2 的 Piper TTS server 在 health check 後又掉了或被重啟。新版 bridge 會立刻結束這輪 conversation follow-up，回到必須重新說 `Hey Jarvis` 的 standby，避免 TTS 沒講出來卻一直錄後續雜音；此時先重啟 Terminal 2，再重啟 Terminal 3。

如果不是完全沒聲音，而是現場聽起來超小聲，Terminal 3 的 Wake Bridge 用：

```bash
--tts-volume-gain 4.8
```

這個增益只放大 Piper raw playback，不會改系統音量；改完要重啟 Terminal 3，且 TTS server 也要是新版。如果會嚇到旁邊的人，把 Terminal 3 改成 `--tts-volume-gain 3.6`，並加 `--beep-volume 0.25 --beep-duration-ms 160`。

直接測新版 TTS server 是否吃到增益：

```bash
curl -X POST http://127.0.0.1:8777/speak_async \
  -H "Content-Type: application/json" \
  -d '{"text":"音量測試，現在應該是固定音量。","interrupt":true,"volume_gain":4.8}'
```

如果回 `422`，代表 Terminal 2 還是舊 TTS server，重啟 Terminal 2。

### 音樂本身太小聲

先分清楚是 Piper TTS 小聲，還是 mpv 音樂小聲。TTS 看 Terminal 2 / `--tts-volume-gain`；音樂看 Terminal 4 / mpv。

```bash
curl http://127.0.0.1:8788/health
```

正常預設要看到：

```text
mpv_volume=125
mpv_volume_max=200
mpv_volume_clamped=false
```

如果 `mpv_volume` 還是 `100`，代表 Terminal 4 或 Terminal 3 autostart 還是舊參數。手動 Terminal 4 就直接重開 Terminal 4：

```bash
pkill -f 'music_web_player.py'
cd /home/asrlab-yian/MakeNTU/music_web_player
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate
python3 music_web_player.py --server --host 127.0.0.1 --port 8788 --backend mpv --mpv-audio-device auto --mpv-volume 125 --mpv-volume-max 200 --mpv-ready-timeout 1.5 --weather-default-location Taipei --weather-timeout 4.5
```

如果是讓 Terminal 3 自動啟動 Music Player，就重開 Terminal 3 並帶環境變數。

只想把音樂再調大，不要動 TTS：

```bash
MUSIC_MPV_VOLUME=150 MUSIC_MPV_VOLUME_MAX=220 ./frdm_uart_context_sender/run_wake_bridge_full_demo.sh
```

如果 `mpv_volume_clamped=true`，代表要求值超過 `mpv_volume_max`；一起提高 `MUSIC_MPV_VOLUME_MAX`。如果 `mpv_actual_volume` 沒出現但你以為正在播歌，代表 mpv process 沒 active 或 IPC 還沒 ready，先檢查 Terminal 4 log。

### 停止音樂或關風扇那輪沒聲音 / 馬達亂動

新版行為應該是：停止/暫停音樂和 ESP32 關風扇都會有 Piper 確認句，但不轉頭。Terminal 3 應看到：

```text
Music stop/pause handled before TTS; local confirmation will be spoken.
speaking head motion skipped: none
FRDM UART TX: Normal 0 0
```

或 ESP32 控制：

```text
ESP32-S3 BLE control:
head_motion      : none
speaking head motion skipped: none
FRDM UART TX: Normal 0 0
```

如果完全沒看到這些 log，通常是 Terminal 3 還在跑舊 process；先停乾淨再重開：

```bash
pkill -f wake_voice_chat_frdm_bridge.py
./frdm_uart_context_sender/run_wake_bridge_full_demo.sh
```

如果 log 有 `TTS speak failed`，先修 Terminal 2 TTS；新版 bridge 會在 TTS 失敗時強制清掉 speaking motion，避免沒講話但馬達繼續動。也可以先跑自測確認 regression guard：

```bash
python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py --self-test
python3 music_web_player/music_web_player.py --self-test
```

### FRDM 收不到 TempRoom 室內溫度

先確認 Terminal 3 有 ESP32 BLE notify：

```text
BLE: connected. Subscribing status notify...
ESP32 status: TEMP=25.43 C, FAN=OFF, SPEED=0, LED=OFF
```

啟動時要看到：

```text
FRDM room temperature UART: TempRoom every 10s, max_age=30s, payload=Celsius*10
```

有新鮮溫度後，每 10 秒左右要看到：

```text
TempRoom UART sent: TempRoom 254 (25.4 C, age=0.5s)
```

如果沒有 `TempRoom UART sent`：

```text
檢查 Terminal 3 是否加了 --no-temp-room-uart
檢查 --temp-room-uart-interval-sec 是否是 0
檢查 ESP32 status 裡 TEMP 是否是 -127.00 或沒有 TEMP
檢查溫度是否超過 --temp-room-uart-max-age-sec 30
```

如果 Terminal 3 有送但 FRDM 沒顯示，問題在 FRDM parser/display。FRDM 端要解析單參數命令：

```text
TempRoom 254
```

轉換公式：

```text
display_c = 254 / 10.0 = 25.4 C
```

### TTS terminal 一直刷 `/queue`

這是 bridge 在等 TTS 播放完成。正式指令已經使用：

```text
--tts-poll-interval 0.75
```

如果還是覺得太吵，可以改成 `--tts-poll-interval 1.0`；只是 FRDM 切回下一個畫面會晚一點點。

### vision_intent=True 但 used_vision=False

看：

```text
image_received
image_size_bytes
vision_error
```

常見原因：

```text
image_received=False  -> Jetson 沒送圖，檢查 camera / --no-camera / --no-vision
image_size_bytes=0    -> camera capture 失敗
vision disabled       -> server 或 client 關了 vision
Ollama error          -> Windows qwen35-fast / Ollama 有問題
```

### FRDM 沒反應

```bash
python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py --list-uarts
lsusb
ls -l /dev/ttyACM* /dev/serial/by-id/* 2>/dev/null
```

確認：

```text
FRDM 接 MCU-LINK USB-C
USB-C 線可傳資料
baudrate 115200
line ending CRLF
```

## 9. Demo Checklist

啟動前 30 秒檢查：

```text
[ ] Windows Terminal 1 server 還開著
[ ] Jetson Terminal 2 TTS 還開著
[ ] Jetson Terminal 3 bridge 是最新重開的，不是舊卡住 process
[ ] ESP32-S3 和 Jetson 在同一個 LAN，本地溫度 POST 目標是 Jetson LAN IP
[ ] 三個 USB：UACDemo 音訊、Global Shutter Camera、FRDM MCU-LINK 都接著
[ ] Quick Start 裡的 Windows IP 還是目前 tailscale ip
```

Windows：

```text
[ ] 已同步 desktop_fast_chat_server.py
[ ] ollama list 有 qwen35-fast:latest
[ ] server health debug_version=13
[ ] vision_model=qwen35-fast:latest
```

Jetson：

```text
[ ] TTS health ready=true
[ ] TTS audio device 是 UACDemo
[ ] --list-mics 有 UACDemo input
[ ] --list-uarts 有 FRDM
[ ] lsusb 有 UACDemo / Global Shutter Camera / MCU-LINK
[ ] ESP32-S3 已燒完整 `esp32s3_ble_fan_led_temp.ino`
[ ] BLE scan-only 看得到 service UUID `12345678-1234-1234-1234-1234567890ab`
[ ] Standalone BLE CLI 測過 `TEMP?`、`LED_ON/OFF`、`FAN_SPEED:255`、`FAN_OFF`
[ ] Terminal 3 用 `ESP32_BLE_ADDRESS=<MAC>` 啟動，看到 `BLE: connected`
[ ] Terminal 3 每 10 秒看到 `TempRoom UART sent: TempRoom ...`
[ ] FRDM 已解析 `TempRoom <攝氏x10>` 並顯示室內溫度
[ ] FRDM `FanSpeed 16` 會送至少 `FAN_SPEED:96`，必要時調 `FAN_MIN_PWM=120`
[ ] 純語音 used_vision=False
[ ] 視覺句 used_vision=True
[ ] FRDM UART TX/RX 正常
```
