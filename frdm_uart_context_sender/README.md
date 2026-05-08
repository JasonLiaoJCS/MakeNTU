# FRDM Wake Bridge + Vision + Focus + To-Do + Music + Weather

這個資料夾是 MakeNTU 桌上寵物機器人的 Jetson 整合層。正式操作請先看 [QUICK_START.md](QUICK_START.md)；本 README 用來理解架構、資料流、控制格式與除錯。

## Read This First

```text
只想啟動 demo        -> 看 QUICK_START.md 的「0. 一頁照貼版」
要理解架構          -> 看本 README 的 What This Does / FRDM State Machine
要改 prompt/control  -> 看 Structured Reply And Control
要確認 FRDM 狀態機    -> 看 FRDM State Machine
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
boot normal delay     : 2 seconds, then Normal 0 0
max_speech_seconds    : 5
max_recording_seconds : 7
audio_read_timeout    : 0.75
camera                : auto, 320x240, JPEG quality 70, memory-only
image_capture         : after end-of-speech beep, before upload
uart                  : auto, 115200, CRLF
tts                   : local Piper /speak_async, AUDIO_DEVICE=plughw:CARD=UACDemoV10,DEV=0
tts volume            : --tts-volume-gain 2.25, server accepts volume_gain 0.05..8.0
music/weather         : local tool server on 127.0.0.1:8788, mpv + Open-Meteo
to-do list            : local JSON voice tool, frdm_uart_context_sender/logs/todo_list.json
focus work mode       : voice-triggered start/stop, periodic /focus-check, JSONL log + Markdown report
```

不要固定 USB 數字 index。重插 USB 後 `--device 25`、`--beep-device 24`、`--camera-id 0` 都可能失效；正式 demo 用 keyword 和 auto。

目前現場音量採保守值，避免 TTS 或 beep 嚇到旁邊的人：

```text
TTS .env default      : DEFAULT_VOLUME_GAIN=2.25
Wake Bridge default   : --tts-volume-gain 2.25
PulseAudio USB sink   : about 80%
Too loud              : try 2.0 and --beep-volume 0.35
Too quiet             : try 3.0 before going higher
```

改過這份 repo 後，最容易忘記的是重啟哪個 terminal：

```text
Windows server / prompt / emotion 改了    -> 重啟 Windows Terminal 1
TTS server / volume_gain 改了             -> 重啟 Jetson Terminal 2
Wake Bridge / UART / routing / docs 改了  -> 重啟 Jetson Terminal 3
Music/weather intent 改了                 -> 重啟 Jetson Terminal 4
```

## What This Does

```text
Bridge process starts
-> FRDM startup waits 2 seconds, then Normal 0 0
Hey Jarvis wake word
-> short beep
-> Thinking 0 0
-> record speech until silence
-> end-of-speech beep + camera JPEG capture in memory
-> POST audio + optional image to Windows /voice-chat
-> Windows ASR transcript
-> Jetson local to-do list if transcript asks for it
-> Jetson local tool routing for music/weather if transcript asks for it
-> focus work mode start/stop if transcript asks for work mode
-> rule-based vision intent routing
-> qwen35-fast:latest text or vision response
-> Jetson parses reply/control
-> Speaking <emotion_code>
-> TTS speaks natural reply
-> head motor motion runs while TTS speaks
-> next FRDM mode: Thinking / Normal / Sleep / Music / Focus
```

Focus work mode is a side mode. When the transcript is a work-mode command, the wake bridge starts `focus_work_mode.py` as a separate process, samples the camera every 60 seconds by default, posts each image to Windows `/focus-check`, writes `focus_log.jsonl`, then generates `focus_summary.json` and `focus_report.md` when the session ends. Photos are memory-only by default; use `--focus-save-images` only for debugging.

不使用 Gemini、OpenAI 或雲端 API。ASR 與 Ollama 在 Windows 桌機本機；wake word、camera、TTS、UART 在 Jetson 本機。
天氣查詢使用 Jetson 本地 tool server 呼叫 Open-Meteo；這不是 LLM 猜測，也不需要 API key。音樂串流使用 Jetson 本地 `mpv`/`yt-dlp`，不經 Windows 桌機。

## FRDM State Machine

目前 FRDM firmware 只需要支援這些 command：

```text
Sleep
Normal
Thinking
Speaking
Music
Focus
ShowNum
MotorPitch
MotorYaw
```

Wake Bridge 會拒絕舊版情緒畫面 command，例如 `Happy 0 0`、`Curious 0 0`。情緒統一改塞進 `Speaking` 的第一個參數。

```text
bridge startup        -> wait 2s -> Normal 0 0
Hey Jarvis detected   -> Thinking 0 0
AI/TTS starts         -> Speaking <0..5>
TTS speaking          -> MotorPitch <angle>, MotorYaw <angle>
follow-up listening   -> Thinking 0 0
掰掰/拜拜/再見        -> Normal 0 0, then wake-only standby
睡覺/休息/晚安        -> Sleep 0 0, then wake-only standby
播放/繼續音樂         -> Music 0 0, then wake-only standby
暫停/停止音樂         -> Normal 0 0, then wake-only standby
專注/專心/工作模式    -> Focus 0 0, then wake-only standby
回來/回到正常         -> Normal 0 0
```

`Speaking`、`MotorPitch`、`MotorYaw` 都是單參數 wire format：

```text
Speaking 2
MotorPitch 90
MotorYaw 90
```

其他畫面 command 保留兩個參數：

```text
Thinking 0 0
Normal 0 0
Sleep 0 0
Music 0 0
Focus 0 0
```

## Table Of Contents

```text
Files
Standard Startup
FRDM State Machine
Music Routing And Playback
Weather Routing
To-Do List
Focus Work Mode
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
focus_work_mode.py               # 專心工作模式子程式，由 wake bridge 啟動/停止
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

完整可複製 Terminal 1/2/4/3 指令在 [QUICK_START.md](QUICK_START.md) 最前面。核心參數如下：

請不要手打最後幾個參數，最常見錯誤是把 `--tts-poll-interval 0.75` 打成 `--uart-debug\terval 0.75`。正確尾端是：

```bash
  --tts-poll-interval 0.75 \
  --tts-debug \
  --uart-debug
```

```bash
cd /home/asrlab-yian/MakeNTU
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate

python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --mic-keyword UACDemo \
  --beep-keyword UACDemo \
  --noisy-room \
  --tts-volume-gain 2.25 \
  --uart-port auto \
  --uart-baudrate 115200 \
  --enable-head-motor \
  --boot-normal-delay 2.0 \
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
  --focus-duration-min 0 \
  --focus-log-root /tmp/focus_voice_test \
  --focus-alert-threshold 2 \
  --todo-list-path /home/asrlab-yian/MakeNTU/frdm_uart_context_sender/logs/todo_list.json \
  --focus-notify-mode discord \
  --focus-discord-webhook-url "$DISCORD_WEBHOOK_URL" \
  --music-backend mpv \
  --music-timeout 5 \
  --music-wake-pause-timeout 0.6 \
  --music-wake-beep-settle 0.18 \
  --post-music-standby-cooldown 0.8 \
  --music-debug \
  --weather-default-location Taipei \
  --weather-timeout 6 \
  --weather-debug \
  --motor-step-delay 0.80 \
  --motor-smooth-step-deg 10 \
  --motor-speaking-step-delay 0.75 \
  --motor-speaking-smooth-step-deg 60 \
  --motor-reset-repeats 4 \
  --motor-reset-delay 0.35 \
  --motor-stop-timeout 6 \
  --motor-join-timeout 6 \
  --device-preflight-verbose \
  --tts-poll-interval 0.75 \
  --tts-debug \
  --uart-debug
```

這是完整功能版：wake word、conversation mode、speech-end 拍照、FRDM UART、TTS、To-Do、Music、Weather、Focus Work Mode 都會開。第一次 `Hey Jarvis` 後會連續聽 follow-up；說 `byebye / 掰掰 / 拜拜 / 再見`、叫它睡覺、音樂控制結束、或 focus mode 指令處理完後，會回到 wake-only standby。

如果只想一問一答，把 `--conversation-mode`、`--turn-listen-timeout`、`--session-idle-timeout`、`--max-session-turns` 拿掉。若想再壓低延遲，可以額外加 `--ultra-response`；如果講話中間常停頓被太早切句，改用比較保守的 `--turbo-response`。

`fast_reply / num_predict` 需要 Windows Terminal 1 也使用最新版 `desktop_fast_chat_server.py` 並重啟；如果 Windows 還是舊 server，只會套用 Jetson 端的錄音/TTS/camera 加速。

流程會變成：啟動後 FRDM 先顯示開機畫面，預設等 2 秒送 `Normal 0 0`；第一次 `Hey Jarvis` 喚醒後送 `Thinking 0 0`，後續 follow-up 不需要再說喚醒詞。每次準備收下一句前會先 beep 讓你知道可以講話，判定你講完時會再 beep 一聲，並在那一刻抓一張照片，跟該輪語音一起送到 Windows。開始 TTS 前會送 `Speaking <emotion_code>`，TTS 期間依情緒跑頭部馬達，TTS 結束後若仍在連續對話會回 `Thinking 0 0` 等下一句。說 `byebye / 掰掰 / 拜拜 / 再見` 後會送 `Normal 0 0`，並回到 wake-only standby。follow-up timeout 也會回 `Normal 0 0`。回到 standby 後，一般講話不會送 ASR/Ollama，必須重新說 `Hey Jarvis`。

現場吵雜時保留 `--noisy-room`。它會把 beep 調成更大聲、更長，並提高 speech/silence gate。TTS 太小聲時保留 `--tts-volume-gain 2.25`；若仍偏大降到 `2.0`，仍太小再回到 `3.0`。只測 beep 可跑：

```bash
python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py --beep-keyword UACDemo --noisy-room --test-beep
```

若背景音量平均約 `10000`、講話約 `19000`，`--noisy-room` 會自動讓門檻接近：

```text
wake accept volume >= 13500
speech start       >= 14500
speech end/silence <= 13000
```

這組是刻意讓背景聲低於 speech start，但仍高於 silence end 的可結束區間。

openWakeWord 的 score 有時會比音量峰值晚幾個 chunk 才出現，所以 wake gate 不是只看當下 `volume`，而是看最近 1 秒的 `recent_peak`。如果 standby log 太多，可以加：

```bash
--standby-progress-interval 0
```

音樂控制也會自動結束 conversation mode：播音樂或繼續播放後會送 `Music 0 0` 並回到 wake-only standby；暫停或停止處理完會送 `Normal 0 0`。所以下次要暫停、停止或換歌，都必須先說 `Hey Jarvis`。如果要保留舊行為，可以加 `--keep-conversation-after-music-control`。

Focus Work Mode 指令也會自動結束 conversation mode，避免工作模式啟動後還一直收 follow-up。開始後會立刻拍第一張工作狀態照片，之後每 `--focus-interval-sec` 秒取樣一次；照片預設只在記憶體中，判斷完即丟棄。`--focus-duration-min 0` 代表不自動結束，要再說「結束工作 / 停止專心 / 下班」才會停。

To-Do List 是 normal mode 的本機工具，不需要 Windows server 額外 endpoint 支援。明確說「新增待辦、列出待辦、完成待辦」時，Wake Bridge 會直接更新本機 JSON，預設路徑是 `frdm_uart_context_sender/logs/todo_list.json`。它不會自動啟動或停止 focus work mode；若 focus mode 正在跑，明確的待辦指令仍會先被處理。

`--no-sleep-on-conversation-end` 現在只保留相容舊指令；新版結束對話預設就是送 `Normal 0 0`。`--conversation-mode` 不要和 `--no-wake-word` 同時使用，程式會直接拒絕啟動，避免結束後仍然不用喚醒詞就錄音。

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
TTS audio : AUDIO_DEVICE=plughw:CARD=UACDemoV10,DEV=0
camera    : --camera-id auto
FRDM      : --uart-port auto
music     : --music-backend mpv
weather   : --weather-default-location Taipei
todo      : --todo-list-path frdm_uart_context_sender/logs/todo_list.json
```

音樂功能是 sidecar，不會接管主流程：

```text
一般聊天 / vision / FRDM 控制 -> 不呼叫 Music Player
點歌 / 換歌                 -> TTS 確認後呼叫 Music Player play
暫停 / 停止音樂             -> 直接呼叫 Music Player pause/stop
繼續播放音樂                -> TTS 確認後呼叫 Music Player resume
任何 Hey Jarvis wake         -> 先 pause 音樂，等 0.18s，再 beep 並開始錄音；講完時再 beep + 抓圖
```

天氣功能也在同一個 sidecar：

```text
一般聊天 / vision / FRDM 控制 -> 不呼叫 Weather Tool
所在地、今天、明天、特定時間天氣 -> 呼叫 http://127.0.0.1:8788/weather
天氣來源                         -> Open-Meteo forecast + geocoding
回答方式                         -> 覆蓋桌機 AI 泛用回答，直接 TTS 念天氣摘要
```

`--music-backend mpv` 會真的播放；`browser` 只開搜尋頁。若要關掉 wake 時自動暫停音樂，可加 `--no-music-pause-on-wake`，但正式 demo 建議保持開啟，避免音樂被錄進 mic。

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

正式現場 `--noisy-room` 值：

