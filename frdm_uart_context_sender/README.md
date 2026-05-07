# FRDM Wake Voice Chat + Vision Bridge

這個資料夾是 MakeNTU 桌上寵物機器人的 Jetson 整合層。正式操作請先看 [QUICK_START.md](QUICK_START.md)；本 README 用來理解架構、資料流、控制格式與除錯。

## Read This First

```text
只想啟動 demo        -> 看 QUICK_START.md 的「0. 必跑指令總覽」
要理解架構          -> 看本 README 的 What This Does / Data Flow
要改 prompt/control  -> 看 Structured Reply And Control
要修 FRDM 行為       -> 看 FRDM UART Timing / Emotion And Head Motion
要修 camera/vision   -> 看 Vision Routing / Camera And Image Storage
要排錯              -> 看 Debug Log Guide / Troubleshooting
```

目前穩定版重點：

```text
model                 : qwen35-fast:latest
server                : http://100.108.141.26:8766/voice-chat
wake                  : hey_jarvis, threshold=0.75
recording             : adaptive gate + callback audio queue
max_speech_seconds    : 5
max_recording_seconds : 7
audio_read_timeout    : 0.75
camera                : auto, 320x240, JPEG quality 70, memory-only
uart                  : auto, 115200, CRLF
tts                   : local Piper /speak_async, AUDIO_DEVICE=auto:UACDemo
```

不要固定 USB 數字 index。重插 USB 後 `--device 25`、`--beep-device 24`、`--camera-id 0` 都可能失效；正式 demo 用 keyword 和 auto。

## What This Does

```text
Hey Jarvis wake word
-> short beep
-> camera JPEG capture in memory
-> record speech until silence
-> POST audio + optional image to Windows /voice-chat
-> Windows ASR transcript
-> rule-based vision intent routing
-> qwen35-fast:latest text or vision response
-> Jetson parses reply/control
-> TTS speaks natural reply
-> FRDM UART controls screen/emotion/head motion
```

不使用 Gemini、OpenAI 或雲端 API。ASR 與 Ollama 在 Windows 桌機本機；wake word、camera、TTS、UART 在 Jetson 本機。

## Table Of Contents

```text
Files
Standard Startup
Windows Server
Structured Reply And Control
FRDM UART Timing
Emotion And Head Motion
Vision Routing
Camera And Image Storage
TTS Playback Completion
USB Replug Auto Discovery
Self-Test And Preflight
Debug Log Guide
Troubleshooting
Demo Checklist
```

## Files

```text
wake_voice_chat_frdm_bridge.py   # 正式 Hey Jarvis hands-free demo
voice_chat_frdm_uart_bridge.py   # 手動 Enter 錄音版本
frdm_uart_context_sender.py      # 單獨測 FRDM UART command
recover_demo_usb.sh              # Jetson USB host controller recovery
QUICK_START.md                   # 現場操作手冊
```

Windows server 檔案：

```text
emotion_robot_controller/voice_stt_remote/desktop_fast_chat_server.py
emotion_robot_controller/voice_stt_remote/windows_desktop_server_bundle/desktop_fast_chat_server.py
```

Windows 桌面實際執行的是 bundle 版本，所以 server 有改時需要 scp 同步。

## Standard Startup

完整可複製三個 terminal 指令在 [QUICK_START.md](QUICK_START.md) 最前面。核心參數如下：

請不要手打最後幾個參數，最常見錯誤是把 `--tts-poll-interval 0.75` 打成 `--uart-debug\terval 0.75`。正確尾端是：

```bash
  --tts-poll-interval 0.75 \
  --tts-debug \
  --uart-debug
```

