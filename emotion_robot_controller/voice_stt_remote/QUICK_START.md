# Quick Start: Windows Ollama + Jetson Voice Chat + Jetson TTS

這份是 demo 前最快啟動流程。照順序開 terminal，不要混用 Windows PowerShell 和 Jetson bash。

完整流程：

```text
Jetson 錄音
-> Windows ASR
-> Windows Ollama 產生 reply
-> Windows 本地規則判斷 emotion
-> Jetson 收到 Transcript / Reply / Emotion / Timing
-> Jetson 把 Reply 丟給 jetson_piper_tts
-> Jetson 喇叭播放 Reply
```

## 0. 先確認分工

Windows PowerShell 跑：

```text
ollama
desktop_fast_chat_server.py
```

Jetson bash 跑：

```text
jetson_piper_tts.server
jetson_fast_voice_chat.py
```

不要在 Jetson bash 跑：

```powershell
cd C:\Users\User\Desktop\windows_desktop_server_bundle
.\.venv\Scripts\Activate.ps1
```

不要在 Windows PowerShell 跑：

```bash
source ../.venv/bin/activate
```

## 1. Windows Terminal A：啟動 / 確認 Ollama

以下在 **Windows PowerShell** 執行。

先確認 Ollama 能用：

```powershell
ollama list
```

確認有這個模型：

```text
qwen35-fast:latest
```

如果沒有，下載：

```powershell
ollama pull qwen35-fast:latest
ollama pull qwen35-fast:latest
```

測試模型會不會正常回字：

```powershell
ollama run qwen35-fast:latest "自然回我一句話"
```

如果你想手動啟動 Ollama server：

```powershell
ollama serve
```

如果看到：

```text
Only one usage of each socket address...
```

通常代表 Ollama 已經在背景執行，不是錯誤。這個 terminal 可以關掉，或保持背景服務原本的狀態。

## 2. Windows Terminal B：啟動桌機 ASR + Ollama Server

以下在 **Windows PowerShell** 執行。

```powershell
cd C:\Users\User\Desktop\windows_desktop_server_bundle
.\.venv\Scripts\Activate.ps1
python desktop_fast_chat_server.py --host 0.0.0.0 --port 8766 --ollama-model qwen35-fast:latest --vision-model qwen35-fast:latest --no-think
```

成功時會看到類似：

```text
Debug log: fast_chat_debug.jsonl
Ollama warm-up: model=qwen35-fast:latest, no_think=True
Ollama warm-up done: neutral
ASR device=cuda:0, dtype=bfloat16
Fast chat server listening on http://0.0.0.0:8766
```

這個 terminal 要保持開著。

如果出現：

```text
ModuleNotFoundError: No module named 'flask'
```

代表 Windows `.venv` 沒啟動或套件沒裝。修：

```powershell
cd C:\Users\User\Desktop\windows_desktop_server_bundle
.\.venv\Scripts\Activate.ps1
pip install -r requirements_desktop_voice_server.txt
```

## 3. Jetson Terminal A：啟動本機 TTS Server

以下在 **Jetson bash** 執行。

```bash
cd /home/asrlab-yian/MakeNTU/jetson_piper_tts
source .venv/bin/activate
python -m jetson_piper_tts.server --host 0.0.0.0 --port 8777
```

成功時會看到：

```text
Application startup complete.
Uvicorn running on http://0.0.0.0:8777
```

這個 terminal 要保持開著。

另開一個 Jetson terminal 可以測試 TTS：

```bash
curl http://127.0.0.1:8777/health | python -m json.tool
curl -X POST http://127.0.0.1:8777/speak_async \
  -H "Content-Type: application/json" \
  -d '{"text":"Jetson TTS 已经启动。","interrupt":true}'
```

如果瀏覽器打開 `http://127.0.0.1:8777/` 看到 404，是正常的。請用 `/health`、`/voices`、`/queue`。

## 4. Jetson Terminal B：檢查整條鏈路

以下在 **Jetson bash** 執行。

```bash
cd /home/asrlab-yian/MakeNTU/emotion_robot_controller/voice_stt_remote
source ../.venv/bin/activate
python jetson_fast_voice_chat.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --check-server
```

成功時應該看到 Windows server：

```text
debug_version: 7
chat_ready   : True
asr_loaded   : True
ollama_model : qwen35-fast:latest
parse_status : plain_reply
```

