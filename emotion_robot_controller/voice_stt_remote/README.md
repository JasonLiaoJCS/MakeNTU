# Voice STT Remote

Demo 前想最快啟動，先看：

```text
QUICK_START.md
```

它包含 Windows Ollama、Windows ASR/Ollama server、Jetson TTS server、Jetson voice client 的完整啟動順序。

這個資料夾是目前語音聊天主線：

```text
Jetson 錄音
-> Windows 桌機 ASR
-> Windows 桌機 Ollama 產生自然回覆
-> Windows 桌機本地規則判斷情緒
-> Jetson 收到 Transcript / Reply / Emotion / Timing
-> Jetson 顯示結果，並把 Reply 送給本機 jetson_piper_tts
-> Jetson 喇叭播放自然回覆
```

這版 `jetson_fast_voice_chat.py` 的主線是「錄音聊天 + 情緒分析 + 本機 TTS 播放」。  
如果你另外有 FRDM / UART JSON 發送流程，可以和這條 reply-to-TTS 流程並行；TTS 只吃 `reply` 文字，不會要求 Windows 或 FRDM 改格式。

目前確認可用版本：

```text
desktop_fast_chat_server.py debug_version: 6
Ollama request: think=false
Reply: Ollama 自然聊天文字
Emotion: server 本地規則
TTS: jetson_piper_tts /speak_async
```

## 架構

```text
Jetson Orin Nano
  - sounddevice 錄音
  - Enter 開始錄音，再按 Enter 停止
  - 轉成 16 kHz WAV
  - POST /voice-chat
  - 收到 reply 後 POST http://127.0.0.1:8777/speak_async
  - 本機 Piper TTS 立刻播放 reply

Windows 桌機
  - Flask server: desktop_fast_chat_server.py
  - Qwen3-ASR: 語音轉文字
  - Ollama: qwen35-fast:latest 自然回覆
  - local rules: emotion JSON

Jetson 本機 TTS
  - FastAPI server: jetson_piper_tts
  - Piper Chinese voice: zh_CN-chaowen-medium / zh_CN-huayan-medium 等
  - 預設 raw streaming playback，減少寫 WAV 檔等待
  - /speak_async 非同步排隊播放

Jetson 顯示
  - Transcript
  - Reply
  - Emotion
  - Timing
  - Debug
```

重要分工：

```text
Windows 桌機跑 server。
Jetson 跑 client。
Windows PowerShell 指令不要在 Jetson bash 裡跑。
Jetson bash 指令不要在 Windows PowerShell 裡跑。
```

## 重要檔案

### 主線快版

```text
desktop_fast_chat_server.py      Windows 桌機 server
jetson_fast_voice_chat.py        Jetson 錄音 client
desk_voice_controller.py         ASR / 共用工具
jetson_remote_voice_client.py    錄音、HTTP、WAV 共用工具
```

### Jetson 本機 TTS

```text
/home/asrlab-yian/MakeNTU/jetson_piper_tts/
```

這是 Reply 文字轉語音的本機服務。`jetson_fast_voice_chat.py` 預設會把 Windows 回傳的 `reply` 丟到：

```text
http://127.0.0.1:8777/speak_async
```

如果 TTS server 沒開，語音聊天不會整個壞掉；client 會印 warning，然後照樣顯示 Transcript / Reply / Emotion。

### Windows bundle

```text
windows_desktop_server_bundle/
windows_desktop_server_bundle.zip
```

這份資料夾是給 Windows 桌機用的 bundle。Jetson 端改完 `desktop_fast_chat_server.py` 後，要複製到 Windows 並重啟 server 才會生效。

### 舊版語音控制

這組目前不是主線，保留給之後控制硬體用：

```text
desktop_voice_server.py
jetson_remote_voice_client.py
REMOTE_VOICE_SETUP.md
```

## Windows 桌機第一次設定

以下全部在 **Windows PowerShell** 執行。

進入 bundle：

```powershell
cd C:\Users\User\Desktop\windows_desktop_server_bundle
```

建立虛擬環境：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements_desktop_voice_server.txt
```

如果 `py -3.12` 不存在，用：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements_desktop_voice_server.txt
```

如果 PowerShell 不讓你執行 `Activate.ps1`，開一次權限：

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

如果啟動 server 時出現：