```bash
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

正式 demo 不要固定 USB index：

```text
不要用 --device 25
不要用 --beep-device 24
不要用 --camera-id 0
不要用 --uart-port /dev/ttyACM0
```

請用：

```text
mic       : --mic-keyword UACDemo
beep      : --beep-keyword UACDemo
TTS audio : AUDIO_DEVICE=auto:UACDemo
camera    : --camera-id auto
FRDM      : --uart-port auto
```

每次啟動會先做 device preflight：

```text
stop old wake bridge / camera test processes
stop stale arecord / aplay / mpv / ffplay / paplay
if UACDemo/camera/FRDM disappeared, reset Jetson USB host
scan /dev/video*, UACDemo /dev/snd/pcm*, /dev/ttyACM*
stop same-user processes that still own those device nodes
keep jetson_piper_tts.server alive
keep pulseaudio / pipewire / wireplumber by default
```

相關參數：

```bash
--no-device-preflight
--device-preflight-only
--device-preflight-dry-run
--device-preflight-verbose
--device-preflight-keep-music
--kill-audio-servers
--no-usb-reset-if-missing
--usb-controller 3610000.usb
--usb-reset-wait 6
--device-ready-timeout 12
```

USB reset 後，ALSA device node 可能先出現，但 PortAudio/sounddevice 還沒刷新。`--device-ready-timeout` 會持續等到 sounddevice 真的看見 `UACDemo` input/output，再進主流程。

錄音 gate 是動態的，不是只靠固定音量門檻。wake 前會估計環境底噪，wake 後使用：

```text
speech_start_threshold = max(volume_min, noise_floor + speech_start_margin)
silence_threshold      = max(volume_min, noise_floor + silence_margin, peak_volume * silence_peak_ratio)
```

預設值：

```text
volume_min=700
speech_start_margin=350
silence_margin=650
silence_peak_ratio=0.35
pre_speech_seconds=0.35
max_speech_seconds=5
max_recording_seconds=7
audio_read_timeout=0.75
recording_progress_interval=1.0
tts_poll_interval=0.75
```

這樣背景噪音偏高時，錄音也會在音量回到底噪附近後停止，不會只因背景音大於 `volume_min` 就一直錄到 `max_speech_seconds`。`max_speech_seconds` 是 speech started 之後的上限；`max_recording_seconds` 是 wake 後整輪錄音硬上限，就算一直沒進入 speech started 也會退出回 standby。音訊讀取使用 callback queue，不再卡在 blocking read；`audio_read_timeout` 會在 USB mic 停止吐 chunk 時讓本輪錄音退出或重開 stream；`recording_progress_interval` 控制錄音狀態 log 頻率。

吵場地調參順序：

```text
一直錄到 max_speech_seconds -> 先加 --silence-margin 800
背景音直接觸發 Speech started -> 加 --speech-start-margin 450
你講話也進不了 Speech started -> 降 --speech-start-margin 250
demo 要更快回覆 -> 降 --max-speech-seconds 4
現場太吵導致等待/錄音像卡住 -> 降 --max-recording-seconds 7 或 6
完全停在 Recording. Speak now -> 降 --audio-read-timeout 0.75，並讓 bridge 重開 stream
```

錄音狀態判讀：

```text
phase=waiting_speech -> wake 已接受，正在等你的音量高過 start threshold
phase=speech         -> 已經錄到人聲，等 silence/max_speech/max_recording
Max recording...     -> 硬上限保護，避免整輪卡住
no audio chunk       -> USB mic stream 停吐，callback watchdog 會退出當輪
```

## Windows Server

同步最新版 server 到 Windows：

```powershell
scp asrlab-yian@100.110.90.72:/home/asrlab-yian/MakeNTU/emotion_robot_controller/voice_stt_remote/windows_desktop_server_bundle/desktop_fast_chat_server.py "$env:USERPROFILE\Desktop\windows_desktop_server_bundle\desktop_fast_chat_server.py"
```

啟動：

```powershell
cd "$env:USERPROFILE\Desktop\windows_desktop_server_bundle"
.\.venv\Scripts\Activate.ps1
ollama pull qwen35-fast:latest
python desktop_fast_chat_server.py --host 0.0.0.0 --port 8766 --ollama-model qwen35-fast:latest --vision-model qwen35-fast:latest --no-think
```

Health 必須看到：

```text
debug_version: 10
chat_ready   : True
asr_loaded   : True
ollama_model : qwen35-fast:latest
vision_model : qwen35-fast:latest
```

如果 `debug_version` 不是 10，代表 Windows 還在跑舊檔或舊 process。

## Structured Reply And Control

Windows `qwen35-fast:latest` 被要求只回傳一個 JSON object：

```json
{
  "reply": "自然語言回覆，給 TTS 播放。不可提到 JSON、UART、MotorPitch、MotorYaw 或內部控制欄位。",
  "control": {
    "persistent_state": "normal | sleep | unchanged",
    "emotion": "neutral | happy | curious | excited | confused | concerned | sleepy",
    "head_motion": "none | nod | double_nod | look_around | shake | gentle_nod | sleepy_drop",
    "reason": "簡短內部理由，不給使用者播放"
  }
}
```

Jetson robust parser：

```text
合法 JSON                  -> 使用 reply/control
前後混入文字              -> 抽第一個 JSON object
server 回舊欄位 uart       -> normalize 成 control
reply 裡混入 JSON/control  -> 抽出自然 reply，不讓 TTS 唸控制資訊
parse 失敗                -> 自然 fallback reply + neutral/none/unchanged
```

`reply` 永遠是給使用者聽的自然語言；`control` 永遠是內部控制。

## FRDM UART Timing

正式時序：

```text
Wake detected
-> beep
-> Thinking 0 0
-> user speech / recording / image capture / upload / ASR / Ollama
-> receive reply/control
-> Speaking 0 0
-> emotion screen command, e.g. Happy 0 0
-> TTS starts
-> head motion thread starts
-> TTS finishes or estimated finished
-> restore Normal 0 0 or Sleep 0 0
```

Jetson 會把 `Speaking 0 0` 和 emotion command 合併在同一次 serial session 送出，降低 TTS 前的 UART 等待；motor 動作仍在獨立 thread 執行，不阻塞 TTS。

Persistent state：

```text
normal
sleep
```

Temporary screen state：

```text
Thinking
Speaking
```

Sleep intent examples：

```text
去睡覺
睡覺吧
休息一下
晚安
安靜一下
sleep
go to sleep
standby
```

Wake/normal intent examples：

```text
起床
醒來
回來
回到正常
不要睡了
wake up
come back
normal
```

## Emotion And Head Motion

Emotion screen command mapping：

```text
neutral   -> Neutral
happy     -> Happy
curious   -> Curious
excited   -> Excited
confused  -> Confused
concerned -> Concerned
sleepy    -> Sleepy
```

Head motion fallback：

```text
neutral   -> none
happy     -> nod
curious   -> look_around
excited   -> double_nod
confused  -> shake
concerned -> gentle_nod
sleepy    -> sleepy_drop
```

Motor safety：

```text
MOTOR_MIN=-15
MOTOR_MAX=15
MOTOR_STEP_DELAY_SEC=0.08
```

所有 `MotorPitch` / `MotorYaw` 都會 clamp 到安全範圍。動作結束一定回原位：

```text
MotorPitch 0 0
MotorYaw 0 0
```

目前允許送給 FRDM 的 command：

```text
Sleep
Normal
Thinking
Speaking
ShowNum
MotorPitch
MotorYaw
Neutral
Happy
Curious
Excited
Confused
Concerned
Sleepy
```

未來情緒 command 若 FRDM 還沒實作，Jetson 不會 crash；之後在 FRDM monitor command list 補 handler 即可。

## Vision Routing

Windows server 先做 ASR，拿到 `transcript` 後才判斷是否需要圖片。

核心函式：

```python
detect_vision_intent(transcript: str) -> tuple[bool, str]
should_use_vision(transcript: str) -> bool
```

策略是高召回率 rule-based，不額外呼叫 LLM。只要回答需要目前鏡頭畫面，就使用 vision path。

會使用 vision：

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

不使用 vision：

```text
幫我開電風扇
關燈
切換安靜模式
今天幾號
講個笑話
解釋 PID 控制
馬達往前走
```

混合句只要有視覺需求就走 vision：

```text
看一下燈有沒有開，沒有的話幫我開燈
我現在是不是在笑，然後把螢幕切成開心表情
```

Vision mode priority：

```text
--no-vision     disables camera and vision
--force-vision  forces vision whenever image exists
auto            detect_vision_intent(transcript)
```

## Camera And Image Storage

正式 bridge 的 camera capture 是 memory-only：

```text
program startup
-> continuous camera warm reader opens /dev/video*
-> latest JPEG bytes kept in memory
wake detected
-> copy latest in-memory JPEG
-> JPEG bytes in memory
-> multipart/form-data image field
-> Windows reads image bytes in memory
-> no permanent jpg/png saved by bridge
```

正式建議：

```text
320x240
JPEG quality 70
camera-id auto
continuous warm reader enabled
latest timeout 1.0s
frame max age 2.0s
```

Global Shutter Camera 第一次開啟可能要 5 到 7 秒才吐出第一張 frame，所以正式 demo 啟動後請等 log 出現：

```text
Camera warm reader opened camera 0.
```

如果 camera timeout、busy、未接上，或 warm reader 還沒有 fresh frame，流程不 crash，會送 audio only。若 transcript 需要 vision 但 image 缺失，server 會 log `vision_error` 並 fallback 純文字回答。

除錯可用：

```bash
--camera-one-shot
--camera-read-timeout 7
--camera-result-timeout 1
```

注意：`vision/camera_ollama_status.py` 是 standalone 測試程式，可能會把測試照片存到 `vision/`；正式 bridge 不會永久存照片。

## TTS Playback Completion

Jetson 預設使用：

```text
http://127.0.0.1:8777/speak_async
```

流程：

```text
POST /speak_async -> get job_id
poll /queue until job_id appears in last_result
if queue status unavailable -> estimate by reply length
restore Normal/Sleep after TTS finished or estimated finished
```

可調 timeout 與 polling 頻率：

```bash
--tts-playback-timeout 45
--tts-poll-interval 0.75
```

`--tts-poll-interval` 預設 0.75 秒，避免 TTS server terminal 因每 0.2 秒 `/queue` access log 而洗版。若想更安靜可調到 `1.0`，若想更快恢復 Normal/Sleep 可調到 `0.5`。

TTS `.env` 建議：

```text
AUDIO_DEVICE=auto:UACDemo
ENABLE_STREAM_PLAYBACK=true
```

USB recovery 或重插 speaker 後，建議重開 TTS server。

## USB Replug Auto Discovery

Bridge 在這些時機重新掃設備：

```text
每次開錄音 stream 前 -> 找 UACDemo input
每次 beep 前          -> 找 UACDemo output
每次 wake capture     -> 掃 /dev/video*
每次 UART send        -> 找 FRDM serial
```

啟動時看到這行代表設定正確：

```text
USB auto-discovery: mic=keyword 'UACDemo'; beep=keyword 'UACDemo'; camera=auto; FRDM UART=auto
```

如果 USB controller 掉線、`lsusb` 看不到 UACDemo/camera/FRDM：

```bash
cd /home/asrlab-yian/MakeNTU/frdm_uart_context_sender
./recover_demo_usb.sh
```

成功應看到：

```text
Jieli Technology UACDemoV1.0
Global Shutter Camera
NXP Semiconductors MCU-LINK FRDM-MCXN947
/dev/video0 /dev/video1
/dev/ttyACM0
```

## Self-Test And Preflight

Jetson self-test：

```bash
cd /home/asrlab-yian/MakeNTU/frdm_uart_context_sender
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate
python3 wake_voice_chat_frdm_bridge.py --self-test
```

Windows self-test：

```powershell
cd "$env:USERPROFILE\Desktop\windows_desktop_server_bundle"
.\.venv\Scripts\Activate.ps1
python desktop_fast_chat_server.py --self-test
```

Device/server checks：

```bash
python3 wake_voice_chat_frdm_bridge.py --list-mics
python3 wake_voice_chat_frdm_bridge.py --list-uarts
python3 wake_voice_chat_frdm_bridge.py --server-url http://100.108.141.26:8766/voice-chat --check-server --uart-dry-run --tts-debug
```

`--list-mics` 應看到 UACDemo input；`--list-uarts` 應看到 `/dev/ttyACM0` 或 `/dev/serial/by-id/...`。

## Debug Log Guide

啟動成功關鍵 log：

```text
Server health: debug_version=10, chat_ready=True, asr_loaded=True
TTS health: ready=True, audio device=UACDemo
Selected input device ... by keyword 'UACDemo'
Selected beep output device ... by keyword 'UACDemo'
Camera ready in continuous warm-reader mode
Audio read watchdog: callback queue, timeout=0.75s
USB auto-discovery: mic=keyword 'UACDemo'; ...; FRDM UART=auto
```

Jetson upload：

```text
Image captured: 13001 bytes
POST audio+image ... (vision_mode=auto, image_size_bytes=13001)
```

Vision summary：

```text
Vision routing:
  normalized_transcript : ...
  vision_intent         : True/False
  vision_reason         : keyword:... or pattern:...
  used_vision           : True/False
  image_received        : True/False
  image_size_bytes      : ...
  vision_model          : qwen35-fast:latest
  vision_error          : ...