也應該看到 Jetson TTS：

```text
TTS health:
  service : jetson_piper_tts
  ready   : True
  url     : http://127.0.0.1:8777/speak_async
```

如果 TTS 沒開，會看到：

```text
WARNING: TTS health check failed
```

先回到「Jetson Terminal A」啟動 `jetson_piper_tts.server`。

## 5. Jetson Terminal B：開始語音聊天

```bash
cd /home/asrlab-yian/MakeNTU/emotion_robot_controller/voice_stt_remote
source ../.venv/bin/activate
python jetson_fast_voice_chat.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --tts-debug
```

操作：

```text
Press Enter to record>   按 Enter 開始錄音
Recording...             開始講話
再按 Enter               停止錄音
等待 Windows 回覆
Jetson 顯示 Transcript / Reply / Emotion / Timing
Jetson 喇叭播放 Reply
輸入 q 離開
Ctrl-C 也可以離開
```

成功時你會看到：

```text
Transcript:
...

Reply:
...

Emotion:
...

TTS:
  url          : http://127.0.0.1:8777/speak_async
  queued       : True
```

## 常用啟動變體

### 不播放 TTS，只看文字

```bash
python jetson_fast_voice_chat.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --no-tts
```

### TTS 沒開就直接退出

Demo 前推薦用這個，避免以為會出聲但其實 TTS server 沒開：

```bash
python jetson_fast_voice_chat.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --require-tts
```

### 臨時換聲音

```bash
python jetson_fast_voice_chat.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --tts-voice zh_CN-huayan-medium
```

可用 voice：

```bash
curl http://127.0.0.1:8777/voices | python -m json.tool
```

目前常用：

```text
zh_CN-chaowen-medium
zh_CN-huayan-medium
zh_CN-huayan-x_low
zh_CN-xiao_ya-medium
```

### 語速稍微加快

```bash
python jetson_fast_voice_chat.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --tts-length-scale 0.9
```

`length_scale` 越小越快。建議先試 `0.85` 到 `0.95`。

## 快速故障排除

### Jetson 連不上 Windows server

在 Jetson 跑：

```bash
curl http://100.108.141.26:8766/health
```

如果連不上，檢查：

```text
Windows desktop_fast_chat_server.py 是否還在跑
Windows IP 是否還是 100.108.141.26
Windows 防火牆是否擋 port 8766
Tailscale 是否連線
```

### Windows server 顯示 Ollama 空回覆

在 Windows PowerShell 跑：

```powershell
ollama run qwen35-fast:latest "自然回我一句話"
```

如果 CLI 也不回字，先修 Ollama / 模型。  
如果 CLI 正常但 server 不正常，確認 server 是用最新版 `desktop_fast_chat_server.py`，並且啟動參數有 `--no-think`。

### Jetson 有 Reply 但沒聲音

在 Jetson 跑：

```bash
curl http://127.0.0.1:8777/health | python -m json.tool
curl -X POST http://127.0.0.1:8777/speak_async \
  -H "Content-Type: application/json" \
  -d '{"text":"测试声音。","interrupt":true}'
```

如果 TTS API 正常但沒聲音，檢查音量：

```bash
alsamixer
```

也可以測 ALSA：

```bash
speaker-test -t wav -c 2
aplay /usr/share/sounds/alsa/Front_Center.wav
```

### 麥克風選錯

列出麥克風：

```bash
python jetson_fast_voice_chat.py --list-mics
```

通常直接省略 `--device` 用 default 就好。不要用 output-only 的 HDMI device。

### URL 打錯

正確：

```text
http://100.108.141.26:8766/voice-chat
```

錯誤：

```text
http://100.108.141.26:8766/voice-chat~
```

新版 client 會盡量修掉尾巴 `~`，但 demo 前最好自己確認。

## Demo 前 Checklist

```text
[ ] Windows: ollama run qwen35-fast:latest 可以回字
[ ] Windows: desktop_fast_chat_server.py 正在跑
[ ] Jetson: jetson_piper_tts.server 正在跑
[ ] Jetson: curl 127.0.0.1:8777/health ready=True
[ ] Jetson: curl 100.108.141.26:8766/health chat_ready=True
[ ] Jetson: --check-server 成功
[ ] Jetson: jetson_fast_voice_chat.py 開始錄音
[ ] 喇叭有播放 Reply
```
