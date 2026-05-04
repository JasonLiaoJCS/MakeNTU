# Remote Voice Control Setup

這個版本的架構是：

```text
Jetson Orin Nano 麥克風錄音
  -> HTTP 上傳 WAV 到 Windows 桌機
  -> Windows 桌機 Qwen3-ASR-1.7B 語音轉文字
  -> Windows 桌機 Ollama qwen3.5:27b 解析成 JSON
  -> JSON 回傳 Jetson
  -> Jetson print / serial / HTTP backend 執行或顯示回覆
```

重點：Jetson 不跑 Qwen3-ASR，也不需要安裝 torch。

## 1. Windows 桌機設定

在 Windows PowerShell：

```powershell
cd C:\你的專案路徑\emotion_robot_controller
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements_desktop_voice_server.txt
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
ollama pull qwen35-fast:latest
```

確認 Ollama 正在跑：

```powershell
ollama serve
```

另開一個 PowerShell 啟動桌機 server：

```powershell
cd C:\你的專案路徑\emotion_robot_controller
.venv\Scripts\Activate.ps1
python desktop_voice_server.py --host 0.0.0.0 --port 8765 --ollama-model qwen35-fast:latest --no-think
```

如果 Windows 防火牆跳出詢問，請允許 Python 在私人網路通訊。

查桌機 IP：

```powershell
ipconfig
```

記下 IPv4，例如：

```text
192.168.1.50
```

## 2. Jetson 設定

在 Jetson：

```bash
cd /home/asrlab-yian/MakeNTU/emotion_robot_controller/voice_stt_remote
source .venv/bin/activate
pip install -r requirements_jetson_voice_client.txt
```

測桌機 server 有沒有連通：

```bash
curl http://DESKTOP_IP:8765/health
```

例如：

```bash
curl http://192.168.1.50:8765/health
```

## 3. 先測文字，不錄音

這一步會讓 Jetson 把文字送給桌機，桌機用 Ollama 解析，再回傳 JSON。

```bash
python jetson_remote_voice_client.py \
  --server-url http://192.168.1.50:8765/voice-command \
  --text "幫我開電風扇" \
  --backend print
```

## 4. 列出 Jetson 麥克風

```bash
python jetson_remote_voice_client.py --list-mics
```

找到你的 USB 麥克風 device index。

## 5. Jetson 錄音送桌機辨識

```bash
python jetson_remote_voice_client.py \
  --server-url http://192.168.1.50:8765/voice-command \
  --device 2 \
  --seconds 4 \
  --backend print
```

按 Enter 後，Jetson 會錄 4 秒，存成暫存 WAV，上傳給桌機。

## 6. Jetson 用 serial backend 控制硬體

```bash
python jetson_remote_voice_client.py \
  --server-url http://192.168.1.50:8765/voice-command \
  --device 2 \
  --seconds 4 \
  --backend serial \
  --serial-port /dev/ttyTHS1 \
  --serial-baudrate 115200
```

目前 serial backend 送出一行：

```text
HOME.FAN.ON\n
```

## 7. Jetson 用 HTTP backend 控制本地服務

```bash
python jetson_remote_voice_client.py \
  --server-url http://192.168.1.50:8765/voice-command \
  --device 2 \
  --backend http \
  --http-url http://127.0.0.1:5000/command
```

payload 會包含：

```json
{
  "wire_command": "HOME.FAN.ON",
  "command": {
    "intent": "HOME.FAN.ON",
    "target": "fan",
    "action": "on",
    "confidence": 0.95,
    "transcript": "幫我開電風扇",
    "reply": "好的，幫你開風扇。"
  }
}
```

## 8. 安全規則

Jetson 收到桌機 JSON 後還會再檢查：

- `intent == UNKNOWN` 不執行
- `confidence < 0.58` 不執行
- server 回傳 `should_execute = false` 不執行

所以「不要開風扇」這類否定語氣，不會直接送硬體控制。

## 9. 常見問題

桌機連不到：

- Windows server 是否有跑 `desktop_voice_server.py`
- Jetson `curl http://DESKTOP_IP:8765/health` 是否成功
- Windows 防火牆是否允許 Python
- Jetson 和桌機是否在同一個 LAN / Tailscale / VPN

Jetson 沒有麥克風：

```bash
python jetson_remote_voice_client.py --list-mics
```

如果沒有 input device，檢查 USB 麥克風或 PulseAudio / ALSA。

桌機 ASR 載入失敗：

- 確認 Windows venv 有安裝 `qwen-asr`
- 確認 `torch.cuda.is_available()` 是 True
- RTX 4090 建議使用 CUDA 版 PyTorch；若 `torch.cuda.is_available()` 是 `False`，先確認 `nvidia-smi` 正常，再重裝 CUDA 版 torch。

Ollama 失敗：

```powershell
ollama list
ollama pull qwen35-fast:latest
ollama serve
```

測試：

```powershell
curl http://localhost:11434/api/chat
```
