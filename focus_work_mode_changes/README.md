# Focus Work Mode Changes

這個資料夾原本只放「專心工作模式 / 分心偵測」相關的變動檔案，不是完整專案副本。現在功能已經整進正式路徑，這裡主要保留為 staging/history 參考。

正式檔案目前在：

```text
frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py
frdm_uart_context_sender/focus_work_mode.py
emotion_robot_controller/voice_stt_remote/desktop_fast_chat_server.py
emotion_robot_controller/voice_stt_remote/windows_desktop_server_bundle/desktop_fast_chat_server.py
```

這個資料夾裡不再保留舊版 wake bridge，避免覆蓋掉組員在主檔案新增的 UART/head motion 等功能。

## 變動檔案

正式測試請優先看 `frdm_uart_context_sender/README.md` 的 Focus Work Mode 和 Windows Server 段落。

## 功能概要

`focus_work_mode.py` 是新的專心工作模式主程式：

- 開始 session 時送 FRDM UART 工作表情。
- 每隔一段時間拍一張使用者照片。
- 將照片送到桌面端 `/focus-check` 判斷使用者狀態。
- 預設不保存照片，判斷完成後只保留結果。
- 將每次判斷結果寫入 `focus_log.jsonl`。
- 結束 session 後產生 `focus_report.md`。
- 結束時送 FRDM UART 回 `Normal`。

目前狀態分類：

```text
focused, away, phone, sleeping, distracted, uncertain, error
```

主專案的 `frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py` 現在負責 work mode 的語音啟動/停止：

- 聽到「開始工作」「專心工作」「工作模式」「番茄鐘」「focus mode」等指令時，啟動 `focus_work_mode.py`。
- 聽到「結束工作」「停止專心」「下班」「end focus」等指令時，停止 work mode。
- work mode 執行中會暫時釋放一般互動用相機，避免和定期拍照衝突。

`desktop_fast_chat_server.py` 新增 `/focus-check`：

- 接收 Jetson 上傳的單張 JPEG。
- 呼叫 vision model 判斷工作狀態。
- 回傳結構化 JSON，供 `focus_work_mode.py` 記錄與產生報告。

## 照片隱私

預設照片是 memory-only：

- 相機拍到的 JPEG bytes 只送去 `/focus-check`。
- 分析完成後不寫入硬碟。
- log 只記錄狀態、信心分數、摘要、證據文字、時間等資料。

只有加上 `--save-images` 時才會保存取樣圖片，這個選項只建議 debug 時使用。

## Self-Test

從 repo 根目錄執行：

```bash
cd /home/asrlab-yian/MakeNTU

python3 frdm_uart_context_sender/focus_work_mode.py --self-test

env PYTHONPATH=/home/asrlab-yian/MakeNTU/emotion_robot_controller/voice_stt_remote \
python3 emotion_robot_controller/voice_stt_remote/desktop_fast_chat_server.py --self-test

python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py --self-test
```

desktop server 指令需要 `PYTHONPATH`，避免從不同工作目錄執行時找不到本地相依模組。

## 測試路線

建議照這個順序測：

1. 先跑 self-test，確認 parser、report、UART dry-run、focus intent 都正常。
2. 跑 mock session，不碰相機、不碰 server，確認 log/report 會產生。
3. 跑真相機 + `/focus-check`，但先保留 `--uart-dry-run`。
4. 最後跑主 wake bridge，從 `Hey Jarvis` 喚醒後說「開始專心工作」。

測試階段先不要移除 `--uart-dry-run`。確認流程正確後，再真的送 FRDM UART。

## 不用相機的快速測試

用 mock state 測 log/report/UART 流程：

```bash
cd /home/asrlab-yian/MakeNTU

python3 frdm_uart_context_sender/focus_work_mode.py \
  --mock-state phone \
  --once \
  --uart-dry-run \
  --log-root /tmp/focus_test
```

查看輸出：

```bash
find /tmp/focus_test -type f
```

應該會看到：

```text
focus_log.jsonl
focus_report.md
session.json
```

## 真相機 + Vision Server 測試

先在桌面端或 server 端啟動新版 `desktop_fast_chat_server.py`：

```bash
cd /home/asrlab-yian/MakeNTU

env PYTHONPATH=/home/asrlab-yian/MakeNTU/emotion_robot_controller/voice_stt_remote \
python3 emotion_robot_controller/voice_stt_remote/desktop_fast_chat_server.py \
  --host 0.0.0.0 \
  --port 8766 \
  --skip-asr-load
```

Jetson 端跑一次拍照判斷：

```bash
cd /home/asrlab-yian/MakeNTU

python3 frdm_uart_context_sender/focus_work_mode.py \
  --server-url http://100.108.141.26:8766/focus-check \
  --once \
  --uart-dry-run
```

測短 session，例如每 20 秒取樣一次、總共跑 1 分鐘：

```bash
python3 frdm_uart_context_sender/focus_work_mode.py \
  --server-url http://100.108.141.26:8766/focus-check \
  --interval-sec 20 \
  --duration-min 1 \
  --uart-dry-run
```

確認 UART 指令正確後，再移除 `--uart-dry-run` 讓它真的送 FRDM UART。

