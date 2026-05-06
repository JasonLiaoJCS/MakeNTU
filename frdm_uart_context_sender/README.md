# FRDM Wake Voice Chat + Vision Bridge

這個資料夾是 MakeNTU demo 的 Jetson 端整合層，負責：

```text
wake word -> beep -> camera capture -> voice recording
-> Windows ASR/Ollama server
-> Jetson TTS
-> FRDM-MCXN947 UART
```

正式操作請優先看 [QUICK_START.md](QUICK_START.md)。

## 每次完整啟動三個 Terminal

日常 demo 最常用的是三個 terminal：

```text
Terminal 1: Windows ASR/Ollama server
Terminal 2: Jetson Piper TTS server
Terminal 3: Jetson wake_voice_chat_frdm_bridge.py
```

完整可複製版本放在 [QUICK_START.md](QUICK_START.md) 最前面。這裡保留同一組核心指令，方便從 README 也能找到。

### Terminal 1: Windows ASR/Ollama Server

Windows PowerShell：

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

Jetson：

```bash
cd /home/asrlab-yian/MakeNTU/jetson_piper_tts
source .venv/bin/activate
pkill -f 'jetson_piper_tts.server' 2>/dev/null || true

python -m jetson_piper_tts.server \
  --host 0.0.0.0 \
  --port 8777 \
  --no-warmup
```

TTS `.env` 建議：

```text
AUDIO_DEVICE=auto:UACDemo
```

### Terminal 3: Jetson Wake Bridge

Jetson：

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

啟動後應看到：

```text
USB auto-discovery: mic=keyword 'UACDemo'; beep=keyword 'UACDemo'; camera=auto; FRDM UART=auto
Listening for wake word 'hey_jarvis'
```

正式 demo 請不要用固定數字或固定 port，例如 `--device 25`、`--beep-device 24`、`--camera-id 0`、`--uart-port /dev/ttyACM0`。重插 USB 後這些值會變；用 keyword / auto 才能自動追回設備。

## 程式

```text
wake_voice_chat_frdm_bridge.py   # 正式 demo：Hey Jarvis hands-free 流程
voice_chat_frdm_uart_bridge.py   # 手動 Enter 錄音版本
frdm_uart_context_sender.py      # 單獨測 FRDM UART command
recover_demo_usb.sh              # Jetson USB controller 掉線時的救援腳本
```

## 架構

```text
Jetson UACDemo microphone
-> openWakeWord hey_jarvis
-> short local beep
-> one-shot camera JPEG capture in memory
-> record speech until silence
-> POST multipart/form-data to Windows /voice-chat
   - audio: WAV
   - image: optional JPEG
   - metadata: vision_mode, image_size_bytes, wake info
-> Windows Qwen ASR transcript
-> detect_vision_intent(transcript)
-> Windows Ollama qwen35-fast:latest
   - pure text path for normal chat/control
   - vision path when current camera view is needed
-> Jetson prints transcript/reply/vision debug
-> Jetson sends UART command to FRDM
-> Jetson queues Piper TTS reply
```

本 demo 不使用 Gemini、OpenAI 或其他雲端 API。ASR 和 Ollama 都跑在 Windows 桌機本機；wake word、camera、TTS、UART 都在 Jetson 本機。

## Windows Server

Windows server 必須是最新版，因為 vision routing 是在 Windows 端做的。Jetson 端改了 server bundle 後，不會自動更新 Windows 桌面那份檔案。

在 Windows PowerShell 同步 server 檔案：

```powershell
scp asrlab-yian@100.110.90.72:/home/asrlab-yian/MakeNTU/emotion_robot_controller/voice_stt_remote/windows_desktop_server_bundle/desktop_fast_chat_server.py "$env:USERPROFILE\Desktop\windows_desktop_server_bundle\desktop_fast_chat_server.py"
```

啟動 Windows server：

