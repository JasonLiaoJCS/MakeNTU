# FRDM Wake Bridge + Vision + Focus + To-Do + Music + Weather

This folder is the Jetson integration layer for the MakeNTU desktop pet robot. For live operation, start with [QUICK_START.md](QUICK_START.md). This README explains the architecture, data flow, UART format, feature boundaries, and debugging workflow.

Mandarin phrases remain in command examples because they are the actual demo utterances and runtime output strings used by the current intent rules.

## Read This First

```text
Only need to run the demo        -> use QUICK_START.md section 0
Need to understand architecture  -> read What This Does / FRDM State Machine
Need to edit prompt/control      -> read Structured Reply And Control
Need to verify FRDM behavior     -> read FRDM State Machine / FRDM UART Timing
Need to tune head motion         -> read Emotion And Head Motion
Need to debug camera/vision      -> read Vision Routing / Camera And Image Storage
Need to debug on-site failures   -> read Debug Log Guide / Troubleshooting
```

Current stable demo baseline:

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
tts                   : local Piper /speak_async, AUDIO_DEVICE=auto:UACDemo
tts volume            : --tts-volume-gain 4.8, server accepts volume_gain 0.05..8.0
beep volume           : --beep-volume 0.35
USB output volume     : PulseAudio UACDemo ~70%, ALSA PCM 70%
music/weather         : local tool server on 127.0.0.1:8788, mpv + Open-Meteo
ESP32-S3 BLE          : fan + MAX7219 LED + DS18B20 status over BLE, ESP32S3_FAN_LED_TEMP
local temperature     : preferred from ESP32-S3 BLE notify, legacy HTTP push/pull still supported
to-do list            : local JSON voice tool, frdm_uart_context_sender/logs/todo_list.json
focus work mode       : voice-triggered start/stop, periodic /focus-check, JSONL log + Markdown report
pet idle reflection   : every ~30s ask /text-chat an internal self-question; most checks stay silent, occasional worthy shares use TTS
```

Do not pin numeric USB indexes. After a USB replug, `--device 25`, `--beep-device 24`, `--camera-id 0`, and `/dev/ttyACM0` may change. For the live demo, use keyword/auto discovery.

Conservative on-site volume settings:

```text
TTS .env default      : DEFAULT_VOLUME_GAIN=4.8
Wake Bridge default   : --tts-volume-gain 4.8
PulseAudio USB sink   : about 70%
ALSA USB PCM          : 70%
Too loud              : try 3.6 and --beep-volume 0.25
Too quiet             : try 6.0 before going higher
```

Volume is reset to absolute values in three places: `~/.config/systemd/user/makentu-uacdemo-volume.service` at boot/login with user linger enabled, `~/.config/autostart/makentu-uacdemo-volume.desktop` after the desktop session starts, and `~/.bashrc` whenever a new terminal opens. `set_uacdemo_volume.sh` rejects relative values such as `+5%`, so repeated boots or terminals do not drift louder or quieter.

When code changes, restart the matching terminal:

```text
Windows server / prompt / emotion changed     -> restart Windows Terminal 1
TTS server / volume_gain changed              -> restart Jetson Terminal 2
Wake Bridge / UART / routing / docs changed   -> restart Jetson Terminal 3
Music/weather intent changed                  -> restart Jetson Terminal 4
```

## What This Does

```text
Bridge process starts
-> FRDM startup waits 2 seconds
-> Jetson sends Time <payload> over UART
-> Jetson calls local /weather for daily and current data, merges latest ESP32 BLE/DS18B20 local temperature if available, and sends both Weather payloads over UART
-> Normal 0 0
-> while idle, every ~30 seconds Jetson may ask the model an internal pet-reflection question
-> if the model answers PET_IDLE_SILENCE, nothing is spoken
-> if the model decides a thought is worth sharing, Jetson speaks one short spontaneous line
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

Focus work mode is a side mode. When the transcript is a work-mode command, the Wake Bridge starts `focus_work_mode.py` as a separate process, samples the camera every 60 seconds by default, posts each image to Windows `/focus-check`, writes `focus_log.jsonl`, then generates `focus_summary.json` and `focus_report.md` when the session ends. The report title format is `專心報告：YYYY/MM/DD/HH 開始的專注時段`, and the same value is stored as `report_title` for Discord or future front-end use. Photos are memory-only by default; use `--focus-save-images` only for debugging.

Pet idle reflection is intentionally blocked while focus work mode is running. The bridge may still do silent internal checks during normal standby, but it will not start a spontaneous TTS line during focus mode. Disable this behavior with `--no-pet-idle-reflection` or `PET_IDLE_REFLECTION=0`; tune it with `--pet-idle-interval-sec`, `--pet-idle-share-cooldown-sec`, and `--pet-idle-debug`.

This bridge does not use Gemini, OpenAI, or cloud LLM APIs. ASR and Ollama run locally on the Windows desktop. Wake word, camera, TTS, UART, music/weather routing, ESP32 BLE fan/LED/temp bridging, to-do list, and focus orchestration run locally on the Jetson. Weather uses the local tool server and Open-Meteo; music uses local `mpv`/`yt-dlp`.

## FRDM State Machine

The current FRDM firmware only needs to support these monitor commands:

```text
Sleep
Normal
Thinking
Speaking
Music
Focus
Weather
Time
Todo
Health
ShowNum
MotorPitch
MotorYaw
MotorYawPitch
```

The Wake Bridge rejects older emotion-screen commands such as `Happy 0 0` or `Curious 0 0`. Facial emotion is now encoded as the first argument to `Speaking`.

```text
bridge startup                                      -> wait 2s -> Time <payload> -> Weather daily <payload> -> Weather current <payload> -> Normal 0 0
Hey Jarvis detected                                 -> Thinking 0 0
AI/TTS starts                                       -> Speaking <0..5>
TTS speaking                                        -> MotorYawPitch <yaw> <pitch> natural motion loop
follow-up listening                                 -> Thinking 0 0
bye / goodbye / 掰掰 / 拜拜 / 再見                 -> Normal 0 0, then wake-only standby
sleep / rest / good night / 睡覺 / 休息 / 晚安      -> Sleep 0 0, then wake-only standby
play/resume music / 播放 / 繼續音樂                -> Music 0 0, then wake-only standby
pause/stop music / 暫停 / 停止音樂                 -> Normal 0 0, then wake-only standby
focus/work mode / 專注 / 專心 / 工作模式           -> Focus 0 0, then wake-only standby
come back / normal / 回來 / 回到正常               -> Normal 0 0
```

`Speaking`, `MotorPitch`, and `MotorYaw` use a single-argument wire format:

```text
Speaking 2
MotorPitch 90
MotorYaw 90
```

`MotorYawPitch` is the only head-motor command with two numeric arguments:

```text
MotorYawPitch 120 90   # yaw=120, pitch=90
```

Other screen commands keep two arguments:

```text
Thinking 0 0
Normal 0 0
Sleep 0 0
Music 0 0
Focus 0 0
```

`Time` is a startup payload for updating GUI clock/date widgets. It also does not switch the screen by itself:

```text
Time 20260509,213005,6,+480
```

Payload format:

```text
Time yyyymmdd,hhmmss,isoweekday,utc_offset_min
```

`Weather` is a startup payload for updating the sleep screen weather data. It does not switch the screen by itself:

```text
Weather daily,23,29,40,61
Weather daily,23,29,40,61,254
Weather current,27,27,0,2
Weather current,27,27,0,2,254
```

Payload format:

```text
Weather kind,low_or_temp,high_or_temp,rain_percent,open_meteo_weather_code
Weather kind,low_or_temp,high_or_temp,rain_percent,open_meteo_weather_code,local_temp_c_x10
```

The optional 6th field is the ESP32/DS18B20 local temperature in Celsius multiplied by 10. For example, `254` means `25.4 C`, and `-42` means `-4.2 C`. The field is omitted when no recent ESP32 temperature is available, so older FRDM firmware that only parses 5 fields can keep running while the temperature path is being tested.

`TempRoom` is the direct periodic room-temperature payload for FRDM widgets. It is sent independently of weather queries whenever a fresh ESP32 BLE temperature notify is available:

```text
TempRoom 254
```

Payload format is also Celsius multiplied by 10. The default live bridge sends it every `--temp-room-uart-interval-sec 10` seconds and skips stale readings older than `--temp-room-uart-max-age-sec 30`.

FRDM parser contract:

```text
wire line      : TempRoom <signed_celsius_x10>
example        : TempRoom 254
meaning        : 25.4 C
valid range    : -550..1250, matching the DS18B20 range -55.0..125.0 C
frequency      : Wake Bridge throttles to one line every 10 seconds by default
screen effect  : update the room-temperature widget only; do not switch pages/screens
```

Recommended FRDM-side state:

```c
static int g_room_temp_c_x10 = 0;
static bool g_room_temp_valid = false;
static uint32_t g_room_temp_updated_ms = 0;
```

When parsing `TempRoom 254`, store `g_room_temp_c_x10 = 254`, set `g_room_temp_valid = true`, and render `25.4 C`. If no `TempRoom` has arrived yet, show `--.- C` or keep the previous value.

Dashboard data commands update swipe-page widgets and do not switch screens by themselves. `Todo x,y` means `x=open/unfinished count` and `y=done/completed count`.

