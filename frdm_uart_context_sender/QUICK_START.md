# Quick Start: Wake Voice Chat + Vision + FRDM UART

這份文件是現場 demo 操作手冊。每次要從零啟動，先看 **0. 必跑指令總覽**，照順序貼三個 terminal 即可。

最短使用方式：

```text
Terminal 1 on Windows : desktop_fast_chat_server.py
Terminal 2 on Jetson  : jetson_piper_tts.server
Terminal 3 on Jetson  : wake_voice_chat_frdm_bridge.py
```

請盡量整段複製，不要手打尾端參數。最常見打錯是把下面兩個參數黏在一起：

```text
錯誤：--uart-debug\terval 0.75
正確：--tts-poll-interval 0.75 \
      --tts-debug \
      --uart-debug
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
```

如果 Tailscale IP 變了，所有指令裡的 IP 要一起改。

目前穩定版參數：

```text
wake_threshold=0.75
wake_volume_min=350
volume_min=700
silence_margin=650
max_speech_seconds=5
max_recording_seconds=7
audio_read_timeout=0.75
camera=auto 320x240 jpeg_quality=70
tts_poll_interval=0.75
```

這組偏向 demo 穩定與低延遲：現場吵也不會一輪卡太久，USB mic 停吐 audio chunk 時也會自動退出當輪。

## 0. 必跑指令總覽

### 0.0 如果已經開過舊程式

Jetson 只需要保留 TTS server，舊 wake bridge 可以先停掉：

```bash
pkill -9 -f wake_voice_chat_frdm_bridge.py 2>/dev/null || true
```

如果 TTS server 已經在 Terminal 2 正常跑，可以不用重開。若你剛重插 USB speaker，建議照 Terminal 2 指令重開 TTS，讓它重新抓 `UACDemo` speaker。

### 0.1 Windows Server 有改過才同步

在 Windows PowerShell 執行：

```powershell
scp asrlab-yian@100.110.90.72:/home/asrlab-yian/MakeNTU/emotion_robot_controller/voice_stt_remote/windows_desktop_server_bundle/desktop_fast_chat_server.py "$env:USERPROFILE\Desktop\windows_desktop_server_bundle\desktop_fast_chat_server.py"
```

同步後一定要重啟 Windows server，health 要看到 `debug_version: 10`。

### 0.2 軟體 Self-Test

這兩個檢查不需要麥克風、相機、FRDM 或 Ollama。剛改過程式時先跑。

Jetson：

```bash
cd /home/asrlab-yian/MakeNTU/frdm_uart_context_sender
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate
python3 wake_voice_chat_frdm_bridge.py --self-test
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

### Terminal 1: Windows ASR/Ollama Server

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

### Terminal 2: Jetson Piper TTS

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
ENABLE_STREAM_PLAYBACK=true
```

### Terminal 3: Jetson Wake Bridge

正式完整模式：

請整段複製貼上，尤其最後三行不要手打錯。