```powershell
cd "$env:USERPROFILE\Desktop\windows_desktop_server_bundle"
.\.venv\Scripts\Activate.ps1
ollama pull qwen35-fast:latest
python desktop_fast_chat_server.py --host 0.0.0.0 --port 8766 --ollama-model qwen35-fast:latest --vision-model qwen35-fast:latest --no-think
```

確認版本：

```powershell
curl.exe http://127.0.0.1:8766/health
```

應看到：

```text
debug_version: 9
vision_model : qwen35-fast:latest
```

如果仍看到 `debug_version: 7` 或舊 model，代表 Windows 還在跑舊檔或舊 process。

## Vision Routing

Windows server 會先做 ASR，取得 `transcript` 後才判斷是否需要圖片。

核心函式：

```python
detect_vision_intent(transcript: str) -> tuple[bool, str]
should_use_vision(transcript: str) -> bool
```

判斷策略是高召回率 rule-based，不額外呼叫 LLM。只要回答需要目前鏡頭畫面，就使用 vision path。

會使用 vision 的例子：

```text
我現在是什麼表情
我看起來累嗎
我是不是在笑
我有沒有皺眉
我現在姿勢怎麼樣
我手上拿什麼
我穿什麼顏色
桌上有什麼
螢幕上寫什麼
這是什麼顏色
what is my expression
how do I look
what am I holding
what is on the desk
check my posture
read this text
identify this
analyze this
```

不使用 vision 的例子：

```text
幫我開電風扇
關燈
切換安靜模式
今天幾號
講個笑話
解釋 PID 控制
把表情切成開心
馬達往前走
```

混合句只要有視覺需求就會使用 vision，例如：

```text
看一下燈有沒有開，沒有的話幫我開燈
我現在是不是在笑，然後把螢幕切成開心表情
```

server log 會印：

```text
transcript='...'
normalized_transcript='...'
vision_intent=True/False
reason=keyword:... or pattern:...
image_received=True/False
image_size_bytes=...
calling vision model=qwen35-fast:latest image_bytes=...
```

Jetson 端 response summary 會印：

```text
Vision routing:
  normalized_transcript : ...
  vision_intent         : True/False
  vision_reason         : ...
  auto_intent           : ...
  used_vision           : True/False
  image_received        : True/False
  image_size_bytes      : ...
  vision_model          : qwen35-fast:latest
  vision_error          : ...
```

## Jetson Start

進入資料夾並啟用 venv：

```bash
cd /home/asrlab-yian/MakeNTU/frdm_uart_context_sender
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate
```

純語音穩定測試：

```bash
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

正式 voice + camera + vision：

```bash
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

強制測試 camera -> server -> vision：

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

完全關閉 vision 和 camera：

```bash
--no-vision
```

只關 camera、server 仍可自動判斷但沒有圖片可用：

```bash
--no-camera
```

## Vision Mode Priority

```text
--no-vision     highest priority, disables camera and vision analysis
--force-vision  uses uploaded image whenever available
auto            detect_vision_intent(transcript)
```

`--force-vision` 是 debug 用，適合測試「camera 有拍到、server 有收到、qwen35-fast 真的有被呼叫」。

## Camera

camera capture 是 memory-only one-shot subprocess：

```text
wake detected
-> start camera capture task
-> capture JPEG bytes
-> no permanent jpg/png saved
-> upload as multipart image field
```

預設建議解析度：

```text
320x240, JPEG quality 70
```

如果 camera busy、沒有接相機、OpenCV timeout，主流程不會 crash，會 fallback 純語音。

## Beep

wake 後會播放短 beep。程式會自動用 `--beep-keyword UACDemo` 找 USB speaker output。

關閉 beep：

```bash
--no-beep
```

不建議在正式 demo 用固定數字 output，例如 `--beep-device 24`。USB 重新插拔後，sounddevice index 可能會變，固定 index 會讓 beep 找錯裝置。正式跑請省略 `--beep-device`，保留預設：