```text
ModuleNotFoundError: No module named 'flask'
```

代表你沒有啟動 Windows 的 `.venv`，或 requirements 沒裝。重新跑：

```powershell
cd C:\Users\User\Desktop\windows_desktop_server_bundle
.\.venv\Scripts\Activate.ps1
pip install -r requirements_desktop_voice_server.txt
```

安裝 PyTorch CUDA：

```powershell
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
```

確認 GPU：

```powershell
python -c "import torch; print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'no cuda')"
```

正常應該看到：

```text
True
NVIDIA GeForce RTX 4090
```

## Windows 桌機 Ollama

以下也在 **Windows PowerShell** 執行。

確認 Ollama：

```powershell
ollama list
```

確認模型存在：

```text
qwen35-fast:latest
```

如果沒有：

```powershell
ollama pull qwen35-fast:latest
```

如果你手動跑：

```powershell
ollama serve
```

看到：

```text
bind: Only one usage of each socket address...
```

通常代表 Ollama 已經在背景執行，不是錯誤。

## 更新 Windows 桌機檔案

如果 Jetson 這邊改了 `desktop_fast_chat_server.py`，要把新版複製到 Windows。

推薦在 **Windows PowerShell** 從 Jetson 抓：

```powershell
cd C:\Users\User\Desktop\windows_desktop_server_bundle
scp asrlab-yian@100.110.90.72:/home/asrlab-yian/MakeNTU/emotion_robot_controller/voice_stt_remote/desktop_fast_chat_server.py .
```

如果 Jetson IP 不同，先在 Jetson 查：

```bash
hostname -I
```

也可以整包抓 zip：

```powershell
scp asrlab-yian@100.110.90.72:/home/asrlab-yian/MakeNTU/emotion_robot_controller/voice_stt_remote/windows_desktop_server_bundle.zip .
```

## 啟動 Windows Server

以下在 **Windows PowerShell** 執行。

```powershell
cd C:\Users\User\Desktop\windows_desktop_server_bundle
.\.venv\Scripts\Activate.ps1
python desktop_fast_chat_server.py --host 0.0.0.0 --port 8766 --ollama-model qwen35-fast:latest --no-think
```

PowerShell 前面應該要看到：

```text
(.venv) PS C:\Users\User\Desktop\windows_desktop_server_bundle>
```

server 啟動時會做 warm-up，然後載入 ASR。成功後會看到類似：

```text
Debug log: fast_chat_debug.jsonl
Ollama warm-up: model=qwen35-fast:latest, no_think=True
Ollama warm-up done: neutral
ASR device=cuda:0, dtype=bfloat16
Fast chat server listening on http://0.0.0.0:8766
```

注意：這段不能在 Jetson bash 執行。Jetson 沒有 `C:\Users\User\...`，也不能跑 `Activate.ps1`。

## Jetson 第一次設定

以下全部在 **Jetson bash** 執行。

```bash
cd /home/asrlab-yian/MakeNTU/emotion_robot_controller/voice_stt_remote
source ../.venv/bin/activate
pip install -r requirements_jetson_voice_client.txt
```

列出音訊輸入：

```bash
python jetson_fast_voice_chat.py --list-mics
```

目前這台 Jetson 曾經看到的預設輸入是：

```text
[26] inputs=32 default_sr=44100.0 name=default  <-- default
```

所以通常不需要指定 `--device`。

不要用：

```bash
--device 0
```

因為 device 0 可能是 HDMI output：

```text
NVIDIA Jetson Orin Nano HDA: HDMI 0
```

如果選到 output-only 裝置，會看到：

```text
Not an input device
```

解法是省略 `--device`，或用 `--list-mics` 選 `inputs > 0` 的 index。

## Jetson TTS Server 啟動

以下在 **Jetson bash** 開另一個 terminal 執行。這個 terminal 要保持開著，`jetson_fast_voice_chat.py` 才能把 reply 播出來。

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

瀏覽器打開 `http://127.0.0.1:8777/` 看到 `404 Not Found` 是正常的，因為 TTS server 沒有首頁。請用這些 endpoint：

```bash
curl http://127.0.0.1:8777/health
curl http://127.0.0.1:8777/voices
curl http://127.0.0.1:8777/queue
```

測試直接播放一句：