```text
volume_min=1100
speech_start_margin=750
speech_start_ratio=1.45
silence_margin=900
silence_noise_ratio=1.30
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
先加 noisy-room preset       -> --noisy-room
一直錄到 max_speech_seconds -> 先加 --silence-noise-ratio 1.4
背景音直接觸發 Speech started -> 加 --speech-start-ratio 1.55
你講話也進不了 Speech started -> 降 --speech-start-ratio 1.35
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

## Music Routing And Playback

音樂播放是 local sidecar，不是主 AI 流程本身。Wake Bridge 仍然每輪照原本方式錄音、送 Windows server、TTS、UART；只有 transcript 被 rule-based music intent 判斷成點歌、換歌、暫停、繼續或停止時，才會呼叫 `music_web_player.py`。

完整時序：

```text
Music is playing
-> user says Hey Jarvis
-> Wake Bridge immediately POSTs {"action": "pause"} to http://127.0.0.1:8788/music
-> beep / Thinking / recording starts
-> transcript returns from Windows ASR
-> detect_music_intent(transcript)
-> no music intent: do nothing with Music Player; music stays paused until the user says resume
-> play/change/resume music: TTS speaks confirmation first, then POST play/query or resume
-> pause/stop music: POST pause/stop before TTS
-> bridge returns to standby and keeps listening for Hey Jarvis
```

常用語句：

```text
Hey Jarvis，我想要聽告白氣球       -> action=play, query=告白氣球
Hey Jarvis，幫我波 稻香            -> action=play, query=稻香
Hey Jarvis，換成 七里香            -> action=play, query=七里香
Hey Jarvis，暫停音樂               -> action=pause
Hey Jarvis，繼續播放音樂           -> action=resume
Hey Jarvis，停止音樂               -> action=stop
Hey Jarvis，講個笑話               -> no music call
Hey Jarvis，我現在是什麼表情       -> no music call, may use vision
```

正式 demo 建議讓 Terminal 4 手動開著：

```bash
cd /home/asrlab-yian/MakeNTU/music_web_player
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate
python3 music_web_player.py --server --host 127.0.0.1 --port 8788 --backend mpv --weather-default-location Taipei
```

如果 Terminal 4 沒開，Wake Bridge 在 `action=play` 時會嘗試自動啟動。Wake 當下的 `pause` 和使用者要求 `resume` 都不會自動啟動 sidecar，因為這兩個動作只對已經存在的 mpv process 有意義。

重要參數：

```text
--music-backend mpv              # 真的播放，auto 也會優先選 mpv
--music-timeout 5                # play/change music request timeout
--music-wake-pause-timeout 0.6   # wake 一偵測到先 pause 的短 timeout
--music-debug                    # 印 Music routing / Music tool log
--no-music                       # 完全關閉 music sidecar routing
--no-music-autostart             # 點歌時也不要自動啟動 sidecar
--no-music-pause-on-wake         # wake 時不要先 pause 音樂，不建議正式 demo 使用
```

`mpv` backend 用 `ytsearch1:<query>` 串流第一個搜尋結果，不會把音樂存到專案資料夾。`pause` / `resume` 透過 mpv IPC socket 控制，所以會保留播放位置；`stop` 才會結束 mpv process。`browser` backend 只開搜尋頁，不保證自動播放，也無法可靠 pause/resume。

Action 對照：

```text
action=play
  input : query，例如 告白氣球
  timing: TTS 確認句結束後呼叫
  effect: stop old mpv if any, start mpv ytdl://ytsearch1:<query>
  log   : action=play query=... ipc_ready=True

action=pause
  input : none
  timing: wake detected 立即呼叫；使用者說暫停音樂時也會呼叫
  effect: mpv IPC set_property pause true
  log   : action=pause paused=True

action=resume
  input : none
  timing: TTS 確認句結束後呼叫
  effect: mpv IPC set_property pause false
  log   : action=resume resumed=True

action=stop
  input : none
  timing: 使用者說停止音樂時立即呼叫
  effect: terminate mpv process
  log   : action=stop stopped=True
```

`curl http://127.0.0.1:8788/health` 的重要欄位：

```text
backend       : mpv 才是正式播放模式
mpv_available : Jetson 找得到 mpv
yt_dlp_available : Jetson 找得到 yt-dlp 或 youtube-dl
active        : 目前有 mpv process
paused        : 目前是否暫停中
last_query    : 最近一次播放 query
ipc_path      : mpv IPC socket；pause/resume 依賴它
weather_available : /weather endpoint 已載入
weather_source    : open-meteo
weather_default_location : 所在地天氣預設城市
```

## Weather Routing

天氣查詢跟音樂共用 Terminal 4 `music_web_player.py`，但走不同 endpoint：

```text
music   -> http://127.0.0.1:8788/music
weather -> http://127.0.0.1:8788/weather
health  -> http://127.0.0.1:8788/health
```

Wake Bridge 每輪收到 Windows ASR transcript 後，會先做本地 rule-based weather intent。只有明確在問天氣時才查 Open-Meteo，一般聊天、FRDM 控制、vision 問題不會多一段網路查詢。

```text
transcript='明天下午三點台北天氣如何'
-> detect_weather_intent=True
-> POST /weather {"text": transcript, "default_location": "Taipei"}
-> Jetson calls Open-Meteo geocoding + forecast API
-> response.reply='台北市、台湾明天約15:00預報約 ...'
-> replace desktop AI generic reply
-> TTS speaks weather answer
-> FRDM uses emotion=curious, head_motion=gentle_nod
```

支援語句：

```text
Hey Jarvis，所在地天氣如何
Hey Jarvis，這裡現在幾度
Hey Jarvis，明天天氣如何
Hey Jarvis，明天下午三點台北天氣如何
Hey Jarvis，今天會下雨嗎
Hey Jarvis，明天要帶傘嗎
Hey Jarvis，新竹明天早上天氣
Hey Jarvis，weather in Tokyo tomorrow
```

通常不會觸發：

```text
Hey Jarvis，今天幾號
Hey Jarvis，講個笑話
Hey Jarvis，幫我開電風扇
Hey Jarvis，我現在是什麼表情
```

Wake Bridge 相關參數：

```text
--weather-url http://127.0.0.1:8788/weather
--weather-default-location Taipei
--weather-timeout 6
--weather-api-timeout 5
--weather-debug
--no-weather
--weather-always-call
```

手動測試：

```bash
curl -X POST http://127.0.0.1:8788/weather \
  -H "Content-Type: application/json" \
  -d '{"text":"明天下午三點所在地天氣如何","default_location":"Taipei"}'
```

天氣失敗 fallback：

```text
/weather 連不到      -> Wake Bridge 會嘗試 autostart Terminal 4 sidecar
Open-Meteo 連不到    -> TTS 說「我剛剛連不到本地天氣工具或天氣資料來源」
intent 沒命中        -> 不查天氣，走原本桌機 AI 回覆
```

## To-Do List

