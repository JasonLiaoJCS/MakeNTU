# Quick Start: FRDM UART Context Sender

這份是 demo 前最快能照著跑的版本。完整規則與參數請看 [README.md](README.md)。

## 1. 進入資料夾

```bash
cd /home/asrlab-yian/MakeNTU/frdm_uart_context_sender
```

如果你要跑「完整語音聊天 + 回覆後自動送 FRDM UART」，請用這支新的橋接程式：

```text
voice_chat_frdm_uart_bridge.py
```

它不會改 `jetson_fast_voice_chat.py`，只是重用原本錄音、送 Windows、TTS、顯示結果的函式，收到 reply/emotion 後立刻產生 `uart.json` 並送 UART。

## 2. 安裝依賴

如果你已經在 MakeNTU 的 Python venv 裡，可以直接裝：

```bash
python3 -m pip install -r requirements.txt
```

只做 `--dry-run` 不需要真的打開 UART；要送到 FRDM 才需要 `pyserial`。

## 3. 插上 FRDM，找 USB UART Port

```bash
ls /dev/ttyACM* /dev/ttyUSB* 2>/dev/null
```

常見會是：

```text
/dev/ttyACM0
```

如果不是 `/dev/ttyACM0`，後面的 `--port` 改成你看到的 port。

## 4. 啟動完整語音聊天 + FRDM UART + TTS

完整 demo 通常會開三個 terminal：

```text
Terminal A: Windows 桌機 ASR + Ollama server
Terminal B: Jetson 本地 Piper TTS server
Terminal C: Jetson 語音聊天 + FRDM UART bridge
```

如果你只想測 FRDM 螢幕，不想讓 Jetson 說話，可以先跳過 Terminal B，並在 Terminal C 加 `--no-tts`。

### Terminal A：Windows 啟動 ASR/Ollama Server

在 Windows PowerShell：

```powershell
cd C:\Users\User\Desktop\windows_desktop_server_bundle
.\.venv\Scripts\Activate.ps1
python desktop_fast_chat_server.py --host 0.0.0.0 --port 8766 --ollama-model qwen35-fast:latest --no-think
```

Windows 本機先測：

```powershell
curl http://127.0.0.1:8766/health
```

確認 Windows 的 Tailscale IP：

```powershell
tailscale ip -4
```

如果 IP 不是 `100.108.141.26`，下面 Jetson 指令裡的 `--server-url` 要換成新的 IP。

### Terminal B：Jetson 啟動 Piper TTS Server

如果你想讓 Jetson 把 Windows 回來的 `reply` 念出來，就要開本地文字轉語音 server。

你這台 Jetson 目前的 USB 喇叭在 ALSA 裡是：

```text
card 1, device 0 -> plughw:1,0
```

我已經把 TTS 設定檔改成：

```text
/home/asrlab-yian/MakeNTU/jetson_piper_tts/.env
AUDIO_DEVICE=plughw:1,0
```

在 Jetson 開一個新 terminal：

```bash
cd /home/asrlab-yian/MakeNTU/jetson_piper_tts
source .venv/bin/activate
python -m jetson_piper_tts.server --host 0.0.0.0 --port 8777
```

另一個 Jetson terminal 可以測：

```bash
curl http://127.0.0.1:8777/health
```

`health` 裡應該看到：

```text
audio device=plughw:1,0
```

也可以直接測喇叭：

```bash
cd /home/asrlab-yian/MakeNTU/jetson_piper_tts
source .venv/bin/activate
python -m jetson_piper_tts.speak "USB喇叭测试。" --device plughw:1,0 --stream
```

### 換不同語音包

目前這台 Jetson 已經有這幾個中文 Piper voice：

```text
zh_CN-chaowen-medium
zh_CN-huayan-medium
zh_CN-huayan-x_low
zh_CN-xiao_ya-medium
```

查 TTS server 目前看得到哪些 voice：

```bash
curl http://127.0.0.1:8777/voices | python3 -m json.tool
```

直接測不同聲音：

```bash
cd /home/asrlab-yian/MakeNTU/jetson_piper_tts
source .venv/bin/activate

python -m jetson_piper_tts.speak "这是朝文的声音。" --voice zh_CN-chaowen-medium --device plughw:1,0 --stream
python -m jetson_piper_tts.speak "这是花妍的声音。" --voice zh_CN-huayan-medium --device plughw:1,0 --stream
python -m jetson_piper_tts.speak "这是小雅的声音。" --voice zh_CN-xiao_ya-medium --device plughw:1,0 --stream
python -m jetson_piper_tts.speak "这是低延迟版本。" --voice zh_CN-huayan-x_low --device plughw:1,0 --stream
```

