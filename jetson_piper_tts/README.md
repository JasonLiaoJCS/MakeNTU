# Jetson Piper TTS

`jetson_piper_tts` 是給 NVIDIA Jetson Orin Nano 使用的本地中文文字轉語音服務。它使用 Piper TTS 與中文 voice，在 Jetson 上離線把外部傳來的回覆文字轉成語音，並透過 ALSA / `aplay` 從喇叭播放。

目前預設模式是給機器人互動 demo 用的低延遲模式：

```text
文字
  -> 繁體轉簡體與文字清理
  -> 常駐 in-process PiperVoice / g2pW / ONNX session
  -> raw PCM audio
  -> aplay 直接播放
```

正常播放時不會先寫 WAV 檔。只有 `--no-play`、`--output` 或 `--file-playback` 才會走 WAV 檔模式。

## 目前狀態

已實作：

- Jetson 本地離線 TTS server
- Piper 中文 voice：預設 `zh_CN-chaowen-medium`
- 支援其他中文 voice：`zh_CN-huayan-medium`、`zh_CN-huayan-x_low`、`zh_CN-xiao_ya-medium`
- 繁體中文自動轉簡體中文
- emoji / 控制字元 / 多餘空白清理
- 長文字依標點與長度切段
- HTTP API
- CLI
- Python client
- 播放佇列
- `interrupt=true` 打斷目前播放
- `stop` / clear queue
- systemd 開機自動啟動
- ALSA device 指定
- Hugging Face tokenizer cache offline 模式
- WAV cache，供檔案模式使用

## 專案結構

```text
jetson_piper_tts/
├── README.md
├── pyproject.toml
├── requirements.txt
├── .env.example
├── .env
├── scripts/
│   ├── setup_jetson.sh
│   ├── download_voice.sh
│   ├── test_audio.sh
│   ├── install_systemd_service.sh
│   └── benchmark_tts.sh
├── systemd/
│   └── jetson-piper-tts.service
├── models/
│   ├── zh_CN-chaowen-medium.onnx
│   └── zh_CN-chaowen-medium.onnx.json
├── cache/
├── g2pW/
├── presets/
│   └── preset_phrases.json
├── jetson_piper_tts/
│   ├── config.py
│   ├── text_normalizer.py
│   ├── piper_engine.py
│   ├── audio_player.py
│   ├── cache.py
│   ├── queue_worker.py
│   ├── server.py
│   ├── speak.py
│   ├── prewarm.py
│   └── client.py
└── tests/
```

## 快速使用

啟動 TTS server：

```bash
cd /home/asrlab-yian/MakeNTU/jetson_piper_tts
source .venv/bin/activate
python -m jetson_piper_tts.server --host 0.0.0.0 --port 8777
```

看到這行代表成功：

```text
Application startup complete.
Uvicorn running on http://0.0.0.0:8777
```

保持這個 terminal 不要關。另開一個 terminal 測試：

```bash
curl -X POST http://127.0.0.1:8777/speak_async \
  -H "Content-Type: application/json" \
  -d '{"text":"现在 TTS 服务已经启动。","interrupt":true}'
```

檢查健康狀態：

```bash
curl http://127.0.0.1:8777/health | python -m json.tool
```

如果用瀏覽器打開 `http://127.0.0.1:8777/` 看到 404，這是正常的。這個服務目前是 API server，不是網站首頁。

## 一鍵安裝

第一次在 Jetson 上安裝：

```bash
cd /home/asrlab-yian/MakeNTU/jetson_piper_tts
chmod +x scripts/*.sh
./scripts/setup_jetson.sh
./scripts/download_voice.sh
source .venv/bin/activate
python -m jetson_piper_tts.speak "系统测试声音。"
```

`setup_jetson.sh` 會做：

- `apt-get update`
- 安裝 `python3-venv`, `python3-pip`, `espeak-ng`, `alsa-utils`, `ffmpeg`, `curl`, `git`
- 建立 `.venv`
- 安裝 FastAPI / uvicorn / OpenCC / requests / pytest 等 Python 依賴
- 安裝 `piper-tts`
- 安裝中文 phonemizer 依賴：`g2pw`, `unicode-rbnf`, `sentence-stream`
- 檢查 PyTorch 是否可被 g2pW 使用
- 如果沒有 `.env`，從 `.env.example` 建立

## 中文 Voice / 換聲音

聲音模型放在：