To-Do List 是 Wake Bridge 內建的本機 voice tool。它只在 transcript 明確提到待辦時觸發，不呼叫額外 server endpoint、不使用相機，也不會改變 focus work mode 狀態。

資料存在本機 JSON：

```text
default path -> frdm_uart_context_sender/logs/todo_list.json
format       -> version, next_id, items[]
privacy      -> only task text + timestamps; no photo/audio
```

常用語句：

```text
Hey Jarvis，新增待辦 寫報告
Hey Jarvis，幫我記一個待辦：整理投影片
Hey Jarvis，把買牛奶加入待辦
Hey Jarvis，列出待辦
Hey Jarvis，我的待辦清單
Hey Jarvis，完成待辦 1
Hey Jarvis，完成第二項待辦
Hey Jarvis，完成待辦 寫報告
Hey Jarvis，清除已完成待辦
Hey Jarvis，清空待辦
```

處理順序：

```text
Windows ASR transcript
-> local to-do intent
-> if matched: update JSON, override reply/control locally, TTS speaks result
-> if not matched: continue focus/music/weather/general AI path
```

這個功能放在 normal mode，但明確待辦指令會優先於 focus mode 的「工作中保護回覆」。也就是 focus mode 跑著時，你仍然可以說「新增待辦 XXX」先記下來；但一般聊天仍會被 focus mode 擋住，避免工作時一直互動。

相關參數：

```text
--todo-list-path /home/asrlab-yian/MakeNTU/frdm_uart_context_sender/logs/todo_list.json
--no-todo-list
--todo-debug
```

快速檢查 JSON：

```bash
cat /home/asrlab-yian/MakeNTU/frdm_uart_context_sender/logs/todo_list.json
```

## Focus Work Mode

Focus work mode 用來偵測使用者是否仍在專心工作，或是離席、看手機、睡覺、分心。預設每 60 秒取樣一次。它和 normal mode 分開運作：

```text
Hey Jarvis
-> 開始專心工作 / 工作模式 / 番茄鐘
-> Wake Bridge 啟動 focus_work_mode.py
-> focus_work_mode.py 每 N 秒拍一張照片
-> POST image to Windows /focus-check
-> append focus_log.jsonl
-> session 結束時寫 focus_summary.json + focus_report.md
-> optional Discord webhook notification
-> 結束工作 / 停止專心 / 下班
-> Wake Bridge 停止 focus process，回到 normal mode
```

照片預設是 memory-only：

```text
camera JPEG bytes -> /focus-check -> delete bytes
log/report only keep state, score, evidence text, timestamp
```

只有加 `--focus-save-images` 時才會保存照片，這只建議 debug 使用。

常用語句：

```text
Hey Jarvis，開始專心工作
Hey Jarvis，開始專心工作 25 分鐘 寫報告
Hey Jarvis，番茄鐘 30 分鐘
Hey Jarvis，結束工作
Hey Jarvis，停止專心
Hey Jarvis，下班
```

狀態分類：

```text
focused     專心
away        離席
phone       疑似手機
sleeping    疑似睡覺
distracted  分心
uncertain   不確定
error       錯誤
```

主 wake bridge 參數：

```bash
--no-focus-mode
--focus-script /home/asrlab-yian/MakeNTU/frdm_uart_context_sender/focus_work_mode.py
--focus-server-url http://100.108.141.26:8766/focus-check
--focus-interval-sec 60
--focus-duration-min 0
--focus-log-root /tmp/focus_voice_test
--focus-alert-threshold 2
--todo-list-path /home/asrlab-yian/MakeNTU/frdm_uart_context_sender/logs/todo_list.json
--focus-notify-mode discord
--focus-discord-webhook-url "$DISCORD_WEBHOOK_URL"
--focus-notify-dry-run
--focus-save-images
```

`--focus-duration-min 0` 代表不自動結束，要再說「結束工作」或「停止專心」才會切回 normal mode。

結束時會產生兩份報告：

```text
focus_summary.json  給後續手機通知、email、Discord 或前端讀取的結構化資料
focus_report.md     給人看的 Markdown 專心報告
```

整合報告會讀同一份 to-do JSON，列出專注期間完成的待辦、結束後仍剩下的待辦、專注時間、分心時間、專注分數與建議。Discord webhook 是 best-effort：有 `DISCORD_WEBHOOK_URL` 就送，送失敗不會讓 focus session 結束流程失敗。

Discord API request 會帶 `User-Agent: DiscordBot (...)`。如果手動測 webhook 時看到 `HTTP 403 error code: 1010`，通常不是 webhook 權限，而是 request 被 Cloudflare 擋掉；請確認測試指令也有帶 `User-Agent`。

手動安全測試，先不用相機/server：

```bash
cd /home/asrlab-yian/MakeNTU

python3 frdm_uart_context_sender/focus_work_mode.py \
  --mock-state phone \
  --once \
  --uart-dry-run \
  --log-root /tmp/focus_test
```

從 wake word 測整合流程時，先保留 `--uart-dry-run`、`--no-tts`：

```bash
python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --mic-keyword UACDemo \
  --uart-port auto \
  --uart-dry-run \
  --no-tts \
  --no-tts-preflight \
  --no-camera \
  --focus-server-url http://100.108.141.26:8766/focus-check \
  --focus-interval-sec 20 \
  --focus-duration-min 0 \
  --focus-log-root /tmp/focus_voice_test \
  --focus-alert-threshold 1 \
  --no-music \
  --no-weather \
  --uart-debug
```

Windows server 必須有 `/focus-check`。如果 `/debug` routes 裡沒有 `/focus-check`，同步正式 Windows bundle 到桌面後重啟 server。

Focus mode 主要會送的 UART screen command：

```text
start focus    -> Focus 0 0
during reply   -> Speaking <emotion_code> + MotorPitch/MotorYaw
still focusing -> Focus 0 0
stop/return    -> Normal 0 0
```

`--focus-alert-threshold 2` 代表非專心狀態要連續出現兩次才切表情，避免單張照片誤判。

Report 檔案預設在：

```text
/home/asrlab-yian/MakeNTU/frdm_uart_context_sender/logs/focus_sessions/focus_YYYYMMDD_HHMMSS/
session.json
focus_log.jsonl
focus_report.md
```

未來手機/daily/weekly report 可以接在這個資料結構上：

```text
focus_sessions/focus_*/focus_log.jsonl
-> daily_report_YYYYMMDD.json / daily_report_YYYYMMDD.md
-> weekly_report_YYYY-WW.json / weekly_report_YYYY-WW.md
-> 手機瀏覽器 dashboard、email digest，或 LINE/Telegram bot
```

建議先做 Jetson 本地 Flask/FastAPI dashboard，手機連同一個網路看 daily/weekly；等資料格式穩定後，再加 email 或訊息通知。

## Windows Server

