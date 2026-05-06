# FRDM UART Context Sender

這個資料夾提供三個工具：

```text
wake_voice_chat_frdm_bridge.py   # 推薦正式 demo：wake word -> Windows AI -> FRDM UART + TTS
voice_chat_frdm_uart_bridge.py   # Enter 版：手動開始/停止錄音 -> Windows AI -> FRDM UART + TTS
frdm_uart_context_sender.py      # 單獨 FRDM UART command sender
```

完整現場操作請看：

```text
QUICK_START.md
```

## 本地 AI 路線

```text
Jetson microphone / wake word
-> Windows /voice-chat
-> Windows local ASR
-> Windows local Ollama qwen35-fast:latest
-> Jetson UART command decision
-> FRDM-MCXN947 UART
-> Jetson Piper TTS
```

這套不使用 Gemini、OpenAI 或其他雲端 AI。`openwakeword` 只在 Jetson 本機做喚醒詞偵測。

## 支援的 FRDM 指令

這版只送目前 FRDM firmware 支援的 monitor commands：

```text
Sleep
Normal
ShowNum <0..999999>
MotorPitch <0..180>
MotorYaw <0..180>
```

送線格式預設是 CRLF：

```text
Sleep\r\n
Normal\r\n
ShowNum 123\r\n
MotorPitch 90\r\n
MotorYaw 90\r\n
```

## 快速執行

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

沒有 FRDM serial、只想先測語音和 TTS：

```bash
python3 wake_voice_chat_frdm_bridge.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --mic-keyword UACDemo \
  --uart-dry-run \
  --tts-debug \
  --uart-debug
```

## 啟動前檢查

```bash
python3 wake_voice_chat_frdm_bridge.py --list-mics
python3 wake_voice_chat_frdm_bridge.py --list-uarts
python3 wake_voice_chat_frdm_bridge.py --server-url http://100.108.141.26:8766/voice-chat --check-server --uart-dry-run --tts-debug
```

如果 `--list-uarts` 顯示 `(none)`，代表 Jetson 還沒看到 FRDM USB serial。請把 FRDM-MCXN947 的 J17 MCU-LINK USB-C 接到 Jetson，使用可傳資料的線，確認 J18 是 open，再檢查一次。

## 單獨 UART 工具

列出 ports：

```bash
python3 frdm_uart_context_sender.py --list-ports
```

只看 TX、不開 serial：

```bash
python3 frdm_uart_context_sender.py --command Sleep --port auto --dry-run
```

真的送出：

```bash
python3 frdm_uart_context_sender.py --command Sleep --port auto
python3 frdm_uart_context_sender.py --command Normal --port auto
python3 frdm_uart_context_sender.py --command "ShowNum 123" --port auto
python3 frdm_uart_context_sender.py --command "MotorPitch 90" --port auto
python3 frdm_uart_context_sender.py --command "MotorYaw 90" --port auto
```

## 檔案

```text
frdm_uart_context_sender/
├── QUICK_START.md
├── README.md
├── requirements.txt
├── frdm_uart_context_sender.py
├── voice_chat_frdm_uart_bridge.py
├── wake_voice_chat_frdm_bridge.py
├── uart.json
└── examples/
    ├── uart_context.json
    └── uart_manual_commands.json
```

## 主要參數

Wake bridge：

```text
--list-mics
--list-uarts
--server-url http://WINDOWS_IP:8766/voice-chat
--mic-keyword UACDemo
--uart-port auto
--uart-dry-run
--tts-debug
--uart-debug
--wake-threshold 0.5
--volume-min 14500
--silence-duration 1.2
--listen-debug
```

UART sender：

```text
--list-ports
--command Sleep
--port auto
--baudrate 115200
--line-ending crlf
--dry-run
--read-ms 250
```

## 故障排查

大多數 demo 失敗會落在這幾類：

```text
--list-uarts 顯示 (none)      -> FRDM USB serial 還沒在 Jetson 枚舉出來。
WinError 10061               -> Windows server 活著，但 Windows Ollama 沒在 localhost:11434 服務。
server health timeout        -> Jetson 連不到 Windows /health；檢查 Tailscale IP 和防火牆。
TTS ready 但沒聲音           -> 檢查 AUDIO_DEVICE=plughw:CARD=UACDemoV10_1,DEV=0，然後重開 TTS server。
RMS 太低 / transcript 很怪   -> 檢查 --list-mics，正式跑用 --mic-keyword UACDemo。
```

完整指令和一步一步排查請看 `QUICK_START.md`。
