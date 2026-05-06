# FRDM Wake Voice Chat Demo Manual

這份文件是現場 demo 用的完整操作手冊。目標是讓 Jetson 聽到喚醒詞後錄音，把語音送到 Windows 本地 ASR/Ollama，再把回覆交給 Jetson TTS 播放，同時把目前狀態用 UART 送到 FRDM-MCXN947。

目前推薦正式執行檔：

```text
wake_voice_chat_frdm_bridge.py
```

不用 wake word、想按 Enter 錄音時才用：

```text
voice_chat_frdm_uart_bridge.py
```

只想單獨測 FRDM UART 時用：

```text
frdm_uart_context_sender.py
```

## 0. 系統路線

```text
Jetson UACDemo microphone
-> wake_voice_chat_frdm_bridge.py
-> Windows http://100.108.141.26:8766/voice-chat
-> Windows local ASR
-> Windows local Ollama qwen35-fast:latest
-> Jetson UART command decision
-> FRDM-MCXN947 UART
-> Jetson Piper TTS speaker playback
```

這套不使用 Gemini、OpenAI 或其他雲端 AI。`openwakeword` 只在 Jetson 本機做喚醒詞偵測；ASR 和 Ollama 都跑在 Windows 本機。

## 1. 硬體接線

### Jetson

Jetson 需要接：

```text
USB microphone: UACDemoV1.0
USB speaker   : UACDemoV1.0 playback side
FRDM board    : FRDM-MCXN947 J17 MCU-LINK USB-C port
```

目前這台 Jetson 的 USB speaker 正確 ALSA device 是：

```text
plughw:CARD=UACDemoV10_1,DEV=0
```

TTS 設定檔應該是：

```text
/home/asrlab-yian/MakeNTU/jetson_piper_tts/.env
AUDIO_DEVICE=plughw:CARD=UACDemoV10_1,DEV=0
ENABLE_STREAM_PLAYBACK=true
```

### FRDM-MCXN947

FRDM 要接到 Jetson 的 **J17 MCU-LINK / CMSIS-DAP USB-C port**。不要只接供電 port，也不要接在 Windows 上。

FRDM-MCXN947 的 MCU-Link VCOM 會透過 J17 枚舉成 Linux serial port，例如：

```text
/dev/ttyACM0
/dev/ttyACM1
/dev/ttyUSB0
/dev/serial/by-id/...
```

程式支援自動選 port，所以正式跑建議使用：

```text
--uart-port auto
```

如果 `--list-uarts` 顯示 `(none)`，代表 Jetson 完全沒看到 FRDM USB serial。這時候不要調 Python 參數，先處理線、port、hub、J18 jumper 和板子供電。

NXP 文件重點：

```text
J17 是 MCU-Link USB connector。
MCU-Link VCOM 是 USB-to-UART bridge。
要使用 MCU-Link USB-to-UART bridge，J18 jumper 要 open，並把 J17 接到 host。
UART terminal 設定是 115200 baud, 8 data bits, no parity, 1 stop bit, no flow control。
```

參考：

```text
https://www.nxp.com/document/guide/getting-started-with-frdm-mcxn947%3AGS-FRDM-MCXNXX
https://community.nxp.com/pwmxy87654/attachments/pwmxy87654/MCX/4254/1/UM12018%20%283%29.pdf
```

## 2. Jetson 目錄和 venv

所有 Jetson 指令先進資料夾：

```bash
cd /home/asrlab-yian/MakeNTU/frdm_uart_context_sender
```

語音 bridge 使用 `emotion_robot_controller` 的 venv：

```bash
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate
python3 -m pip install -r requirements.txt
```

`requirements.txt` 包含：

```text
pyserial
numpy
sounddevice
openwakeword
```

第一次跑 wake word 需要下載 `hey_jarvis` 模型。程式會自動下載；也可以手動測：

```bash
python3 - <<'PY'
from openwakeword.utils import download_models
download_models(["hey_jarvis"])
print("hey_jarvis model ready")
PY
```

## 3. Terminal A: Windows ASR/Ollama Server

在 Windows PowerShell 先確認 Ollama：

```powershell
ollama list
ollama pull qwen35-fast:latest
ollama serve
```

如果 `ollama serve` 顯示 port already in use，通常代表 Ollama 已經在背景執行。

另外開一個 Windows PowerShell 啟動桌機 server：