## 語音整合測試

wake bridge 的語音啟動/停止功能已經 merge 到主專案最新版，不需要再從這個資料夾複製 wake bridge。

正式整合後的對應路徑如下：

```text
frdm_uart_context_sender/focus_work_mode.py
emotion_robot_controller/voice_stt_remote/desktop_fast_chat_server.py
emotion_robot_controller/voice_stt_remote/windows_desktop_server_bundle/desktop_fast_chat_server.py
```

套回後，啟動 wake bridge 時可調整 work mode 參數：

```bash
python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py \
  --focus-interval-sec 180 \
  --focus-duration-min 0 \
  --focus-server-url http://100.108.141.26:8766/focus-check
```

`--focus-duration-min 0` 代表不自動結束，要再用語音指令「結束工作」或「停止專心」切回一般模式。

### Terminal 手動測試

Terminal 1 在 Windows PowerShell 跑新版 desktop server。正式 Windows bundle 已包含 `/focus-check`：

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\Desktop\windows_desktop_server_bundle" | Out-Null

scp asrlab-yian@100.110.90.72:/home/asrlab-yian/MakeNTU/emotion_robot_controller/voice_stt_remote/windows_desktop_server_bundle/desktop_fast_chat_server.py "$env:USERPROFILE\Desktop\windows_desktop_server_bundle\desktop_fast_chat_server.py"

cd "$env:USERPROFILE\Desktop\windows_desktop_server_bundle"
.\.venv\Scripts\Activate.ps1
python desktop_fast_chat_server.py --self-test
python desktop_fast_chat_server.py --host 0.0.0.0 --port 8766 --ollama-model qwen35-fast:latest --vision-model qwen35-fast:latest --no-think
```

如果 `scp` 連不到 Jetson，先在 Jetson 跑 `tailscale ip -4`，再把 `100.110.90.72` 換成目前 Jetson Tailscale IP。

Terminal 2 在 Jetson 跑主 wake bridge。這個版本會等 `Hey Jarvis`，喚醒後進入錄音監聽：

```bash
cd /home/asrlab-yian/MakeNTU
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate

python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --mic-keyword UACDemo \
  --uart-port auto \
  --uart-dry-run \
  --no-tts \
  --no-tts-preflight \
  --no-camera \
  --wake-threshold 0.75 \
  --wake-volume-min 350 \
  --volume-min 700 \
  --silence-duration 1.2 \
  --silence-margin 650 \
  --max-speech-seconds 5 \
  --max-recording-seconds 7 \
  --audio-read-timeout 0.75 \
  --recording-progress-interval 1.0 \
  --focus-script /home/asrlab-yian/MakeNTU/frdm_uart_context_sender/focus_work_mode.py \
  --focus-server-url http://100.108.141.26:8766/focus-check \
  --focus-interval-sec 20 \
  --focus-duration-min 0 \
  --focus-log-root /tmp/focus_voice_test \
  --focus-alert-threshold 1 \
  --no-music \
  --no-weather \
  --uart-debug
```

測試語句：

```text
Hey Jarvis
開始專心工作 1 分鐘 測試分心偵測

Hey Jarvis
結束工作
```

Terminal 3 看 log/report：

```bash
find /tmp/focus_voice_test -type f | sort
tail -f /tmp/focus_voice_test/focus_*/focus_log.jsonl
sed -n '1,180p' /tmp/focus_voice_test/focus_*/focus_report.md
```

也可以直接說：

```text
開始專心工作 25 分鐘 寫 UART 報告
結束工作，切回一般模式
```

## 常用參數

`focus_work_mode.py`：

- `--interval-sec 180`：取樣間隔，預設 180 秒。
- `--duration-min 25`：自動結束時間，不填則直到手動停止。
- `--once`：只取樣一次後產生報告。
- `--mock-state phone`：不用相機/server，直接模擬狀態。
- `--uart-dry-run`：印出 UART 指令，不真的送。
- `--no-uart`：完全不送 UART。
- `--save-images`：debug 用，保存取樣照片。
- `--log-root PATH`：指定 log/report 輸出資料夾。

`wake_voice_chat_frdm_bridge.py`：

- `--no-focus-mode`：關閉語音觸發 work mode。
- `--focus-script PATH`：指定要啟動的 `focus_work_mode.py`。
- `--focus-server-url URL`：指定 `/focus-check`。
- `--focus-interval-sec 180`：work mode 取樣間隔。
- `--focus-duration-min 0`：預設自動結束時間，0 表示手動結束。
- `--focus-log-root PATH`：work mode log/report 輸出位置。
- `--focus-save-images`：讓 focus script 保存照片，僅 debug 使用。

## 注意事項

- 這個資料夾不是 branch，也不是完整副本，只是一包待套用的變動檔案。
- 主專案多人同時修改時，套用前要先比對目前主檔案，避免覆蓋別人的更新。
- `wake_voice_chat_frdm_bridge.py` 已經在主專案中手動 merge focus hook；不要再用舊版整支覆蓋。
- 測試階段建議先使用 `--uart-dry-run`，確認流程和 log/report 都正常後再真的送 UART。
