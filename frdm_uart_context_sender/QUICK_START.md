# Quick Start: Wake Voice Chat + Camera Vision + FRDM UART

這份是現場 demo 操作手冊。日常啟動時先看最前面的 **0. 每次完整啟動：三個 Terminal**，照貼三段指令即可進入完整狀態。後面章節是拆解說明、同步、測試和除錯。

## 0. 每次完整啟動：三個 Terminal

目前預設：

```text
Windows Tailscale: 100.108.141.26
Jetson Tailscale : 100.110.90.72
Windows server   : http://100.108.141.26:8766/voice-chat
Jetson TTS       : http://127.0.0.1:8777
Vision/Text model: qwen35-fast:latest
Wake word        : Hey Jarvis
```

如果 Windows IP 或 Jetson IP 變了，下面指令裡的 IP 要一起改。

### 0.1 只有更新過 Windows server 程式時才做

在 **Windows PowerShell** 執行一次，把 Jetson repo 裡最新版 server 同步到 Windows 桌面 bundle：

```powershell
scp asrlab-yian@100.110.90.72:/home/asrlab-yian/MakeNTU/emotion_robot_controller/voice_stt_remote/windows_desktop_server_bundle/desktop_fast_chat_server.py "$env:USERPROFILE\Desktop\windows_desktop_server_bundle\desktop_fast_chat_server.py"
```

### Terminal 1: Windows ASR/Ollama Server

在 **Windows PowerShell** 開第一個 terminal，貼上整段。這個 terminal 要保持開著：

```powershell
# Make sure Ollama is available. If it is already running, this does nothing.
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

正常狀態：這個 terminal 會停在 server log，不要關掉。

### Terminal 2: Jetson Piper TTS

在 **Jetson** 開第二個 terminal，貼上整段。這個 terminal 要保持開著：

```bash
cd /home/asrlab-yian/MakeNTU/jetson_piper_tts
source .venv/bin/activate

# Restart TTS cleanly so .env and USB speaker auto-detection are refreshed.
pkill -f 'jetson_piper_tts.server' 2>/dev/null || true

python -m jetson_piper_tts.server \
  --host 0.0.0.0 \
  --port 8777 \
  --no-warmup
```

正常狀態應看到：

```text
Application startup complete
Uvicorn running on http://0.0.0.0:8777
```

TTS 使用：

```text
AUDIO_DEVICE=auto:UACDemo
```

所以重插 USB speaker 後，播放前會重新找 UACDemo。

### Terminal 3: Jetson Wake + Camera + FRDM Bridge

在 **Jetson** 開第三個 terminal，貼上整段。這就是正式完整模式：

```bash
cd /home/asrlab-yian/MakeNTU/frdm_uart_context_sender
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate

python3 wake_voice_chat_frdm_bridge.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --mic-keyword UACDemo \
  --uart-port auto \
  --wake-threshold 0.75 \
  --wake-volume-min 350 \
  --volume-min 3000 \
  --silence-duration 1.2 \
  --camera-id auto \
  --camera-width 320 \
  --camera-height 240 \
  --camera-jpeg-quality 70 \
  --tts-debug \
  --uart-debug
```

正常啟動時要看到這些重點：

```text
Server health:
  debug_version: 9
  chat_ready   : True
  asr_loaded   : True
  vision       : enabled=True model=qwen35-fast:latest

TTS health:
  ready : True

Selected input device ... by keyword 'UACDemo'.
Selected beep output device ... by keyword 'UACDemo'.
Camera ready in one-shot mode.
FRDM UART: auto @ 115200
USB auto-discovery: mic=keyword 'UACDemo'; beep=keyword 'UACDemo'; camera=auto; FRDM UART=auto
Listening for wake word 'hey_jarvis'
```

之後直接說：

```text
Hey Jarvis，今天天氣如何？
Hey Jarvis，我現在是什麼表情？
Hey Jarvis，幫我看一下桌上有什麼。
Hey Jarvis，幫我開電風扇。
```

### 每次重插 USB 後

只要不是 Jetson USB controller 整個卡死，正式 bridge 會自動重新找：

```text
mic       : --mic-keyword UACDemo
beep      : --beep-keyword UACDemo, 不要固定 --beep-device
TTS audio : AUDIO_DEVICE=auto:UACDemo
camera    : --camera-id auto
FRDM      : --uart-port auto
```

不要在正式指令裡加：

```text
--device 25
--beep-device 24
--camera-id 0
--uart-port /dev/ttyACM0
```

這些固定 index/port 在 USB 重插後容易變。

如果 `lsusb` 都看不到設備、camera timeout 一直刷、或程式停不了，跑：

```bash
cd /home/asrlab-yian/MakeNTU/frdm_uart_context_sender
./recover_demo_usb.sh
```

### 快速 health check

需要確認時可以另外開 terminal 跑：

Windows PowerShell：

```powershell
curl.exe http://127.0.0.1:8766/health
```

Jetson：

```bash
curl http://127.0.0.1:8777/health