Windows 桌面實際跑的是 Desktop bundle 裡的 `desktop_fast_chat_server.py`。只要 Jetson 端 bundle 有更新、`debug_version` 不對、或不確定 Windows 是不是最新版，就先 refresh/scp。正式 bundle 已包含 `/focus-check`：

```text
/home/asrlab-yian/MakeNTU/emotion_robot_controller/voice_stt_remote/windows_desktop_server_bundle/desktop_fast_chat_server.py
```

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

如果 scp 連不到 Jetson，先在 Jetson 跑 `tailscale ip -4`，再把 `100.110.90.72` 換成目前 Jetson Tailscale IP。

啟動 Windows server：

```powershell
cd "$env:USERPROFILE\Desktop\windows_desktop_server_bundle"
.\.venv\Scripts\Activate.ps1
ollama pull qwen35-fast:latest
python desktop_fast_chat_server.py --host 0.0.0.0 --port 8766 --ollama-model qwen35-fast:latest --vision-model qwen35-fast:latest --no-think
```

Health 必須看到：

```text
debug_version: 11
chat_ready   : True
asr_loaded   : True
ollama_model : qwen35-fast:latest
vision_model : qwen35-fast:latest
```

如果 `debug_version` 不是 11，代表 Windows 還在跑舊檔或舊 process。

Focus Work Mode 另需確認 `/debug` routes 內有 `/focus-check`：

```powershell
curl http://127.0.0.1:8766/debug
```

若 routes 沒有 `/focus-check`，請 scp 正式 bundle 並重啟 Windows server。

## Structured Reply And Control

Windows `qwen35-fast:latest` 被要求只回傳一個 JSON object：

```json
{
  "reply": "自然語言回覆，給 TTS 播放。不可提到 JSON、UART、MotorPitch、MotorYaw 或內部控制欄位。",
  "control": {
    "persistent_state": "normal | sleep | unchanged",
    "screen_mode": "normal | sleep | music | focus | thinking | unchanged",
    "emotion": "neutral | concerned | angry | sad | happy | curious | excited | confused | sleepy",
    "head_motion": "none | nod | double_nod | look_around | shake | gentle_nod | sleepy_drop",
    "reason": "簡短內部理由，不給使用者播放"
  }
}
```

Jetson robust parser：

### FRDM Speaking 表情不變

如果 log 長這樣：

```text
FRDM UART TX: Speaking 4
FRDM UART RX: Speaking 4
FRDM UART RX: switch to SPEAKINGemotion: neutral
```

代表 Jetson 有送到、FRDM 有收到，但 FRDM `SpeakingGui(char *pValue)` 沒把參數解析成數字。請把 FRDM 端 `sscanf(pValue, "%u", &value)` 換成 [speaking_gui_emotion_fix.c](/home/asrlab-yian/MakeNTU/emotion_robot_controller/frdm_firmware/patches/speaking_gui_emotion_fix.c) 裡的 `ReadSpeakingEmotionOrNeutral()`。

目前對表：

```text
Speaking 0 -> neutral
Speaking 1 -> concerned
Speaking 2 -> angry
Speaking 3 -> sad
Speaking 4 -> happy
Speaking 5 -> confused
```

```text
合法 JSON                  -> 使用 reply/control
前後混入文字              -> 抽第一個 JSON object
server 回舊欄位 uart       -> normalize 成 control
reply 裡混入 JSON/control  -> 抽出自然 reply，不讓 TTS 唸控制資訊
parse 失敗                -> 自然 fallback reply + neutral/none/unchanged
```

`reply` 永遠是給使用者聽的自然語言；`control` 永遠是內部控制。

控制優先順序：

```text
1. 明確本機工具意圖：to-do / focus / music / weather
2. 明確 conversation 結束詞：掰掰、拜拜、再見 -> Normal 0 0
3. 明確睡覺/休息意圖 -> Sleep 0 0，並結束連續聆聽
4. 明確回來/正常意圖 -> Normal 0 0
5. 一般回答 -> Speaking <emotion_code>，TTS 後在 conversation mode 回 Thinking 0 0
```

`screen_mode` 是給 Jetson 的模式提示，不會給使用者聽。若 server 沒回、回錯、或 reply 裡混入 JSON，Jetson 端會用 transcript 的本機關鍵字規則補救。

## FRDM UART Timing

正式時序：

```text
Bridge process starts
-> wait 2 seconds for FRDM boot screen
-> Normal 0 0
Wake detected
-> beep
-> Thinking 0 0
-> user speech / recording
-> end-of-speech beep + image capture / upload / ASR / Ollama
-> receive reply/control
-> Speaking <emotion_code>
-> TTS starts
-> head motion thread starts
-> TTS finishes or estimated finished
-> Thinking 0 0 for the next follow-up, or mode command such as Normal/Music/Focus/Sleep
```

新版 FRDM 不再接收 `Happy 0 0`、`Curious 0 0` 這種舊情緒畫面指令。Jetson 會把情緒轉成單參數 `Speaking 0-5`，motor 動作仍在獨立 thread 執行，不阻塞 TTS。

Persistent state：

```text
normal
sleep
```

Temporary screen state：

```text
Thinking
Speaking <0..5>
Music
Focus
```

Mode command examples：

