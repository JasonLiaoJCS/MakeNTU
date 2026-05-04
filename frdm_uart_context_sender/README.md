# FRDM UART Context Sender

Demo 前想最快跑起來，先看短版：[QUICK_START.md](QUICK_START.md)。

這是一個全新的獨立小工具，不會改到原本 `voice_stt_remote`、`emotion_robot_controller` 或 `jetson_piper_tts` 的程式。

用途：

```text
目前情境 JSON / transcript / reply / emotion
-> 只根據目前 FRDM 已有的 SMONITORCOMMAND 決定 UART 指令
-> 產生 uart.json
-> 先印出 reply
-> 直接透過 USB serial 送 UART 給 FRDM MCXN947
```

## 目前支援的 FRDM 指令

這版只支援你現在 FRDM firmware 裡列出的 5 個 command，不做其他複雜判斷：

```text
Sleep
Normal
ShowNum <number>
MotorPitch <angle>
MotorYaw <angle>
```

對應你 FRDM 端目前的 command table：

```c
SMONITORCOMMAND sMonitorFuncList[] =
{
 { "Sleep",      "<var 1> <var 2>", "switch to SLEEP",        SLEEPGui },
 { "Normal",     "<var 1> <var 2>", "switch to NORMAL",       NORMALGui },
 { "ShowNum",    "<var 1> <var 2>", "Print the input numbers", ShowNumber },
 { "MotorPitch", "<var 1> <var 2>", "control motor P",        MotorControlPitch },
 { "MotorYaw",   "<var 1> <var 2>", "control motor Y",        MotorControlYaw },
 { 0, 0, 0, 0 }
};
```

送到 FRDM 的實際 UART 文字會長這樣：

```text
Sleep\r\n
Normal\r\n
ShowNum 7\r\n
MotorPitch 90\r\n
MotorYaw 90\r\n
```

這支工具預設會送 `CRLF`，也就是每行 command 後面加 `\r\n`。你這片 FRDM 的 monitor command parser 需要 CRLF 才會真的執行，例如回 `switch to SLEEP`。

如果之後 firmware 改成只吃 LF，可以用 `--line-ending lf`。

## 檔案

```text
frdm_uart_context_sender/
├── QUICK_START.md
├── README.md
├── requirements.txt
├── frdm_uart_context_sender.py
├── voice_chat_frdm_uart_bridge.py
├── wake_voice_chat_frdm_bridge.py
└── examples/
    ├── uart_context.json
    └── uart_manual_commands.json
```

## 完整語音聊天 + FRDM UART 橋接

## 重要：不使用雲端 AI

不要跑 `stt_node.py` 那套 Gemini Flash STT。那支會用 `GEMINI_API_KEY` 呼叫雲端 API。

這包的 Enter 版和 hands-free 版 bridge 都走本地端 AI：

```text
Jetson 本地錄音 / wake word
-> Windows 桌機 /voice-chat
-> Windows 本地 ASR
-> Windows 本地 Ollama qwen35-fast
-> Jetson FRDM UART + Piper TTS
```

這包不會呼叫 Gemini、OpenAI 或其他雲端 AI API。`openwakeword` 只在 Jetson 本機做喚醒詞偵測。

如果你要跑完整流程，不要直接跑舊的 `jetson_fast_voice_chat.py`。請跑這個新檔：

這包目前預設 TTS voice 是小雅：

```text
zh_CN-xiao_ya-medium
```

Enter 版和 hands-free 版 bridge 都會在沒有指定 `--tts-voice` 時自動使用小雅。

```bash
cd /home/asrlab-yian/MakeNTU/frdm_uart_context_sender
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate

python3 voice_chat_frdm_uart_bridge.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --mic-keyword UACDemo \
  --uart-port /dev/ttyACM0
```

如果你要省略 Enter，改用 wake word 自動錄音，跑：

```bash
python3 wake_voice_chat_frdm_bridge.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --mic-keyword UACDemo \
  --uart-port /dev/ttyACM0 \
  --tts-debug
```

hands-free 版流程：

```text
常駐監聽
-> 偵測 "hey_jarvis"
-> 開始錄音
-> 音量低於 VOLUME_MIN 並持續 SILENCE_DURATION 秒
-> 自動停止錄音
-> 送 Windows /voice-chat
-> FRDM UART + TTS
```

常用調整：

```bash
--wake-threshold 0.45      # 越低越容易喚醒
--volume-min 12000         # 越低越容易保留講話聲
--silence-duration 1.0     # 安靜多久後停止錄音
--max-speech-seconds 15    # 最長錄音秒數
--listen-debug             # 印出監聽狀態
```

