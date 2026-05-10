# MakeNTU Desktop Pet Demo

This repository is the integration project for the MakeNTU desktop pet system. The Jetson side handles wake word detection, recording, camera capture, TTS, FRDM UART, the music/weather sidecar, the local to-do list, and focus work mode. The Windows desktop handles ASR, Ollama/Qwen replies, vision, and `/focus-check`.

For the live demo, start here:

```text
frdm_uart_context_sender/QUICK_START.md   # full on-site startup guide
frdm_uart_context_sender/README.md        # architecture, UART, focus/to-do/music/weather details
smart_home_dashboard/README.md            # phone/web dashboard API for smart home mode
```

## Current Main Features

```text
Wake word             : Hey Jarvis / openWakeWord
Conversation mode     : continuous follow-up after one wake; returns to wake-only standby when done
Windows AI server     : /voice-chat, /text-chat, /focus-check
Local model path      : Windows ASR + Ollama qwen35-fast:latest
Vision                : captures after the end-of-speech beep, uploads to Windows; images are memory-only by default
FRDM UART             : auto @ 115200 CRLF
FRDM state machine    : Normal / Thinking / Speaking / Music / Focus / Sleep
FRDM startup data     : Time + Weather UART payloads before Normal
FRDM dashboard data   : Todo / Music / Focus / Health UART payloads for swipe pages
Head motor            : MotorPitch 65..90..115, MotorYaw 0..90..180
TTS                   : Jetson Piper /speak_async, AUDIO_DEVICE=auto:UACDemo
Music tool            : Jetson local /music, mpv + yt-dlp
Weather tool          : Jetson local /weather, Open-Meteo
Smart home dashboard  : Jetson local /dashboard + REST API on port 8789
To-do list            : local JSON voice tool, frdm_uart_context_sender/logs/todo_list.json
Focus work mode       : samples every 60 seconds; writes focus_summary.json + focus_report.md
Focus report title    : "專心報告：YYYY/MM/DD/HH 開始的專注時段"
Focus notification    : optional Discord webhook; fallback reads ~/.config/makentu/discord_webhook_url
Display toolkit       : OBS/Hagibis capture troubleshooting scripts, kept below
```

Do not pin numeric USB indexes. For the live demo, use keyword/auto discovery:

```text
mic       : --mic-keyword UACDemo
beep      : --beep-keyword UACDemo
TTS audio : AUDIO_DEVICE=auto:UACDemo
camera    : --camera-id auto
FRDM UART : --uart-port auto
```

## One-Page Startup

Startup order:

```text
Terminal 1 on Windows : desktop_fast_chat_server.py
Terminal 2 on Jetson  : jetson_piper_tts.server
Terminal 4 on Jetson  : music_web_player.py   # /music + /weather
Terminal 5 on Jetson  : smart_home_dashboard/server.py   # phone/web dashboard
Terminal 3 on Jetson  : wake_voice_chat_frdm_bridge.py
```

Current default Tailscale addresses and endpoints:

```text
Windows Tailscale : 100.108.141.26
Jetson Tailscale  : 100.110.90.72
Windows server    : http://100.108.141.26:8766/voice-chat
Jetson TTS        : http://127.0.0.1:8777
Local tools       : http://127.0.0.1:8788/music and /weather
Phone dashboard   : http://jetson-ip:8789/dashboard
```

If the IP changes, update every `100.108.141.26` in the commands below.

### Terminal 1: Windows ASR/Ollama Server

Windows PowerShell:

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

If the server code changed, sync the latest bundle from the Jetson first:

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\Desktop\windows_desktop_server_bundle" | Out-Null
scp asrlab-yian@100.110.90.72:/home/asrlab-yian/MakeNTU/emotion_robot_controller/voice_stt_remote/windows_desktop_server_bundle/desktop_fast_chat_server.py "$env:USERPROFILE\Desktop\windows_desktop_server_bundle\desktop_fast_chat_server.py"
```

### Terminal 2: Jetson Piper TTS

Jetson:

```bash
cd /home/asrlab-yian/MakeNTU/jetson_piper_tts
source .venv/bin/activate