```bash
cd /home/asrlab-yian/MakeNTU/frdm_uart_context_sender
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate

python3 wake_voice_chat_frdm_bridge.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --mic-keyword UACDemo \
  --uart-port auto \
  --wake-threshold 0.75 \
  --wake-volume-min 350 \
  --volume-min 700 \
  --silence-duration 1.2 \
  --silence-margin 650 \
  --max-speech-seconds 5 \
  --max-recording-seconds 7 \
  --audio-read-timeout 0.75 \
  --recording-progress-interval 1.0 \
  --camera-id auto \
  --camera-width 320 \
  --camera-height 240 \
  --camera-jpeg-quality 70 \
  --camera-latest-timeout 1.0 \
  --camera-frame-max-age 2.0 \
  --device-preflight-verbose \
  --tts-poll-interval 0.75 \
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

錄音使用 adaptive recording gate。程式會在 wake 前估計環境底噪，wake 後自動算：

```text
speech_start_threshold = max(volume_min, noise_floor + speech_start_margin)
silence_threshold      = max(volume_min, noise_floor + silence_margin, peak_volume * silence_peak_ratio)
```

所以吵的環境不會只因背景音量高於 `--volume-min 700` 就一路錄到最長秒數。
正式預設 `max_speech_seconds=5`，避免真的開始說話後錄太久；`max_recording_seconds=7` 是從 wake 被接受開始算的硬上限，就算現場太吵、speech start 判斷怪掉，也會退出回 standby；`audio_read_timeout=0.75` 是 USB mic watchdog，若 mic stream 停止吐 audio chunk，Python 不會卡在 blocking read；`recording_progress_interval=1.0` 會每秒印一行錄音狀態；`tts_poll_interval=0.75` 會降低 TTS `/queue` 查詢頻率，避免 Terminal 2 被 log 洗版。

錄音 log 快速判讀：

```text
phase=waiting_speech -> wake 成功，但你講話音量還沒高過 start threshold
phase=speech         -> 已開始錄音，等 silence 或 max_speech 結束
Max recording...     -> 硬上限保護觸發，這是正常防卡死
audio chunk warning  -> USB mic 暫時沒吐資料，watchdog 會讓當輪退出
```

## 1. 啟動成功要看到

Windows server health：

```text
debug_version: 10
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
Selected input device ... by keyword 'UACDemo'.
Selected beep output device ... by keyword 'UACDemo'.
Camera ready in continuous warm-reader mode.
Camera warm reader opened camera 0.
Adaptive recording gate: on, noise_p75, speech_margin=350, silence_margin=650, peak_ratio=0.35
Audio read watchdog: callback queue, timeout=0.75s, progress_interval=1s
TTS queue polling: every 0.75s, playback_timeout=45s
USB auto-discovery: mic=keyword 'UACDemo'; beep=keyword 'UACDemo'; camera=auto; FRDM UART=auto
Listening for wake word 'hey_jarvis'
```

一次互動的正常時序：

```text
Wake detected
Recording thresholds: noise_floor=..., speech_start_threshold=..., silence_base_threshold=..., adaptive=on
beep played
FRDM UART TX: Thinking 0 0
Recording. Speak now
Recording progress: phase=...
POST audio+image ...
AI control:
  persistent_state : unchanged / normal / sleep
  emotion          : neutral / happy / curious / excited / confused / concerned / sleepy
  head_motion      : none / nod / double_nod / look_around / shake / gentle_nod / sleepy_drop
FRDM UART TX: Speaking 0 0
FRDM UART TX: Happy 0 0        # 依 emotion 改變
TTS started
head motion started
TTS finished
FRDM UART TX: Normal 0 0       # 或 Sleep 0 0
```

TTS 只會唸 `reply`。`control` 是內部 JSON，不會被唸出來。

## 2. 立刻測

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

## 3. 快速 Health Check

Windows PowerShell：

```powershell
curl.exe http://127.0.0.1:8766/health
curl.exe http://127.0.0.1:11434/api/tags
tailscale ip -4
```

Jetson：

```bash
curl http://127.0.0.1:8777/health

cd /home/asrlab-yian/MakeNTU/frdm_uart_context_sender
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate
python3 wake_voice_chat_frdm_bridge.py --list-mics
python3 wake_voice_chat_frdm_bridge.py --list-uarts
lsusb
ls -l /dev/video* /dev/ttyACM* 2>/dev/null
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

## 4. USB 重插與 Recovery

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

## 5. 只測純語音

如果 camera 不穩，先用這個確認 wake / ASR / TTS / UART：

```bash
cd /home/asrlab-yian/MakeNTU/frdm_uart_context_sender
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate

python3 wake_voice_chat_frdm_bridge.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --mic-keyword UACDemo \
  --uart-port auto \
  --no-camera \
  --no-beep \
  --wake-threshold 0.75 \
  --wake-volume-min 350 \
  --volume-min 700 \
  --silence-duration 1.2 \
  --silence-margin 650 \
  --max-speech-seconds 5 \
  --max-recording-seconds 7 \
  --audio-read-timeout 0.75 \
  --recording-progress-interval 1.0 \
  --tts-poll-interval 0.75 \
  --tts-debug \
  --uart-debug
```