流程：

```text
Jetson 錄音
-> Windows 桌機 ASR
-> Windows 桌機 Ollama 產生自然回覆
-> Windows 桌機本地規則判斷情緒
-> Jetson 顯示 Transcript / Reply / Emotion / Timing
-> Jetson 依目前情境產生 uart.json
-> Jetson 直接送 UART 給 FRDM
-> Jetson 播放 TTS，如果沒有加 --no-tts
```

先測 Windows server，但不送 UART：

```bash
python3 voice_chat_frdm_uart_bridge.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --check-server \
  --uart-dry-run \
  --no-tts
```

如果出現 health timeout：

```text
ERROR: server health check failed: <urlopen error timed out>
Check Windows server, then try: curl http://100.108.141.26:8766/health
```

代表 Jetson 連不到 Windows 桌機 server，還沒進到錄音、ASR、Ollama 或 FRDM UART。先在 Jetson 測：

```bash
curl -v --connect-timeout 5 http://100.108.141.26:8766/health
```

如果 timeout，回 Windows PowerShell 啟動 server：

```powershell
ollama serve
```

另外開一個 Windows PowerShell：

```powershell
cd C:\Users\User\Desktop\windows_desktop_server_bundle
.\.venv\Scripts\Activate.ps1
python desktop_fast_chat_server.py --host 0.0.0.0 --port 8766 --ollama-model qwen35-fast:latest --no-think
```

Windows 本機先測：

```powershell
curl http://127.0.0.1:11434/api/tags
curl http://127.0.0.1:8766/health
```

如果 Windows 本機 OK、Jetson 還是 timeout，確認 Windows Tailscale IP：

```powershell
tailscale ip -4
```

如果 IP 變了，更新 Jetson 指令裡的 `--server-url`。

如果只要測 FRDM，不要 TTS：

```bash
python3 voice_chat_frdm_uart_bridge.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --mic-keyword UACDemo \
  --uart-port /dev/ttyACM0 \
  --no-tts
```

## 安裝

這包 bridge 實際執行時用的是語音聊天 venv，所以依賴要裝進：

```text
/home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv
```

```bash
cd /home/asrlab-yian/MakeNTU/frdm_uart_context_sender
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate
python3 -m pip install -r requirements.txt
```

只做 `--dry-run` 不需要 `pyserial`，真的要送 UART 才需要。

hands-free 版需要 `openwakeword`。如果看到：

```text
ERROR: Missing dependency: openwakeword
```

代表你沒有把依賴裝進上面這個 venv，重新跑一次 `python3 -m pip install -r requirements.txt`。

第一次使用 wake word 時還需要 `hey_jarvis` 模型檔。新版 `wake_voice_chat_frdm_bridge.py` 會自動下載；如果要手動確認：

```bash
python3 - <<'PY'
from openwakeword.utils import download_models
download_models(["hey_jarvis"])
print("hey_jarvis model ready")
PY
```

## 找 USB UART Port

FRDM 用 USB 接 Jetson 時，常見會出現在：

```bash
ls /dev/ttyACM* /dev/ttyUSB* 2>/dev/null
```

常見結果：

```text
/dev/ttyACM0
```

如果沒有權限開 port：

```bash
sudo usermod -aG dialout "$USER"
sudo reboot
```

臨時測試也可以：

```bash
sudo chmod 666 /dev/ttyACM0
```

## 最快測試：Dry Run

不開 serial，只看會產生什麼 `uart.json`、會送什麼 UART：

```bash
cd /home/asrlab-yian/MakeNTU/frdm_uart_context_sender

python3 frdm_uart_context_sender.py \
  --text "我有點累了，先安靜一點" \
  --reply "好，我切到安靜模式，先不打擾你。" \
  --emotion tired \
  --dry-run
```

預期：

```text
Reply:
好，我切到安靜模式，先不打擾你。

uart_json: uart.json
TX: Sleep
```

`uart.json` 會包含：

```json
{
  "target": "FRDM-MCXN947",
  "transport": "usb-serial",
  "commands": [
    {
      "name": "Sleep",
      "wire": "Sleep",
      "reason": "context.mode requests sleep"
    }
  ]
}
```

## 直接送到 FRDM

```bash
python3 frdm_uart_context_sender.py \
  --text "我回來了，可以恢復正常" \
  --reply "好，我切回正常模式。" \
  --emotion neutral \
  --port /dev/ttyACM0
```

預期送出：

```text
Normal\r\n
```

如果 FRDM 有回 `PRINTF`，程式會在 `RX:` 印出來。