```bash
curl -X POST http://127.0.0.1:8777/speak_async \
  -H "Content-Type: application/json" \
  -d '{"text":"系统测试声音。","interrupt":true}'
```

如果要測長文字，不要把超長內容直接塞在一行 shell 裡，容易因為引號、空白、`[`、`]` 被 bash 拆壞。建議用 JSON 檔：

```bash
cat > /tmp/tts_test.json <<'JSON'
{
  "text": "这是一段比较长的测试文字。请确认语音可以正常播放。",
  "interrupt": true
}
JSON

curl -X POST http://127.0.0.1:8777/speak_async \
  -H "Content-Type: application/json" \
  --data-binary @/tmp/tts_test.json
```

如果要調音量：

```bash
alsamixer
```

或用 PulseAudio/PipeWire 預設輸出時：

```bash
pactl set-sink-volume @DEFAULT_SINK@ 80%
```

如果沒有聲音，先測 ALSA：

```bash
speaker-test -t wav -c 2
aplay /usr/share/sounds/alsa/Front_Center.wav
```

更多 Piper / voice / systemd / 快取 / raw streaming 細節看：

```text
/home/asrlab-yian/MakeNTU/jetson_piper_tts/README.md
```

## Jetson 檢查 Server

錄音前先檢查 Windows server、Ollama、ASR、debug 版本，以及 Jetson TTS `/health`：

```bash
cd /home/asrlab-yian/MakeNTU/emotion_robot_controller/voice_stt_remote
source ../.venv/bin/activate

python jetson_fast_voice_chat.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --check-server
```

成功狀態應該包含：

```text
debug_version: 6
chat_ready   : True
asr_loaded   : True
ollama_model : qwen35-fast:latest
think: False
parse_status: plain_reply
ollama_content_chars: 大於 0
```

如果 TTS server 也有開，還會看到：

```text
TTS health:
  service : jetson_piper_tts
  ready   : True
  url     : http://127.0.0.1:8777/speak_async
```

`--check-server` 只做 TTS health check，不會自動播放聲音。如果要確認喇叭真的有出聲，另外跑：

```bash
curl http://127.0.0.1:8777/health
curl -X POST http://127.0.0.1:8777/speak_async \
  -H "Content-Type: application/json" \
  -d '{"text":"TTS 也正常。","interrupt":true}'
```

如果 `debug_version` 不是 `6`，代表 Windows 還在跑舊版 server。重新複製 `desktop_fast_chat_server.py` 到 Windows，然後 Ctrl-C 停掉舊 server 再重啟。

## Jetson 文字測試

不錄音，直接測 Windows Ollama 回覆，並把 reply 交給 Jetson TTS 播放：

```bash
python jetson_fast_voice_chat.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --text "自然回我一句話"
```

這會 POST 到 Windows 的 `/text-chat`，不跑 ASR。

如果文字測試都失敗，問題在 Windows server 或 Ollama。  
如果文字測試成功但錄音模式失敗，問題多半在 ASR、錄音、麥克風裝置或上傳 WAV。
如果畫面有 Reply 但喇叭沒聲音，先看 Jetson TTS server terminal，再加 `--tts-debug`。

只測文字、不播放 TTS：

```bash
python jetson_fast_voice_chat.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --text "自然回我一句話" \
  --no-tts
```

## Jetson 語音聊天

一般使用。這會錄音、送 Windows、拿 reply，然後立刻丟給本機 TTS 播放：

```bash
python jetson_fast_voice_chat.py \
  --server-url http://100.108.141.26:8766/voice-chat
```

想看 Windows debug 與 TTS enqueue/playback debug：

```bash
python jetson_fast_voice_chat.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --debug \
  --tts-debug
```

如果只想顯示 reply，不播放聲音：

```bash
python jetson_fast_voice_chat.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --no-tts
```

如果 demo 時你希望 TTS server 沒開就直接失敗，不要靜默跳過：

```bash
python jetson_fast_voice_chat.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --require-tts
```

操作方式：

```text
Press Enter to record>   按 Enter 開始錄音
Recording...             開始說話
Press Enter again        再按 Enter 停止
等待 Windows 回覆
收到 Reply 後自動播放
輸入 q 離開
Ctrl-C 也會安靜退出
```

輸出會長這樣：