```text
/home/asrlab-yian/MakeNTU/jetson_piper_tts/models/
```

目前這台 Jetson 已下載的中文 voice：

```text
zh_CN-chaowen-medium
zh_CN-huayan-medium
zh_CN-huayan-x_low
zh_CN-xiao_ya-medium
```

可以用 API 查目前 `models/` 裡有哪些 voice：

```bash
curl http://127.0.0.1:8777/voices | python -m json.tool
```

也可以直接看檔案：

```bash
ls -lh /home/asrlab-yian/MakeNTU/jetson_piper_tts/models/*.onnx
```

voice 名稱規則：

```text
zh_CN-chaowen-medium
│     │       └─ quality: medium / x_low
│     └─ speaker name: chaowen / huayan / xiao_ya
└─ locale: zh_CN
```

每個 voice 必須同時有兩個檔案：

```text
zh_CN-huayan-medium.onnx
zh_CN-huayan-medium.onnx.json
```

少了 `.onnx.json` 也不能用。

### 方法 1：HTTP 單次指定 Voice

這是最快測聲音的方法，不用改設定、不用重啟 server。

```bash
curl -X POST http://127.0.0.1:8777/speak_async \
  -H "Content-Type: application/json" \
  -d '{"text":"我换一个声音说话。","voice":"zh_CN-xiao_ya-medium","interrupt":true}'
```

多試幾個：

```bash
curl -X POST http://127.0.0.1:8777/speak_async \
  -H "Content-Type: application/json" \
  -d '{"text":"这是朝文的声音。","voice":"zh_CN-chaowen-medium","interrupt":true}'

curl -X POST http://127.0.0.1:8777/speak_async \
  -H "Content-Type: application/json" \
  -d '{"text":"这是花妍的声音。","voice":"zh_CN-huayan-medium","interrupt":true}'

curl -X POST http://127.0.0.1:8777/speak_async \
  -H "Content-Type: application/json" \
  -d '{"text":"这是小雅的声音。","voice":"zh_CN-xiao_ya-medium","interrupt":true}'
```

`zh_CN-huayan-x_low` 通常比較輕，但音質會比 medium 粗一點：

```bash
curl -X POST http://127.0.0.1:8777/speak_async \
  -H "Content-Type: application/json" \
  -d '{"text":"这是低延迟版本。","voice":"zh_CN-huayan-x_low","interrupt":true}'
```

### 方法 2：CLI 單次指定 Voice

CLI 適合測試，不適合拿來量實際互動延遲，因為每次 CLI 都會重新啟動 Python / Piper。

```bash
cd /home/asrlab-yian/MakeNTU/jetson_piper_tts
source .venv/bin/activate

python -m jetson_piper_tts.speak "这是小雅的声音。" --voice zh_CN-xiao_ya-medium
python -m jetson_piper_tts.speak "这是花妍的声音。" --voice zh_CN-huayan-medium
python -m jetson_piper_tts.speak "这是低延迟版本。" --voice zh_CN-huayan-x_low
```

### 方法 3：永久改預設 Voice

如果你已經決定 demo 要固定用某個聲音，就改 `.env`：

```bash
nano /home/asrlab-yian/MakeNTU/jetson_piper_tts/.env
```

把這兩行改成你要的 voice。

例如改成 `zh_CN-huayan-medium`：

```env
PIPER_MODEL=./models/zh_CN-huayan-medium.onnx
PIPER_CONFIG=./models/zh_CN-huayan-medium.onnx.json
```

例如改成 `zh_CN-xiao_ya-medium`：

```env
PIPER_MODEL=./models/zh_CN-xiao_ya-medium.onnx
PIPER_CONFIG=./models/zh_CN-xiao_ya-medium.onnx.json
```

改完一定要重啟 TTS server，因為 server 會把 voice 常駐載入記憶體：

```bash
cd /home/asrlab-yian/MakeNTU/jetson_piper_tts
source .venv/bin/activate
python -m jetson_piper_tts.server --host 0.0.0.0 --port 8777
```

重啟後確認預設 voice：

```bash
curl http://127.0.0.1:8777/health | python -m json.tool
```

看輸出裡的：

```text
engine.model
engine.config
```

應該要指到你剛剛改的 `.onnx` 和 `.onnx.json`。

### 方法 4：從 Voice Chat 臨時指定 Voice