臨時讓 bridge 用某個 voice，不改設定檔：

```bash
cd /home/asrlab-yian/MakeNTU/frdm_uart_context_sender
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate

python3 voice_chat_frdm_uart_bridge.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --device 0 \
  --uart-port /dev/ttyACM0 \
  --tts-voice zh_CN-xiao_ya-medium \
  --tts-debug
```

如果你覺得某個 voice 最適合 demo，要永久改預設 voice，改 TTS 的 `.env`：

```bash
sed -i 's#^PIPER_MODEL=.*#PIPER_MODEL=./models/zh_CN-xiao_ya-medium.onnx#' /home/asrlab-yian/MakeNTU/jetson_piper_tts/.env
sed -i 's#^PIPER_CONFIG=.*#PIPER_CONFIG=./models/zh_CN-xiao_ya-medium.onnx.json#' /home/asrlab-yian/MakeNTU/jetson_piper_tts/.env
```

改完一定要重開 TTS server：

```bash
cd /home/asrlab-yian/MakeNTU/jetson_piper_tts
source .venv/bin/activate
python -m jetson_piper_tts.server --host 0.0.0.0 --port 8777
```

如果要下載新的 Piper voice：

```bash
cd /home/asrlab-yian/MakeNTU/jetson_piper_tts
./scripts/download_voice.sh VOICE_NAME
```

例如：

```bash
./scripts/download_voice.sh zh_CN-huayan-medium
```

如果你只是要先測 FRDM UART，可以不開 TTS server，後面的 bridge 指令加 `--no-tts`。

### Terminal C：Jetson 啟動 Voice + FRDM Bridge

Jetson 這邊用原本語音 venv：

```bash
cd /home/asrlab-yian/MakeNTU/frdm_uart_context_sender
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate
```

先做 server smoke test，但不要真的送 UART：

```bash
python3 voice_chat_frdm_uart_bridge.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --check-server \
  --uart-dry-run \
  --no-tts
```

如果出現：

```text
ERROR: server health check failed: <urlopen error timed out>
Check Windows server, then try: curl http://100.108.141.26:8766/health
```

代表 Jetson 目前連不到 Windows 的 `desktop_fast_chat_server.py`，這不是 FRDM UART 問題。先在 Jetson 測：

```bash
curl -v --connect-timeout 5 http://100.108.141.26:8766/health
```

如果也 timeout，去 Windows PowerShell 啟動 server：

```powershell
cd C:\Users\User\Desktop\windows_desktop_server_bundle
.\.venv\Scripts\Activate.ps1
python desktop_fast_chat_server.py --host 0.0.0.0 --port 8766 --ollama-model qwen35-fast:latest --no-think
```

Windows 本機先測和 Tailscale IP 檢查請看上面的 Terminal A。

正式跑完整流程：

```bash
python3 voice_chat_frdm_uart_bridge.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --device 0 \
  --uart-port /dev/ttyACM0 \
  --tts-debug
```

如果你暫時不想讓 Piper TTS 說話，只想測 FRDM：

```bash
python3 voice_chat_frdm_uart_bridge.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --device 0 \
  --uart-port /dev/ttyACM0 \
  --no-tts
```

如果你沒有開 Terminal B 的 TTS server，但是這裡又沒有加 `--no-tts`，會看到：

```text
WARNING: TTS health check failed: <urlopen error [Errno 111] Connection refused>
Voice chat will continue, but replies will not be spoken until jetson_piper_tts is running.
```

這不是 FRDM 問題，意思只是 TTS server 沒開；聊天和 UART 仍然可以繼續。

完整流程會是：

```text
Jetson 錄音
-> Windows ASR
-> Windows Ollama reply
-> Windows emotion
-> Jetson 顯示 Transcript / Reply / Emotion / Timing
-> Jetson 產生 uart.json
-> Jetson 送 CRLF UART 給 FRDM
-> Jetson 播放 TTS，如果沒有 --no-tts
```

## 5. 先 Dry Run，不送 UART

這一步只會印出 reply、產生 `uart.json`、顯示準備送出的 UART 指令。

```bash
python3 frdm_uart_context_sender.py \
  --text "我有點累了，先安靜一點" \
  --reply "好，我切到安靜模式，先不打擾你。" \
  --emotion tired \
  --dry-run
```

預期看到：

```text
Reply:
好，我切到安靜模式，先不打擾你。

uart_json: uart.json
TX: Sleep
```

## 6. 手動測 FRDM 指令