```text
Transcript:
...

Reply:
...

Emotion:
  primary        : ...
  intensity      : ...
  valence        : ...
  arousal        : ...
  support_needed : ...
  summary        : ...

Timing:
  asr_ms   : ...
  llm_ms   : ...
  total_ms : ...
```

有加 `--tts-debug` 時會多一段：

```text
TTS:
  url          : http://127.0.0.1:8777/speak_async
  post_ms      : ...
  queued       : True
  job_id       : ...
```

預設 TTS 使用 `/speak_async`，所以 Jetson client 不會等整句播放完才印結果。TTS server 會自己排隊播放。預設 `interrupt=true`，新的 reply 會打斷上一句，適合即時對話。

如果你想讓每句慢慢排隊播完，不要打斷：

```bash
python jetson_fast_voice_chat.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --tts-no-interrupt
```

如果你真的需要等 TTS 播完才回到下一輪錄音：

```bash
python jetson_fast_voice_chat.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --tts-blocking
```

通常不建議 demo 用 `--tts-blocking`，因為它會讓每輪等待時間變長。

## TTS 參數

預設 TTS endpoint：

```text
http://127.0.0.1:8777/speak_async
```

如果你的 TTS server 不在本機或 port 不同：

```bash
python jetson_fast_voice_chat.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --tts-url http://127.0.0.1:8777/speak_async
```

### 換 TTS 聲音

目前 Jetson TTS 已下載的 voice 通常有：

```text
zh_CN-chaowen-medium
zh_CN-huayan-medium
zh_CN-huayan-x_low
zh_CN-xiao_ya-medium
```

可用 voice 看：

```bash
curl http://127.0.0.1:8777/voices | python -m json.tool
```

臨時換聲音，最推薦用 `--tts-voice`。這不會改 `.env`，只影響這次 voice chat：

```bash
python jetson_fast_voice_chat.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --tts-voice zh_CN-huayan-medium
```

例如改成小雅：

```bash
python jetson_fast_voice_chat.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --tts-voice zh_CN-xiao_ya-medium
```

例如用比較輕的低延遲版本：

```bash
python jetson_fast_voice_chat.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --tts-voice zh_CN-huayan-x_low
```

如果你想永久改預設聲音，改 Jetson TTS 專案的 `.env`：

```bash
nano /home/asrlab-yian/MakeNTU/jetson_piper_tts/.env
```

改這兩行：

```env
PIPER_MODEL=./models/zh_CN-huayan-medium.onnx
PIPER_CONFIG=./models/zh_CN-huayan-medium.onnx.json
```

改完重啟 TTS server：

```bash
cd /home/asrlab-yian/MakeNTU/jetson_piper_tts
source .venv/bin/activate
python -m jetson_piper_tts.server --host 0.0.0.0 --port 8777
```

只想測某個 voice，不跑完整語音聊天：

```bash
curl -X POST http://127.0.0.1:8777/speak_async \
  -H "Content-Type: application/json" \
  -d '{"text":"这是换声音测试。","voice":"zh_CN-huayan-medium","interrupt":true}'
```

如果出現 `Piper model does not exist`，代表那個 voice 沒下載。到 TTS 專案下載：

```bash
cd /home/asrlab-yian/MakeNTU/jetson_piper_tts
source .venv/bin/activate
./scripts/download_voice.sh zh_CN-huayan-medium
```

完整 voice 管理說明在：

```text
/home/asrlab-yian/MakeNTU/jetson_piper_tts/README.md
```

調整語速。`length_scale` 越小越快，建議先試 `0.85` 到 `0.95`：

```bash
python jetson_fast_voice_chat.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --tts-length-scale 0.9
```

如果你要強制走舊式 WAV 檔播放，而不是 raw streaming：

```bash
python jetson_fast_voice_chat.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --tts-file-playback
```

一般 demo 建議不要加 `--tts-file-playback`，raw streaming 通常比較快。

## URL 注意事項

正確 URL：

```text
http://100.108.141.26:8766/voice-chat
```

錯誤例子：

```text
http://100.108.141.26:8766/voice-chat~
```

新版 client 會自動修掉最後多打的 `~`，但還是建議手動確認 URL。

如果 POST 到錯路徑，會看到：

```text
HTTP 404 NOT FOUND
```