如果你是從 `emotion_robot_controller/voice_stt_remote/jetson_fast_voice_chat.py` 跑語音聊天，不想改 `.env`，直接在 voice chat client 加 `--tts-voice`：

```bash
cd /home/asrlab-yian/MakeNTU/emotion_robot_controller/voice_stt_remote
source ../.venv/bin/activate

python jetson_fast_voice_chat.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --tts-voice zh_CN-huayan-medium
```

這只影響這次 voice chat 執行，不會改 `.env`。

### 下載新的中文 Voice

下載腳本格式：

```bash
./scripts/download_voice.sh VOICE_NAME
```

例子：

```bash
cd /home/asrlab-yian/MakeNTU/jetson_piper_tts
source .venv/bin/activate

./scripts/download_voice.sh zh_CN-huayan-medium
./scripts/download_voice.sh zh_CN-huayan-x_low
./scripts/download_voice.sh zh_CN-xiao_ya-medium
```

腳本會下載：

```text
models/VOICE_NAME.onnx
models/VOICE_NAME.onnx.json
```

如果下載失敗，通常是 voice 名稱打錯，或 Hugging Face 連線不穩。手動來源在 README 最下面的「參考 Voice 來源」。

### 建議 Demo 選法

先用 HTTP 快速試：

```bash
curl -X POST http://127.0.0.1:8777/speak_async \
  -H "Content-Type: application/json" \
  -d '{"text":"你好，我现在用这个声音跟你说话。","voice":"zh_CN-huayan-medium","interrupt":true}'
```

如果覺得好聽，就把 `.env` 的 `PIPER_MODEL` / `PIPER_CONFIG` 改成那個 voice。  
如果只是當天想臨時換，就不要改 `.env`，在 `jetson_fast_voice_chat.py` 加 `--tts-voice` 就好。

## 設定檔

主要設定在 `.env`。

目前建議設定：

```bash
PIPER_BIN=piper

MODEL_DIR=./models
PIPER_MODEL=./models/zh_CN-chaowen-medium.onnx
PIPER_CONFIG=./models/zh_CN-chaowen-medium.onnx.json

AUDIO_DEVICE=default
APLAY_BIN=aplay

EXTRA_PYTHONPATH=

CACHE_DIR=./cache
HOST=0.0.0.0
PORT=8777

DEFAULT_LENGTH_SCALE=0.90
DEFAULT_NOISE_SCALE=0.667
DEFAULT_NOISE_W=0.8

MAX_TEXT_CHARS=600
MAX_CHUNK_CHARS=70
ENABLE_TRADITIONAL_TO_SIMPLIFIED=true
ENABLE_CACHE=true

ENABLE_STREAM_PLAYBACK=true
ENABLE_INPROCESS_PIPER=true
ENABLE_HF_OFFLINE=true

SYNTH_TIMEOUT_SECONDS=45
LOG_LEVEL=INFO
```

常改項目：

- `AUDIO_DEVICE=default`：使用 ALSA default。
- `AUDIO_DEVICE=auto:UACDemo`：每次播放前從 `aplay -L` 重新找 UACDemo USB speaker，最適合 demo 反覆重插 USB。
- `AUDIO_DEVICE=plughw:CARD=UACDemoV10,DEV=0`：用 USB 喇叭的穩定 ALSA 卡名，比 `plughw:1,0` 不容易因為重新插拔而失效。
- `DEFAULT_LENGTH_SCALE=0.85`：語速更快。
- `DEFAULT_LENGTH_SCALE=1.0`：語速較自然。
- `MAX_TEXT_CHARS=600`：單次最多處理 600 字。
- `MAX_CHUNK_CHARS=70`：每個 chunk 約 70 字以內。
- `ENABLE_STREAM_PLAYBACK=true`：使用 raw streaming，回覆出來後比較快開始播放。
- `ENABLE_INPROCESS_PIPER=true`：server 常駐載入 Piper，不每句重開 CLI。
- `ENABLE_HF_OFFLINE=true`：使用本機 Hugging Face cache，不啟動時連線檢查 tokenizer。

改 `.env` 後一定要重啟 server。

## 為什麼 CLI 比 Server 慢

CLI：

```bash
python -m jetson_piper_tts.speak "测试。"
```

每次都會重新啟動 Python process，重新載入 Piper / g2pW / PyTorch / ONNX session，所以不適合作為實際互動延遲指標。

Server：

```bash
python -m jetson_piper_tts.server --host 0.0.0.0 --port 8777
```