```powershell
cd C:\Users\User\Desktop\windows_desktop_server_bundle
.\.venv\Scripts\Activate.ps1
python desktop_fast_chat_server.py --host 0.0.0.0 --port 8766 --ollama-model qwen35-fast:latest --no-think
```

Windows 本機測試：

```powershell
curl.exe http://127.0.0.1:11434/api/tags
curl.exe http://127.0.0.1:8766/health
```

確認 Windows Tailscale IP：

```powershell
tailscale ip -4
```

本文件預設 Windows IP 是：

```text
100.108.141.26
```

如果 IP 不同，Jetson 指令裡的 `--server-url` 要改成新的 IP。

## 4. Terminal B: Jetson Piper TTS

開 Jetson TTS server：

```bash
cd /home/asrlab-yian/MakeNTU/jetson_piper_tts
source .venv/bin/activate
python -m jetson_piper_tts.server --host 0.0.0.0 --port 8777
```

另一個 Jetson terminal 測 health：

```bash
curl http://127.0.0.1:8777/health
```

應該看到：

```text
ready: true
audio.device: plughw:CARD=UACDemoV10_1,DEV=0
piper_path: /home/asrlab-yian/MakeNTU/jetson_piper_tts/.venv/bin/piper
```

直接測喇叭：

```bash
cd /home/asrlab-yian/MakeNTU/jetson_piper_tts
source .venv/bin/activate
python -m jetson_piper_tts.speak "USB喇叭测试。" --device 'plughw:CARD=UACDemoV10_1,DEV=0' --stream
```

查看 TTS queue：

```bash
curl http://127.0.0.1:8777/queue
```

## 5. Terminal C: Jetson Preflight

回到 bridge 資料夾：

```bash
cd /home/asrlab-yian/MakeNTU/frdm_uart_context_sender
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate
```

檢查麥克風：

```bash
python3 wake_voice_chat_frdm_bridge.py --list-mics
```

應該看到名字包含 `UACDemo` 的 input device。正式跑會用：

```text
--mic-keyword UACDemo
```

檢查 FRDM UART：

```bash
python3 wake_voice_chat_frdm_bridge.py --list-uarts
```

成功時會看到至少一個 serial port，例如：

```text
UART serial ports:
 * /dev/serial/by-id/usb-NXP_MCU-Link...
   /dev/ttyACM0
```

如果顯示 `(none)`，先看第 11 節「UART 完全看不到」。

檢查 Windows server + TTS：

```bash
python3 wake_voice_chat_frdm_bridge.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --check-server \
  --uart-dry-run \
  --tts-debug
```

這個指令會：

```text
GET Windows /health
GET Windows /debug
POST Windows /text-chat smoke test
檢查 Jetson TTS /health
建立 uart.json
只印 UART TX，不開 serial
```

## 6. 正式跑 Hands-Free Demo

FRDM 已被 `--list-uarts` 看見後，用這個：

```bash
cd /home/asrlab-yian/MakeNTU/frdm_uart_context_sender
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate

python3 wake_voice_chat_frdm_bridge.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --mic-keyword UACDemo \
  --uart-port auto \
  --tts-debug \
  --uart-debug
```

看到這行後開始講喚醒詞：

```text
Listening for wake word 'hey_jarvis'
```

流程會是：

```text
偵測 hey_jarvis
-> 錄音
-> POST audio to Windows /voice-chat
-> 印 Transcript / Reply / Emotion / Timing
-> 決定 UART commands
-> 寫入 uart.json
-> 送 UART 給 FRDM
-> enqueue TTS
```

如果 FRDM 還沒接好，但想先讓語音和 TTS 跑通：

```bash
python3 wake_voice_chat_frdm_bridge.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --mic-keyword UACDemo \
  --uart-dry-run \
  --tts-debug \
  --uart-debug
```

## 7. Enter 版 Demo

不想用 wake word 時，用 Enter 版：

```bash
cd /home/asrlab-yian/MakeNTU/frdm_uart_context_sender
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate

python3 voice_chat_frdm_uart_bridge.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --mic-keyword UACDemo \
  --uart-port auto \
  --tts-debug \
  --uart-debug
```

每次按 Enter 後開始錄音，再按 Enter 停止錄音。

## 8. 不錄音，只測文字路徑

這個測試會直接送文字到 Windows `/text-chat`，再走 UART/TTS：