pkill -f 'jetson_piper_tts.server' 2>/dev/null || true

python -m jetson_piper_tts.server \
  --host 0.0.0.0 \
  --port 8777 \
  --no-warmup
```

Recommended `.env`:

```text
AUDIO_DEVICE=auto:UACDemo
DEFAULT_VOLUME_GAIN=3.6
ENABLE_STREAM_PLAYBACK=true
```

### Terminal 4: Jetson Music/Weather Tool

Jetson:

```bash
cd /home/asrlab-yian/MakeNTU/music_web_player
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate

python3 music_web_player.py \
  --server \
  --host 127.0.0.1 \
  --port 8788 \
  --backend mpv \
  --mpv-audio-device auto \
  --mpv-volume 150 \
  --mpv-volume-max 200 \
  --weather-default-location Taipei
```

Health check:

```bash
curl http://127.0.0.1:8788/health
```

### Terminal 5: Jetson Smart Home Dashboard API

This is the phone/web entrypoint for the smart-home version of the demo.

```bash
cd /home/asrlab-yian/MakeNTU
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate

python3 smart_home_dashboard/server.py \
  --host 0.0.0.0 \
  --port 8789
```

Open from a phone on the same network:

```text
http://jetson-ip:8789/dashboard
```

The website should call Jetson, not FRDM directly:

```text
phone browser / website
-> Jetson dashboard API on :8789
-> local JSON state, camera frame, focus reports, music/weather sidecars
-> optional FRDM UART sync for HMI pages
```

Dashboard API surface:

```text
GET  /api/status                         # time, devices, sensors, todo, focus, music, weather, health
GET  /api/camera/latest                  # pet camera snapshot
GET  /api/camera/stream?fps=2            # MJPEG stream for phone monitor mode
GET  /api/devices
POST /api/devices/{device_id}/set        # appliance control
GET  /api/sensors
POST /api/sensors/{sensor_id}/update     # FRDM/Jetson sensor hub state
GET  /api/todo
POST /api/todo
POST /api/todo/{id}/done                 # phone checkbox -> Jetson todo JSON
GET  /api/focus/summaries?range=today|week|month|all
GET  /api/music/status
POST /api/music/control
GET  /api/weather?location=Taipei
POST /api/frdm/power-cycle
GET  /api/events
```

FRDM sync mode:

```text
Default live voice mode:
  Wake Bridge owns FRDM UART. Dashboard updates Jetson state and exposes it to phone/web.
  Dashboard can still power-cycle USB-powered FRDM without owning UART.

Smart-home HMI mode:
  Dashboard may own FRDM UART and push website actions to FRDM:
  python3 smart_home_dashboard/server.py --host 0.0.0.0 --port 8789 --frdm-uart-port auto
```

Do not intentionally run two different processes as the main FRDM UART owner for a demo. Pick Wake Bridge for the voice demo, or Dashboard API for the phone-first smart-home HMI demo.

FRDM recovery button:

```text
POST /api/frdm/power-cycle cuts Jetson-supplied FRDM power and restores it.
Default mode resets Jetson xUSB controller 3610000.usb, so USB camera/audio on that controller may briefly reconnect.
If the website says sudo needs a password, configure NOPASSWD for /usr/bin/tee on the xUSB bind/unbind sysfs files or run the dashboard as root.
For a cleaner demo, use a dedicated hub port, relay, load switch, or script mode for FRDM-only power control.
```

### Discord Webhook Setup For Focus Reports

Store the webhook in a secret file so you do not need to export it every time. Do not commit this file:

```bash
mkdir -p ~/.config/makentu
printf '%s\n' 'https://discord.com/api/webhooks/...' > ~/.config/makentu/discord_webhook_url
chmod 600 ~/.config/makentu/discord_webhook_url
```

Check it without printing the full URL:

```bash
python3 - <<'PY'
from pathlib import Path
p = Path.home() / ".config/makentu/discord_webhook_url"
value = p.read_text().strip() if p.exists() else ""
print("exists:", p.exists())
print("mode:", oct(p.stat().st_mode & 0o777) if p.exists() else "missing")
print("len:", len(value))
print("prefix:", value[:38])
PY
```

The program reads these sources in order:

```text
--focus-discord-webhook-url
FOCUS_DISCORD_WEBHOOK_URL
DISCORD_WEBHOOK_URL
~/.config/makentu/discord_webhook_url
```

### Terminal 3: Jetson Wake Bridge Full Demo

Jetson:

```bash
cd /home/asrlab-yian/MakeNTU
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate

bash frdm_uart_context_sender/auto_demo_devices.sh
./frdm_uart_context_sender/run_wake_bridge_full_demo.sh
```

Manual equivalent, if you need to tune one parameter live:

```bash
cd /home/asrlab-yian/MakeNTU
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate

python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --mic-keyword UACDemo \
  --beep-keyword UACDemo \
  --beep-player auto \
  --noisy-room \
  --tts-volume-gain 3.6 \
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
  --todo-list-path /home/asrlab-yian/MakeNTU/frdm_uart_context_sender/logs/todo_list.json \
  --focus-notify-mode discord \
  --music-backend mpv \
  --music-mpv-audio-device auto \
  --music-mpv-volume 70 \
  --music-mpv-ready-timeout 1.5 \
  --music-timeout 5 \
  --music-wake-pause-timeout 0.25 \
  --music-wake-beep-settle 0.05 \
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

If the FRDM motor parser is still being debugged, temporarily replace `--enable-head-motor` with:

```bash
--disable-head-motor \
```

## Voice Test Flow

After the Wake Bridge starts, use this flow to test the full feature set:

The following Mandarin lines are intentional demo utterances, because they are the actual phrases currently tested by the voice intent rules.

```text
Hey Jarvis，講個笑話
我現在是什麼表情
所在地天氣如何
我想要聽告白氣球
暫停音樂
繼續播放音樂
新增待辦 測試 Discord 專心報告
列出待辦
開始專心工作 1 分鐘 測試報告整合
```

After entering focus mode, the focus command returns the bridge to wake-only standby, so wake it again:

```text
Hey Jarvis，完成待辦 1
Hey Jarvis，結束工作
```

Expected result:

```text
FRDM switches among Focus / Thinking / Speaking / Normal states
camera captures and uploads at speech end
to-do JSON is updated
focus session writes focus_summary.json + focus_report.md
focus_report.md H1 and Discord title use "專心報告：YYYY/MM/DD/HH 開始的專注時段"
Discord receives the focus summary
```

Inspect the latest focus report:

```bash
latest=$(find /tmp/focus_voice_test -maxdepth 1 -type d -name 'focus_*' | sort | tail -n 1)
echo "$latest"
python3 -m json.tool "$latest/focus_summary.json" | sed -n '1,220p'
sed -n '1,220p' "$latest/focus_report.md"
```

## Direct Tests

Test focus report + Discord without starting the full wake bridge:

```bash
cd /home/asrlab-yian/MakeNTU
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate

unset DISCORD_WEBHOOK_URL
unset FOCUS_DISCORD_WEBHOOK_URL

python3 frdm_uart_context_sender/focus_work_mode.py \
  --mock-state focused \
  --once \
  --uart-dry-run \
  --log-root /tmp/focus_discord_test \
  --notify-mode discord
```

Capture one camera preview:

```bash
cd /home/asrlab-yian/MakeNTU
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate

python3 frdm_uart_context_sender/focus_work_mode.py \
  --once \
  --save-images \
  --uart-dry-run \
  --timeout 1 \
  --server-url http://127.0.0.1:9/focus-check \
  --log-root /tmp/camera_preview_focus
```

Test the FRDM head motor without mic/camera/TTS/server:

```bash
python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py \
  --test-speaking-head-motion shake \
  --test-speaking-seconds 8 \
  --uart-port auto \
  --uart-baudrate 115200 \
  --enable-head-motor \
  --uart-debug
```

## FRDM UART State Machine Quick Reference