會在啟動時 warm-up，並把 Piper voice 常駐在記憶體。之後每句話就不用重載模型，延遲會低很多。

看 API 回傳時，最快的正常模式應該長這樣：

```json
{
  "wav_files": [],
  "playback": {
    "streaming": true,
    "producer": "inprocess",
    "mode": "inprocess_raw_stream"
  }
}
```

如果看到：

```json
"producer": "subprocess"
```

代表 in-process Piper 失敗，fallback 到 Piper CLI，通常會慢很多。

## HTTP API

### GET `/health`

檢查 server、模型、音訊、cache、queue。

```bash
curl http://127.0.0.1:8777/health | python -m json.tool
```

重要欄位：

- `ready`: 是否可用
- `engine.ready`: Piper model / config 是否存在
- `audio.available`: `aplay` 是否可用
- `audio.device`: 使用中的 ALSA device
- `settings.enable_stream_playback`: 是否直接串流播放
- `settings.enable_inprocess_piper`: 是否使用常駐 Piper
- `queue.queue_size`: 播放佇列長度

### GET `/voices`

列出 `models/` 下的 voice。

```bash
curl http://127.0.0.1:8777/voices | python -m json.tool
```

### GET `/queue`

查看 queue 狀態：

```bash
curl http://127.0.0.1:8777/queue | python -m json.tool
```

### POST `/speak_async`

推薦機器人互動使用。加入 queue 後立刻回傳，不等播放完。

```bash
curl -X POST http://127.0.0.1:8777/speak_async \
  -H "Content-Type: application/json" \
  -d '{"text":"你好，我是桌面助手。","interrupt":true}'
```

常用 body：

```json
{
  "text": "你好，我是桌面助手。",
  "interrupt": true,
  "voice": "zh_CN-chaowen-medium",
  "length_scale": 0.9,
  "stream": true
}
```

參數說明：

- `text`: 要講的文字。
- `interrupt`: `true` 會停止目前播放並清空 queue，馬上講新句。
- `voice`: 可選，指定 voice 名稱。
- `length_scale`: 可選，越小越快。
- `stream`: 可選，`true` 走 raw streaming；`false` 走 WAV 檔路徑。

### POST `/speak`

同步播放，會等播放完成才回傳。

```bash
curl -X POST http://127.0.0.1:8777/speak \
  -H "Content-Type: application/json" \
  -d '{"text":"这句话会播放完才返回。","blocking":true,"interrupt":true}'
```

### POST `/stop`

停止目前播放並清空 queue。

```bash
curl -X POST http://127.0.0.1:8777/stop
```

## 長文字正確送法

不要把超長文字直接塞進一行 `curl -d '...'`，容易被 shell 拆壞。尤其文字裡有：

- 換行
- 單引號
- 雙引號
- `[15]`
- 很多空白
- 很長的中文段落

請用 JSON 檔案：

```bash
cat > /tmp/tts_payload.json <<'JSON'
{
  "text": "房子大了電話小了 感覺越來越好 飯菜香了穿戴美了 生活越來越好",
  "interrupt": true
}
JSON

curl -X POST http://127.0.0.1:8777/speak_async \
  -H "Content-Type: application/json" \
  --data-binary @/tmp/tts_payload.json
```

如果你剛剛打成這樣：

```bash
curl -X POST http://curl -X POST http://127.0.0.1:8777/speak_async ...
```

這是錯的。`http://curl` 會讓 curl 把 `curl` 當成 host，後面文字也會被當成 URL，於是出現：

```text
curl: (6) Could not resolve host
curl: (3) bad range in URL position
```

這不是 TTS 壞掉，是指令被 shell 拆壞。

## 長文字限制

預設：

```bash
MAX_TEXT_CHARS=600
MAX_CHUNK_CHARS=70
```

超過 600 字會被截斷。要念更長的文章，改 `.env`：

```bash
MAX_TEXT_CHARS=3000
MAX_CHUNK_CHARS=60
```

然後重啟 server。

長文字建議用 `/speak_async`，不要用 `/speak`，避免 client 等太久。

## CLI

直接播放：

```bash
python -m jetson_piper_tts.speak "系统测试声音。"
```

輸出 JSON metrics：

```bash
python -m jetson_piper_tts.speak "系统测试声音。" --json
```

只合成、不播放：

```bash
python -m jetson_piper_tts.speak "系统测试声音。" --no-play --output /tmp/tts.wav
```