```text
Normal 0 0     # default face / wake-only standby
Thinking 0 0   # listening, ASR/LLM thinking, or waiting for follow-up
Speaking 2     # TTS speaking with angry emotion
Music 0 0      # music playback screen
Focus 0 0      # focus work mode screen
Sleep 0 0      # sleep/rest screen
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

Wake Bridge 會分開控制「說話情緒碼」和「頭部馬達」：

```text
emotion      -> Speaking 0..5
head_motion  -> MotorPitch / MotorYaw continuous sequence
```

Windows server 可以在 `control.head_motion` 明確指定頭部動作；如果 server 沒給、給空字串、或給 `none`，Jetson 會用 `emotion` 自動選一個 fallback motion。這樣一般聊天只要判斷情緒，頭就會跟著動；特殊情境仍可由 server 指定更精準的 motion。

Emotion speaking-code mapping：

```text
neutral   -> Speaking 0
concerned -> Speaking 1
angry     -> Speaking 2
sad       -> Speaking 3
happy     -> Speaking 4
curious   -> Speaking 5   # FRDM has no separate curious face; use confused face
excited   -> Speaking 4   # FRDM has no separate excited face; use happy face
confused  -> Speaking 5
sleepy    -> Speaking 3   # FRDM has no separate sleepy face; use sad face
```

Emotion to head motion fallback：

```text
neutral   -> none
concerned -> gentle_nod
angry     -> shake
sad       -> gentle_nod
happy     -> nod
curious   -> look_around
excited   -> double_nod
confused  -> shake
sleepy    -> sleepy_drop
```

常見模型輸出的 emotion alias 會先正規化，再送 FRDM：

```text
calm / normal / 中性              -> neutral
joy / joyful / positive / 開心    -> happy
interested / thinking / 好奇      -> curious
surprised / amazed / 興奮         -> excited
unsure / uncertain / puzzled      -> confused
anxious / worried / 急 / 擔心     -> concerned
angry / 生氣 / 火大 / 操你媽      -> angry
sad / 難過 / 沮喪                 -> sad
tired / drowsy / 想睡 / 疲累      -> sleepy
```

本地 fallback 也會處理常見語句：

```text
我操你媽的                 -> angry, shake, Speaking 2
我很難過                   -> sad, gentle_nod, Speaking 3
太酷了我超期待             -> excited, double_nod, Speaking 4
這個結果怪怪的我看不懂     -> confused, shake, Speaking 5
我有點擔心                 -> concerned, gentle_nod, Speaking 1
我好睏想睡                 -> sleepy, sleepy_drop, Speaking 3
為什麼會這樣               -> curious, look_around, Speaking 5
太好了很棒                 -> happy, nod, Speaking 4
```

Sleep / wake intent has higher priority than normal emotion fallback：

```text
去睡覺 / sleep / standby  -> emotion=sleepy, head_motion=sleepy_drop, persistent_state=sleep
起床 / wake up / normal   -> persistent_state=normal; unsafe sleepy/shake motions become nod
```

### Motor UART Coordinate System

`MotorPitch` / `MotorYaw` 是絕對 servo 角度，不是相對位移。馬達 UART wire format 只能送一個角度參數，例如 `MotorPitch 90`；不要在角度後面再加第二個數值。

Terminal 3 的完整 Hey Jarvis 指令預設會送 `MotorPitch` / `MotorYaw`。啟動指令內要有：

```bash
--enable-head-motor \
```

若 FRDM parser 正在 debug、需要暫時關閉頭部馬達，把 `--enable-head-motor` 改成 `--disable-head-motor`。完整 Hey Jarvis 流程仍會送畫面 UART，例如 `Thinking 0 0`、`Speaking 2`、`Music 0 0`、`Focus 0 0`、`Normal 0 0`。

啟動 log 必須看到：

```text
Head motor motion: enabled=True
```

```text
MotorPitch 65   -> low/down limit
MotorPitch 90   -> center
MotorPitch 115  -> up limit

MotorYaw 0      -> right limit
MotorYaw 90     -> center
MotorYaw 180    -> left limit
```

程式內的安全邊界：

```text
MOTOR_PITCH_MIN=65
MOTOR_PITCH_CENTER=90
MOTOR_PITCH_MAX=115
MOTOR_YAW_MIN=0
MOTOR_YAW_CENTER=90
MOTOR_YAW_MAX=180
MOTOR_STEP_DELAY_SEC=0.80
MOTOR_SMOOTH_STEP_DEG=10
MOTOR_SPEAKING_STEP_DELAY_SEC=0.75
MOTOR_SPEAKING_SMOOTH_STEP_DEG=60
MOTOR_RESET_REPEATS=4
MOTOR_RESET_DELAY_SEC=0.35
MOTOR_STOP_TIMEOUT_SEC=6.0
MOTOR_JOIN_TIMEOUT_SEC=6.0
```

任何 `MotorPitch` / `MotorYaw` 指令都會先 clamp 到上述範圍；內部 tuple 的第三個值只保留給程式相容，真正送到 FRDM 時會被省略成單參數。這可以避免舊版相對角度或錯誤 LLM control 把馬達推到不合理位置。

如果實機 log 出現這種回覆，問題在 FRDM 端 parser，不是 Jetson 送出的角度：

```text
FRDM UART TX: MotorPitch 90
FRDM UART RX: Motor Pitch = 537190203
```

`537190203 = 0x2004df3b`，看起來像 Cortex-M RAM 位址。這通常代表 FRDM 的 `MotorControlPitch(char *pValue)` / `MotorControlYaw(char *pValue)` 沒有成功把字串 `"90"` 轉成整數 `90`，或 `sscanf` 失敗後用了未初始化的 `value`。Terminal 3 預設會送頭部馬達；如果收到超出範圍的 ACK，當次程序會停送後續馬達指令，避免馬達被錯誤值推到極限。修好 FRDM firmware 後重啟 bridge 即可。

FRDM 端 handler 應該先轉數字、再 clamp、再控制 PWM，概念如下：

```c
#include <stdbool.h>

static bool ParseMotorAngle(const char *pValue, int *out_angle)
{
    int angle = 0;

    if (pValue == NULL || out_angle == NULL) {
        return false;
    }

    if (sscanf(pValue, " %d", &angle) == 1 ||
        sscanf(pValue, " %*s %d", &angle) == 1) {
        *out_angle = angle;
        return true;
    }

    return false;
}

void MotorControlPitch(char *pValue)
{
    int angle = 90;
    PRINTF("Motor Pitch raw pValue = [%s]\r\n", pValue ? pValue : "(null)");
    if (!ParseMotorAngle(pValue, &angle)) {
        PRINTF("Motor Pitch parse failed: %s\r\n", pValue ? pValue : "(null)");
        return;
    }
    if (angle < 65) angle = 65;
    if (angle > 115) angle = 115;
    PRINTF("Motor Pitch = %d\r\n", angle);
    Servo_GotoPitch(angle);
}

void MotorControlYaw(char *pValue)
{
    int angle = 90;
    PRINTF("Motor Yaw raw pValue = [%s]\r\n", pValue ? pValue : "(null)");
    if (!ParseMotorAngle(pValue, &angle)) {
        PRINTF("Motor Yaw parse failed: %s\r\n", pValue ? pValue : "(null)");
        return;
    }
    if (angle < 0) angle = 0;
    if (angle > 180) angle = 180;
    PRINTF("Motor Yaw = %d\r\n", angle);
    Servo_GotoYaw(angle);
}
```

### Motion Sequences

目前 motion table 是高階 keyframe；送到 FRDM 前會再展開成平滑小步，預設每次最多跨 `10deg`。例如 `MotorYaw 35 -> MotorYaw 145` 不會只送兩個點，會展開成多個 `45 / 55 / ... / 145` 中間角度，避免實體看起來只轉一次就停。

目前連續動作的 keyframe：

```text
none:
  MotorPitch 90 -> MotorYaw 90

nod:
  MotorPitch 90 -> MotorPitch 106 -> MotorPitch 106
  -> MotorPitch 74 -> MotorPitch 74 -> MotorPitch 90

double_nod:
  MotorPitch 90 -> MotorPitch 110 -> MotorPitch 110
  -> MotorPitch 72 -> MotorPitch 72
  -> MotorPitch 106 -> MotorPitch 106
  -> MotorPitch 74 -> MotorPitch 74 -> MotorPitch 90