```text
bridge startup        -> wait 2s -> Time <payload> -> Weather daily <payload> -> Weather current <payload> -> Normal 0 0
Hey Jarvis detected   -> Thinking 0 0
AI/TTS starts         -> Speaking <0..5>
TTS speaking          -> MotorPitch <angle>, MotorYaw <angle>
follow-up listening   -> Thinking 0 0
bye / goodbye / 掰掰 / 拜拜 / 再見             -> Normal 0 0, then wake-only standby
sleep / rest / good night / 睡覺 / 休息 / 晚安  -> Sleep 0 0, then wake-only standby
play/resume music / 播放/繼續音樂              -> Music 0 0, then wake-only standby
pause/stop music / 暫停/停止音樂               -> Normal 0 0, then wake-only standby
focus/work mode / 專注/專心/工作模式           -> Focus 0 0, then wake-only standby
```

`Speaking`, `MotorPitch`, and `MotorYaw` use a single-argument wire format:

```text
Speaking 4
MotorPitch 90
MotorYaw 90
```

Other screen commands keep two arguments:

```text
Normal 0 0
Thinking 0 0
Music 0 0
Focus 0 0
Sleep 0 0
```

Startup data commands update GUI data and do not switch screens by themselves:

```text
Time 20260509,213005,6,+480
Weather daily,23,29,40,61
Weather current,27,27,0,2
Todo 3,1                  # open_count, done_count
TodoItem 1,17,open,Write%20report
TodoEnd 1
Music playing,Lo-fi%20Study,mpv
Focus focused,25,2        # state, remaining_min, streak_count
Health win=1,tts=1,music=1,camera=1
```

FRDM checkbox completion is a reverse UART event:

```text
TodoDone 17
```

Jetson completes item id `17` and sends a fresh `Todo` / `TodoItem` / `TodoEnd` snapshot back to FRDM.

Servo angle definition:

```text
Pitch 65  = lower/down limit
Pitch 90  = center
Pitch 115 = upper/up limit
Yaw 0     = right limit
Yaw 90    = center
Yaw 180   = left limit
```

## OBS Capture Display Toolkit

# Jetson Nano OBS Capture Display Toolkit

This toolkit helps diagnose and reduce black-screen output problems when a Jetson Nano desktop is connected to a Hagibis USB HDMI capture card for OBS capture on Windows.

The common pattern is:

- Jetson Nano displays normally on a real monitor.
- Windows and OBS detect the Hagibis capture device.
- OBS preview stays black when the Jetson is connected only to the capture card.

That usually means the capture card is not presenting EDID or hot-plug information in a way the Jetson accepts, or the Jetson is outputting a mode the capture card does not like. It can also be caused by X11 screen blanking or DPMS power saving after login.

These scripts do not install packages, reboot the Jetson, or perform system-level custom EDID changes.

## Files

- `jetson_display_diagnose.sh`: logs X11, `xrandr`, DRM connector status, and DPMS/screen saver state.
- `jetson_disable_display_sleep.sh`: disables X11 blanking/DPMS and common GNOME idle lock settings, then creates a user autostart entry.
- `jetson_force_display_mode.sh`: detects the connected output, prefers `DP-1`, and tries capture-friendly modes.
- `install_jetson_display_fix.sh`: makes scripts executable, disables display sleep, runs diagnosis, and optionally forces 720p.

## Step-By-Step Usage

Run these on the Jetson Nano while connected to the real monitor first:

```bash
chmod +x *.sh
./jetson_display_diagnose.sh
./jetson_disable_display_sleep.sh
./jetson_force_display_mode.sh
sudo shutdown now
```

Run the commands from the Jetson desktop Terminal when possible. If you run them over SSH, the scripts will try to discover the active X11 desktop environment automatically, but `xrandr` and `xset` still require access to the logged-in graphical session.

Then connect:

```text
Jetson Nano -> Hagibis capture card -> Windows PC running OBS
```

Boot the Jetson and check OBS.

You can also run the installer:

```bash
chmod +x *.sh
./install_jetson_display_fix.sh
```

To let the installer also run the force-mode script:

```bash
./install_jetson_display_fix.sh --force-720p
```

If you are running over SSH while the Jetson is sitting at the GDM login screen, use the GDM greeter fallback:

```bash
./install_jetson_display_fix.sh --force-720p --use-gdm-greeter
```