```text
Todo 3,1
TodoItem 1,17,open,%E5%AF%AB%E5%A0%B1%E5%91%8A
TodoItem 2,18,open,Buy%20milk
TodoEnd 2
Music playing,Lo-fi%20Study,mpv
Focus focused,25,2
Health win=1,tts=1,music=1,camera=1
```

Payload formats:

```text
Todo open_count,done_count
TodoItem slot,id,status,url_encoded_text
TodoEnd visible_open_item_count
Music state,url_encoded_title,backend
Focus state,remaining_min,state_streak_count
Health win=0_or_1,tts=0_or_1,music=0_or_1,camera=0_or_1
```

FRDM checkbox events go the other direction. FRDM should send one completed item id at a time:

```text
TodoDone 17
```

Jetson marks that to-do item done by stable `id`, then sends a fresh `Todo` + `TodoItem` + `TodoEnd` snapshot back to FRDM. Use the `id` field, not the visible `slot`, because slots change after each completion.

The Jetson calls the existing `/weather` tool and Open-Meteo. The FRDM has no network dependency; it only parses UART. The FRDM reference patches are:

```text
emotion_robot_controller/frdm_firmware/patches/time_uart_screen.c
emotion_robot_controller/frdm_firmware/patches/weather_uart_sleep_screen.c
emotion_robot_controller/frdm_firmware/patches/motor_yaw_pitch_parser.c
```

## Table Of Contents

```text
Files
Standard Startup
Music Routing And Playback
Weather Routing
ESP32-S3 BLE Fan LED Temperature
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
wake_voice_chat_frdm_bridge.py   # official Hey Jarvis hands-free demo
run_wake_bridge_full_demo.sh      # recommended Terminal 3 launcher with auto device detection
auto_demo_devices.sh              # waits for UACDemo speaker/mic, FRDM UART, and camera
set_uacdemo_volume.sh             # normalizes UACDemo PulseAudio/ALSA volume
focus_work_mode.py               # focus work mode subprocess, started/stopped by the Wake Bridge
esp32s3_ble_fan_led_controller.py # standalone BLE CLI + helper used by Wake Bridge
voice_chat_frdm_uart_bridge.py   # manual Enter-to-record version
frdm_uart_context_sender.py      # standalone FRDM UART command sender
recover_demo_usb.sh              # Jetson USB host controller recovery
QUICK_START.md                   # on-site operating guide
examples/esp32s3_ble_fan_led_temp/          # full ESP32-S3 Arduino BLE + fan + LED + temp sketch
examples/esp32s3_ble_advertise_minimal/     # minimal BLE advertisement/connectivity sketch
```

Windows server files:

```text
emotion_robot_controller/voice_stt_remote/desktop_fast_chat_server.py
emotion_robot_controller/voice_stt_remote/windows_desktop_server_bundle/desktop_fast_chat_server.py
```

The Windows desktop runs the bundle copy, so server changes must be synced with `scp` before restarting the Windows server.

## Standard Startup

The full copy-paste Terminal 1/2/4/3 flow is in [QUICK_START.md](QUICK_START.md). The recommended Terminal 3 path is:

```bash
cd /home/asrlab-yian/MakeNTU
bash frdm_uart_context_sender/auto_demo_devices.sh
./frdm_uart_context_sender/run_wake_bridge_full_demo.sh
```

`run_wake_bridge_full_demo.sh` exports the auto/keyword device settings, normalizes UACDemo volume, then launches the bridge. Override only when needed:

```bash
MAKE_NTU_TTS_VOLUME_GAIN=3.6 BEEP_VOLUME=0.25 MUSIC_MPV_VOLUME=220 MUSIC_MPV_VOLUME_MAX=300 ./frdm_uart_context_sender/run_wake_bridge_full_demo.sh
```

`MUSIC_MPV_VOLUME` controls only mpv music playback. The default launcher now uses `220` with `MUSIC_MPV_VOLUME_MAX=300`, so music is much louder without changing Piper TTS speech volume.

For the ESP32-S3 BLE board, prefer pinning the MAC discovered by scan-only:

```bash
ESP32_BLE_ADDRESS=78:E3:6D:18:94:6A ESP32_BLE_ADAPTER=hci0 ./frdm_uart_context_sender/run_wake_bridge_full_demo.sh
```

If the fan reports `FAN=ON` but stalls at low slider values, raise the minimum nonzero PWM:

```bash
FAN_MIN_PWM=120 ESP32_BLE_ADDRESS=78:E3:6D:18:94:6A ./frdm_uart_context_sender/run_wake_bridge_full_demo.sh
```

Do not hand-type the last few parameters in manual mode; the common mistake is accidentally typing `--uart-debug\terval 0.75`. The correct tail is:

```bash
  --tts-poll-interval 0.75 \
  --tts-debug \
  --uart-debug
```

Core Wake Bridge command:

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
  --focus-alert-threshold 1 \
  --fan-device-id desk_fan \
  --fan-speed-max 100 \
  --esp32-ble \
  --esp32-ble-adapter hci0 \
  --esp32-ble-scan-duplicates \
  --esp32-ble-min-fan-pwm 96 \
  --esp32-ble-command-queue-max 64 \
  --esp32-dashboard-host 127.0.0.1 \
  --esp32-dashboard-port 8791 \
  --fan-duplicate-suppress-sec 2.0 \
  --todo-list-path /home/asrlab-yian/MakeNTU/frdm_uart_context_sender/logs/todo_list.json \
  --focus-notify-mode discord \
  --focus-discord-webhook-url "$DISCORD_WEBHOOK_URL" \
  --music-backend mpv \
  --music-mpv-audio-device auto \
  --music-mpv-volume 220 \
  --music-mpv-volume-max 300 \
  --music-mpv-ready-timeout 1.5 \
  --music-timeout 5 \
  --music-wake-pause-timeout 0.25 \
  --music-wake-beep-settle 0.05 \
  --post-music-standby-cooldown 0.8 \
  --music-debug \
  --weather-default-location Taipei \
  --weather-timeout 6 \
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
  --tts-debug \
  --uart-debug
```

This full mode enables wake word, conversation mode, speech-end image capture, FRDM UART, TTS, To-Do, Music, Weather, and Focus Work Mode. After the first `Hey Jarvis`, follow-up turns do not need the wake word. The bridge returns to wake-only standby after a goodbye phrase, sleep command, music command, focus command, or follow-up timeout.

For one-shot Q&A, remove `--conversation-mode`, `--turn-listen-timeout`, `--session-idle-timeout`, and `--max-session-turns`. For lower latency, add `--ultra-response`; if speech is cut too early, use the more conservative `--turbo-response`.

Keep `--noisy-room` for loud demo spaces. It raises speech/silence gates; the live demo launcher also passes `--beep-volume 0.35` so the cue beep stays controlled. TTS uses a fixed absolute `--tts-volume-gain 4.8`, which is `2x` over the previous `2.4`. If TTS is too loud, try `3.6`; if it is still too quiet, try `6.0`.

Beep-only test:

```bash
python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py --beep-keyword UACDemo --noisy-room --beep-volume 0.35 --test-beep
```

For a room with background volume around `10000` and speech around `19000`, `--noisy-room` should put thresholds near:

```text
wake accept volume >= 13500
speech start       >= 14500
speech end/silence <= 13000
```

Do not pin device indexes:

```text
do not use --device 25
do not use --beep-device 24
do not use --camera-id 0
do not use --uart-port /dev/ttyACM0
```

Use:

```text
mic       : --mic-keyword UACDemo
beep      : --beep-keyword UACDemo
TTS audio : AUDIO_DEVICE=auto:UACDemo
camera    : --camera-id auto
FRDM      : --uart-port auto
music     : --music-backend mpv
weather   : --weather-default-location Taipei
todo      : --todo-list-path frdm_uart_context_sender/logs/todo_list.json
```

Device preflight runs at startup:

```text
stop old wake bridge / camera test processes
stop stale arecord / aplay / mpv / ffplay / paplay
if UACDemo/camera/FRDM disappeared, reset Jetson USB host
scan /dev/video*, UACDemo /dev/snd/pcm*, /dev/ttyACM*
stop same-user processes that still own those device nodes
skip sounddevice output wait unless --beep-player sounddevice is explicitly used
keep jetson_piper_tts.server alive
keep pulseaudio / pipewire / wireplumber by default
```

Related options:

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

Recording gates are adaptive, not just fixed volume thresholds:

```text
speech_start_threshold = max(volume_min, noise_floor + speech_start_margin)
silence_threshold      = max(volume_min, noise_floor + silence_margin, peak_volume * silence_peak_ratio)
```

On-site `--noisy-room` values:

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

Tuning order in noisy rooms:

```text
start with noisy-room preset             -> --noisy-room
records until max_speech_seconds         -> try --silence-noise-ratio 1.4
background triggers Speech started       -> raise --speech-start-ratio 1.55
speech never reaches Speech started      -> lower --speech-start-ratio 1.35
demo needs faster replies                -> lower --max-speech-seconds 4
room is so noisy recording feels stuck   -> lower --max-recording-seconds 7 or 6
stuck at Recording. Speak now            -> lower --audio-read-timeout 0.75 and let bridge reopen stream
```

Recording status:

```text
phase=waiting_speech -> wake accepted; waiting for volume above start threshold
phase=speech         -> speech captured; waiting for silence/max_speech/max_recording
Max recording...     -> hard limit protection
no audio chunk       -> USB mic stream stopped; callback watchdog exits the turn
```

## Music Routing And Playback

Music playback is a local sidecar, not the main AI flow. The Wake Bridge still records, sends to the Windows server, runs TTS, and controls UART normally. It only calls `music_web_player.py` when the transcript is a rule-based music intent.

Sequence:

```text
Music is playing
-> user says Hey Jarvis
-> Wake Bridge immediately POSTs {"action": "pause"} to http://127.0.0.1:8788/music
-> beep / Thinking / recording starts
-> transcript returns from Windows ASR
-> detect_music_intent(transcript)
-> no music intent: do nothing with Music Player; music stays paused until the user says resume
-> play/change/resume music: TTS speaks confirmation first, then POST play/query or resume
-> pause/stop music: POST pause/stop before TTS, then speak a short local confirmation
-> bridge returns to standby and keeps listening for Hey Jarvis
```

Music control timing is intentionally asymmetric:

```text
play/change  -> TTS first, then start mpv, then FRDM Music 0 0, wake-only standby
resume       -> TTS first, then resume mpv from IPC pause position, FRDM Music 0 0, wake-only standby
pause        -> pause mpv first, speak "好，我先暫停音樂。", FRDM Normal 0 0, wake-only standby
stop         -> stop mpv first, speak "好，我把音樂關掉了。", FRDM Normal 0 0, wake-only standby
stop no mpv  -> speak "音樂現在沒有在播放。", FRDM Normal 0 0, wake-only standby
```

The pause/stop confirmations are local control replies. They still use Piper TTS, but force `head_motion=none`; the Wake Bridge also clears any stale speaking-motion thread before speaking them. This prevents the failure mode where music was stopped successfully but the robot made no sound while the head motor kept moving.

Example phrases:

```text
Hey Jarvis，我想要聽告白氣球       -> action=play, query=告白氣球
Hey Jarvis，幫我播 稻香            -> action=play, query=稻香
Hey Jarvis，換成 七里香            -> action=play, query=七里香
Hey Jarvis，暫停音樂               -> action=pause
Hey Jarvis，繼續播放音樂           -> action=resume
Hey Jarvis，停止音樂               -> action=stop
Hey Jarvis，講個笑話               -> no music call
Hey Jarvis，我現在是什麼表情       -> no music call, may use vision
```

For the live demo, keep Terminal 4 running:

```bash
cd /home/asrlab-yian/MakeNTU/music_web_player
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate
python3 music_web_player.py \
  --server \
  --host 127.0.0.1 \
  --port 8788 \
  --backend mpv \
  --mpv-audio-device auto \
  --mpv-volume 220 \
  --mpv-volume-max 300 \
  --mpv-ready-timeout 1.5 \
  --weather-default-location Taipei