強制舊 WAV 播放路徑：

```bash
python -m jetson_piper_tts.speak "测试旧播放路径。" --file-playback --json
```

指定聲音：

```bash
python -m jetson_piper_tts.speak "换一个声音。" --voice zh_CN-xiao_ya-medium
```

指定 ALSA device：

```bash
python -m jetson_piper_tts.speak "测试 USB 喇叭。" --device 'plughw:CARD=UACDemoV10,DEV=0' --stream
```

## Python Client

```python
from jetson_piper_tts.client import JetsonPiperTTSClient

tts = JetsonPiperTTSClient("http://127.0.0.1:8777")
tts.speak("你好，我是桌面助手。", blocking=False, interrupt=True)
```

停止：

```python
tts.stop()
```

查看健康狀態：

```python
print(tts.health())
```

## 與語音聊天系統整合

桌機 LLM / ASR server 產生 reply text 後，把文字送到 Jetson：

```python
import requests

requests.post(
    "http://JETSON_IP:8777/speak_async",
    json={
        "text": reply_text,
        "interrupt": True,
        "length_scale": 0.9,
    },
    timeout=3,
)
```

建議：

- 使用 `/speak_async`
- 新一輪使用者講話時用 `interrupt=true`
- 不重要的狀態提示用 `interrupt=false`
- 回覆太長時先在 LLM 端縮短，或調大 `MAX_TEXT_CHARS`

## 音量調整

進入 ALSA mixer：

```bash
alsamixer
```

操作：

- `F6` 選聲卡
- 左右鍵選 `Master` / `PCM` / `Speaker`
- 上下鍵調音量
- 如果底下是 `MM`，按 `M` 解除靜音
- `Esc` 離開

命令列調整：

```bash
amixer -D default sset Master 80% unmute
amixer -D default sset PCM 80% unmute
```

USB speaker 常見是 card 1：

```bash
alsamixer -c 1
amixer -c 1 sset Speaker 80% unmute
```

測試 audio：

```bash
./scripts/test_audio.sh
```

指定裝置測：

```bash
AUDIO_DEVICE='plughw:CARD=UACDemoV10,DEV=0' ./scripts/test_audio.sh
```

## ALSA Device

列出播放硬體：

```bash
aplay -l
```

列出 PCM 裝置：

```bash
aplay -L
```

常見設定：

```bash
AUDIO_DEVICE=default
AUDIO_DEVICE=auto:UACDemo
AUDIO_DEVICE=plughw:CARD=UACDemoV10,DEV=0
AUDIO_DEVICE=plughw:0,0
AUDIO_DEVICE=hw:0,0
```

如果 `default` 沒聲音，先試 USB speaker：

```bash
python -m jetson_piper_tts.speak "测试声音。" --device 'plughw:CARD=UACDemoV10,DEV=0' --stream
```

可用後寫進 `.env`：

```bash
AUDIO_DEVICE=auto:UACDemo
```

重啟 server。

## Hugging Face / g2pW Offline

中文 Piper voice 會用 g2pW 處理拼音。g2pW 會用 `bert-base-chinese` tokenizer。

第一次啟動可能看到：

```text
Warning: You are sending unauthenticated requests to the HF Hub
HTTP Request: HEAD https://huggingface.co/bert-base-chinese/...
```

第一次成功後，本機通常會有 cache：

```bash
ls ~/.cache/huggingface/hub/models--bert-base-chinese
```

之後可設定：

```bash
ENABLE_HF_OFFLINE=true
```

這會在載入 Piper 前設定：

```bash
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
HF_HUB_DISABLE_TELEMETRY=1
TOKENIZERS_PARALLELISM=false
```

如果 cache 還不存在就打開 offline，可能會載入失敗。乾淨新機器建議先：

```bash
ENABLE_HF_OFFLINE=false
python -m jetson_piper_tts.server --host 0.0.0.0 --port 8777
```

啟動成功一次後，再改 `ENABLE_HF_OFFLINE=true`。

## 預設語句與 Prewarm

預設語句在：

```text
presets/preset_phrases.json
```

內容包含：

```json
{
  "startup": "系统启动完成。",
  "work_mode": "我侦测到你正在工作，我会安静一点。",
  "rest_mode": "休息一下也很好，记得喝水。",
  "sleep_mode": "进入安静模式，不打扰你。",
  "away_mode": "主人离席，我开始整理桌面。",
  "fan_on": "电风扇已开启。",
  "light_on": "灯已开启。",
  "error": "抱歉，我刚刚没有成功处理这个指令。"
}
```