```bash
python3 wake_voice_chat_frdm_bridge.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --text "請你去睡覺。" \
  --uart-dry-run \
  --tts-debug \
  --uart-debug
```

如果要真的送 FRDM：

```bash
python3 wake_voice_chat_frdm_bridge.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --text "請你去睡覺。" \
  --uart-port auto \
  --tts-debug \
  --uart-debug
```

## 9. 單獨測 FRDM UART

列出 serial ports：

```bash
python3 frdm_uart_context_sender.py --list-ports
```

只看會送什麼，不開 serial：

```bash
python3 frdm_uart_context_sender.py --command Sleep --port auto --dry-run
```

真的送到 FRDM：

```bash
python3 frdm_uart_context_sender.py --command Sleep --port auto
python3 frdm_uart_context_sender.py --command Normal --port auto
python3 frdm_uart_context_sender.py --command "ShowNum 123" --port auto
python3 frdm_uart_context_sender.py --command "MotorPitch 90" --port auto
python3 frdm_uart_context_sender.py --command "MotorYaw 90" --port auto
```

預設 UART 設定：

```text
baudrate: 115200
line ending: CRLF
timeout: 1.0s
read after TX: 250ms
```

如果 firmware 改成只吃 LF：

```bash
python3 frdm_uart_context_sender.py --command Sleep --port auto --line-ending lf
```

## 10. FRDM UART 指令規則

支援的 FRDM 指令：

```text
Sleep
Normal
ShowNum <0..999999>
MotorPitch <0..180>
MotorYaw <0..180>
```

常見語意對應：

```text
請你去睡覺 / 休息 / sleep / tired -> Sleep
你好 / 醒來 / 回來 / normal / work -> Normal
數字 / show number / ShowNum -> ShowNum
看左 / 看右 -> MotorYaw
抬頭 / 低頭 -> MotorPitch
```

沒有明確狀態時，預設會送：

```text
Normal
```

不想預設送 `Normal`：

```bash
--no-default-normal
```

看判斷原因：

```bash
--uart-debug
```

## 11. 故障排查

### UART 完全看不到

現象：

```text
UART serial ports:
  (none)
WARNING: FRDM UART failed: No UART serial device is visible
```

確認：

```bash
python3 wake_voice_chat_frdm_bridge.py --list-uarts
ls -l /dev/ttyACM* /dev/ttyUSB* /dev/serial/by-id/* 2>/dev/null
lsusb
lsusb -t
```

重插 FRDM 時開 kernel log：

```bash
journalctl -k -f
```

正常應該看到類似：

```text
new full-speed USB device
MCU-Link
CMSIS-DAP
cdc_acm
ttyACM0
```

如果重插完全沒 log：

```text
線不是 data cable
插錯 FRDM port
FRDM 沒接到 Jetson
USB hub 供電或相容性問題
```

處理順序：

```text
1. 改接 FRDM J17 MCU-LINK USB-C port
2. 換一條確定能傳資料的 USB-C cable
3. 不經 hub，直接接 Jetson
4. 或改用 powered USB hub
5. 確認 J18 jumper 是 open
6. 重新跑 --list-uarts
```

成功看到 port 後正式指令用：

```bash
--uart-port auto
```

### UART Permission denied

現象：

```text
Permission denied: /dev/ttyACM0
```

臨時測：

```bash
sudo chmod 666 /dev/ttyACM0
```

長期解法：

```bash
sudo usermod -aG dialout "$USER"
sudo reboot
```

### FRDM 有 port 但沒反應

先送最小指令：

```bash
python3 frdm_uart_context_sender.py --command Sleep --port auto --read-ms 1000
```

再檢查：

```text
FRDM firmware 是否正在跑 monitor command parser
baudrate 是否 115200
line ending 是否 CRLF
FRDM 端 UART RX/TX 是否接的是 MCU-Link VCOM 對應腳位
terminal 是否被其他程式佔用同一個 port
```

如果懷疑 line ending：

```bash
python3 frdm_uart_context_sender.py --command Sleep --port auto --line-ending crlf
python3 frdm_uart_context_sender.py --command Sleep --port auto --line-ending lf
```

### Windows server timeout

現象：

```text
server health check failed
urlopen error timed out
```

Jetson 測：

```bash
curl -v --connect-timeout 5 http://100.108.141.26:8766/health
```

Windows 檢查：