```

Windows server vision path：

```text
voice-chat xxxx: transcript='我手上拿什麼'
voice-chat xxxx: normalized_transcript='我手上拿什麼'
voice-chat xxxx: vision_intent=True reason=keyword:我手上 ...
voice-chat xxxx: calling vision model=qwen35-fast:latest image_bytes=...
```

UART timing：

```text
FRDM UART TX: Thinking 0 0
FRDM UART TX: Speaking 0 0
FRDM UART TX: Happy 0 0
FRDM UART TX: MotorPitch ...
FRDM UART TX: Normal 0 0
```

Recording wait：

```text
Recording thresholds: noise_floor=900, speech_start_threshold=1250, silence_base_threshold=1080, adaptive=on
Recording progress: phase=waiting_speech, elapsed=..., volume=..., start_threshold=..., wake_timeout_in=...
Speech started. volume=...
Silence detected. volume=..., silence_threshold=..., peak=...
```

表示 wake 成功，但音量還沒高過「底噪 + speech_start_margin」。靠近 mic，或降低 `--speech-start-margin`。
如果已經 `Speech started` 但沒有 `Silence detected`，代表背景音或回音還在高於 silence threshold，優先增加 `--silence-margin` 或縮短 `--max-speech-seconds`。

## Troubleshooting

```text
No microphone matching UACDemo
-> Jetson 沒看到 USB mic。跑 lsusb / --list-mics；若 lsusb 看不到 UACDemo，跑 ./recover_demo_usb.sh。