## Debug Endpoint

Windows server 提供：

```text
GET  /health
GET  /debug
POST /text-chat
POST /voice-chat
```

Jetson 查 health：

```bash
curl http://100.108.141.26:8766/health
```

Jetson 查上一筆 debug：

```bash
curl http://100.108.141.26:8766/debug
```

Windows server 也會寫：

```text
fast_chat_debug.jsonl
```

這個檔案在 Windows bundle 資料夾裡，每行是一筆 JSON debug record。

## Ollama `think=false` 問題

這是目前最重要的修正。

之前 `qwen35-fast:latest` 會回：

```text
message.thinking: 有內容
message.content : 空字串
```

也就是模型有在思考，但沒有吐正式回答。新版 server 會在 Ollama payload 加：

```json
"think": false
```

所以正常狀態會是：

```text
think: False
ollama_message_keys: ['role', 'content']
ollama_content_chars: 大於 0
parse_status: plain_reply
```

如果又看到：

```text
ollama_message_keys: ['role', 'content', 'thinking']
ollama_message_thinking_chars: 很大
ollama_content_chars: 0
```

代表 Windows 還不是最新版，或 `qwen35-fast:latest` 的 Modelfile/template 又出問題。

## 常見錯誤

### 在 Jetson 跑 Windows 指令

錯誤：

```bash
cd C:\Users\User\Desktop\windows_desktop_server_bundle
.\.venv\Scripts\Activate.ps1
```

Jetson 會顯示：

```text
bash: cd: C:UsersUserDesktopwindows_desktop_server_bundle: No such file or directory
bash: ..venvScriptsActivate.ps1: command not found
```

原因：這是 Windows PowerShell 指令，不是 Jetson bash 指令。

正確做法：

```text
Windows PowerShell 跑 desktop_fast_chat_server.py
Jetson bash 跑 jetson_fast_voice_chat.py
```

### `ModuleNotFoundError: No module named 'flask'`

原因：Windows server 沒在 `.venv` 裡跑，或 requirements 沒裝。

修法在 Windows PowerShell：

```powershell
cd C:\Users\User\Desktop\windows_desktop_server_bundle
.\.venv\Scripts\Activate.ps1
pip install -r requirements_desktop_voice_server.txt
```

### `Not an input device`

原因：選到 HDMI output，不是 microphone input。

修法：

```bash
python jetson_fast_voice_chat.py --list-mics
```

選 `inputs > 0` 的 device，或直接省略 `--device`。

### `HTTP 404 NOT FOUND`

常見原因：

```text
/voice-chat 打成 /voice-chat~
/voice-chat 打成 /text-chat
server 沒有跑最新版
```

先跑：

```bash
python jetson_fast_voice_chat.py --server-url http://100.108.141.26:8766/voice-chat --check-server
```

### `debug_version` 不是 6

原因：Windows 還在跑舊檔案。

修法：

1. Windows PowerShell 複製新版 `desktop_fast_chat_server.py`
2. Ctrl-C 停掉舊 server
3. 重新啟動 server
4. Jetson 跑 `--check-server`

### `ollama_content_chars: 0`

如果 `think: False` 仍然 content 是 0：

1. Windows 測 Ollama：

```powershell
ollama run qwen35-fast:latest "自然回我一句話"
```

2. 如果 CLI 也空，換模型或重建 `qwen35-fast:latest`。

3. 如果 CLI 正常但 API 空，看 `fast_chat_debug.jsonl` 和 `/debug`。

### 文字測試成功，語音失敗

問題多半在 ASR 或 audio upload。看：

```text
asr_ms
RMS
transcript
```

如果 RMS 太低：

```text
SKIP: audio RMS too low
```

代表沒錄到聲音或麥克風太小聲。

### Reply 有出現，但沒有播放聲音

先確認 TTS server 有開：

```bash
curl http://127.0.0.1:8777/health
```

如果連不上，開另一個 Jetson terminal：

```bash
cd /home/asrlab-yian/MakeNTU/jetson_piper_tts
source .venv/bin/activate
python -m jetson_piper_tts.server --host 0.0.0.0 --port 8777
```

再用 client 看 TTS debug：

```bash
python jetson_fast_voice_chat.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --tts-debug
```

如果看到：

```text
WARNING: TTS speak failed
```