預熱：

```bash
python -m jetson_piper_tts.prewarm
```

預先合成 preset 到 cache：

```bash
python -m jetson_piper_tts.prewarm --presets
```

注意：目前正常播放是 raw stream，不走 WAV cache；preset cache 主要給 `--no-play`、`--output` 或舊檔案播放路徑使用。

## Systemd 開機自動啟動

安裝 service：

```bash
cd /home/asrlab-yian/MakeNTU/jetson_piper_tts
./scripts/install_systemd_service.sh
```

啟動：

```bash
sudo systemctl start jetson-piper-tts
```

查看狀態：

```bash
sudo systemctl status jetson-piper-tts --no-pager
```

看 log：

```bash
journalctl -u jetson-piper-tts -f
```

停止：

```bash
sudo systemctl stop jetson-piper-tts
```

重啟：

```bash
sudo systemctl restart jetson-piper-tts
```

如果 systemd 啟動後沒聲音，通常是使用者沒有 audio 權限：

```bash
sudo usermod -aG audio "$USER"
sudo reboot
```

或指定正確的 `AUDIO_DEVICE`。

## Benchmark

跑 benchmark：

```bash
./scripts/benchmark_tts.sh
```

注意：目前 benchmark 主要測檔案 / cache path，不等於 server 常駐 raw streaming 的實際延遲。真實 demo 延遲請用 HTTP server 測。

## 測試

單元測試：

```bash
source .venv/bin/activate
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q
```

`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` 是為了避免 ROS / 系統 pytest plugin 干擾。

語法檢查：

```bash
python -m compileall jetson_piper_tts tests
```

## 常見問題

### Server 啟動後 `GET /` 是 404

正常。這是 API server，目前沒有首頁。

請用：

```bash
curl http://127.0.0.1:8777/health
```

### `GET /favicon.ico` 是 404

正常。這是瀏覽器自動要求網站 icon，不影響 TTS。

### `GPU device discovery failed`

常見 warning：

```text
GPU device discovery failed: ... /sys/class/drm/card1/device/vendor
```

通常可忽略。Piper / onnxruntime 還是會用 CPU provider 正常跑。

### `ModuleNotFoundError: No module named 'g2pw'`

中文 voice 缺 phonemizer：

```bash
source .venv/bin/activate
python -m pip install g2pw unicode-rbnf sentence-stream
```

### `Piper model does not exist`

通常是 `voice` 名稱打錯，或 `.env` 指到還沒下載的模型。

先看目前有哪些 voice：

```bash
ls -lh /home/asrlab-yian/MakeNTU/jetson_piper_tts/models/*.onnx
curl http://127.0.0.1:8777/voices | python -m json.tool
```

如果你要用的 voice 不在裡面，先下載：

```bash
cd /home/asrlab-yian/MakeNTU/jetson_piper_tts
source .venv/bin/activate
./scripts/download_voice.sh zh_CN-huayan-medium
```

如果你是改 `.env` 後出錯，確認這兩行完全對得上檔名：

```env
PIPER_MODEL=./models/zh_CN-huayan-medium.onnx
PIPER_CONFIG=./models/zh_CN-huayan-medium.onnx.json
```

注意：`PIPER_CONFIG` 要包含 `.onnx.json`，不是只寫 `.json`。

### 改了 `.env` 但聲音沒有變

TTS server 會在啟動時載入 voice。改 `.env` 後一定要重啟 server：

```bash
cd /home/asrlab-yian/MakeNTU/jetson_piper_tts
source .venv/bin/activate
python -m jetson_piper_tts.server --host 0.0.0.0 --port 8777
```

如果你是用 systemd：

```bash
sudo systemctl restart jetson-piper-tts
journalctl -u jetson-piper-tts -f
```

重啟後檢查：

```bash
curl http://127.0.0.1:8777/health | python -m json.tool
```

看 `engine.model` 是不是新的 voice。

### `--tts-voice` 沒有效果

`--tts-voice` 是 `voice_stt_remote/jetson_fast_voice_chat.py` 的參數，不是 `jetson_piper_tts.server` 的參數。

正確：