cd /home/asrlab-yian/MakeNTU/frdm_uart_context_sender
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate
python3 wake_voice_chat_frdm_bridge.py --list-mics
python3 wake_voice_chat_frdm_bridge.py --list-uarts
```

目標流程：

```text
Hey Jarvis
-> beep
-> Jetson camera 拍 memory-only JPEG
-> Jetson 錄音到 silence
-> Windows /voice-chat 做 ASR
-> detect_vision_intent(transcript)
-> qwen35-fast:latest text or vision path
-> Jetson TTS reply
-> Jetson UART command to FRDM-MCXN947
```

目前預設 IP：

```text
Jetson Tailscale : 100.110.90.72
Windows Tailscale: 100.108.141.26
Windows server   : http://100.108.141.26:8766/voice-chat
Jetson TTS       : http://127.0.0.1:8777
```

如果 Tailscale IP 變了，所有指令裡的 IP 要一起改。

## 1. Windows: Sync Server Code

只要 Jetson repo 裡的 `desktop_fast_chat_server.py` 有改，就要同步到 Windows。Jetson 不會自動更新 Windows 桌面那份 server。

在 Windows PowerShell 執行：

```powershell
scp asrlab-yian@100.110.90.72:/home/asrlab-yian/MakeNTU/emotion_robot_controller/voice_stt_remote/windows_desktop_server_bundle/desktop_fast_chat_server.py "$env:USERPROFILE\Desktop\windows_desktop_server_bundle\desktop_fast_chat_server.py"
```

如果 scp 問 yes/no，輸入：

```text
yes
```

如果 scp 要密碼，輸入 Jetson 使用者密碼。

## 2. Windows: Start Ollama

在 Windows PowerShell：

```powershell
ollama list
ollama pull qwen35-fast:latest
ollama serve
```

如果 `ollama serve` 顯示 port already in use，通常代表 Ollama 已經在背景執行，不是錯誤。

測 Ollama：

```powershell
curl.exe http://127.0.0.1:11434/api/tags
```

要看到 model list 裡有：

```text
qwen35-fast:latest
```

## 3. Windows: Start ASR/Ollama Server

另開一個 Windows PowerShell：

```powershell
cd "$env:USERPROFILE\Desktop\windows_desktop_server_bundle"
.\.venv\Scripts\Activate.ps1