## 用 JSON 檔輸入

```bash
python3 frdm_uart_context_sender.py \
  --input examples/uart_context.json \
  --port /dev/ttyACM0
```

或先 dry-run：

```bash
python3 frdm_uart_context_sender.py \
  --input examples/uart_context.json \
  --dry-run
```

## 手動指定 Commands

如果你不想讓程式判斷情境，可以直接給 command。這是最穩的測試方式。

```bash
python3 frdm_uart_context_sender.py \
  --command Sleep \
  --port /dev/ttyACM0
```

```bash
python3 frdm_uart_context_sender.py \
  --command Normal \
  --command "MotorPitch 90" \
  --command "MotorYaw 90" \
  --command "ShowNum 7" \
  --port /dev/ttyACM0
```

也可以用 JSON：

```bash
python3 frdm_uart_context_sender.py \
  --input examples/uart_manual_commands.json \
  --port /dev/ttyACM0
```

## 情境判斷規則

這版規則很簡單，沒有 AI，沒有多餘 motion profile，也沒有之前那些更複雜的 emotion mapping。

### Sleep

會送：

```text
Sleep
```

條件：

```text
context.mode = "sleep"
emotion.primary = "sleepy" 或 "tired"
文字裡有：睡、睡覺、休息、安靜、離席、累、困、sleep、tired、quiet
```

### Normal

會送：

```text
Normal
```

條件：

```text
context.mode = "normal"
文字裡有：正常、回來、醒、工作、開始、聊天、hello、你好
沒有判斷到 Sleep 時，預設也會送 Normal
```

如果你不想沒有情境時自動送 `Normal`：

```bash
python3 frdm_uart_context_sender.py \
  --text "隨便聊聊" \
  --no-default-normal \
  --dry-run
```

### MotorPitch

會送：

```text
MotorPitch <0..180>
```

條件：

```json
{
  "context": {
    "pitch": 90
  }
}
```

或文字裡有：

```text
MotorPitch 90
pitch 90
抬頭 60
低頭 120
```

沒有數字但有關鍵字時：

```text
抬頭 / 往上 -> MotorPitch 60
低頭 / 往下 -> MotorPitch 120
```

### MotorYaw

會送：

```text
MotorYaw <0..180>
```

條件：

```json
{
  "context": {
    "yaw": 90
  }
}
```

或文字裡有：

```text
MotorYaw 90
yaw 90
轉頭 120
```

沒有數字但有關鍵字時：

```text
左 / 往左 -> MotorYaw 60
右 / 往右 -> MotorYaw 120
正面 / 回正 / 中間 -> MotorYaw 90
```

如果你的機構方向相反，先不用改 FRDM，直接改 Python 裡的這幾個預設值即可：

```python
LEFT_WORDS  -> yaw = 60
RIGHT_WORDS -> yaw = 120
UP_WORDS    -> pitch = 60
DOWN_WORDS  -> pitch = 120
```

### ShowNum

會送：

```text
ShowNum <0..999999>
```

條件：

```json
{
  "context": {
    "show_num": 7
  }
}
```

或文字裡有：

```text
ShowNum 7
顯示 7
显示数字 7
```

## 建議接到語音聊天流程的方式

你現在 `voice_stt_remote` 已經能拿到 Windows 回來的：

```json
{
  "transcript": "...",
  "reply": "...",
  "emotion": {
    "primary": "..."
  }
}
```

不要改原本程式也可以先用檔案串：

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

如果之後要接進 `jetson_fast_voice_chat.py`，概念就是在印出 reply 後，把同一份 response JSON 丟給這支工具，或直接 import `decide_commands()` / `send_uart()`。

## uart.json 格式

程式每次預設會在目前目錄寫出：

```text
uart.json
```

內容範例：

```json
{
  "version": 1,
  "created_at": "2026-05-04T21:00:00+08:00",
  "target": "FRDM-MCXN947",
  "transport": "usb-serial",
  "allowed_commands": ["Sleep", "Normal", "ShowNum", "MotorPitch", "MotorYaw"],
  "reply": "好，我切到安靜模式。",
  "transcript": "我有點累了，先休息一下。",
  "emotion": {
    "primary": "tired"
  },
  "commands": [
    {
      "name": "Sleep",
      "wire": "Sleep",
      "reason": "sleep/tired context"
    }
  ],
  "serial": {
    "port": "/dev/ttyACM0",
    "baudrate": 115200,
    "line_ending": "crlf",
    "dry_run": false,
    "results": [
      {
        "tx": "Sleep",
        "rx": ["switch to SLEEP"]
      }
    ]
  }
}
```