That fallback uses the Xorg `-auth` file from the running GDM display and may prompt for `sudo`. Prefer the normal desktop Terminal flow after logging in when possible.

## What The Scripts Try

`jetson_disable_display_sleep.sh` runs:

```bash
xset s off
xset -dpms
xset s noblank
```

It also tries these GNOME settings if the schemas and keys exist:

```bash
gsettings set org.gnome.desktop.session idle-delay 0
gsettings set org.gnome.desktop.screensaver lock-enabled false
gsettings set org.gnome.desktop.screensaver idle-activation-enabled false
```

It creates:

```text
~/.config/autostart/disable-display-sleep.desktop
```

with:

```text
Exec=sh -c 'xset s off; xset -dpms; xset s noblank'
```

`jetson_force_display_mode.sh` detects connected displays from `xrandr`, prefers `DP-1` when it is connected, and then tries:

1. `1280x720` at `60`
2. `1280x720` at `59.94`
3. `1280x720` at `50`
4. `1920x1080` at `60`
5. `1920x1080` at `59.94`

If none of those modes appear in `xrandr`, it prints available modes and does not force anything.

## Recommended OBS Settings

- Source: Video Capture Device
- Device: Hagibis
- Resolution/FPS type: Custom
- Resolution: 1280x720
- FPS: 30
- Video format: MJPEG first, then YUY2 if needed

If OBS gets stuck at a small black frame such as 640x360, remove and recreate the Video Capture Device source, then set Custom resolution and format again.

## Important Interpretation

Disabling DPMS and screen blanking can help only when the Jetson detects a display but the desktop is blanked or sleeping.

If `xrandr --query` shows the output as disconnected when the Jetson is connected only to the Hagibis capture card, software mode forcing usually cannot fix that by itself. In that case, the more reliable fix is hardware EDID management.

If the output says `Authorization required, but no authorization protocol specified`, the script could not access the X11 desktop session. Run it from the Jetson desktop Terminal, or make sure `DISPLAY` and `XAUTHORITY` point to the active logged-in desktop session.

If the diagnosis shows only `gdm` or `gdm-launch-environment` processes, the Jetson is at the graphical login screen rather than the logged-in user desktop. Log in on the Jetson first, open Terminal on that desktop, and rerun the scripts. SSH sessions often show an empty `DISPLAY` and cannot control `xrandr` directly.

Recommended hardware workaround:

```text
Jetson Nano -> HDMI splitter with EDID management
             -> real monitor
             -> Hagibis capture card -> OBS
```

An HDMI EDID emulator can also work. Custom Linux EDID configuration is intentionally not performed by these scripts because it is easier to break display login behavior if configured incorrectly.

## Troubleshooting

| Symptom | Likely Meaning | What To Try |
| --- | --- | --- |
| OBS detects Hagibis but black preview | Capture device is present, but no usable video signal is arriving | Set OBS to Custom 1280x720, 30 FPS, MJPEG first |
| Jetson connected to real monitor works | Jetson GPU and desktop output are basically working | Run diagnosis and force 720p while on the monitor |
| Jetson connected to Hagibis alone shows disconnected in `xrandr` | EDID or hot-plug detection problem | Use an HDMI splitter with EDID management or an EDID emulator |
| Windows Camera app sees Hagibis but black image | The capture card is visible to Windows but receives no usable HDMI signal | Check Jetson output mode and EDID/hot-plug behavior |
| OBS source size stuck at 640x360 black | OBS may have cached a bad capture mode | Recreate the source and set Custom 1280x720, MJPEG |

## Rollback

Remove the autostart entry:

```bash
rm -f ~/.config/autostart/disable-display-sleep.desktop
```

Re-enable GNOME screen lock if desired:

```bash
gsettings set org.gnome.desktop.screensaver lock-enabled true
gsettings set org.gnome.desktop.screensaver idle-activation-enabled true
gsettings set org.gnome.desktop.session idle-delay 300
```

The scripts do not perform risky system-level EDID modification by default. There is no `/etc/X11`, bootloader, kernel command line, or firmware display override to undo.