```

Important options:

```text
--music-backend mpv              # real playback; auto also prefers mpv
--music-mpv-audio-device auto    # prefer the UACDemo USB speaker for mpv
--music-mpv-volume 220           # mpv music volume only; does not change Piper TTS
--music-mpv-volume-max 300       # mpv --volume-max ceiling, needed for values above 100
--music-timeout 5                # play/change request timeout
--music-wake-pause-timeout 0.6   # short timeout for immediate pause on wake
--music-debug                    # print Music routing / Music tool logs
--no-music                       # disable music sidecar routing
--no-music-autostart             # do not autostart sidecar on play
--no-music-pause-on-wake         # do not pause music on wake; not recommended for live demo
```

The `mpv` backend streams the first `ytsearch1:<query>` result; it does not save music into the repository. `pause` and `resume` use the mpv IPC socket, so playback position is preserved. `browser` backend only opens a search page and cannot reliably pause/resume.

Music volume is separate from Piper TTS volume:

```text
Piper TTS speech volume -> Terminal 3 --tts-volume-gain / Jetson TTS DEFAULT_VOLUME_GAIN
mpv music volume        -> Terminal 4 --mpv-volume, or Terminal 3 --music-mpv-volume when autostarting
launcher override       -> MUSIC_MPV_VOLUME=180 MUSIC_MPV_VOLUME_MAX=300 ./frdm_uart_context_sender/run_wake_bridge_full_demo.sh
```

If the music itself is quiet but TTS is fine, adjust `MUSIC_MPV_VOLUME` / `--music-mpv-volume`, not `--tts-volume-gain`. Values above 100 require `MUSIC_MPV_VOLUME_MAX` / `--music-mpv-volume-max`; otherwise mpv may clamp the request.

Health fields from `curl http://127.0.0.1:8788/health`:

```text
backend                  : mpv is the live playback mode
mpv_available            : Jetson can find mpv
yt_dlp_available         : Jetson can find yt-dlp or youtube-dl
active                   : mpv process is active
paused                   : current pause state
requested_mpv_volume     : requested music volume before clamping
requested_mpv_volume_max : requested mpv --volume-max before clamping
mpv_volume               : configured mpv music volume
mpv_volume_max           : configured mpv --volume-max ceiling
mpv_actual_volume        : live volume read back from mpv IPC while active
mpv_effective_volume     : actual volume when active, configured fallback when idle
mpv_volume_clamped       : requested volume was capped to a safe mpv range
last_query               : latest query
ipc_path                 : mpv IPC socket; pause/resume depends on it
weather_available        : /weather endpoint loaded
weather_source           : open-meteo
weather_default_location : default city for local weather
```

## Weather Routing

Weather uses the same Terminal 4 sidecar as music, with a separate endpoint:

```text
music   -> http://127.0.0.1:8788/music
weather -> http://127.0.0.1:8788/weather
health  -> http://127.0.0.1:8788/health
```

After Windows ASR returns a transcript, the Wake Bridge runs local rule-based weather intent detection. It only calls Open-Meteo for explicit weather questions.

```text
transcript='明天下午三點台北天氣如何'
-> detect_weather_intent=True
-> POST /weather {"text": transcript, "default_location": "Taipei"}
-> Jetson calls Open-Meteo geocoding + forecast API
-> Wake Bridge reads the latest ESP32/DS18B20 local temperature if enabled
-> response.reply='台北市、台湾明天約15:00預報約 ...'
-> replace desktop AI generic reply
-> FRDM UART gets Weather <Open-Meteo data>[,<local_temp_c_x10>]
-> TTS speaks weather answer
-> FRDM uses emotion=curious, head_motion=curious_peek
```

Supported phrases:

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

Usually not weather intent:

```text
Hey Jarvis，今天幾號
Hey Jarvis，講個笑話
Hey Jarvis，幫我開電風扇
Hey Jarvis，我現在是什麼表情
```

Wake Bridge options:

```text
--weather-url http://127.0.0.1:8788/weather
--weather-default-location Taipei
--weather-timeout 6
--weather-api-timeout 5
--weather-debug
--no-weather
--weather-always-call
--esp32-temperature-mode disabled|push|pull|both
--esp32-temperature-host 0.0.0.0
--esp32-temperature-port 8790
--esp32-temperature-path /temperature
--esp32-temperature-url http://ESP32_IP/temperature
--esp32-temperature-timeout 0.6
--esp32-temperature-max-age-sec 120
--esp32-temperature-debug
--no-weather-local-temperature
```

ESP32 local-temperature merge:

```text
preferred : DS18B20 GPIO7 -> ESP32-S3 BLE notify -> Jetson Terminal 3 -> Weather UART -> FRDM
legacy    : DS18B20 -> ESP32 HTTP POST/pull -> Jetson Terminal 3 -> Weather UART -> FRDM
```

Startup weather sends two payloads: one whole-day payload from "今天天氣如何" and one current payload from "現在天氣如何". Explicit whole-day questions such as "明天天氣如何" still produce `Weather daily,...`; current/location weather questions produce `Weather current,...`.

If `--esp32-ble` is enabled and BLE status notify is fresh, the `TEMP` field from `TEMP:27.31,FAN:ON,SPEED:180,LED:ON` is used as the local temperature. The older HTTP push/pull path below remains available for temperature-only boards.

Recommended live mode is `push`. Terminal 3 opens an HTTP receiver on the Jetson, and the ESP32 periodically POSTs its current DS18B20 reading:

```text
Jetson receiver : http://JETSON_LAN_IP:8790/temperature
ESP32 payload   : {"ok":true,"temperature_c":25.4}
UART output     : Weather daily,19,23,76,53,254
UART output     : Weather current,20,20,0,3,254
```

Use `pull` only if the ESP32 already exposes its own HTTP API, for example `http://ESP32_IP/temperature`. In `both` mode, the Wake Bridge first uses a recent pushed reading and falls back to pulling the ESP32 URL. A pushed reading older than `--esp32-temperature-max-age-sec` is ignored.

Manual ESP32 receiver test after Terminal 3 is running:

```bash
curl -X POST http://127.0.0.1:8790/temperature \
  -H "Content-Type: application/json" \
  -d '{"ok":true,"temperature_c":25.4}'
```

Expected Terminal 3 log:

```text
ESP32 temperature receiver: http://0.0.0.0:8790/temperature
Weather local temperature: push receiver http://0.0.0.0:8790/temperature
Weather UART sent: Weather daily,19,23,76,53,254 (local=25.4 C)
Weather UART sent: Weather current,20,20,0,3,254 (local=25.4 C)
```

FRDM firmware note: update `WeatherGui` / `ParseWeatherPayload` to accept either 5 fields or 6 fields. The 6th field is `local_temp_c_x10`, not a float. Display it as integer Celsius plus one decimal digit, for example `254 -> 25.4 C`, in the desired LVGL label.

Manual test:

```bash
curl -X POST http://127.0.0.1:8788/weather \
  -H "Content-Type: application/json" \
  -d '{"text":"明天下午三點所在地天氣如何","default_location":"Taipei"}'
```

Fallback behavior:

```text
/weather unreachable    -> Wake Bridge tries to autostart Terminal 4 sidecar
Open-Meteo unreachable  -> TTS says the local weather tool or source was unavailable
ESP32 temp unavailable  -> Weather UART keeps the old 5-field payload
intent not matched      -> no weather call; normal desktop AI reply continues
```

## ESP32-S3 BLE Fan LED Temperature

The current preferred ESP32 path is BLE, not the older WiFi temperature-only path. One ESP32-S3 peripheral exposes fan control, MAX7219 LED control, and DS18B20 temperature status:

```text
BLE device name      : ESP32S3_FAN_LED_TEMP
Service UUID         : 12345678-1234-1234-1234-1234567890ab
Command char UUID    : 12345678-1234-1234-1234-1234567890ac
Status char UUID     : 12345678-1234-1234-1234-1234567890ad
Status notify format : TEMP:27.31,FAN:ON,SPEED:180,LED:ON
```

ESP32 hardware sketch:

```text
full sketch    : examples/esp32s3_ble_fan_led_temp/esp32s3_ble_fan_led_temp.ino
BLE-only test  : examples/esp32s3_ble_advertise_minimal/esp32s3_ble_advertise_minimal.ino

L9110S fan     : INA=GPIO5 PWM, INB=GPIO6 LOW, VCC=5V, GND common
MAX7219 8x8    : DIN=GPIO11, CS=GPIO10, CLK=GPIO12
DS18B20        : DATA=GPIO7, VCC=3.3V, GND common, 4.7k pull-up to 3.3V
```

Jetson standalone BLE CLI:

```bash
cd /home/asrlab-yian/MakeNTU
source emotion_robot_controller/.venv/bin/activate

python3 frdm_uart_context_sender/esp32s3_ble_fan_led_controller.py \
  --address 78:E3:6D:18:94:6A \
  --adapter hci0 \
  --scan-duplicates \
  --no-tts-reminder
```

CLI commands:

```text
TEMP?
LED_ON
LED_OFF
FAN_ON
FAN_OFF
FAN_SPEED:180
PERCENT:50      # FRDM/manual percent -> FAN_SPEED:128
FanSpeed 16     # FRDM slider percent -> FAN_SPEED:96 by default
voice fan faster
voice led off
```

The percent conversion intentionally has a nonzero floor. Small DC fans often do not start at very low PWM duty, so values such as `FanSpeed 16` no longer send `41`; they send at least `FAN_MIN_PWM`, default `96`.

```text
0%      -> FAN_OFF
1..37%  -> FAN_SPEED:96 by default
50%     -> FAN_SPEED:128
75%     -> FAN_SPEED:191
100%    -> FAN_SPEED:255
```

Tune the floor if the motor still stalls:

```bash
FAN_MIN_PWM=120 python3 frdm_uart_context_sender/esp32s3_ble_fan_led_controller.py \
  --address 78:E3:6D:18:94:6A \
  --adapter hci0 \
  --scan-duplicates \
  --no-tts-reminder
```

Wake Bridge integration:

```bash
ESP32_BLE_ADDRESS=78:E3:6D:18:94:6A \
ESP32_BLE_ADAPTER=hci0 \
ESP32_BLE_COMMAND_QUEUE_MAX=64 \
FAN_MIN_PWM=96 \
./frdm_uart_context_sender/run_wake_bridge_full_demo.sh
```

BLE is intentionally isolated from the rest of the Wake Bridge. The reconnect loop runs in its own background thread named `esp32s3-ble-reconnect-loop`; scan/connect retries must not block wake-word recording, TTS, music, weather, FRDM UART, to-do, focus mode, or the dashboard. If `bleak` or the helper module is missing, the bridge prints a warning and starts in degraded mode instead of exiting.

Runtime behavior:

```text
BLE connected                  -> fan/LED commands write immediately, TEMP notify feeds Weather + TempRoom
BLE disconnected               -> reconnect loop keeps scanning/connecting in the background
voice/dashboard/FRDM command   -> command is queued while disconnected, then sent after reconnect
command queue full             -> oldest pending BLE command is dropped; other bridge features continue
bleak/helper unavailable       -> ESP32 BLE is disabled for this run; Wake/TTS/music/weather/FRDM continue
```

The disconnected command queue is bounded by `--esp32-ble-command-queue-max` / `ESP32_BLE_COMMAND_QUEUE_MAX`, default `64`.

When `--esp32-ble` is enabled, Wake Bridge also exposes a local Dashboard control API:

```text
GET  http://127.0.0.1:8791/api/esp32/status
POST http://127.0.0.1:8791/api/esp32/control
```

Useful status fields:

```text
requested          : --esp32-ble was requested
enabled            : BLE runtime is usable; false if helper/bleak is unavailable
running            : reconnect worker thread is alive
connected          : currently connected to ESP32-S3
queued_pending     : BLE commands waiting for reconnect
dropped_pending    : old queued BLE commands dropped because the queue hit its limit
unavailable_reason : why BLE is disabled in degraded mode
```

The Smart Home Dashboard uses this path for the Live tab appliance controls:

```text
LED Light toggle -> LED_ON / LED_OFF
Fan Speed slider -> FAN_ON + FAN_SPEED:<0..255>, or FAN_OFF at 0%
Local Temperature -> TEMP from BLE status notify
```

FRDM also receives the same BLE temperature as a direct periodic UART line:

```text
ESP32 notify TEMP:25.43,... -> TempRoom 254 every 10 seconds
```

Tune with `TEMP_ROOM_UART_INTERVAL_SEC` / `--temp-room-uart-interval-sec`; disable with `--no-temp-room-uart`.

Two runtime paths can send `TempRoom`:

```text
normal demo path : wake_voice_chat_frdm_bridge.py owns FRDM UART and sends TempRoom from BLE notify
standalone path  : esp32s3_ble_fan_led_controller.py --frdm-uart-port auto sends TempRoom while testing BLE only
```

Standalone BLE-to-FRDM test:

```bash
python3 frdm_uart_context_sender/esp32s3_ble_fan_led_controller.py \
  --address 78:E3:6D:18:94:6A \
  --adapter hci0 \
  --scan-duplicates \
  --frdm-uart-port auto \
  --frdm-temp-room-interval-sec 10 \
  --no-tts-reminder
```

Use the standalone path only when the full Wake Bridge is stopped; otherwise both processes will compete for the same FRDM UART.

Expected log:

```text
[12:00:01] ESP32 status: TEMP=25.43 C, FAN=OFF, SPEED=0, LED=OFF
FRDM UART TX: TempRoom 254
```

Wake Bridge also writes live listening health for the Dashboard:

```text
frdm_uart_context_sender/logs/wake_status.json
```

That file contains the current listening phase, volume, recent peak, wake score, threshold, and noise floor. The Dashboard System Health label is `Wake Listen`, not `TTS`, so it reflects whether the microphone/wake loop is still updating.

Boot auto-start:

```bash
cd /home/asrlab-yian/MakeNTU
./frdm_uart_context_sender/install_wake_bridge_service.sh \
  --address 78:E3:6D:18:94:6A \
  --adapter hci0

systemctl --user start makentu-wake-bridge.service
journalctl --user -u makentu-wake-bridge.service -f
```

The service loads `~/.config/makentu/wake-bridge.env`, enables user linger, and restarts the full Wake Bridge if the process exits. BLE reconnect is inside a separate worker thread, so normal short ESP32/BlueZ drops do not require a manual restart and do not pause the main voice loop. Edit the env file when the ESP32 MAC, Tailscale URL, queue limit, or `FAN_MIN_PWM` changes:

```bash
nano ~/.config/makentu/wake-bridge.env
systemctl --user restart makentu-wake-bridge.service
```

When voice fan/LED control is requested while BLE is disconnected, the Jetson reply explicitly says the ESP32-S3 Bluetooth link is not connected and that it is reconnecting. The command is queued, then sent automatically after the BLE bridge reconnects. If BLE is connected and the latest ESP32 status is already `FAN:OFF`, asking to turn the fan off gets a spoken already-off reply and skips the redundant `FAN_OFF` write.

Fan intent parsing is deliberately narrower than generic audio volume parsing. Phrases like `音樂太小聲，幫我調大音量` are treated as audio/music volume complaints, not fan speed commands. Fan speed changes require fan context such as `風扇`, `風量`, `風速`, or hot/cold wording.

Voice appliance control is handled before the general AI route, like a local tool:

```text
voice "關風扇" with BLE connected        -> send FAN_OFF, speak confirmation, head_motion=none
voice "關風扇" and ESP32 already FAN:OFF -> no duplicate BLE write, speak already-off reply
voice "全部關掉" / ALL_OFF               -> send ALL_OFF, speak confirmation, head_motion=none
voice command while BLE disconnected     -> queue command, speak reconnecting/queued explanation
voice "音樂太小聲，調大音量"             -> not BLE; handled as audio/music volume context
after local ESP32 control in conversation mode -> FRDM Normal 0 0, wake-only standby
```

The `head_motion=none` guarantee matters here too: closing a fan or LED should never start a speaking head-motion loop. If TTS cannot start, the bridge clears active speaking motion and returns the face/motor to idle instead of continuing to move silently.

Wake Bridge behavior:

```text
ESP32 status notify      -> updates latest local temperature for Weather UART
ESP32 status notify      -> sends TempRoom <Celsius*10> to FRDM every 10 seconds
FRDM FanSpeed 0..100     -> FAN_OFF or FAN_SPEED 0..255 with min PWM floor
voice "開風扇 / 關風扇" -> BLE fan commands handled locally before general AI route
voice "轉快 / 轉慢"     -> relative fan speed commands
voice "LED on/off"      -> LED_ON / LED_OFF
TEMP > threshold         -> optional passive TTS reminder, user can answer by voice or FRDM
BLE connected + FAN:OFF -> voice "關風扇" gets a spoken already-off reply, no duplicate FAN_OFF write
connected FAN_OFF/ALL_OFF -> spoken confirmation, no head motion, return to wake-only standby in conversation mode
```

The full ESP32 sketch also protects the hardware side: `FAN_ON` uses a default speed, nonzero speeds are clamped to `FAN_MIN_RUNNING_SPEED=96`, and a short `255` PWM start kick helps the fan overcome static friction. If `FAN_SPEED:255` still does not move the motor, debug wiring/power before changing software.

BLE scan and BlueZ notes:

```bash
python3 frdm_uart_context_sender/esp32s3_ble_fan_led_controller.py \
  --scan-only \
  --scan-target-only \
  --adapter hci0 \
  --scan-duplicates \
  --scan-timeout 30
```

Expected target line may be unnamed but still valid:

```text
* 78:E3:6D:18:94:6A    (unnamed) rssi=-95 uuids=12345678-1234-1234-1234-1234567890ab
```

Use `--address <MAC>` when the name is missing or unstable. If BlueZ says the device address was not found, scan first with duplicates, then connect by the seen address. If the phone is connected to the ESP32 peripheral, disconnect it before Jetson connects.

BLE recovery commands:

```bash
pkill -f esp32s3_ble_fan_led_controller.py || true
pkill -f bluetoothctl || true
sudo systemctl restart bluetooth
sleep 3
sudo rfkill unblock bluetooth
bluetoothctl show
```

Dashboard warning note:

```text
WARNING: fan dashboard sync failed: <urlopen error [Errno 111] Connection refused>
```

This only means the optional `smart_home_dashboard/server.py` API on port `8789` is not running. It does not block BLE fan control. Start Terminal 5 or add `--no-fan-dashboard-sync` when dashboard sync is not needed.

## To-Do List

The To-Do List is a local voice tool built into the Wake Bridge. It only triggers when the transcript explicitly asks for to-do operations. It does not call another server endpoint, use the camera, or change focus work mode state.

Data is stored locally:

```text
default path -> frdm_uart_context_sender/logs/todo_list.json
format       -> version, next_id, items[]
privacy      -> task text + timestamps only; no photo/audio
```

Common phrases:

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

Processing order:

```text
Windows ASR transcript
-> local to-do intent
-> if matched: update JSON, override reply/control locally, TTS speaks result
-> if not matched: continue focus/music/weather/general AI path
```

The tool lives in normal mode, but explicit to-do commands still take priority while focus mode is running. General chatting is still blocked during focus mode so the robot does not encourage distraction.

Options:

```text
--todo-list-path /home/asrlab-yian/MakeNTU/frdm_uart_context_sender/logs/todo_list.json
--no-todo-list
--todo-debug
```

Quick JSON check:

```bash
cat /home/asrlab-yian/MakeNTU/frdm_uart_context_sender/logs/todo_list.json
```

## Focus Work Mode

Focus Work Mode detects whether the user is focused, away, using a phone, sleeping, distracted, uncertain, or in an error state. It samples once every 60 seconds by default and runs separately from normal conversation mode.

```text
Hey Jarvis
-> start focus work / work mode / pomodoro
-> Wake Bridge starts focus_work_mode.py
-> focus_work_mode.py captures one photo every N seconds
-> POST image to Windows /focus-check
-> append focus_log.jsonl
-> on session end, write focus_summary.json + focus_report.md
-> optional Discord webhook notification
-> end work / stop focus
-> Wake Bridge stops focus process and returns to normal mode
```

Images are memory-only by default:

```text
camera JPEG bytes -> /focus-check -> delete bytes
log/report only keep state, score, evidence text, timestamp
```

Use `--focus-save-images` only for debugging.

Common phrases:

```text
Hey Jarvis，開始專心工作
Hey Jarvis，開始專心工作 25 分鐘 寫報告
Hey Jarvis，番茄鐘 30 分鐘
Hey Jarvis，結束工作
Hey Jarvis，停止專心
Hey Jarvis，下班
```

State labels:

```text
focused     focused
away        away
phone       likely using phone
sleeping    likely sleeping
distracted  distracted
uncertain   uncertain
error       error
```

Wake Bridge focus options:

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

`--focus-duration-min 0` means the session does not auto-end. Say an end-work phrase to return to normal mode.

Session outputs:

```text
focus_summary.json  structured data for phone notification, email, Discord, or a future frontend
focus_report.md     human-readable Markdown report
```

The integrated report reads the same to-do JSON and includes completed to-dos during the focus session, remaining to-dos, focused time, distracted time, focus score, encouragement, and suggestions. `focus_report.md` H1, `focus_summary.json.report_title`, and the Discord first line all use `專心報告：YYYY/MM/DD/HH 開始的專注時段`.

Discord notification is best-effort. If `DISCORD_WEBHOOK_URL` or the secret file is available, the report sends a short summary; if it fails, focus session cleanup still succeeds. Discord requests include `User-Agent: DiscordBot (...)` to avoid common Cloudflare `403 error code: 1010` failures.

Safe manual test without camera/server:

```bash
cd /home/asrlab-yian/MakeNTU

python3 frdm_uart_context_sender/focus_work_mode.py \
  --mock-state phone \
  --once \
  --uart-dry-run \
  --log-root /tmp/focus_test
```

Wake-word integration test with dry-run UART and no TTS:

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

Windows server must expose `/focus-check`. If `/debug` routes do not include `/focus-check`, sync the official Windows bundle and restart the server.

Focus mode screen UART:

```text
start focus    -> Focus 0 0
during reply   -> Speaking <emotion_code> + MotorPitch/MotorYaw
still focusing -> Focus 0 0
stop/return    -> Normal 0 0
```

`--focus-alert-threshold 2` means non-focused states must appear twice in a row before changing expression, reducing single-frame false positives.

Default report directory:

```text
/home/asrlab-yian/MakeNTU/frdm_uart_context_sender/logs/focus_sessions/focus_YYYYMMDD_HHMMSS/
session.json
focus_log.jsonl
focus_summary.json
focus_report.md
```

Future phone/daily/weekly reports can build on this structure:

```text
focus_sessions/focus_*/focus_log.jsonl
-> daily_report_YYYYMMDD.json / daily_report_YYYYMMDD.md
-> weekly_report_YYYY-WW.json / weekly_report_YYYY-WW.md
-> mobile browser dashboard, email digest, Discord, LINE, or Telegram bot
```

## Windows Server

The Windows desktop runs the bundle copy of `desktop_fast_chat_server.py`. Refresh it whenever the Jetson bundle changed, `debug_version` is wrong, or you are unsure which version Windows is running.

Bundle path on Jetson:

```text
/home/asrlab-yian/MakeNTU/emotion_robot_controller/voice_stt_remote/windows_desktop_server_bundle/desktop_fast_chat_server.py
```

Windows PowerShell refresh:

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

If `scp` cannot reach the Jetson, run `tailscale ip -4` on the Jetson and replace `100.110.90.72`.

Start server:

```powershell
cd "$env:USERPROFILE\Desktop\windows_desktop_server_bundle"
.\.venv\Scripts\Activate.ps1
ollama pull qwen35-fast:latest
python desktop_fast_chat_server.py --host 0.0.0.0 --port 8766 --ollama-model qwen35-fast:latest --vision-model qwen35-fast:latest --no-think
```

Health must show:

```text
debug_version: 13
chat_ready   : True
asr_loaded   : True
ollama_model : qwen35-fast:latest
vision_model : qwen35-fast:latest
```

If `debug_version` is not 13, Windows is still running an old file or old process. For focus mode, also verify `/debug` routes include `/focus-check`.

## Structured Reply And Control

Windows `qwen35-fast:latest` is asked to return exactly one JSON object:

```json
{
  "reply": "Natural-language reply for TTS. Do not mention JSON, UART, MotorPitch, MotorYaw, or internal control fields.",
  "control": {
    "persistent_state": "normal | sleep | unchanged",
    "screen_mode": "normal | sleep | music | focus | thinking | unchanged",
    "emotion": "neutral | concerned | angry | sad | happy | curious | excited | confused | sleepy",
    "head_motion": "none | nod | double_nod | look_around | shake | gentle_nod | sleepy_drop | happy_bounce | excited_bounce | curious_peek | concerned_tilt | sad_droop | confused_tilt | firm_shake",
    "reason": "short internal reason, not spoken to the user"
  }
}
```

Robust parser behavior on Jetson:

```text
valid JSON                  -> use reply/control
text around JSON            -> extract the first JSON object
old server field uart       -> normalize into control
reply contains JSON/control -> extract natural reply so TTS does not speak control text
parse failure               -> natural fallback reply + neutral/gentle_nod/unchanged
```

`reply` is always for the user. `control` is always internal.

Control priority:

```text
1. Explicit local tool intent: to-do / focus / music / weather
2. Explicit conversation end: bye/goodbye/掰掰/拜拜/再見 -> Normal 0 0
3. Explicit sleep/rest intent -> Sleep 0 0 and end continuous listening
4. Explicit wake/normal intent -> Normal 0 0
5. Normal answer -> Speaking <emotion_code>; after TTS, return to Thinking 0 0 in conversation mode
```

If FRDM receives `Speaking N` but the face stays neutral:

```text
FRDM UART TX: Speaking 4
FRDM UART RX: Speaking 4
FRDM UART RX: switch to SPEAKINGemotion: neutral
```

The Jetson sent the right command, but FRDM `SpeakingGui(char *pValue)` did not parse the numeric value. Apply the parser helper in:

```text
emotion_robot_controller/frdm_firmware/patches/speaking_gui_emotion_fix.c
```

Speaking code table:

```text
Speaking 0 -> neutral
Speaking 1 -> concerned
Speaking 2 -> angry
Speaking 3 -> sad
Speaking 4 -> happy
Speaking 5 -> confused / curious
```

## FRDM UART Timing

Live sequence:

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
-> Thinking 0 0 for next follow-up, or Normal/Music/Focus/Sleep
```

FRDM no longer receives old emotion screen commands such as `Happy 0 0` or `Curious 0 0`. The Jetson converts emotion into single-argument `Speaking 0-5`. Motor motion runs in a separate thread and does not block TTS.

Mode examples:

```text
Normal 0 0     # default face / wake-only standby
Thinking 0 0   # listening, ASR/LLM thinking, or waiting for follow-up
Speaking 2     # TTS speaking with angry emotion
Music 0 0      # music playback screen
Focus 0 0      # focus work mode screen
Sleep 0 0      # sleep/rest screen
```

## Emotion And Head Motion

The Wake Bridge controls facial emotion and head motor separately:

```text
emotion      -> Speaking 0..5
head_motion  -> MotorYawPitch continuous sequence
```

Windows may explicitly return `control.head_motion`. If it is missing or `none`, the Jetson chooses a fallback motion from the emotion.

The `emotion` is the robot's response, not a direct copy of the user's emotion. If the user is angry or uses profanity, the default robot response is `concerned -> Speaking 1` so it stays calm and supportive. `angry -> Speaking 2` is reserved for cases where the robot itself is setting a firm boundary.

Emotion speaking-code mapping:

```text
neutral   -> Speaking 0
concerned -> Speaking 1
angry     -> Speaking 2
sad       -> Speaking 3
happy     -> Speaking 4
curious   -> Speaking 5
excited   -> Speaking 4
confused  -> Speaking 5
sleepy    -> Speaking 3
```

Emotion-to-motion fallback:

```text
neutral   -> gentle_nod
concerned -> concerned_tilt
angry     -> firm_shake
sad       -> sad_droop
happy     -> happy_bounce
curious   -> curious_peek
excited   -> excited_bounce
confused  -> confused_tilt
sleepy    -> sleepy_drop
```

Common emotion aliases are normalized before UART:

```text
calm / normal / 中性              -> neutral
joy / joyful / positive / 開心    -> happy
interested / thinking / 好奇      -> curious
surprised / amazed / 興奮         -> excited
unsure / uncertain / puzzled      -> confused
anxious / worried / 急 / 擔心     -> concerned
angry                         -> angry
user anger / 生氣 / 火大 / 操你媽 -> concerned local fallback
sad / 難過 / 沮喪                 -> sad
tired / drowsy / 想睡 / 疲累      -> sleepy
```

Local fallback examples:

```text
我操你媽的                 -> concerned, concerned_tilt, Speaking 1
我很難過                   -> concerned, concerned_tilt, Speaking 1
太酷了我超期待             -> excited, excited_bounce, Speaking 4
這個結果怪怪的我看不懂     -> confused, confused_tilt, Speaking 5
我有點擔心                 -> concerned, concerned_tilt, Speaking 1
我好睏想睡                 -> sleepy, sleepy_drop, Speaking 3
為什麼會這樣               -> curious, curious_peek, Speaking 5
太好了很棒                 -> happy, happy_bounce, Speaking 4
```

Sleep/wake intent has higher priority than emotion fallback.

### Motor UART Coordinate System

`MotorPitch`, `MotorYaw`, and `MotorYawPitch` are absolute servo angles, not relative movement. Single-axis commands still send one angle argument. `MotorYawPitch` is the only motor command that sends two numeric arguments.

```text
MotorPitch 90
MotorYaw 90
MotorYawPitch 120 90
```

Terminal 3 full demo sends motor commands by default only when this flag is present:

```bash
--enable-head-motor \
```

If FRDM parser work is still in progress, replace it with:

```bash
--disable-head-motor \
```

The startup log must show:

```text
Head motor motion: enabled=True
```

Servo coordinates:

```text
MotorPitch 65   -> low/down limit
MotorPitch 90   -> center
MotorPitch 115  -> up limit

MotorYaw 0      -> right limit
MotorYaw 90     -> center
MotorYaw 180    -> left limit
```

Program safety bounds:

```text
MOTOR_PITCH_MIN=65
MOTOR_PITCH_CENTER=90
MOTOR_PITCH_MAX=115
MOTOR_YAW_MIN=0
MOTOR_YAW_CENTER=90
MOTOR_YAW_MAX=180
MOTOR_STEP_DELAY_SEC=0.55
MOTOR_SMOOTH_STEP_DEG=120
MOTOR_SPEAKING_STEP_DELAY_SEC=0.72
MOTOR_SPEAKING_SMOOTH_STEP_DEG=120
MOTOR_RESET_REPEATS=1
MOTOR_RESET_DELAY_SEC=0.35
MOTOR_STOP_TIMEOUT_SEC=6.0
MOTOR_JOIN_TIMEOUT_SEC=6.0
```

If the real log shows this, the problem is in the FRDM parser, not the Jetson angle:

```text
FRDM UART TX: MotorPitch 90
FRDM UART RX: Motor Pitch = 537190203
```

`537190203 = 0x2004df3b`, which looks like a Cortex-M RAM pointer. It usually means `MotorControlPitch(char *pValue)` or `MotorControlYaw(char *pValue)` did not parse `"90"` into integer `90`, or used an uninitialized value after `sscanf` failed. The bridge safety-locks further motor commands in that process if ACK is out of range.

FRDM handler concept:

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

Motion tables are high-level held poses. The current strategy is not dense micro-stepping; it is a small number of clear `MotorYawPitch` targets with longer pauses at expressive poses. This makes the head look like it is intentionally looking, nodding, or reacting instead of constantly vibrating. The bridge still supports interpolation if you lower `--motor-smooth-step-deg`, but the demo default is large pose-to-pose movement. The bridge also safety-checks `MotorYawPitch` ACK lines such as `Motor YawPitch = yaw:120 pitch:90`; if FRDM reports out-of-range values, motor commands are locked out until the bridge restarts.

The latest motion table is slightly more expressive: neutral replies now use a small `gentle_nod`, and emotion motions include extra diagonal keyframes so the robot does not only pitch up/down. Local tool confirmations such as music stop/pause and ESP32 fan/LED commands still force `head_motion=none`, so they speak without moving the head.

Current keyframes:

```text
none:
  MotorYawPitch 90 90

nod:
  right/up -> left/down -> center/up -> right/up -> center/down -> center

double_nod:
  right/up -> left/down -> center/up -> right/up -> left/down -> center/up -> center

look_around:
  right prep -> right limit -> right/up settle -> left prep -> left limit -> left/up settle -> center

shake:
  right -> left -> right/down -> left/up -> center

gentle_nod:
  right/up -> left/down -> center/up -> right/down -> center

sleepy_drop:
  right/drowsy -> center/down -> left/down -> held right/down -> left/drowsy -> center

happy_bounce:
  right/up -> center/up -> left/up -> right/down -> left/up -> center/up -> center

excited_bounce:
  big right/up -> right attentive -> big left/up -> left attentive -> right/down -> left/up -> center/up -> center

curious_peek:
  right prep -> right/up look -> right/up settle -> center attentive -> left prep -> left/up look -> left/up settle -> center

concerned_tilt:
  right/down -> left/down -> center/down -> right/down -> left attentive -> center

sad_droop:
  right/down -> center/deep down -> left/deep down -> right/deep down -> left/down -> center

confused_tilt:
  right/up -> left/down -> center attentive -> right/up -> left/down -> right/down -> center

firm_shake:
  right/up limit -> left/up limit -> right/center -> left/down -> right/up -> left/center -> center
