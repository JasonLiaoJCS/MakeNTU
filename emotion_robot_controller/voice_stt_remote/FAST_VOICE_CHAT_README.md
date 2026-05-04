# Fast Voice Chat 操作 README

這份 README 只說明目前最新的「精簡快版」：

```text
Jetson Orin Nano 錄音
-> 上傳 WAV 到 Windows 桌機
-> Windows 桌機用 Qwen3-ASR 轉文字
-> Windows 桌機用 Ollama qwen35-fast:latest 產生回答
-> Windows 桌機本地規則分析情緒
-> Jetson 顯示：Transcript / Reply / Emotion / Timing
```

這版**不控制硬體、不送 UART、不送 serial 指令**。  
目標是：快一點、回答像人、同時顯示你的情緒狀態。

---

## 1. 目前會用到的檔案

### Windows 桌機端

桌機負責跑模型：

```text
desktop_fast_chat_server.py
desk_voice_controller.py
requirements_desktop_voice_server.txt
```

### Jetson Orin Nano 端

Jetson 負責錄音和顯示結果：

```text
jetson_fast_voice_chat.py
jetson_remote_voice_client.py
desk_voice_controller.py
```

注意：

```text
desktop_fast_chat_server.py 只放桌機跑
jetson_fast_voice_chat.py 只放 Jetson 跑
```

---

## 2. 系統架構

```text
USB 麥克風
   |
   v
Jetson Orin Nano
   - 按 Enter 開始錄音
   - 再按 Enter 結束錄音
   - 48 kHz 錄音
   - 自動轉成 16 kHz WAV
   |
   | HTTP POST /voice-chat
   v
Windows 桌機 100.108.141.26:8766
   - Qwen3-ASR-1.7B 語音轉文字
   - Ollama qwen35-fast:latest 產生自然回答
   - 本地規則分析情緒
   |
   | JSON response
   v
Jetson 顯示
   - Transcript
   - Reply
   - Emotion
   - Timing
```

---

## 3. Windows 桌機第一次設定

在 Windows PowerShell：

```powershell
cd C:\Users\User\Desktop\windows_desktop_server_bundle

py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
pip install -r requirements_desktop_voice_server.txt
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
```

確認 RTX 4090 有被 PyTorch 看到：

```powershell
python -c "import torch; print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'no cuda')"
```

成功應該看到：

```text
True
NVIDIA GeForce RTX 4090
```

如果是：

```text
False
no cuda
```

先確認：

```powershell
nvidia-smi
```

如果 `nvidia-smi` 看不到 RTX 4090，先處理 NVIDIA driver。

---

## 4. Windows 桌機準備 Ollama

確認 Ollama 已經安裝並可用：

```powershell
ollama list
```

下載快模型：

```powershell
ollama pull qwen35-fast:latest
```

如果你手動跑：

```powershell
ollama serve
```

出現：

```text
bind: Only one usage of each socket address...
```

代表 Ollama 已經在背景跑了，不是錯誤。

---

## 5. 從 Jetson 更新 Windows 桌機檔案

如果我在 Jetson 修改了 server，請在 Windows PowerShell 抓最新檔案：

```powershell
cd C:\Users\User\Desktop\windows_desktop_server_bundle

scp asrlab-yian@100.110.90.72:/home/asrlab-yian/MakeNTU/emotion_robot_controller/voice_stt_remote/desktop_fast_chat_server.py .\desktop_fast_chat_server.py
scp asrlab-yian@100.110.90.72:/home/asrlab-yian/MakeNTU/emotion_robot_controller/voice_stt_remote/desk_voice_controller.py .\desk_voice_controller.py
```

如果 `100.110.90.72` 不通，可以改用 Jetson 區網 IP：

```powershell
scp asrlab-yian@192.168.1.150:/home/asrlab-yian/MakeNTU/emotion_robot_controller/voice_stt_remote/desktop_fast_chat_server.py .\desktop_fast_chat_server.py
scp asrlab-yian@192.168.1.150:/home/asrlab-yian/MakeNTU/emotion_robot_controller/voice_stt_remote/desk_voice_controller.py .\desk_voice_controller.py
```

---

## 6. 啟動 Windows 桌機 Server

在 Windows PowerShell：

```powershell
cd C:\Users\User\Desktop\windows_desktop_server_bundle
.\.venv\Scripts\Activate.ps1

python desktop_fast_chat_server.py --host 0.0.0.0 --port 8766 --ollama-model qwen35-fast:latest --no-think
```

