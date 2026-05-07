# Jarvis Conversation Mode

這份 README 說明 MakeNTU 目前的 Jarvis 連續對話模式。

重要結論先放前面：這個資料夾原本是「只在一開始說 Hey Jarvis，後續不用重複喚醒詞」的原型。現在正式整合版已經放進原本完整橋接程式：

```text
/home/asrlab-yian/MakeNTU/frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py
```

正式 demo、相機、音樂、天氣、FRDM UART、TTS、one-wake conversation mode 都請跑上面這支。這個資料夾裡的 `jarvis_conversation_loop.py` 保留給單獨測麥克風、wake、ASR、TTS 的實驗用途，不是目前主流程。

## 功能目標

正式版現在支援：

```text
第一次互動      -> 必須說 Hey Jarvis
同一段對話後續  -> 不需要重複 Hey Jarvis
準備收音前      -> beep 一聲，提示可以開始說話
判定你講完時    -> 再 beep 一聲，並立刻拍照
送到桌機        -> audio + image 一起送 Windows /voice-chat
說 byebye/掰掰  -> 進入 Sleep，回到只聽喚醒詞
播音樂/繼續播放 -> 自動結束對話模式，進入 Sleep，下一次仍需 Hey Jarvis
暫停/停止音樂   -> 處理完也回到 wake-only standby，下一次仍需 Hey Jarvis
```

回到 standby 後，你講一般話不會送 ASR/Ollama，也不會回答。必須重新說 `Hey Jarvis`。

## 系統架構

```text
Jetson
  wake_voice_chat_frdm_bridge.py
  -> openWakeWord 偵測 Hey Jarvis
  -> sounddevice 收音
  -> camera warm reader 抓最新 JPEG
  -> local music/weather sidecar
  -> Piper TTS
  -> FRDM UART

Windows 桌機
  desktop_fast_chat_server.py
  -> ASR
  -> Ollama qwen35-fast
  -> vision model
  -> /voice-chat 回覆 JSON
```

資料流：

```text
Hey Jarvis
-> pause music if needed
-> start beep
-> record until silence / max_speech / max_recording
-> speech-end beep
-> capture image
-> POST audio + image to Windows /voice-chat
-> Windows ASR + Ollama / vision
-> Jetson handles music/weather if needed
-> TTS reply
-> FRDM screen/emotion/head motion
-> continue listening for follow-up or return to wake-only standby
```

## 相關檔案

```text
frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py
  正式主程式。請優先使用。

frdm_uart_context_sender/QUICK_START.md
  現場 demo 照貼指令。

frdm_uart_context_sender/README.md
  完整架構與詳細排錯。

emotion_robot_controller/voice_stt_remote/windows_desktop_server_bundle/desktop_fast_chat_server.py
  Windows 實際跑的 ASR/Ollama server bundle。

music_web_player/music_web_player.py
  Jetson 本地 /music + /weather sidecar。

jetson_piper_tts
  Jetson 本地 TTS server。

jarvis_conversation_mode/jarvis_conversation_loop.py
  舊的 standalone 原型，只建議做隔離測試。
```

## 啟動順序

請照這個順序開四個 terminal。

```text
1. Windows Terminal 1 : desktop_fast_chat_server.py
2. Jetson Terminal 2  : jetson_piper_tts.server
3. Jetson Terminal 4  : music_web_player.py
4. Jetson Terminal 3  : wake_voice_chat_frdm_bridge.py
```

如果我有改過 Windows server bundle，Windows Terminal 1 要重啟並使用最新的 `desktop_fast_chat_server.py`，不然 `fast_reply` / `num_predict` 的加速不會完整生效。

## Windows Terminal 1

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

健康檢查：

```powershell
Invoke-RestMethod http://127.0.0.1:8766/health
```

## Jetson Terminal 2: TTS

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
ENABLE_STREAM_PLAYBACK=true
```

健康檢查：

```bash
curl http://127.0.0.1:8777/health
```

## Jetson Terminal 4: Music / Weather

```bash
cd /home/asrlab-yian/MakeNTU/music_web_player
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate

python3 music_web_player.py \
  --server \
  --host 127.0.0.1 \
  --port 8788 \
  --backend mpv \
  --weather-default-location Taipei
```

健康檢查：

```bash
curl http://127.0.0.1:8788/health
```

`mpv` 會真的播放音樂，支援 pause/resume/stop。`browser` 只開搜尋頁，不適合正式 demo。

## Jetson Terminal 3: 正式 Jarvis Bridge

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
  --music-backend mpv \
  --music-timeout 5 \
  --music-wake-pause-timeout 0.6 \
  --music-wake-beep-settle 0.18 \
  --post-music-standby-cooldown 0.8 \
  --music-debug \
  --weather-default-location Taipei \
  --weather-timeout 6 \
  --weather-debug \
  --motor-step-delay 0.35 \
  --motor-reset-repeats 4 \
  --motor-reset-delay 0.22 \
  --motor-join-timeout 6 \
  --device-preflight-verbose \
  --conversation-mode \
  --ultra-response \
  --quiet-dialog \
  --tts-poll-interval 0.75 \
  --tts-debug \
  --uart-debug
```

如果 IP 有變，改這行：

```bash
--server-url http://100.108.141.26:8766/voice-chat
```

查 Jetson Tailscale IP：

```bash
tailscale ip -4
```

## 成功啟動時會看到

```text
Server health: debug_version=10, chat_ready=True, asr_loaded=True
TTS health: ready=True
Selected input device ... by keyword 'UACDemo'
Selected beep output device ... by keyword 'UACDemo'
Camera ready in continuous warm-reader mode.
Camera warm reader opened camera 0.
Conversation mode: enabled
Recording cues: start beep before each turn; speech-end beep + image capture before upload
Music tool: http://127.0.0.1:8788/music, backend=mpv->mpv
Listening for wake word 'hey_jarvis'
```

Global Shutter Camera 第一次開啟可能要 5 到 7 秒才有 frame。正式測 vision 前，請等看到：

```text
Camera warm reader opened camera 0.
```

## 一輪互動正常 log

```text
Wake detected: hey_jarvis score=...
Recording beep played.
Recording. Speak now; I will stop after silence.
Speech started.
Silence detected.
Recorded ...s; RMS=...
Speech-end capture beep played.
Speech-end image capture started.
POST audio+image to http://.../voice-chat
Round trip: ... ms
TTS started
TTS finished
Wake-only standby restored. Say Hey Jarvis before speaking again.
```

conversation mode 中，第一輪回答完後如果沒有說 `byebye`，會直接聽下一句 follow-up：

```text
Follow-up recording beep played.
Conversation listening for follow-up speech
```

## 測試腳本

### 1. 一般對話

```text
你：Hey Jarvis，講個笑話
Jarvis：回答
你：那你再講一個
Jarvis：不用再說 Hey Jarvis，也會回答
```

### 2. 結束對話

```text
你：掰掰
```

預期：

```text
Conversation end keyword detected
TTS skipped for end command.
FRDM UART TX: Sleep 0 0
Wake-only standby restored. Say Hey Jarvis before speaking again.
```

結束後你隨便講話，它不應該送 `/voice-chat`。下一次必須重新說：

```text
Hey Jarvis
```

### 3. 音樂

```text
你：Hey Jarvis，我想要聽告白氣球
```

預期：

```text
Music tool action=play
Music control action (play) handled
Post-music standby cooldown: 0.8s
Wake-only standby restored
```

播音樂後，如果要暫停、停止、換歌，也必須重新喚醒：

```text
Hey Jarvis，暫停音樂
Hey Jarvis，繼續播放音樂
Hey Jarvis，停止音樂
Hey Jarvis，換成七里香
```

### 4. Vision

```text
你：Hey Jarvis，我現在拿著什麼
```

預期：