```

Emotion fallback uses the expressive versions by default: `gentle_nod`, `happy_bounce`, `excited_bounce`, `curious_peek`, `concerned_tilt`, `sad_droop`, `confused_tilt`, and `firm_shake`. The older generic motions remain available for explicit commands like "點頭" or "搖頭".

In live dialogue, TTS starts a speaking motion loop. While TTS is playing, the loop repeats short motions. When TTS finishes, the stop event centers the head and exits, then the bridge sends the next screen mode.

With `--uart-debug`, logs include both keyframes and expanded UART:

```text
head motion keyframes: MotorYawPitch:yaw=90,pitch=90 -> MotorYawPitch:yaw=0,pitch=102 -> ...
head motion expanded: MotorYawPitch:yaw=90,pitch=90 -> MotorYawPitch:yaw=0,pitch=102 -> ...
head motion delays: 0.63s -> 1.07s -> ...
```

### Motor Tuning

```text
motion is twitchy / sends too many commands -> --motor-smooth-step-deg 120
motion needs a bit more transition          -> --motor-smooth-step-deg 60
motion while speaking is too fast           -> --motor-speaking-step-delay 0.9
motion while speaking is too slow           -> --motor-speaking-step-delay 0.55
one-shot test motion is too fast            -> --motor-step-delay 0.75
one-shot test motion is too slow            -> --motor-step-delay 0.35
occasionally does not center                -> --motor-reset-repeats 2
reset commands are too dense for FRDM       -> --motor-reset-delay 0.45
screen switches too early after TTS         -> --motor-join-timeout 8
only inspect outgoing commands              -> add --uart-dry-run --uart-debug
```

If the angles themselves need changes, edit the `PITCH_*` / `YAW_*` constants in `wake_voice_chat_frdm_bridge.py`, not the server prompt.

### Direct Head Motion Test

Use direct tests before debugging through Hey Jarvis, ASR, TTS, or AI.

Dry-run all motion UART sequences:

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

Dry-run emotion fallback:

```bash
python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py \
  --test-head-emotion all \
  --uart-dry-run \
  --uart-debug \
  --motor-step-delay 0.01 \
  --motor-reset-delay 0.01 \
  --test-head-gap 0
```

After FRDM firmware is fixed and `MotorPitch 90` returns `Motor Pitch = 90`, test speaking-loop motion:

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

Then test the one-shot motion table on hardware:

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

If dry-run contains `MotorPitch/MotorYaw` but hardware does not move:

```bash
python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py --list-uarts
./frdm_uart_context_sender/recover_demo_usb.sh
```

## Vision Routing

Windows server runs ASR first, then decides whether the image is needed.

Core functions:

```python
detect_vision_intent(transcript: str) -> tuple[bool, str]
should_use_vision(transcript: str) -> bool
```

The strategy is high-recall rule-based routing. Any request that depends on the current camera image uses vision.

Uses vision:

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

Does not use vision:

```text
幫我開電風扇
關燈
切換安靜模式
今天幾號
講個笑話
explain PID control
move the motor forward
```

Mixed requests use vision if any part requires the image.

Vision mode priority:

```text
--no-vision     disables camera and vision
--force-vision  forces vision whenever image exists
auto            detect_vision_intent(transcript)
```

## Camera And Image Storage

The live bridge keeps camera data memory-only:

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

Recommended live settings:

```text
320x240
JPEG quality 70
camera-id auto
continuous warm reader enabled
latest timeout 1.0s
frame max age 2.0s
speech-end image delay 0.0s
```

The Global Shutter Camera may take 5 to 7 seconds to produce the first frame. Wait for:

```text
Camera warm reader opened camera 0.
```

If the camera times out, is busy, is unplugged, or has no fresh frame, the flow does not crash; it sends audio only. If the transcript requires vision but no image exists, the server logs `vision_error` and falls back to text.

Debug options:

```bash
--camera-one-shot
--camera-read-timeout 7
--camera-result-timeout 1
--speech-end-image-delay 0
```

`vision/camera_ollama_status.py` is a standalone test and may save test images under `vision/`. The live bridge does not permanently save images.

## TTS Playback Completion

Jetson TTS endpoint:

```text
http://127.0.0.1:8777/speak_async
```

Flow:

```text
POST /speak_async -> get job_id
keep FRDM on Thinking while text exists but audio has not started
poll /queue and /health
when /health audio.playing=true -> send Speaking <emotion> and start speaking head motion
poll /queue until job_id appears in last_result
stop speaking head motion and send the next FRDM screen only after TTS playback finishes
```

The default is intentionally strict: `Speaking` is not sent from text readiness, queue position, or elapsed time alone. The cue starts only after the TTS server reports `audio.playing=true`, which means the audio player process is active for the UACDemo speaker. If a TTS job finishes before `audio.playing` is observed, the bridge does not flash `Speaking`; it moves to the post-reply screen after the job completes.

Local control replies use the same playback cue, but some of them deliberately set `head_motion=none`:

```text
music pause/stop confirmation -> Speaking <emotion>, no head motor, then Normal 0 0
ESP32 FAN_OFF/ALL_OFF reply   -> Speaking <emotion>, no head motor, then Normal 0 0
TTS never starts              -> cue cleanup stops any previous speaking-motion thread
low-RMS skipped turn          -> force motion idle and center the head
conversation end without TTS  -> force motion idle and center the head
```

This keeps screen, TTS, and motor state aligned even when the user stops music, turns the fan off, or the TTS service fails mid-turn.

Timeout and polling options:

```bash
--tts-playback-timeout 45
--tts-poll-interval 0.75
--tts-start-poll-interval 0.12
--tts-speaking-require-audio
```

For emergency compatibility with an older TTS server that does not expose `audio.playing`, use `--no-tts-speaking-require-audio`. That restores the older fallback where `Speaking` may start after the TTS job becomes current for `--tts-speaking-start-timeout` seconds.

TTS `.env` recommendation:

```text
AUDIO_DEVICE=auto:UACDemo
DEFAULT_VOLUME_GAIN=4.8
ENABLE_STREAM_PLAYBACK=true
```

After USB recovery, speaker replug, or `.env` changes, restart the TTS server. `auto:UACDemo` resolves the current UACDemo ALSA card name at playback time, so it survives card-number changes better than `plughw:1,0`. The gain value is absolute, not multiplied on each start.

## USB Replug Auto Discovery

The bridge rescans devices at these points:

```text
before every recording stream -> find UACDemo input
before every beep             -> find UACDemo output
before every camera capture   -> scan /dev/video*
before every UART send        -> find FRDM serial
```

Correct startup log:

```text
USB auto-discovery: mic=keyword 'UACDemo'; beep=keyword 'UACDemo'; camera=auto; FRDM UART=auto
TTS audio: AUDIO_DEVICE=auto:UACDemo
```

If the USB controller drops and `lsusb` cannot see UACDemo/camera/FRDM:

```bash
cd /home/asrlab-yian/MakeNTU/frdm_uart_context_sender
./recover_demo_usb.sh
```

Expected devices:

```text
Jieli Technology UACDemoV1.0
Global Shutter Camera
NXP Semiconductors MCU-LINK FRDM-MCXN947
/dev/video0 /dev/video1
/dev/ttyACM0
```

## Self-Test And Preflight

Jetson self-test:

```bash
cd /home/asrlab-yian/MakeNTU
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate
python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py --self-test
```

Head motor dry-run:

```bash
python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py \
  --test-head-motion all \
  --uart-dry-run \
  --uart-debug \
  --motor-step-delay 0.01 \
  --motor-reset-delay 0.01 \
  --test-head-gap 0
```

Emotion-to-motion dry-run:

```bash
python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py \
  --test-head-emotion all \
  --uart-dry-run \
  --uart-debug \
  --motor-step-delay 0.01 \
  --motor-reset-delay 0.01 \
  --test-head-gap 0
```

Music intent self-test:

```bash
python3 music_web_player/music_web_player.py --self-test
```

TTS volume API smoke test:

```bash
curl -X POST http://127.0.0.1:8777/speak_async \
  -H "Content-Type: application/json" \
  -d '{"text":"音量測試，現在應該是固定音量。","interrupt":true,"volume_gain":4.8}'
```

Focus work mode self-test:

```bash
cd /home/asrlab-yian/MakeNTU
python3 frdm_uart_context_sender/focus_work_mode.py --self-test
```

Windows self-test:

```powershell
cd "$env:USERPROFILE\Desktop\windows_desktop_server_bundle"
.\.venv\Scripts\Activate.ps1
python desktop_fast_chat_server.py --self-test
```

Device/server checks:

```bash
python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py --list-mics
python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py --list-uarts
python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py --server-url http://100.108.141.26:8766/voice-chat --check-server --uart-dry-run --tts-debug
```

Focus mock session:

```bash
python3 frdm_uart_context_sender/focus_work_mode.py \
  --mock-state focused \
  --once \
  --uart-dry-run \
  --log-root /tmp/focus_test
```

Expected focus files: `session.json`, `focus_log.jsonl`, `focus_summary.json`, and `focus_report.md`.

## Debug Log Guide

Successful startup logs:

```text
Server health: debug_version=13, chat_ready=True, asr_loaded=True
TTS health: ready=True, audio device=UACDemo
Selected input device ... by keyword 'UACDemo'
Selected beep output device ... by keyword 'UACDemo'
Camera ready in continuous warm-reader mode
Audio read watchdog: callback queue, timeout=0.75s
USB auto-discovery: mic=keyword 'UACDemo'; ...; FRDM UART=auto
```

Jetson upload:

```text
Image captured: 13001 bytes
POST audio+image ... (vision_mode=auto, image_size_bytes=13001)
```

Vision summary:

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

UART timing:

```text
FRDM UART TX: Thinking 0 0
FRDM UART TX: Speaking 1
FRDM UART TX: MotorPitch 90
FRDM UART TX: Normal 0 0
```

Recording wait:

```text
Recording thresholds: noise_floor=900, speech_start_threshold=1250, silence_base_threshold=1080, adaptive=on
Recording progress: phase=waiting_speech, elapsed=..., volume=..., start_threshold=..., wake_timeout_in=...
Speech started. volume=...
Silence detected. volume=..., silence_threshold=..., peak=...
```

If wake succeeded but speech never starts, volume is below `noise_floor + speech_start_margin`. Move closer to the mic or lower `--speech-start-margin`. If `Speech started` appears but `Silence detected` never appears, background sound or echo is still above the silence threshold; raise `--silence-margin` or lower `--max-speech-seconds`.

## Troubleshooting

```text
another wake bridge is already running
-> pkill -9 -f wake_voice_chat_frdm_bridge.py, then restart Terminal 3.