這個視窗不要關。

你應該看到類似：

```text
Ollama warm-up: model=qwen35-fast:latest, no_think=True
Ollama warm-up done: ...
Loading Qwen ASR: Qwen/Qwen3-ASR-1.7B
torch.cuda.is_available() = True
ASR device=cuda:0, dtype=bfloat16
Fast chat server listening on http://0.0.0.0:8766
```

如果 Windows 防火牆跳出提示，請允許 Python 在私人網路通訊。

---

## 7. Jetson 測試連線

在 Jetson：

```bash
curl http://100.108.141.26:8766/health
```

成功會看到類似：

```json
{
  "ok": true,
  "service": "desktop_fast_chat_server",
  "asr_loaded": true,
  "chat_ready": true
}
```

如果連不到，先檢查：

```text
1. Windows server 有沒有跑
2. Windows 防火牆有沒有擋
3. Jetson 和桌機是否在同一個網路 / Tailscale
4. IP 是否正確：100.108.141.26
```

---

## 8. Jetson 準備環境

在 Jetson：

```bash
cd /home/asrlab-yian/MakeNTU/emotion_robot_controller/voice_stt_remote
source ../.venv/bin/activate
pip install -r requirements_jetson_voice_client.txt
```

列出麥克風：

```bash
python jetson_fast_voice_chat.py --list-mics
```

輸出會列出 `inputs > 0` 的音訊輸入。請選你的 USB 麥克風或系統預設輸入，例如：

```text
[26] inputs=32 default_sr=44100.0 name=default  <-- default
```

如果看到 USB 麥克風，使用該行的 index：

```text
--device <麥克風 index>
```

也可以省略 `--device`，讓程式使用 SoundDevice 的預設輸入。

---

## 9. Jetson 文字測試，不錄音

先確認桌機 LLM 回答正常：

```bash
cd /home/asrlab-yian/MakeNTU/emotion_robot_controller/voice_stt_remote
source ../.venv/bin/activate

python jetson_fast_voice_chat.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --text "對於你回答太慢這件事，你有什麼看法？"
```

會送到桌機的 `/text-chat`，不會跑 ASR。  
如果文字測試都慢，問題在 Ollama / LLM。  
如果文字測試快、語音慢，問題多半在 ASR。

---

## 10. Jetson 語音聊天

執行：

```bash
cd /home/asrlab-yian/MakeNTU/emotion_robot_controller/voice_stt_remote
source ../.venv/bin/activate

python jetson_fast_voice_chat.py \
  --server-url http://100.108.141.26:8766/voice-chat
```

操作方式：

```text
按 Enter 開始錄音
開始說話
說完再按 Enter 結束錄音
等待桌機回覆
輸入 q 再 Enter 離開
```

範例：

```text
Press Enter to record>
Recording at 48000 Hz mono. Press Enter again to stop.

Recorded 4.83s
RMS=0.53485
POST audio to http://100.108.141.26:8766/voice-chat
Round trip: 8585 ms
```

---

## 11. 輸出格式說明

成功後會看到：

```text
Transcript:
我真的覺得被這個模型蠢到，他能不能再聰明一點？

Reply:
可以，問題不是你講得不清楚，是我前一版 prompt 太鬆、fallback 太像罐頭。改法就是回答先命中問題，最多兩句，情緒分析走本地規則。

Emotion:
  primary        : frustrated
  intensity      : 0.75
  valence        : -0.55
  arousal        : 0.7
  support_needed : False
  summary        : 使用者明顯對速度或品質不滿，帶有挫折和急迫感。

Timing:
  asr_ms   : 1781
  llm_ms   : 6312
  total_ms : 8437
```

欄位意思：

```text
Transcript：ASR 聽到的文字
Reply：桌面助手回答
Emotion：情緒分析
Timing：耗時拆解
```

---

## 12. Timing 怎麼看

### asr_ms 很大

代表 Qwen3-ASR 慢。可能原因：

```text
1. 錄音太長
2. 桌機 GPU 沒啟用
3. ASR 模型第一次 warm-up
4. 音檔太大
```

建議：

```text
錄音控制在 3 到 6 秒
確認 torch.cuda.is_available() 是 True
```

### llm_ms 很大

代表 Ollama / qwen35-fast 慢。