look_around:
  MotorPitch 90 -> MotorYaw 90 -> MotorPitch 98
  -> MotorYaw 35 -> MotorYaw 35
  -> MotorYaw 145 -> MotorYaw 145
  -> MotorYaw 90 -> MotorPitch 90

shake:
  MotorYaw 90 -> MotorYaw 45 -> MotorYaw 45
  -> MotorYaw 135 -> MotorYaw 135
  -> MotorYaw 55 -> MotorYaw 55 -> MotorYaw 90

gentle_nod:
  MotorPitch 90 -> MotorPitch 80 -> MotorPitch 80
  -> MotorPitch 100 -> MotorPitch 100 -> MotorPitch 90

sleepy_drop:
  MotorPitch 90 -> MotorPitch 82 -> MotorPitch 74
  -> MotorPitch 65 -> MotorPitch 65 -> MotorPitch 90
```

每個 motion 結束後都會再送多次中心位置 reset：

```text
MotorPitch 90
MotorYaw 90
MotorPitch 90
MotorYaw 90
MotorPitch 90
MotorYaw 90
MotorPitch 90
MotorYaw 90
```

正式對話不會跑完整一次性 motion table。TTS 開始時會啟動 speaking motion loop，TTS 還在播就持續循環短動作；TTS 結束後會送 stop event，motion thread 立刻回中心並退出，再依狀態送 `Thinking 0 0`、`Normal 0 0`、`Sleep 0 0`、`Music 0 0` 或 `Focus 0 0`。

`--uart-debug` 會同時印出 keyframe 和展開後的 UART 序列：

```text
head motion keyframes: MotorPitch:90 -> MotorPitch:106 -> MotorPitch:106 -> MotorPitch:74 -> MotorPitch:74 -> MotorPitch:90
head motion expanded: MotorPitch:90 -> MotorPitch:98 -> MotorPitch:106 -> MotorPitch:106 -> MotorPitch:98 -> MotorPitch:90 -> MotorPitch:82 -> MotorPitch:74 -> MotorPitch:74 -> MotorPitch:82 -> MotorPitch:90
```

### Motor Tuning

```text
看起來只轉一次、不連續      -> --motor-smooth-step-deg 6 或 8
講話時動作太快              -> --motor-speaking-step-delay 0.9 或 1.0
講話時動作太少              -> --motor-speaking-step-delay 0.55
一次性測試動作太快          -> --motor-step-delay 1.0
一次性測試太慢              -> --motor-step-delay 0.6
偶爾沒有回正                -> --motor-reset-repeats 5
回正指令太密或 FRDM 吃不穩   -> --motor-reset-delay 0.45
TTS 結束後太早切下一個畫面      -> --motor-join-timeout 8
只想看會送什麼              -> 加 --uart-dry-run --uart-debug
```

如果角度本身要改，優先改 `wake_voice_chat_frdm_bridge.py` 裡 `PITCH_*` / `YAW_*` motion 常數，不要在文件或 server prompt 裡寫相對角度。

### Direct Head Motion Test

要調馬達時，先用 direct test，不要透過 Hey Jarvis、ASR、TTS、AI。這個模式只會碰 FRDM UART，方便確認「指令有沒有送到、delay 是否夠、最後有沒有回正」。

先 dry-run 看全部 motion 的完整 UART 序列：

```bash
cd /home/asrlab-yian/MakeNTU
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate

python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py \
  --test-head-motion all \
  --uart-dry-run \
  --uart-debug \
  --motor-step-delay 0.02 \
  --motor-smooth-step-deg 10 \
  --motor-reset-delay 0.02 \
  --test-head-gap 0
```

測 emotion fallback 是否正確：

```bash
python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py \
  --test-head-emotion all \
  --uart-dry-run \
  --uart-debug \
  --motor-step-delay 0.02 \
  --motor-smooth-step-deg 10 \
  --motor-reset-delay 0.02 \
  --test-head-gap 0
```

FRDM firmware 修好、手動確認 `MotorPitch 90` 會回 `Motor Pitch = 90` 後，才實機測講話期間循環動作：

```bash
python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py \
  --uart-port auto \
  --uart-debug \
  --enable-head-motor \
  --test-speaking-head-motion shake \
  --test-speaking-seconds 6 \
  --motor-speaking-step-delay 0.75 \
  --motor-speaking-smooth-step-deg 60 \
  --motor-reset-repeats 4 \
  --motor-reset-delay 0.35
```

FRDM ACK 正常後，再實機測一次性 motion table：

```bash
python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py \
  --uart-port auto \
  --uart-debug \
  --enable-head-motor \
  --test-head-motion all \
  --motor-step-delay 0.80 \
  --motor-smooth-step-deg 10 \
  --motor-reset-repeats 4 \
  --motor-reset-delay 0.35
```

如果 dry-run 有 `MotorPitch/MotorYaw`，但實體完全不動，先檢查：

```bash
python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py --list-uarts
./frdm_uart_context_sender/recover_demo_usb.sh
```

看到 `No UART serial device is visible` 時，代表 Jetson 當下沒有看到 FRDM 的 `/dev/ttyACM*`，程式會跳過 UART 並繼續 TTS，但馬達不會動。

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
-> beep
-> record speech until silence
speech ended
-> end-of-speech beep
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
speech-end image delay 0.0s
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
--speech-end-image-delay 0
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
send next FRDM screen after TTS finished or estimated finished
```

可調 timeout 與 polling 頻率：

```bash
--tts-playback-timeout 45
--tts-poll-interval 0.75
```

`--tts-poll-interval` 預設 0.75 秒，避免 TTS server terminal 因每 0.2 秒 `/queue` access log 而洗版。若想更安靜可調到 `1.0`，若想更快切回下一個 FRDM 畫面可調到 `0.5`。

TTS `.env` 建議：

```text
AUDIO_DEVICE=plughw:CARD=UACDemoV10,DEV=0
DEFAULT_VOLUME_GAIN=2.25
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
cd /home/asrlab-yian/MakeNTU
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate
python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py --self-test
```

Head motor dry-run：

```bash
python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py \
  --test-head-motion all \
  --uart-dry-run \
  --uart-debug \
  --motor-step-delay 0.02 \
  --motor-smooth-step-deg 10 \
  --motor-reset-delay 0.02 \
  --test-head-gap 0
```

Emotion to head motion dry-run：

```bash
python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py \
  --test-head-emotion all \
  --uart-dry-run \
  --uart-debug \
  --motor-step-delay 0.02 \
  --motor-smooth-step-deg 10 \
  --motor-reset-delay 0.02 \
  --test-head-gap 0
```

Music intent self-test：

```bash
python3 music_web_player/music_web_player.py --self-test
```

這會確認「我想聽歌」仍會觸發音樂，但「為什麼沒聲音、我聽到聲音超小」不會被誤判成點歌。