```text
Speech-end capture beep played.
Speech-end image capture started.
POST audio+image ...
Vision routing:
  used_vision: True
```

如果想強制每次有圖都讓模型看圖，加：

```bash
--force-vision
```

## 重要參數

### 對話模式

```text
--conversation-mode
  一次 Hey Jarvis 後進入多輪對話。

--quiet-dialog
  Terminal 不印完整 transcript/reply，只保留 timing 和 tool log。

--speak-end-reply
  說掰掰後仍播放 AI 告別回覆。預設跳過，會比較快回 standby。

--no-sleep-on-conversation-end
  說掰掰後不送 Sleep，只恢復原 persistent state。

--keep-conversation-after-music-control
  音樂控制後不自動結束 conversation mode。正式 demo 不建議。
```

### 低延遲

```text
--ultra-response
  最快。silence=0.38s、max_speech=4s、turn_timeout=3s、TTS polling=0.1s，
  並送 fast_reply metadata 給 Windows server。

--turbo-response
  比 ultra 保守。silence=0.55s、max_speech=5s、turn_timeout=4s。

--fast-reply-num-predict 70
  給 Windows Ollama 的短回覆 token 上限提示。
```

如果 `--ultra-response` 太容易把一句話切太早，改用：

```bash
--turbo-response
```

### 錄音與環境音

```text
--wake-volume-min 350
  喚醒詞最低音量，避免很小聲的環境音誤喚醒。

--volume-min 700
  基本語音音量門檻。

--speech-start-margin 350
  底噪 + 多少才算開始說話。

--silence-margin 650
  底噪 + 多少以下才偏向 silence。

--silence-duration 1.2
  靜音持續多久算一句話結束。

--max-speech-seconds 5
  真正開始講話後最多錄幾秒。

--max-recording-seconds 7
  wake 後整輪硬上限，避免卡住。
```

現場很吵：

```bash
--speech-start-margin 500 --silence-margin 900
```

講話進不了錄音：

```bash
--speech-start-margin 250 --volume-min 500
```

### Beep

```text
--beep-duration-ms 120
--beep-frequency 880
--beep-volume 0.14
--beep-keyword UACDemo
```

目前有兩種 beep：

```text
Recording beep          -> 開始收音前，可以開始講話
Speech-end capture beep -> 判定講話結束，準備拍照上傳
```

如果播音樂後 UACDemo output 被 `mpv` 暫時佔住，程式會自動 retry default output：

```text
Retrying recording beep on default output.
```

### Camera / Vision

```text
--camera-id auto
--camera-width 320
--camera-height 240
--camera-jpeg-quality 70
--camera-latest-timeout 1.0
--camera-frame-max-age 2.0
--speech-end-image-delay 0
```

現在照片是在講話結束後拍，不是在開始講話前拍。

關閉 camera/vision：

```bash
--no-vision
```

只關 camera：

```bash
--no-camera
```

強制 vision：

```bash
--force-vision
```

### Music

```text
--music-backend mpv
--music-wake-pause-timeout 0.6
--music-wake-beep-settle 0.18
--post-music-standby-cooldown 0.8
```

`--music-wake-beep-settle` 是 wake 後先 pause 音樂，再等一點點才 beep，避免 mpv 還佔著音訊裝置。

`--post-music-standby-cooldown` 是播音樂後回 standby 前先等一下，避免音樂剛開始就被誤判成下一次 wake。

## Self-Test

Jetson bridge：

```bash
cd /home/asrlab-yian/MakeNTU
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate

python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py --self-test
```

Windows server bundle：

```bash
cd /home/asrlab-yian/MakeNTU/emotion_robot_controller/voice_stt_remote/windows_desktop_server_bundle
python3 desktop_fast_chat_server.py --self-test
```

Music sidecar：

```bash
cd /home/asrlab-yian/MakeNTU/music_web_player
python3 music_web_player.py --self-test
```

列麥克風：

```bash
python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py --list-mics
```

列 UART：