```text
--beep-keyword UACDemo
```

## FRDM UART

目前支援 FRDM monitor commands：

```text
Sleep
Normal
ShowNum <0..999999>
MotorPitch <0..180>
MotorYaw <0..180>
```

預設 serial：

```text
--uart-port auto
baudrate: 115200
line ending: CRLF
```

只測 UART：

```bash
python3 frdm_uart_context_sender.py --list-ports
python3 frdm_uart_context_sender.py --command Sleep --port auto
python3 frdm_uart_context_sender.py --command Normal --port auto
python3 frdm_uart_context_sender.py --command "ShowNum 123" --port auto
```

## Preflight

```bash
python3 wake_voice_chat_frdm_bridge.py --list-mics
python3 wake_voice_chat_frdm_bridge.py --list-uarts
python3 wake_voice_chat_frdm_bridge.py --server-url http://100.108.141.26:8766/voice-chat --check-server --uart-dry-run --tts-debug
```

`--list-mics` 應看到 UACDemo input。`--list-uarts` 應看到 `/dev/ttyACM0` 或 `/dev/serial/by-id/...`。

## USB Replug Auto Discovery

正式 demo 請使用名稱或 `auto`，不要綁固定數字 index：

```text
mic       : --mic-keyword UACDemo
speaker  : --beep-keyword UACDemo, 不要加 --beep-device 24
TTS audio : /home/asrlab-yian/MakeNTU/jetson_piper_tts/.env 使用 AUDIO_DEVICE=auto:UACDemo
camera   : --camera-id auto
FRDM     : --uart-port auto
```

`wake_voice_chat_frdm_bridge.py` 會在每次開錄音 stream 前重新掃描 UACDemo input，wake 後播放 beep 前重新掃描 UACDemo output，camera one-shot capture 會在每次 wake 重新掃 `/dev/video*`，FRDM UART 則在每次送 command 前用 `--uart-port auto` 重新選 `/dev/ttyACM*`、`/dev/ttyUSB*` 或 `/dev/serial/by-id/*`。

啟動時看到這行代表是 replug-friendly 設定：

```text
USB auto-discovery: mic=keyword 'UACDemo'; beep=keyword 'UACDemo'; camera=auto; FRDM UART=auto
```

如果你手動傳了 `--device` 或 `--beep-device`，程式會提醒這是固定數字 index，重插 USB 後不保證穩。

## USB Recovery

Jetson USB controller 卡住、camera 卡住、程式停不了或裝置消失時：

```bash
cd /home/asrlab-yian/MakeNTU/frdm_uart_context_sender
./recover_demo_usb.sh
```

如果只想殺掉 bridge：

```bash
pkill -9 -f wake_voice_chat_frdm_bridge.py
```

## Troubleshooting

```text
Windows health debug_version 不是 9
-> Windows server 沒同步新版 desktop_fast_chat_server.py，或還在跑舊 process。

vision_intent=True 但 used_vision=False
-> 看 vision_error。通常是 image missing、server vision disabled、Ollama error。

image_received=False
-> Jetson 沒有送 image。檢查 camera log、--no-camera、--no-vision。

image_size_bytes=0
-> multipart 有 image 欄位但讀不到有效 bytes，檢查 camera capture。

calling vision model 沒出現
-> 沒走 vision path。看 vision_intent 和 vision_reason。

TTS ready 但沒聲音
-> 檢查 /home/asrlab-yian/MakeNTU/jetson_piper_tts/.env 的 AUDIO_DEVICE=auto:UACDemo，重開 TTS server。

Wake 被 ignore
-> 這是低音量保護。正式建議 --wake-volume-min 350；仍漏叫可降到 200，但誤觸發會增加。

FRDM 沒反應
-> 先跑 --list-uarts，再確認 J17 MCU-LINK USB-C、data cable、J18 open、baudrate 115200、line ending CRLF。
```