代表 Jetson client 已經收到 reply，但 POST 給 `jetson_piper_tts` 失敗。看 TTS server terminal 的錯誤。

### `WARNING: TTS health check failed`

代表 voice chat client 啟動時找不到 `http://127.0.0.1:8777/health`。

如果你只是先測 Windows ASR/Ollama，可以忽略；client 會繼續跑，只是不播放聲音。  
如果你要 demo，請先啟動 TTS server，或加 `--require-tts` 讓它檢查失敗就停下來。

### TTS server `GET / HTTP/1.1" 404 Not Found`

正常。TTS server 沒有首頁。

請打：

```bash
curl http://127.0.0.1:8777/health
curl http://127.0.0.1:8777/voices
```

### TTS 播放很慢

優先確認你是開 server，不是每次用 CLI：

```bash
python -m jetson_piper_tts.server --host 0.0.0.0 --port 8777
```

不要每輪 reply 都跑：

```bash
python -m jetson_piper_tts.speak "..."
```

server 會常駐、warm-up、使用 queue，延遲比較穩。

再確認 voice chat client 沒有加 `--tts-blocking` 或 `--tts-file-playback`。最快 demo 通常是預設：

```text
/speak_async + raw streaming + interrupt=true
```

可以把語速略加快：

```bash
--tts-length-scale 0.9
```

### TTS 句子太長，開始播放慢

Windows Ollama 回覆如果太長，Piper 要合成比較久。建議在 Windows prompt 裡要求回覆短一點，例如「1 到 2 句，不要超過 60 個中文字」。  
Jetson TTS server 已經會做分句與 queue，但第一句本身太長還是會慢。

### TTS 音量太小

Jetson 上調：

```bash
alsamixer
```

或：

```bash
pactl set-sink-volume @DEFAULT_SINK@ 90%
```

若用指定 ALSA 裝置，請到 `jetson_piper_tts/.env` 調整：

```text
AUDIO_DEVICE=default
```

常見可試：

```text
AUDIO_DEVICE=default
AUDIO_DEVICE=plughw:0,0
AUDIO_DEVICE=plughw:1,0
```

## 最短成功流程

這是完整 demo 的三個 terminal。

### Terminal 1：Windows PowerShell

```powershell
cd C:\Users\User\Desktop\windows_desktop_server_bundle
.\.venv\Scripts\Activate.ps1
python desktop_fast_chat_server.py --host 0.0.0.0 --port 8766 --ollama-model qwen35-fast:latest --no-think
```

### Terminal 2：Jetson TTS Server

```bash
cd /home/asrlab-yian/MakeNTU/jetson_piper_tts
source .venv/bin/activate
python -m jetson_piper_tts.server --host 0.0.0.0 --port 8777
```

### Terminal 3：Jetson Voice Client

```bash
cd /home/asrlab-yian/MakeNTU/emotion_robot_controller/voice_stt_remote
source ../.venv/bin/activate
python jetson_fast_voice_chat.py --server-url http://100.108.141.26:8766/voice-chat --check-server
python jetson_fast_voice_chat.py --server-url http://100.108.141.26:8766/voice-chat --tts-debug
```

成功後流程會是：

```text
按 Enter 錄音
Windows 回傳 transcript / reply / emotion
Jetson 印出結果
Jetson 同時把 reply 丟到 127.0.0.1:8777/speak_async
喇叭播放 reply
```

## 目前設計決策

- Ollama 只產生自然 reply，不輸出 JSON。
- Emotion 由本地規則產生，確保格式穩定。
- `think=false` 強制關閉 thinking，避免只有 `message.thinking` 沒有 `message.content`。
- 如果 `/api/chat` 空回覆，server 會嘗試 `/api/generate`。
- Jetson 預設先跑 `/health` preflight，避免錄完才發現 server 壞。
- Debug 用 `request_id` 串起 Jetson output、Windows terminal、`fast_chat_debug.jsonl`。
- Jetson TTS 預設用 `/speak_async`，避免播放語音時卡住下一輪操作。
- TTS server 不在線時，voice chat 仍繼續顯示文字；demo 要嚴格檢查時加 `--require-tts`。
- 預設 `interrupt=true`，新 reply 會打斷舊 reply，讓互動比較像即時對話。