No microphone matching UACDemo
-> Jetson cannot see the USB mic. Run lsusb / --list-mics. If lsusb cannot see UACDemo, run ./recover_demo_usb.sh.

Recording feels stuck after wake
-> If phase=waiting_speech, speech has not crossed the start threshold. Lower --speech-start-ratio from 1.45 to 1.35, then adjust --speech-start-margin if needed.
-> If Speech started but never stops, raise --silence-noise-ratio from 1.30 to 1.40, or lower --max-speech-seconds from 5 to 4.
-> If the venue has continuous fans or voices, keep --noisy-room.
-> If wake score is high but ignored as low volume, inspect recent_peak; try --wake-volume-ratio 1.25 or --wake-volume-window-seconds 1.5.
-> --max_speech_seconds only applies after Speech started. Use --max-recording-seconds to protect the whole turn.

Command tail contains --uart-debug\terval
-> Command was mistyped. Use --tts-poll-interval 0.75, --tts-debug, and --uart-debug as three separate lines.

Wake is ignored
-> Low-volume protection. Live mode uses --wake-volume-min 500. If it still misses, try 200.

Camera timeout
-> Does not crash. Run lsusb / ls -l /dev/video* / ./recover_demo_usb.sh.
-> If /dev/video0 exists but image_received=False, wait 5 to 7 seconds for warm reader to get the first frame.

Windows health timeout
-> Check the Windows server terminal, Tailscale IP, and whether port 8766 is occupied by an old process.

Ollama WinError 10061 / connection refused
-> Windows server is running but Windows Ollama is not. In PowerShell, run curl.exe http://127.0.0.1:11434/api/tags. If it fails, run Start-Process -FilePath "ollama" -ArgumentList "serve".

debug_version is not 13
-> Re-sync the Windows bundle, stop the old server, and restart.

TTS ready but no sound
-> Check /health. Confirm configured_device is auto:UACDemo and audio.device resolved to UACDemo. Restart TTS.

TTS is audible but too quiet
-> Use --tts-volume-gain 4.8 first. If still too quiet, try 6.0 and restart Wake Bridge.
-> If /speak_async with volume_gain returns 422, Terminal 2 is still the old TTS server; restart it.

Music is audible but too quiet
-> This is mpv volume, not Piper TTS. Check curl http://127.0.0.1:8788/health.
-> Expected defaults are mpv_volume=220, mpv_volume_max=300, mpv_volume_clamped=false.
-> Raise only music with MUSIC_MPV_VOLUME=260 MUSIC_MPV_VOLUME_MAX=350 ./frdm_uart_context_sender/run_wake_bridge_full_demo.sh.
-> If mpv_actual_volume is missing while music should be playing, mpv is not active or IPC is not ready.

Music starts when the user is complaining about audio volume
-> Restart Terminal 4 and Terminal 3. Latest routing treats phrases about no sound / low sound / volume as audio complaints, not song requests.
-> ESP32 BLE should also ignore "音樂太小聲 / 調大音量" unless the phrase contains fan context such as 風扇, 風量, 風速, 好熱, or 好冷.

Music stop / fan off succeeds but there is no spoken reply, or the head moves silently
-> Restart Terminal 3 so the local-control confirmation path is active.
-> For music stop/pause, Terminal 3 should print "Music stop/pause handled before TTS; local confirmation will be spoken."
-> For ESP32 fan/LED control, Terminal 3 should print "ESP32-S3 BLE control:" followed by head_motion none.
-> During the local confirmation, logs may show "speaking head motion skipped: none"; that is correct.
-> Run python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py --self-test to verify the regression guards.

FRDM room temperature does not update
-> Terminal 3 should show "BLE: connected. Subscribing status notify..." and recurring "ESP32 status: TEMP=...".
-> Terminal 3 should show "FRDM room temperature UART: TempRoom every 10s" at startup.
-> When a fresh reading exists, Terminal 3 should print "TempRoom UART sent: TempRoom 254 (... C)" every 10 seconds.
-> If Weather local temperature works but TempRoom does not, check --no-temp-room-uart and --temp-room-uart-interval-sec.
-> If Terminal 3 prints "WARNING: refusing unknown UART raw line 'TempRoom ...'", an old bridge is running; restart Terminal 3.
-> If Terminal 3 sends TempRoom but FRDM shows nothing, add a FRDM parser for single-argument "TempRoom <Celsius*10>".

ESP32 BLE is disconnected but wake/TTS/music should keep working
-> This is expected. BLE reconnect runs in the esp32s3-ble-reconnect-loop worker and must not block the main Wake Bridge.
-> Check curl http://127.0.0.1:8791/api/esp32/status. running=true, connected=false means the background reconnect loop is alive.
-> queued_pending shows fan/LED commands waiting for reconnect; dropped_pending increments if the queue exceeded --esp32-ble-command-queue-max.
-> If enabled=false with unavailable_reason, install/fix bleak or the helper module. Other features continue in degraded mode.
-> To intentionally run without ESP32 BLE, start with ESP32_BLE=0.

Emotion face is wrong
-> Check Windows debug control.emotion and Terminal 3 FRDM UART TX: Speaking N.
-> Expected mapping: angry -> 2, concerned -> 1, happy/excited -> 4, confused/curious -> 5, sad/sleepy -> 3.
-> If Terminal 3 still prints an old code mapping, an old bridge is running. pkill and restart.
-> If FRDM RX has Speaking N but still logs neutral, fix FRDM SpeakingGui parser.

Music pause/resume does not work
-> Ensure Terminal 4 music_web_player.py --backend mpv is running. Check active/paused from /health. Browser backend cannot reliably pause/resume.

ESP32 BLE scan cannot find the name
-> Scan by service UUID and allow duplicates:
   python3 frdm_uart_context_sender/esp32s3_ble_fan_led_controller.py --scan-only --scan-target-only --adapter hci0 --scan-duplicates --scan-timeout 30
-> The ESP32 may appear as "(unnamed)" while still advertising the correct service UUID. Use --address with that MAC.
-> If a phone BLE app is connected, disconnect it before Jetson connects.

BlueZ says Device with address ... was not found
-> BlueZ cache is stale or the adapter did not create a device object yet. Run scan-only first, then connect by the MAC seen in that scan.
-> If bluetoothctl reports Busy, kill old scanner processes and restart bluetooth:
   pkill -f esp32s3_ble_fan_led_controller.py || true
   pkill -f bluetoothctl || true
   sudo systemctl restart bluetooth
   sleep 3
   sudo rfkill unblock bluetooth

ESP32 status says FAN=ON but physical fan does not spin
-> First send FAN_SPEED:255. If it still does not spin, check L9110S VCC=5V, common GND, INA=GPIO5, INB=GPIO6, and motor output wiring.
-> If FAN_SPEED:255 works but low FRDM slider values do not, increase FAN_MIN_PWM, for example FAN_MIN_PWM=120.
-> The ESP32 full sketch also applies FAN_MIN_RUNNING_SPEED=96 and a 250ms start kick; reflash the full sketch after changes.

fan dashboard sync failed: Connection refused
-> Optional dashboard server on port 8789 is not running. Start Terminal 5 or add --no-fan-dashboard-sync. BLE control is unaffected.

vision_intent=True but used_vision=False
-> Check image_received / image_size_bytes / vision_error.

FRDM does not respond
-> Run --list-uarts, lsusb, and check /dev/ttyACM*. Verify MCU-LINK USB-C, data cable, baudrate 115200, CRLF.
```

## Demo Checklist

Windows:

```text
[ ] desktop_fast_chat_server.py synced
[ ] ollama list has qwen35-fast:latest
[ ] /health debug_version=13
[ ] /debug routes include /focus-check for focus mode tests
[ ] vision_model=qwen35-fast:latest
```

Jetson:

```text
[ ] TTS /health ready=true
[ ] TTS audio device is UACDemo
[ ] --list-mics shows UACDemo input
[ ] --list-uarts shows FRDM
[ ] lsusb shows UACDemo / Global Shutter Camera / MCU-LINK
[ ] Music Web Player /health ok=true
[ ] Music backend is mpv; pause/resume tested
[ ] ESP32-S3 BLE scan sees service UUID 12345678-1234-1234-1234-1234567890ab
[ ] ESP32 standalone BLE CLI can run TEMP?, LED_ON/OFF, FAN_SPEED:255, FAN_OFF
[ ] ESP32 full sketch is flashed, not only the minimal advertise sketch
[ ] Terminal 3 shows TempRoom UART sent every 10 seconds after BLE TEMP notify
[ ] FRDM parser handles TempRoom <Celsius*10> and displays room temperature
[ ] FRDM FanSpeed 16 relays to at least FAN_SPEED:96 or configured FAN_MIN_PWM
[ ] Optional legacy ESP32 HTTP POST/pull returns {"ok":true,"temperature_c":...} if that path is used
[ ] wake bridge self-test OK
[ ] focus_work_mode.py self-test OK
[ ] focus mock session writes focus_log.jsonl / focus_summary.json / focus_report.md
[ ] text-only utterance used_vision=False
[ ] visual utterance used_vision=True
[ ] FRDM UART TX/RX normal
```