```bash
python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py --list-uarts
```

測 FRDM 頭部馬達：

```bash
python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py \
  --uart-port auto \
  --uart-debug \
  --test-head-motion nod
```

## Troubleshooting

### 叫 Hey Jarvis 沒反應

先確認 mic：

```bash
python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py --list-mics
```

應看到類似：

```text
UACDemoV1.0: USB Audio
```

如果沒有：

```bash
cd /home/asrlab-yian/MakeNTU/frdm_uart_context_sender
./recover_demo_usb.sh
```

### 喚醒後沒 beep

看 log：

```text
WARNING: recording beep failed
Retrying recording beep on default output.
```

如果常發生在播音樂後，把 settle 拉長：

```bash
--music-wake-beep-settle 0.35
```

不要固定 `--beep-device 24`。正式用：

```bash
--beep-keyword UACDemo
```

### 說完話沒有拍照

正常 log 應該有：

```text
Speech-end capture beep played.
Speech-end image capture started.
POST audio+image ...
```

如果看到 `audio only`：

```text
Speech-end image capture skipped or unavailable.
```

檢查：

```bash
ls /dev/video*
```

並確認沒有加：

```bash
--no-camera
--no-vision
```

### 說 byebye 後還會回答

正常不應該。確認沒有加：

```bash
--speak-end-reply
--keep-conversation-after-music-control
--no-wake-word
```

說 byebye 後應看到：

```text
Conversation end keyword detected
TTS skipped for end command.
Wake-only standby restored.
```

### 播音樂後下一次又自動開始聽

正式版會在 music play/resume 後：

```text
Post-music standby cooldown: 0.8s
Wake-only standby restored.
```

如果音樂太大聲造成 wake 誤判：

```bash
--wake-threshold 0.8 --wake-volume-min 500
```

或把喇叭音量降一點。

### 回覆太慢

分段看 log：

```text
Silence detected 很晚
  -> 錄音結束太慢，調低 --silence-duration 或用 --ultra-response

Round trip 很久
  -> Windows ASR/Ollama 慢，確認 Windows server 是最新版並重啟

TTS 很久
  -> TTS 播放或 queue 等待久，確認 TTS server health
```

完整低延遲建議：

```bash
--conversation-mode --ultra-response --quiet-dialog
```

Windows server 必須是最新版，否則 `fast_reply` 只會在 Jetson 端部分生效。

## Standalone 原型用法

只有當你想隔離測「麥克風 + wake + ASR + TTS」，才跑這個資料夾的原型：

```bash
cd /home/asrlab-yian/MakeNTU/jarvis_conversation_mode
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate

python3 jarvis_conversation_loop.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --tts-url http://127.0.0.1:8777/speak_async \
  --mic-keyword UACDemo \
  --turbo-response \
  --quiet-dialog \
  --wake-threshold 0.75 \
  --wake-volume-min 350 \
  --volume-min 700 \
  --silence-margin 650 \
  --tts-debug
```

Standalone 限制：

```text
不控制 FRDM UART
不做 camera / vision upload
不處理 music / weather
只適合單獨測 conversation recorder
```

正式整合測試請回到：

```bash
cd /home/asrlab-yian/MakeNTU/frdm_uart_context_sender
python3 wake_voice_chat_frdm_bridge.py ...
```

## Demo Checklist

```text
[ ] Windows /health 正常，debug_version 正確
[ ] Ollama 有 qwen35-fast:latest
[ ] TTS /health ready=True
[ ] Terminal 4 music/weather server 正常
[ ] UACDemo mic/speaker 都看得到
[ ] Camera warm reader opened camera
[ ] FRDM UART auto 找得到
[ ] Wake bridge self-test OK
[ ] 說 Hey Jarvis 會 beep
[ ] 講完會再 beep + 拍照
[ ] POST audio+image
[ ] byebye 後 Sleep + wake-only standby
[ ] 播音樂後下一次仍需要 Hey Jarvis
```