先測最小指令，確認 FRDM firmware 的 monitor command parser 收得到。

```bash
python3 frdm_uart_context_sender.py \
  --command Normal \
  --port /dev/ttyACM0
```

切到睡眠畫面：

```bash
python3 frdm_uart_context_sender.py \
  --command Sleep \
  --port /dev/ttyACM0
```

測 pitch / yaw servo：

```bash
python3 frdm_uart_context_sender.py \
  --command "MotorPitch 90" \
  --command "MotorYaw 90" \
  --port /dev/ttyACM0
```

測數字：

```bash
python3 frdm_uart_context_sender.py \
  --command "ShowNum 7" \
  --port /dev/ttyACM0
```

這支工具現在預設用 `CRLF` 結尾，因為你這片 FRDM 的 monitor command parser 需要 `\r\n` 才會真的執行 command。

如果之後你的 firmware 改成只吃 LF，可以改加：

```bash
--line-ending lf
```

完整例子：

```bash
python3 frdm_uart_context_sender.py \
  --command Normal \
  --line-ending lf \
  --port /dev/ttyACM0
```

## 7. 用情境自動決定 UART

切到 Normal：

```bash
python3 frdm_uart_context_sender.py \
  --text "我回來了，可以恢復正常" \
  --reply "好，我切回正常模式。" \
  --emotion neutral \
  --port /dev/ttyACM0
```

切到 Sleep：

```bash
python3 frdm_uart_context_sender.py \
  --text "我想休息一下，先安靜一點" \
  --reply "好，我切到安靜模式。" \
  --emotion tired \
  --port /dev/ttyACM0
```

顯示數字：

```bash
python3 frdm_uart_context_sender.py \
  --text "請顯示 183" \
  --reply "好，我顯示 183。" \
  --emotion neutral \
  --port /dev/ttyACM0
```

## 8. 用 JSON 檔測試

先看會送什麼：

```bash
python3 frdm_uart_context_sender.py \
  --input examples/uart_context.json \
  --dry-run
```

真的送到 FRDM：

```bash
python3 frdm_uart_context_sender.py \
  --input examples/uart_context.json \
  --port /dev/ttyACM0
```

手動 commands JSON：

```bash
python3 frdm_uart_context_sender.py \
  --input examples/uart_manual_commands.json \
  --port /dev/ttyACM0
```

## 9. 接到語音聊天流程

你的語音流程已經會拿到：

```json
{
  "transcript": "我有點累了，先休息一下。",
  "reply": "好，我切到安靜模式。",
  "emotion": {
    "primary": "tired"
  }
}
```

現在建議直接跑新橋接程式：

```bash
python3 /home/asrlab-yian/MakeNTU/frdm_uart_context_sender/voice_chat_frdm_uart_bridge.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --uart-port /dev/ttyACM0
```

如果只是要測 FRDM sender 本身，也可以用檔案串：

```bash
cat > /tmp/current_context.json <<'JSON'
{
  "transcript": "我有點累了，先休息一下。",
  "reply": "好，我切到安靜模式。",
  "emotion": {
    "primary": "tired"
  }
}
JSON

python3 /home/asrlab-yian/MakeNTU/frdm_uart_context_sender/frdm_uart_context_sender.py \
  --input /tmp/current_context.json \
  --port /dev/ttyACM0
```

程式會先顯示 reply，接著立刻送 UART。

## 10. uart.json 在哪裡

預設會在目前資料夾產生：

```text
uart.json
```

它會記錄這次 reply、transcript、emotion、要送的 FRDM command、serial 結果，方便 debug。

指定輸出路徑：

```bash
python3 frdm_uart_context_sender.py \
  --input examples/uart_context.json \
  --output /tmp/uart.json \
  --dry-run
```

不寫檔：

```bash
python3 frdm_uart_context_sender.py \
  --input examples/uart_context.json \
  --no-write-json \
  --dry-run
```

## 11. 常見問題

### `server health check failed: <urlopen error timed out>`

Jetson 連不到 Windows server，FRDM UART 還沒開始送。先測：

```bash
curl -v --connect-timeout 5 http://100.108.141.26:8766/health
```

Windows PowerShell 啟動 server：

```powershell
cd C:\Users\User\Desktop\windows_desktop_server_bundle
.\.venv\Scripts\Activate.ps1
python desktop_fast_chat_server.py --host 0.0.0.0 --port 8766 --ollama-model qwen35-fast:latest --no-think
```

Windows 本機測：

```powershell
curl http://127.0.0.1:8766/health
```

確認 Windows Tailscale IP：