python desktop_fast_chat_server.py --host 0.0.0.0 --port 8766 --ollama-model qwen35-fast:latest --vision-model qwen35-fast:latest --no-think
```

保持這個 terminal 開著。

確認 health：

```powershell
curl.exe http://127.0.0.1:8766/health
```

必須確認：

```text
debug_version : 9
chat_ready    : true
asr_loaded    : true
vision_model  : qwen35-fast:latest
vision_enabled: true
```

如果 `debug_version` 不是 9：

```text
1. 你還沒 scp 同步新版 server。
2. 你啟動到舊資料夾的 server。
3. 舊的 Python process 還占著 port 8766。
```

## 4. Jetson: Start Piper TTS

開 Jetson terminal：

```bash
cd /home/asrlab-yian/MakeNTU/jetson_piper_tts
source .venv/bin/activate
python -m jetson_piper_tts.server --host 0.0.0.0 --port 8777 --no-warmup
```

如果出現：

```text
address already in use
```

代表 TTS server 已經在跑。另開 terminal 測：

```bash
curl http://127.0.0.1:8777/health
```

要看到：

```text
ready: true
audio.configured_device: auto:UACDemo
audio.device: plughw:CARD=UACDemoV10,DEV=0 或目前實際 UACDemo ALSA device
```

如果沒聲音，檢查：

```bash
cat /home/asrlab-yian/MakeNTU/jetson_piper_tts/.env
```

應包含：

```text
AUDIO_DEVICE=auto:UACDemo
ENABLE_STREAM_PLAYBACK=true
```

改 `.env` 後要重開 TTS server。

## 5. Jetson: Preflight

開另一個 Jetson terminal：

```bash
cd /home/asrlab-yian/MakeNTU/frdm_uart_context_sender
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate
```

檢查 mic 和 speaker：

```bash
python3 wake_voice_chat_frdm_bridge.py --list-mics
```

應看到：

```text
input : UACDemoV1.0
output: UACDemoV1.0
```

正式跑用：

```text
--mic-keyword UACDemo
```

beep 會自動用 `--beep-keyword UACDemo` 找 USB speaker。正式 demo 不要手動指定固定 output index，例如不要加 `--beep-device 24`，因為重插 USB 後 index 可能會變。

```text
--beep-keyword UACDemo
```

檢查 FRDM UART：

```bash
python3 wake_voice_chat_frdm_bridge.py --list-uarts
```

成功時會看到：

```text
/dev/ttyACM0
```

或：

```text
/dev/serial/by-id/usb-NXP_MCU-Link...
```

檢查 Windows server + TTS + UART dry-run：

```bash
python3 wake_voice_chat_frdm_bridge.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --check-server \
  --uart-dry-run \
  --tts-debug
```

這裡如果 health 顯示 `debug_version: 7`，請回到第 1 節同步 Windows server。

## 6. USB Recovery

正式 demo 的 USB 自動尋找規則：

```text
mic       : --mic-keyword UACDemo
speaker  : --beep-keyword UACDemo, 不要固定 --beep-device
TTS audio : /home/asrlab-yian/MakeNTU/jetson_piper_tts/.env 使用 AUDIO_DEVICE=auto:UACDemo
camera   : --camera-id auto
FRDM     : --uart-port auto
```

bridge 會在每次錄音前重新找 UACDemo mic、每次 beep 前重新找 UACDemo speaker、每次 wake capture 重新掃 `/dev/video*`，每次送 UART 前重新找 FRDM serial。啟動 log 應看到：

```text
USB auto-discovery: mic=keyword 'UACDemo'; beep=keyword 'UACDemo'; camera=auto; FRDM UART=auto
```

如果看到 `fixed index`，代表你傳了 `--device` 或 `--beep-device`，重插 USB 後比較容易失效。

如果程式卡住、Ctrl+C 停不了、camera timeout 一直刷、`lsusb` 看不到 UACDemo/camera/FRDM，先跑：

```bash
cd /home/asrlab-yian/MakeNTU/frdm_uart_context_sender
./recover_demo_usb.sh
```

如果只要殺掉 bridge：

```bash
pkill -9 -f wake_voice_chat_frdm_bridge.py
```

## 7. Start: Pure Voice First

先確認純語音、ASR、TTS、UART 都通，暫時不開 camera 和 beep：

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
  --volume-min 3000 \
  --silence-duration 1.2 \
  --tts-debug \
  --uart-debug
```

測試語句：

```text
Hey Jarvis，今天天氣如何？
Hey Jarvis，幫我開電風扇。
```

預期：

```text
vision_intent=False
used_vision=False
TTS 有聲音
FRDM UART 有 TX/RX
```

## 8. Start: Full Voice + Camera + Vision

純語音正常後，用正式版本：

```bash
cd /home/asrlab-yian/MakeNTU/frdm_uart_context_sender
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate

python3 wake_voice_chat_frdm_bridge.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --mic-keyword UACDemo \
  --uart-port auto \
  --wake-threshold 0.75 \
  --wake-volume-min 350 \
  --volume-min 3000 \
  --silence-duration 1.2 \
  --camera-id auto \
  --camera-width 320 \
  --camera-height 240 \
  --camera-jpeg-quality 70 \
  --camera-read-timeout 2.5 \
  --camera-warmup-frames 2 \
  --tts-debug \
  --uart-debug
```

測試不看圖：

```text
Hey Jarvis，講個笑話。
```

預期：

```text
POST audio+image ...
vision_intent=False
used_vision=False
image_received=True
```