## 6. Force Vision / No Vision

強制每次有圖就看圖，用來測 camera -> server -> qwen35-fast vision：

```bash
python3 wake_voice_chat_frdm_bridge.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --mic-keyword UACDemo \
  --uart-port auto \
  --force-vision \
  --wake-threshold 0.75 \
  --wake-volume-min 350 \
  --volume-min 700 \
  --silence-duration 1.2 \
  --silence-margin 650 \
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
python3 wake_voice_chat_frdm_bridge.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --mic-keyword UACDemo \
  --uart-port auto \
  --no-vision \
  --wake-threshold 0.75 \
  --wake-volume-min 350 \
  --volume-min 700 \
  --silence-duration 1.2 \
  --silence-margin 650 \
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

## 7. 常見問題速查

### No microphone matching UACDemo

代表 Jetson 現在沒有看到 UACDemo input。先看：

```bash
lsusb
python3 wake_voice_chat_frdm_bridge.py --list-mics
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
python3 wake_voice_chat_frdm_bridge.py \
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
--silence-margin 800
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

現場很吵時可以直接用這組覆蓋值：

```text
--speech-start-margin 450 --silence-margin 650 --max-speech-seconds 5 --max-recording-seconds 7 --audio-read-timeout 0.75 --recording-progress-interval 1.0
```

如果變成「叫醒後你說話也不開始錄」，把 `--speech-start-margin` 從 450 降回 300 或 250。

錄音調參表：

```text
漏收你的聲音              -> --speech-start-margin 250
背景音太容易觸發 speech   -> --speech-start-margin 450
speech 後一直停不下來     -> --silence-margin 800
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
--wake-volume-min 350
```

仍漏叫可降到 200，但誤觸發會變多。

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

### debug_version 不是 10

重新 scp 同步，關掉舊 server，重開 Terminal 1。

### TTS ready 但沒聲音

```bash
curl http://127.0.0.1:8777/health
aplay -l
cat /home/asrlab-yian/MakeNTU/jetson_piper_tts/.env
```

確認：

```text
AUDIO_DEVICE=auto:UACDemo
```

改 `.env` 或 USB recovery 後重開 TTS server。

### TTS terminal 一直刷 `/queue`

這是 bridge 在等 TTS 播放完成。正式指令已經使用：

```text
--tts-poll-interval 0.75
```

如果還是覺得太吵，可以改成 `--tts-poll-interval 1.0`；只是 FRDM 回 Normal/Sleep 會晚一點點。

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
python3 wake_voice_chat_frdm_bridge.py --list-uarts
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

## 8. Demo Checklist

啟動前 30 秒檢查：

```text
[ ] Windows Terminal 1 server 還開著
[ ] Jetson Terminal 2 TTS 還開著
[ ] Jetson Terminal 3 bridge 是最新重開的，不是舊卡住 process
[ ] 三個 USB：UACDemo 音訊、Global Shutter Camera、FRDM MCU-LINK 都接著
[ ] Quick Start 裡的 Windows IP 還是目前 tailscale ip
```

Windows：

```text
[ ] 已同步 desktop_fast_chat_server.py
[ ] ollama list 有 qwen35-fast:latest
[ ] server health debug_version=10
[ ] vision_model=qwen35-fast:latest
```

Jetson：

```text
[ ] TTS health ready=true
[ ] TTS audio device 是 UACDemo
[ ] --list-mics 有 UACDemo input
[ ] --list-uarts 有 FRDM
[ ] lsusb 有 UACDemo / Global Shutter Camera / MCU-LINK
[ ] 純語音 used_vision=False
[ ] 視覺句 used_vision=True
[ ] FRDM UART TX/RX 正常
```
