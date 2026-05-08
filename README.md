# MakeNTU Desktop Pet Demo

這個 repo 目前主要是 MakeNTU 桌寵系統的整合專案：Jetson 端負責 wake word、錄音、相機、TTS、FRDM UART、music/weather sidecar、to-do list、專心工作模式；Windows 桌機負責 ASR、Ollama/Qwen 回覆與 vision/focus-check。

正式 demo 建議優先看：

```text
frdm_uart_context_sender/QUICK_START.md   # 現場完整啟動手冊
frdm_uart_context_sender/README.md        # 架構、UART、focus/to-do/music/weather 詳細說明
```

## Current Main Features

```text
Wake word             : Hey Jarvis / openWakeWord
Conversation mode     : 一次喚醒後可連續 follow-up，結束後回 wake-only standby
Windows AI server     : /voice-chat, /text-chat, /focus-check
Local model path      : Windows ASR + Ollama qwen35-fast:latest
Vision                : speech-end beep 後拍照，上傳到 Windows；照片預設 memory-only
FRDM UART             : auto @ 115200 CRLF
FRDM state machine    : Normal / Thinking / Speaking / Music / Focus / Sleep
Head motor            : MotorPitch 65..90..115, MotorYaw 0..90..180
TTS                   : Jetson Piper /speak_async, UACDemo audio
Music tool            : Jetson local /music, mpv + yt-dlp
Weather tool          : Jetson local /weather, Open-Meteo
To-do list            : local JSON voice tool, frdm_uart_context_sender/logs/todo_list.json
Focus work mode       : 每 60 秒取樣，產生 focus_summary.json + focus_report.md
Focus notification    : optional Discord webhook; fallback reads ~/.config/makentu/discord_webhook_url
Display toolkit       : OBS/Hagibis capture troubleshooting scripts, kept below
```

不要固定 USB 數字 index。正式 demo 用 keyword/auto：

```text
mic       : --mic-keyword UACDemo
beep      : --beep-keyword UACDemo
camera    : --camera-id auto
FRDM UART : --uart-port auto
```

## One-Page Startup

啟動順序：

```text
Terminal 1 on Windows : desktop_fast_chat_server.py
Terminal 2 on Jetson  : jetson_piper_tts.server
Terminal 4 on Jetson  : music_web_player.py   # /music + /weather
Terminal 3 on Jetson  : wake_voice_chat_frdm_bridge.py
```

目前預設 Tailscale / endpoint：

```text
Windows Tailscale : 100.108.141.26
Jetson Tailscale  : 100.110.90.72
Windows server    : http://100.108.141.26:8766/voice-chat
Jetson TTS        : http://127.0.0.1:8777
Local tools       : http://127.0.0.1:8788/music and /weather
```

如果 IP 變了，把下面指令裡的 `100.108.141.26` 一起改掉。

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

如果 server code 有更新，先從 Jetson 同步最新版 bundle：

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\Desktop\windows_desktop_server_bundle" | Out-Null
scp asrlab-yian@100.110.90.72:/home/asrlab-yian/MakeNTU/emotion_robot_controller/voice_stt_remote/windows_desktop_server_bundle/desktop_fast_chat_server.py "$env:USERPROFILE\Desktop\windows_desktop_server_bundle\desktop_fast_chat_server.py"
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

`.env` 建議：

```text
AUDIO_DEVICE=plughw:CARD=UACDemoV10,DEV=0
DEFAULT_VOLUME_GAIN=2.25
ENABLE_STREAM_PLAYBACK=true
```

### Terminal 4: Jetson Music/Weather Tool

Jetson：

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

Health check：

```bash
curl http://127.0.0.1:8788/health
```

### Discord Webhook Setup For Focus Reports

建議把 webhook 放在 secret file，不要每次 export，也不要 commit 到 git：

```bash
mkdir -p ~/.config/makentu
printf '%s\n' 'https://discord.com/api/webhooks/...' > ~/.config/makentu/discord_webhook_url
chmod 600 ~/.config/makentu/discord_webhook_url
```

確認不要印完整 URL：

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

程式會依序讀：

```text
--focus-discord-webhook-url
FOCUS_DISCORD_WEBHOOK_URL
DISCORD_WEBHOOK_URL
~/.config/makentu/discord_webhook_url
```

### Terminal 3: Jetson Wake Bridge Full Demo

Jetson：

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

若 FRDM motor parser 還在修，暫時把 `--enable-head-motor` 換成：

```bash
--disable-head-motor \
```

## Voice Test Flow

Wake Bridge 啟動後，可以照這個順序測完整功能：

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

進入專心模式後，focus 指令會回到 wake-only standby，所以要再喚醒：

```text
Hey Jarvis，完成待辦 1
Hey Jarvis，結束工作
```

預期結果：

```text
FRDM 切到 Focus / Thinking / Speaking / Normal 等狀態
speech-end 時相機拍照並上傳
to-do JSON 被更新
focus 結束產生 focus_summary.json + focus_report.md
Discord 收到 focus summary
```

查最新 focus 報告：

```bash
latest=$(find /tmp/focus_voice_test -maxdepth 1 -type d -name 'focus_*' | sort | tail -n 1)
echo "$latest"
python3 -m json.tool "$latest/focus_summary.json" | sed -n '1,220p'
sed -n '1,220p' "$latest/focus_report.md"
```

## Direct Tests

不開完整 wake bridge，只測 focus report + Discord：

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

抓一張相機視野：

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

測 FRDM head motor，不開 mic/camera/TTS/server：

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
```

`Speaking`、`MotorPitch`、`MotorYaw` 是單參數格式：

```text
Speaking 4
MotorPitch 90
MotorYaw 90
```

其他畫面 command 保留兩個參數：

```text
Normal 0 0
Thinking 0 0
Music 0 0
Focus 0 0
Sleep 0 0
```

Servo 角度定義：

```text
Pitch 65  = 低頭極限
Pitch 90  = 中間
Pitch 115 = 抬頭極限
Yaw 0     = 右轉極限
Yaw 90    = 中間
Yaw 180   = 左轉極限
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