注意：Jetson 仍會拍照並上傳，但 server 不會呼叫 vision path。

測試看圖：

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
transcript='我現在是什麼表情'
normalized_transcript='我現在是什麼表情'
vision_intent=True reason=pattern:zh_self_expression
image_received=True image_size_bytes=...
calling vision model=qwen35-fast:latest image_bytes=...
```

## 9. Force Vision Debug

如果你要測 camera -> server -> qwen35-fast vision 完整路徑，不管 transcript 是什麼都看圖：

```bash
python3 wake_voice_chat_frdm_bridge.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --mic-keyword UACDemo \
  --uart-port auto \
  --force-vision \
  --wake-threshold 0.75 \
  --wake-volume-min 350 \
  --volume-min 3000 \
  --camera-id auto \
  --camera-width 320 \
  --camera-height 240 \
  --camera-jpeg-quality 70 \
  --tts-debug \
  --uart-debug
```

隨便說：

```text
Hey Jarvis，測試一下。
```

預期：

```text
vision_reason=forced_by_client_metadata
used_vision=True
calling vision model=qwen35-fast:latest
```

## 10. No Vision Mode

完全關閉 vision 和 camera：

```bash
python3 wake_voice_chat_frdm_bridge.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --mic-keyword UACDemo \
  --uart-port auto \
  --no-vision \
  --wake-threshold 0.75 \
  --wake-volume-min 350 \
  --volume-min 3000 \
  --tts-debug \
  --uart-debug
```

預期：

```text
Vision mode: off
Camera disabled by --no-vision.
POST audio only ...
```

優先順序：

```text
--no-vision > --force-vision > auto detect_vision_intent
```

## 11. How To Read Logs

### Jetson

```text
Image captured: 13001 bytes
POST audio+image ... (vision_mode=auto, image_size_bytes=13001)
```

代表 Jetson 有拍到圖並送到 server。

```text
Vision routing:
  vision_intent    : True
  vision_reason    : pattern:zh_self_expression
  used_vision       : True
  image_received    : True
  image_size_bytes  : 13001
  vision_model      : qwen35-fast:latest