TTS volume API smoke test：

```bash
curl -X POST http://127.0.0.1:8777/speak_async \
  -H "Content-Type: application/json" \
  -d '{"text":"音量測試，現在應該比較大聲。","interrupt":true,"volume_gain":2.25}'
```

如果回 `422` 或 `volume_gain` 不被接受，重啟 Jetson Terminal 2 的 `jetson_piper_tts.server`。

Focus work mode self-test：

```bash
cd /home/asrlab-yian/MakeNTU
python3 frdm_uart_context_sender/focus_work_mode.py --self-test
```

Windows self-test：

```powershell
cd "$env:USERPROFILE\Desktop\windows_desktop_server_bundle"
.\.venv\Scripts\Activate.ps1
python desktop_fast_chat_server.py --self-test
```

Device/server checks：

```bash
python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py --list-mics
python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py --list-uarts
python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py --server-url http://100.108.141.26:8766/voice-chat --check-server --uart-dry-run --tts-debug
```

`--list-mics` 應看到 UACDemo input；`--list-uarts` 應看到 `/dev/ttyACM0` 或 `/dev/serial/by-id/...`。

Focus mock session：

```bash
python3 frdm_uart_context_sender/focus_work_mode.py \
  --mock-state focused \
  --once \
  --uart-dry-run \
  --log-root /tmp/focus_test
```

應產生 `session.json`、`focus_log.jsonl`、`focus_report.md`。

## Debug Log Guide

啟動成功關鍵 log：

```text
Server health: debug_version=11, chat_ready=True, asr_loaded=True
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
FRDM UART TX: Speaking 2
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
another wake bridge is already running
-> pkill -9 -f wake_voice_chat_frdm_bridge.py，然後重開 Terminal 3。

No microphone matching UACDemo
-> Jetson 沒看到 USB mic。跑 lsusb / --list-mics；若 lsusb 看不到 UACDemo，跑 ./recover_demo_usb.sh。

Recording 後像卡住
-> 如果顯示 phase=waiting_speech，代表還沒高過 start threshold；先把 --speech-start-ratio 1.45 降到 1.35，再視情況降 --speech-start-margin。
-> 如果已經 Speech started 但停不下來，可把 --silence-noise-ratio 1.30 提到 1.40，或把 --max-speech-seconds 5 降到 4。
-> 如果現場一直有風扇/人聲，優先加 `--noisy-room`；背景約 10000、講話約 19000 時，重點看 log 裡 `speech_start_threshold` 是否約 14500、`silence_threshold` 是否約 13000。
-> 如果 `wake` 很高但被 Low-volume 忽略，先看 `recent_peak`；如果 recent_peak 仍低於門檻，可試 `--wake-volume-ratio 1.25` 或 `--wake-volume-window-seconds 1.5`。
-> `--max-speech-seconds` 只在 Speech started 後生效；要防止整輪卡住請用 `--max-recording-seconds`。
-> 如果完全沒有 Recording progress，代表舊版 blocking read 卡住或 USB mic stream 停吐；新版會印 WARNING 並退出當輪。

命令尾端出現 `--uart-debug\terval`
-> 指令打錯。改成 --tts-poll-interval 0.75、--tts-debug、--uart-debug 三行。

Wake 被 ignore
-> 低音量保護。正式用 --wake-volume-min 500；仍漏叫可降到 200。

Camera timeout
-> 不會 crash。跑 lsusb / ls -l /dev/video* / ./recover_demo_usb.sh。
-> 如果 /dev/video0 存在但 image_received=False，等 5 到 7 秒讓 warm reader 拿到第一張 frame。

Windows health timeout
-> 確認 Windows server terminal 開著、Tailscale IP 正確、port 8766 沒被舊 process 占用。

Ollama WinError 10061 / connection refused
-> Windows server 開著，但 Windows Ollama 沒開。PowerShell 跑 curl.exe http://127.0.0.1:11434/api/tags；失敗就 Start-Process -FilePath "ollama" -ArgumentList "serve"。

debug_version 不是 11
-> 重新 scp 同步 Windows bundle，關掉舊 server，重開。

TTS ready 但沒聲音
-> curl /health，確認 AUDIO_DEVICE=plughw:CARD=UACDemoV10,DEV=0 和 audio.device 是 UACDemo；重開 TTS。

TTS 有聲音但超小
-> Terminal 3 加 `--tts-volume-gain 2.25` 後重啟 Wake Bridge；這會送 `volume_gain` 給新版 TTS server，只放大 raw playback，不改 ALSA 系統音量。
-> 若 curl /speak_async 帶 `volume_gain` 回 422，代表 Terminal 2 還是舊 server，重啟 TTS server。

說「為什麼沒聲音」卻跑去播音樂
-> 重啟 Terminal 4 music tool 和 Terminal 3 bridge；最新版會把「沒聲音 / 聲音太小 / 音量 / 聽到聲音」當成 audio complaint，不會因單字 `聽` 觸發 play。

情緒表情不對
-> 先看 Windows debug 裡 `control.emotion` 和 Terminal 3 的 `FRDM UART TX: Speaking N`。目前正規化後應是：`angry -> Speaking 2`、`concerned -> 1`、`happy/excited -> 4`、`confused/curious -> 5`、`sad/sleepy -> 3`。如果 Terminal 3 還印 `concerned code 4`，代表舊 bridge 還活著，先 `pkill -9 -f wake_voice_chat_frdm_bridge.py` 再重啟。
-> 如果 FRDM RX 有 `Speaking N` 但仍印 `emotion: neutral`，問題在 FRDM `SpeakingGui(char *pValue)` parser，套用 `emotion_robot_controller/frdm_firmware/patches/speaking_gui_emotion_fix.c`。

音樂 pause/resume 沒反應
-> 確認 Terminal 4 `music_web_player.py --backend mpv` 開著，`curl http://127.0.0.1:8788/health` 裡 active/paused 合理。browser backend 不能可靠 pause/resume。

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
[ ] /health debug_version=11
[ ] Focus Work Mode 測試時，/debug routes 有 /focus-check
[ ] vision_model=qwen35-fast:latest
```

Jetson：

```text
[ ] TTS /health ready=true
[ ] TTS audio device 是 UACDemo
[ ] --list-mics 有 UACDemo input
[ ] --list-uarts 有 FRDM
[ ] lsusb 有 UACDemo / Global Shutter Camera / MCU-LINK
[ ] Music Web Player /health ok=true
[ ] Music backend 是 mpv，pause/resume 測試 OK
[ ] wake bridge self-test OK
[ ] focus_work_mode.py self-test OK
[ ] focus mock session 產生 focus_log.jsonl / focus_report.md
[ ] 純語音 used_vision=False
[ ] 視覺句 used_vision=True
[ ] FRDM UART TX/RX 正常
```