```powershell
tailscale ip -4
```

如果 Windows 本機 OK、Jetson 還是 timeout，用系統管理員 PowerShell 開防火牆：

```powershell
New-NetFirewallRule -DisplayName "MakeNTU voice chat 8766" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8766
```

### `RMS=0.00000`

這代表目前選到的錄音裝置沒有收到聲音，不是 Windows server，也不是 FRDM UART 問題。

先列出麥克風：

```bash
python3 voice_chat_frdm_uart_bridge.py --list-mics
```

你這台 Jetson 目前有看到 USB 麥克風：

```text
[ 0] inputs=1 default_sr=48000.0 name=UACDemoV1.0: USB Audio (hw:0,0)
```

所以啟動時請加：

```bash
--device 0
```

完整例子：

```bash
python3 voice_chat_frdm_uart_bridge.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --device 0 \
  --uart-port /dev/ttyACM0 \
  --no-tts
```

### `TTS health check failed: Connection refused`

這代表 Jetson 本地 Piper TTS server 沒開，所以 reply 不會被念出來；但 Windows ASR/Ollama 和 FRDM UART 可以繼續跑。

如果你要讓 Jetson 說話，開一個新 terminal：

```bash
cd /home/asrlab-yian/MakeNTU/jetson_piper_tts
source .venv/bin/activate
python -m jetson_piper_tts.server --host 0.0.0.0 --port 8777
```

測 TTS server：

```bash
curl http://127.0.0.1:8777/health
```

然後 bridge 不要加 `--no-tts`：

```bash
cd /home/asrlab-yian/MakeNTU/frdm_uart_context_sender
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate

python3 voice_chat_frdm_uart_bridge.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --device 0 \
  --uart-port /dev/ttyACM0
```

如果你只是先測 FRDM 螢幕切換，就不用開 TTS server，bridge 加 `--no-tts` 即可。

### TTS server ready，但喇叭沒有聲音

先確認 Jetson 看得到播放裝置：

```bash
aplay -l
```

你這台目前看到 USB speaker：

```text
card 1: UACDemoV1.0, device 0: USB Audio
```

所以 TTS 要用：

```text
AUDIO_DEVICE=plughw:1,0
```

確認設定：

```bash
grep '^AUDIO_DEVICE=' /home/asrlab-yian/MakeNTU/jetson_piper_tts/.env
```

如果不是 `plughw:1,0`，改成：

```bash
sed -i 's/^AUDIO_DEVICE=.*/AUDIO_DEVICE=plughw:1,0/' /home/asrlab-yian/MakeNTU/jetson_piper_tts/.env
```

然後重開 TTS server：

```bash
cd /home/asrlab-yian/MakeNTU/jetson_piper_tts
source .venv/bin/activate
python -m jetson_piper_tts.server --host 0.0.0.0 --port 8777
```

測試直接播放：

```bash
python -m jetson_piper_tts.speak "USB喇叭测试。" --device plughw:1,0 --stream
```

如果 direct speak 有聲音，但 bridge 沒聲音，bridge 加 `--tts-debug` 看 TTS 是否有 enqueue：

```bash
python3 voice_chat_frdm_uart_bridge.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --device 0 \
  --uart-port /dev/ttyACM0 \
  --tts-debug
```

### `Missing dependency: pyserial`

```bash
python3 -m pip install -r requirements.txt
```

### `Permission denied: /dev/ttyACM0`

長期解法：

```bash
sudo usermod -aG dialout "$USER"
sudo reboot
```

臨時測試：

```bash
sudo chmod 666 /dev/ttyACM0
```

### 找不到 `/dev/ttyACM0`

```bash
dmesg | tail -50
ls /dev/ttyACM* /dev/ttyUSB* 2>/dev/null
```

如果看到 `/dev/ttyUSB0`，就用：

```bash
--port /dev/ttyUSB0
```

### FRDM 沒反應

先送最小指令：

```bash
python3 frdm_uart_context_sender.py \
  --command Normal \
  --port /dev/ttyACM0
```

這個工具預設已經是 CRLF；如果你想明確指定，可以這樣：

```bash
python3 frdm_uart_context_sender.py \
  --command Normal \
  --line-ending crlf \
  --port /dev/ttyACM0
```

同時確認：

- FRDM firmware 的 baudrate 是 `115200`
- USB 線有資料傳輸功能，不是只能充電
- Jetson 打開的是 monitor command parser 對應的那個 UART
- FRDM 端目前 command table 只有 `Sleep`、`Normal`、`ShowNum`、`MotorPitch`、`MotorYaw`