Recording 後像卡住
-> 如果顯示 phase=waiting_speech，代表還沒高過 start threshold；可把 --speech-start-margin 350 降到 250。
-> 如果已經 Speech started 但停不下來，可把 --silence-margin 650 提到 800，或把 --max-speech-seconds 5 降到 4。
-> 如果現場一直有風扇/人聲，直接用 --speech-start-margin 450 --silence-margin 650 --max-speech-seconds 5 --max-recording-seconds 7 --audio-read-timeout 0.75 --recording-progress-interval 1.0。
-> `--max-speech-seconds` 只在 Speech started 後生效；要防止整輪卡住請用 `--max-recording-seconds`。
-> 如果完全沒有 Recording progress，代表舊版 blocking read 卡住或 USB mic stream 停吐；新版會印 WARNING 並退出當輪。

Wake 被 ignore
-> 低音量保護。正式用 --wake-volume-min 350；仍漏叫可降到 200。

Camera timeout
-> 不會 crash。跑 lsusb / ls -l /dev/video* / ./recover_demo_usb.sh。
-> 如果 /dev/video0 存在但 image_received=False，等 5 到 7 秒讓 warm reader 拿到第一張 frame。

Windows health timeout
-> 確認 Windows server terminal 開著、Tailscale IP 正確、port 8766 沒被舊 process 占用。