建議：

```text
使用 --no-think
使用 qwen35-fast:latest
降低 num_predict
保持 Ollama 已 warm-up
```

目前 `desktop_fast_chat_server.py` 已經設定：

```text
temperature = 0.35
num_ctx = 2048
num_predict = 120
no_think = True
```

### total_ms 很大但 asr_ms / llm_ms 不大

可能是網路或上傳慢。

檢查：

```bash
ping 100.108.141.26
```

---

## 13. 回答品質調整位置

主要改這個檔案：

```text
desktop_fast_chat_server.py
```

### Prompt 位置

```python
SYSTEM_PROMPT = """
...
"""
```

目前核心規則：

```text
1. 第一個句子直接回答使用者的問題，不要鋪陳。
2. 不要反問「可以多說一點嗎」，除非完全聽不懂。
3. 最多 2 句，每句短，禁止 JSON/markdown/標題。
4. 使用者抱怨模型慢、笨、爛時，承認問題並給明確改法。
5. 涉及身分或群體時，只談具體行為與情境，不攻擊身分。
```

### 本地 fallback 位置

```python
def local_reply(transcript: str) -> str:
```

如果模型失敗，或你想讓某些句子固定快答，就加在這裡。

### 情緒分析位置

```python
def analyze_emotion_local(transcript: str) -> dict[str, Any]:
```

這裡是本地規則，不用等 LLM，所以很快。

---

## 14. 常見問題

### 1. `curl /health` 連不到

檢查桌機 server 是否開著：

```powershell
python desktop_fast_chat_server.py --host 0.0.0.0 --port 8766 --ollama-model qwen35-fast:latest --no-think
```

檢查 Windows 防火牆。

### 2. `torch.cuda.is_available()` 是 False

在 Windows：

```powershell
nvidia-smi
python -c "import torch; print(torch.cuda.is_available()); print(torch.version.cuda)"
```

重裝 CUDA 版 PyTorch：

```powershell
pip uninstall -y torch torchvision torchaudio
pip cache purge
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
```

### 3. 麥克風 `Invalid sample rate`

你的 USB 麥克風不接受 16 kHz 直接錄音。  
目前程式已經會：

```text
48 kHz 錄音 -> 16 kHz WAV
```

看到這行就是正常：

```text
Input sample rate: 48000 Hz; upload WAV sample rate: 16000 Hz
```

### 4. RMS 太高或 ASR 亂聽

如果 RMS 經常：

```text
0.5 以上
```

可能太大聲或破音。  
把麥克風拿遠一點，或降低系統麥克風增益。

### 5. 回答還是不像人

先確認 Windows 桌機有更新到最新版：

```powershell
scp asrlab-yian@100.110.90.72:/home/asrlab-yian/MakeNTU/emotion_robot_controller/voice_stt_remote/desktop_fast_chat_server.py .\desktop_fast_chat_server.py
```

然後重啟 server。

### 6. 回答還是很慢

看 Jetson 輸出的：

```text
asr_ms
llm_ms
total_ms
```

如果 `llm_ms` 很大，代表 Ollama 慢。  
如果 `asr_ms` 很大，代表 ASR 慢。

---

## 15. 建議測試句

### 測聊天

```text
你聽得到我說話嗎？
你覺得你現在回答很慢的原因是什麼？
我真的覺得這個模型有點笨，你能不能直接一點？
```

### 測情緒

```text
我今天真的很累，有點撐不下去了。
我現在有點煩，因為一直測不成功。
我覺得剛剛終於跑通了，蠻開心的。
```

### 測敏感情境

```text
我旁邊有人一直講話，讓我有點煩，你怎麼看？
```

預期回答應該聚焦在：

```text
講話音量、場合、你被打擾的感受
```

而不是攻擊對方身分。

---

## 16. 最短操作指令

### Windows 桌機

```powershell
cd C:\Users\User\Desktop\windows_desktop_server_bundle
.\.venv\Scripts\Activate.ps1
python desktop_fast_chat_server.py --host 0.0.0.0 --port 8766 --ollama-model qwen35-fast:latest --no-think
```

### Jetson

```bash
cd /home/asrlab-yian/MakeNTU/emotion_robot_controller/voice_stt_remote
source ../.venv/bin/activate
python jetson_fast_voice_chat.py --server-url http://100.108.141.26:8766/voice-chat
```