換輸出路徑：

```bash
python3 frdm_uart_context_sender.py \
  --input examples/uart_context.json \
  --output /tmp/uart.json \
  --dry-run
```

不寫 `uart.json`：

```bash
python3 frdm_uart_context_sender.py \
  --input examples/uart_context.json \
  --no-write-json \
  --dry-run
```

## Troubleshooting

### `server health check failed: <urlopen error timed out>`

這代表 Jetson 連不到 Windows 的 `desktop_fast_chat_server.py`。這時候 FRDM UART 還沒有開始送，不是 FRDM 螢幕或 UART 的問題。

Jetson 先測：

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

如果 IP 不是 `100.108.141.26`，把 Jetson 指令改成新的 IP：

```bash
python3 voice_chat_frdm_uart_bridge.py \
  --server-url http://WINDOWS_TAILSCALE_IP:8766/voice-chat \
  --uart-port /dev/ttyACM0
```

如果 Windows 本機測得到，但 Jetson 測不到，通常是 Windows 防火牆擋住。用系統管理員 PowerShell 開 port：

```powershell
New-NetFirewallRule -DisplayName "MakeNTU voice chat 8766" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8766
```

`--no-preflight` 只能跳過啟動時的 `/health` 檢查；如果網路本身不通，後面的 `/voice-chat` 一樣會失敗。

### `Ollama request failed: WinError 10061`

這代表 Jetson 已經連到 Windows 的 `desktop_fast_chat_server.py`，但是桌機 server 連不到 Windows 本機 Ollama。通常是 Ollama 沒啟動，或 `localhost:11434` 沒在服務。

Windows PowerShell 先測：

```powershell
curl http://127.0.0.1:11434/api/tags
```

如果連不上，開一個新的 PowerShell 跑：

```powershell
ollama serve
```

然後保留 Ollama 視窗，再重開桌機 server：

```powershell
cd C:\Users\User\Desktop\windows_desktop_server_bundle
.\.venv\Scripts\Activate.ps1
python desktop_fast_chat_server.py --host 0.0.0.0 --port 8766 --ollama-model qwen35-fast:latest --no-think
```

### `RMS=0.00000`

這代表目前選到的 Jetson 錄音裝置沒有收到聲音。Windows server 和 FRDM UART 都還沒有問題，因為程式在 RMS 太低時會直接 skip，不會 POST audio。

先列出麥克風：

```bash
python3 voice_chat_frdm_uart_bridge.py --list-mics
```

你這台 Jetson 的 USB 麥克風名稱通常是 `UACDemoV1.0`，但前面的 index 可能會因為重新插拔而變成 `[0]`、`[1]` 或其他數字。例如：

```text
[ 1] inputs=1 default_sr=48000.0 name=UACDemoV1.0: USB Audio (hw:1,0)
```

所以正式啟動時建議用名稱自動找麥克風：

```bash
python3 voice_chat_frdm_uart_bridge.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --mic-keyword UACDemo \
  --uart-port /dev/ttyACM0
```

如果你一定要寫死 index，請用 `--list-mics` 當下顯示的數字；例如目前顯示 `[1]` 就用 `--device 1`。如果 RMS 還是 0，檢查 USB mic 是否靜音、接觸不良、音量太低，或用下面指令看 PulseAudio 預設 source：

```bash
pactl get-default-source
pactl list short sources
```

### `Missing dependency: pyserial`

```bash
python3 -m pip install pyserial
```

或：

```bash
python3 -m pip install -r requirements.txt
```

### `Permission denied: /dev/ttyACM0`

```bash
sudo usermod -aG dialout "$USER"
sudo reboot
```

或臨時：

```bash
sudo chmod 666 /dev/ttyACM0
```

### 沒看到 `/dev/ttyACM0`

插拔 FRDM USB 後看：

```bash
dmesg | tail -50
ls /dev/ttyACM* /dev/ttyUSB* 2>/dev/null
```

可能是 `/dev/ttyUSB0`，那就改：

```bash
--port /dev/ttyUSB0
```

### FRDM 收不到

先手動送最小 command：

```bash
python3 frdm_uart_context_sender.py \
  --command Normal \
  --port /dev/ttyACM0
```

這個工具預設已經送 CRLF；如果你想明確指定：

```bash
python3 frdm_uart_context_sender.py \
  --command Normal \
  --line-ending crlf \
  --port /dev/ttyACM0
```

確認 FRDM 端的 baudrate 是 `115200`，且 USB serial 對應的是 monitor command parser 那個 UART。