Ollama WinError 10061 / connection refused
-> Windows server 開著，但 Windows Ollama 沒開。PowerShell 跑 curl.exe http://127.0.0.1:11434/api/tags；失敗就 Start-Process -FilePath "ollama" -ArgumentList "serve"。

debug_version 不是 10
-> 重新 scp 同步 Windows bundle，關掉舊 server，重開。

TTS ready 但沒聲音
-> curl /health，確認 AUDIO_DEVICE=auto:UACDemo 和 audio.device 是 UACDemo；重開 TTS。

vision_intent=True 但 used_vision=False
-> 看 image_received / image_size_bytes / vision_error。

FRDM 沒反應
-> --list-uarts、lsusb、/dev/ttyACM*；確認 MCU-LINK USB-C、data cable、baudrate 115200、CRLF。
```

## Demo Checklist

Windows：

```text
[ ] desktop_fast_chat_server.py 已同步
[ ] ollama list 有 qwen35-fast:latest
[ ] /health debug_version=10
[ ] vision_model=qwen35-fast:latest
```

Jetson：

```text
[ ] TTS /health ready=true
[ ] TTS audio device 是 UACDemo
[ ] --list-mics 有 UACDemo input
[ ] --list-uarts 有 FRDM
[ ] lsusb 有 UACDemo / Global Shutter Camera / MCU-LINK
[ ] wake bridge self-test OK
[ ] 純語音 used_vision=False
[ ] 視覺句 used_vision=True
[ ] FRDM UART TX/RX 正常
```