```

代表 server 有收到圖片，並真的走 vision path。

```text
vision_intent=True
used_vision=False
vision_error=...
```

代表 server 判斷該看圖，但圖片缺失、vision disabled 或 Ollama error。看 `vision_error`。

### Windows

```text
voice-chat abcd1234: transcript='我手上拿什麼'
voice-chat abcd1234: normalized_transcript='我手上拿什麼'
voice-chat abcd1234: vision_intent=True reason=keyword:我手上 auto=True:keyword:我手上 image_received=True image_size_bytes=13001
voice-chat abcd1234: calling vision model=qwen35-fast:latest image_bytes=13001
```

這四行是 vision 成功路徑最重要的證據。

## 12. Intent Test Cases

必須走 vision：

```text
我現在是什麼表情
我看起來累嗎
我手上拿什麼
桌上有什麼
螢幕上寫什麼
這是什麼顏色
what is my expression
what am I holding
check my posture
read this text
```

必須不走 vision：

```text
幫我開電風扇
切換安靜模式
今天幾號
講個笑話
解釋 PID 控制
馬達往前走
```

## 13. Camera Standalone Test

測原本 vision camera 程式：

```bash
cd /home/asrlab-yian/MakeNTU/vision
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate
python3 camera_ollama_status.py
```

注意：這支測試程式會把照片存到 `vision/`，正式 bridge 不會永久存照片。

## 14. FRDM UART Standalone

列 ports：

```bash
python3 frdm_uart_context_sender.py --list-ports
```

送指令：

```bash
python3 frdm_uart_context_sender.py --command Sleep --port auto
python3 frdm_uart_context_sender.py --command Normal --port auto
python3 frdm_uart_context_sender.py --command "ShowNum 123" --port auto
python3 frdm_uart_context_sender.py --command "MotorPitch 90" --port auto
python3 frdm_uart_context_sender.py --command "MotorYaw 90" --port auto
```

只看 TX 不真的開 serial：

```bash
python3 frdm_uart_context_sender.py --command Sleep --port auto --dry-run
```

## 15. Troubleshooting

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

如果 Windows IP 改了，更新 Jetson `--server-url`。

### debug_version 不是 9

重新同步：

```powershell
scp asrlab-yian@100.110.90.72:/home/asrlab-yian/MakeNTU/emotion_robot_controller/voice_stt_remote/windows_desktop_server_bundle/desktop_fast_chat_server.py "$env:USERPROFILE\Desktop\windows_desktop_server_bundle\desktop_fast_chat_server.py"
```

然後關掉舊 server，重開：

```powershell
cd "$env:USERPROFILE\Desktop\windows_desktop_server_bundle"
.\.venv\Scripts\Activate.ps1
python desktop_fast_chat_server.py --host 0.0.0.0 --port 8766 --ollama-model qwen35-fast:latest --vision-model qwen35-fast:latest --no-think
```

### Ollama 連不到

Windows：

```powershell
curl.exe http://127.0.0.1:11434/api/tags
ollama list
ollama serve
```

### Vision intent True 但沒有看圖

看 Jetson response：

```text
image_received
image_size_bytes
used_vision
vision_error
```

常見原因：

```text
image_received=False  -> Jetson 沒上傳圖，檢查 camera / --no-camera / --no-vision
image_size_bytes=0    -> 圖片欄位空，檢查 camera capture
vision disabled       -> server 用了 --no-vision 或 client 用了 --no-vision
Ollama error          -> Windows Ollama model 或 /api/chat 有問題
```

### Camera timeout

先確認 USB：

```bash
lsusb
ls -l /dev/video*
```

跑 USB recovery：

```bash
./recover_demo_usb.sh
```

正式 demo 可以先降負載：

```bash
--camera-width 320 --camera-height 240 --camera-jpeg-quality 70
```

暫時關 camera：

```bash
--no-camera
```

### TTS ready 但沒聲音

```bash
curl http://127.0.0.1:8777/health
aplay -l
```

確認：

```text
AUDIO_DEVICE=auto:UACDemo
```

重開 TTS server。

### Wake 被 ignore

這是低音量保護：

```text
Low-volume wake-like score ignored ...
```

正式建議：

```text
--wake-volume-min 350
```

如果仍漏叫，可試：

```text
--wake-volume-min 200
```

但越低越容易被背景聲或 TTS 回音誤觸。

### Mic 或 ASR 很怪

```bash
python3 wake_voice_chat_frdm_bridge.py --list-mics
```

正式跑必須用：

```text
--mic-keyword UACDemo
```

如果 transcript 空或 RMS 太低：

```text
靠近 mic
降低 --volume-min
增加 --silence-duration
確認不是選到 NVIDIA Jetson APE input
```

可試：

```bash
--volume-min 2500 --silence-duration 1.5 --listen-debug
```

### FRDM UART 看不到

```bash
python3 wake_voice_chat_frdm_bridge.py --list-uarts
lsusb
ls -l /dev/ttyACM* /dev/serial/by-id/* 2>/dev/null
```

檢查：

```text
FRDM 接 J17 MCU-LINK USB-C
USB-C 線可傳資料
J18 jumper open
baudrate 115200
line ending CRLF
```

## 16. Demo Checklist

Windows：

```text
[ ] scp 同步 desktop_fast_chat_server.py
[ ] ollama list 有 qwen35-fast:latest
[ ] Windows server health debug_version=9
[ ] vision_model=qwen35-fast:latest
```

Jetson：

```text
[ ] TTS health ready=true
[ ] AUDIO_DEVICE=auto:UACDemo
[ ] --list-mics 有 UACDemo input
[ ] --list-uarts 有 FRDM /dev/ttyACM0
[ ] camera capture 有 Image captured: ... bytes
[ ] 純語音測試 used_vision=False
[ ] 視覺句測試 used_vision=True
[ ] FRDM UART TX/RX 正常
```

最後正式指令：

```bash
cd /home/asrlab-yian/MakeNTU/frdm_uart_context_sender
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate

python3 wake_voice_chat_frdm_bridge.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --mic-keyword UACDemo \
  --uart-port auto \
  --wake-threshold 0.75 \
  --wake-volume-min 350 \
  --volume-min 3000 \
  --silence-duration 1.2 \
  --camera-id auto \
  --camera-width 320 \
  --camera-height 240 \
  --camera-jpeg-quality 70 \
  --tts-debug \
  --uart-debug
```