```powershell
curl.exe http://127.0.0.1:8766/health
tailscale ip -4
```

如果 Windows IP 變了，更新 Jetson 的 `--server-url`。

### Ollama WinError 10061

現象：

```text
Ollama request failed: WinError 10061
ollama_url: http://localhost:11434/api/chat
```

代表 Jetson 連得到 Windows server，但 Windows server 連不到 Windows 本機 Ollama。

Windows PowerShell：

```powershell
curl.exe http://127.0.0.1:11434/api/tags
ollama list
ollama serve
```

然後重開桌機 server：

```powershell
cd C:\Users\User\Desktop\windows_desktop_server_bundle
.\.venv\Scripts\Activate.ps1
python desktop_fast_chat_server.py --host 0.0.0.0 --port 8766 --ollama-model qwen35-fast:latest --no-think
```

### TTS ready 但沒聲音

確認 TTS health：

```bash
curl http://127.0.0.1:8777/health
```

應該看到：

```text
ready: true
audio.device: plughw:CARD=UACDemoV10_1,DEV=0
```

確認 ALSA 播放裝置：

```bash
aplay -l
aplay -L | grep -A2 -B1 UACDemo
```

直接測：

```bash
cd /home/asrlab-yian/MakeNTU/jetson_piper_tts
source .venv/bin/activate
python -m jetson_piper_tts.speak "USB喇叭测试。" --device 'plughw:CARD=UACDemoV10_1,DEV=0' --stream
```

如果 `.env` 改過，要重開 TTS server 才會生效。

### Mic 沒聲音或 ASR 很怪

列出 mic：

```bash
python3 wake_voice_chat_frdm_bridge.py --list-mics
```

正式跑固定用：

```bash
--mic-keyword UACDemo
```

如果錄音 RMS 很低或 transcript 只有「嗯」：

```text
靠近 mic
降低 --volume-min
增加 --silence-duration
確認不是選到 Jetson APE 虛擬 input
```

可試：

```bash
--volume-min 10000 --silence-duration 1.5 --listen-debug
```

### Wake word 太容易誤觸

提高 threshold：

```bash
--wake-threshold 0.65
```

Wake word 太難觸發：

```bash
--wake-threshold 0.4
```

不用 wake word，只靠音量啟動錄音：

```bash
--no-wake-word
```

### Audio input overflow

偶爾看到：

```text
Audio input overflow; continuing.
```

通常可以先忽略。若頻繁出現，試：

```bash
--wake-chunk-ms 120
```

或關掉其他吃 CPU 的工作。

## 12. Demo 前檢查表

```bash
cd /home/asrlab-yian/MakeNTU/frdm_uart_context_sender
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate

python3 wake_voice_chat_frdm_bridge.py --list-mics
python3 wake_voice_chat_frdm_bridge.py --list-uarts
curl http://127.0.0.1:8777/health
curl http://100.108.141.26:8766/health
```

全部 OK 後跑：

```bash
python3 wake_voice_chat_frdm_bridge.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --mic-keyword UACDemo \
  --uart-port auto \
  --tts-debug \
  --uart-debug
```

沒有 FRDM serial 但想先跑語音：

```bash
python3 wake_voice_chat_frdm_bridge.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --mic-keyword UACDemo \
  --uart-dry-run \
  --tts-debug \
  --uart-debug
```

## 13. 常用命令速查

```bash
# 列 mic
python3 wake_voice_chat_frdm_bridge.py --list-mics

# 列 UART
python3 wake_voice_chat_frdm_bridge.py --list-uarts

# 檢查 Windows/TTS
python3 wake_voice_chat_frdm_bridge.py --server-url http://100.108.141.26:8766/voice-chat --check-server --uart-dry-run --tts-debug

# 單獨 dry-run UART
python3 frdm_uart_context_sender.py --command Sleep --port auto --dry-run

# 單獨送 UART
python3 frdm_uart_context_sender.py --command Sleep --port auto

# Hands-free 正式跑
python3 wake_voice_chat_frdm_bridge.py --server-url http://100.108.141.26:8766/voice-chat --mic-keyword UACDemo --uart-port auto --tts-debug --uart-debug

# Hands-free 無 FRDM 跑語音
python3 wake_voice_chat_frdm_bridge.py --server-url http://100.108.141.26:8766/voice-chat --mic-keyword UACDemo --uart-dry-run --tts-debug --uart-debug
```