```bash
cd /home/asrlab-yian/MakeNTU/emotion_robot_controller/voice_stt_remote
source ../.venv/bin/activate
python jetson_fast_voice_chat.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --tts-voice zh_CN-xiao_ya-medium
```

錯誤：

```bash
python -m jetson_piper_tts.server --tts-voice zh_CN-xiao_ya-medium
```

server 要永久換預設 voice，請改 `.env` 的 `PIPER_MODEL` / `PIPER_CONFIG`。

### `ModuleNotFoundError: No module named 'torch'`

g2pW 需要 PyTorch。先檢查系統 Python 是否有 torch：

```bash
python3 - <<'PY'
import torch
print(torch.__version__, torch.__file__)
print("cuda:", torch.cuda.is_available())
PY
```

如果系統 Python 有 torch，但 `.venv` 找不到，通常不用把 torch 重裝進 `.venv`。本專案會自動把 user site 加到 import path。

如果 torch 在特殊位置，設定：

```bash
EXTRA_PYTHONPATH=/path/to/site-packages
```

### 安裝 torch 變成缺 `libcudart.so.13`

你可能在 `.venv` 裝了不完整的 torch wheel。可移除：

```bash
source .venv/bin/activate
python -m pip uninstall torch
```

然後使用 Jetson 系統或 user-site 已安裝好的 PyTorch。

### 沒有聲音

先測 ALSA：

```bash
aplay -l
aplay -L
speaker-test -D default -t sine -f 440 -c 2
```

再測 TTS：

```bash
python -m jetson_piper_tts.speak "测试声音。"
```

如果 USB 喇叭是 `UACDemoV1.0`：

```bash
python -m jetson_piper_tts.speak "测试声音。" --device 'plughw:CARD=UACDemoV10,DEV=0' --stream
```

### `aplay: device busy`

停止目前播放：

```bash
curl -X POST http://127.0.0.1:8777/stop
```

查看音訊佔用：

```bash
fuser -v /dev/snd/*
```

### 回覆很慢

先確認是不是用 server，而不是每次 CLI：

```bash
python -m jetson_piper_tts.server --host 0.0.0.0 --port 8777
```

再看 API 回傳：

- `playback.mode=inprocess_raw_stream`：最快正常路徑。
- `producer=inprocess`：使用常駐 Python Piper。
- `wav_files=[]`：沒有寫 WAV。
- `producer=subprocess`：fallback 到 Piper CLI，會慢。
- `synth_ms` 很高：可能第一次 warm-up、句子太長、CPU 負載高。

### 長文字只念前面

預設 `MAX_TEXT_CHARS=600`。改 `.env`：

```bash
MAX_TEXT_CHARS=3000
MAX_CHUNK_CHARS=60
```

重啟 server。

### 改了 `.env` 沒效果

需要重啟 server。

手動啟動時按 `Ctrl+C` 停掉，再重跑：

```bash
python -m jetson_piper_tts.server --host 0.0.0.0 --port 8777
```

systemd：

```bash
sudo systemctl restart jetson-piper-tts
```

## 建議 Demo 流程

1. 啟動 server：

```bash
cd /home/asrlab-yian/MakeNTU/jetson_piper_tts
source .venv/bin/activate
python -m jetson_piper_tts.server --host 0.0.0.0 --port 8777
```

2. 另開 terminal 測健康狀態：

```bash
curl http://127.0.0.1:8777/health | python -m json.tool
```

3. 測一句短句：

```bash
curl -X POST http://127.0.0.1:8777/speak_async \
  -H "Content-Type: application/json" \
  -d '{"text":"你好，我准备好了。","interrupt":true}'
```

4. 測換聲音：

```bash
curl -X POST http://127.0.0.1:8777/speak_async \
  -H "Content-Type: application/json" \
  -d '{"text":"我现在换一个声音说话。","voice":"zh_CN-xiao_ya-medium","interrupt":true}'
```

5. 測停止：

```bash
curl -X POST http://127.0.0.1:8777/stop
```

## 參考 Voice 來源

Piper voices 來源：

- `https://huggingface.co/rhasspy/piper-voices/tree/main/zh/zh_CN`
- `https://huggingface.co/rhasspy/piper-voices/tree/main/zh/zh_CN/chaowen`
- `https://huggingface.co/rhasspy/piper-voices/tree/main/zh/zh_CN/huayan`
- `https://huggingface.co/rhasspy/piper-voices/tree/main/zh/zh_CN/xiao_ya`
