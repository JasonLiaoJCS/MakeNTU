# FRDM Wake Voice Chat + Vision Bridge

這個資料夾是 MakeNTU 桌上寵物機器人的 Jetson 整合層。正式操作請先看 [QUICK_START.md](QUICK_START.md)；本 README 用來理解架構、資料流、控制格式與除錯。

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

```bash
python3 wake_voice_chat_frdm_bridge.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --mic-keyword UACDemo \
  --uart-port auto \
  --wake-threshold 0.75 \
  --wake-volume-min 350 \
  --volume-min 700 \
  --silence-duration 1.2 \
  --camera-id auto \
  --camera-width 320 \
  --camera-height 240 \
  --camera-jpeg-quality 70 \
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
wake detected
-> one-shot camera subprocess
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
```

如果 camera timeout、busy 或未接上，流程不 crash，會送 audio only。若 transcript 需要 vision 但 image 缺失，server 會 log `vision_error` 並 fallback 純文字回答。

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

可調 timeout：

```bash
--tts-playback-timeout 45
```

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
Waiting for speech... volume=xxx < volume_min=700
```

表示 wake 成功，但錄音音量還沒超過 speech threshold。靠近 mic 或降低 `--volume-min`。

## Troubleshooting

```text
No microphone matching UACDemo
-> Jetson 沒看到 USB mic。跑 lsusb / --list-mics；若 lsusb 看不到 UACDemo，跑 ./recover_demo_usb.sh。

Recording 後像卡住
-> 看 Waiting for speech log。可把 --volume-min 700 降到 500。

Wake 被 ignore
-> 低音量保護。正式用 --wake-volume-min 350；仍漏叫可降到 200。

Camera timeout
-> 不會 crash。跑 lsusb / ls -l /dev/video* / ./recover_demo_usb.sh。

Windows health timeout
-> 確認 Windows server terminal 開著、Tailscale IP 正確、port 8766 沒被舊 process 占用。

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
