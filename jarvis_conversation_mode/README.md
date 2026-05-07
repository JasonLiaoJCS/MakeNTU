# Jarvis Conversation Mode

這個資料夾是獨立的新版本，用來處理「只在一開始說 Hey Jarvis，後面多輪對話不用重複喚醒詞」。

它不會修改其他資料夾的程式。目前會沿用既有桌機 `/voice-chat` server 做 ASR + Ollama 回覆，沿用 Jetson Piper TTS 播放回覆。

新版主幹參考：

```text
/home/asrlab-yian/MakeNTU/frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py
```

錄音核心保留原 bridge 的 callback queue、自適應底噪門檻、音量 gate、`wake_volume_min`、`max_recording_seconds`、`audio_read_timeout` 這些設計。差別是：第一次用 `hey_jarvis` 喚醒，後續 follow-up turn 不再要求喚醒詞。

這個 standalone 版本只處理「wake + 多輪對話錄音 + 上傳 /voice-chat + TTS 等待完成」。為了之後好合併，它會接受一批原 bridge 的參數名稱，例如 `--camera-*`、`--music-*`、`--weather-*`、`--uart-*`，但目前不會真的執行 camera、music、weather 或 FRDM UART。

## 流程

```text
standby
  -> 只聽 hey_jarvis
  -> 喚醒成功後進入 conversation session
  -> 聽使用者一句話
  -> 偵測到你停止說話後，自動判斷這一句結束
  -> 上傳音訊到 /voice-chat
  -> 播放 AI reply
  -> 播完後繼續聽下一句，不需要再說 hey_jarvis
  -> 使用者說「掰掰 / 拜拜 / bye bye」或一段時間沒講話，就回到 standby
  -> standby 只跑喚醒詞偵測；一般講話不會送 ASR，也不會送桌機 AI
```

## 結束判斷

這裡分成兩種「結束」：

`一句話結束`

你開始講話後，如果音量降到靜音門檻以下並持續 `--silence-duration` 秒，程式就會判斷你停止說話，立刻停止錄音並送去辨識。

`整段對話結束`

只要 ASR transcript 裡出現 `掰掰`、`拜拜`、`白白`、`bye bye`、`buy buy`、`good bye`、`結束對話`、`不用聽了` 這類結束詞，會立刻結束 conversation session，回到 standby。預設不會再念 AI 的告別回覆，這樣說完 `byebye` 後可以最快回到只聽喚醒詞。下一次要重新說 `Hey Jarvis`。

如果 AI 回覆完後 `--turn-listen-timeout` 秒內沒有偵測到新的有效人聲，也會判斷這段對話結束，回到 standby，只聽 `hey_jarvis`。

回到 standby 後，你講任何沒有包含喚醒詞的話都只會被本機 wake word detector 掃過，不會被錄成一輪對話，不會上傳 `/voice-chat`，也不會讓桌機 ASR/Ollama 回答。Terminal 會看到類似：

```text
Returning to standby. Say Hey Jarvis to start a new conversation.
Wake-only standby restored. Say Hey Jarvis before speaking again.
Listening for wake word 'hey_jarvis'
```

這時候如果你隨便講話，正常狀況是不會看到 `POST audio to ...`。下一次必須再說 `Hey Jarvis` 才會重新進入對話。

## 安裝

建議沿用原本完整 bridge 使用的 voice venv：

```bash
cd /home/asrlab-yian/MakeNTU/jarvis_conversation_mode
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate
python3 -m pip install -r requirements.txt
```

這版不使用 `webrtcvad`，而是跟原本完整 bridge 一樣，主要靠自適應音量 gate 判斷你是否開始/停止說話。這樣 Jetson 端判斷更單純，延遲也比較可控。

## 執行

先列出麥克風：

```bash
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate
python3 jarvis_conversation_loop.py --list-mics
```

正式跑：

```bash
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate

python3 jarvis_conversation_loop.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --tts-url http://127.0.0.1:8777/speak_async \
  --mic-keyword UACDemo \
  --turbo-response \
  --quiet-dialog \
  --wake-threshold 0.75 \
  --wake-volume-min 350 \
  --volume-min 700 \
  --silence-margin 650 \
  --tts-debug
```

如果你只是想用原本 Terminal 3 那串指令快速改過來測，也可以保留很多原 bridge 參數；這個 standalone 版本會接受但忽略 camera/music/weather/UART 相關動作，方便之後合併。

如果沒有 TTS server，先用純文字模式測：

```bash
python3 jarvis_conversation_loop.py \
  --server-url http://100.108.141.26:8766/voice-chat \
  --no-tts \
  --mic-keyword UACDemo
```

## 重要參數

`--fast-response`

低延遲測試用 preset，會自動套用：

```text
silence_duration=0.75
max_speech_seconds=6
max_recording_seconds=9
turn_listen_timeout=6
session_idle_timeout=24
tts_poll_interval=0.35
post_tts_settle_seconds=0.15
```

`--turbo-response`

比 `--fast-response` 更積極的低延遲 preset，適合你現在想要「講完快點送出、快點回答」的測試：

```text
silence_duration=0.55
max_speech_seconds=5
max_recording_seconds=7
turn_listen_timeout=4
session_idle_timeout=18
tts_poll_interval=0.2
post_tts_settle_seconds=0.05
tts_length_scale=0.86
```

如果你講話中間會停很久，`--turbo-response` 可能比較容易提早切句；這時改回 `--fast-response`。

`--quiet-dialog`

Terminal 不顯示 transcript 和 AI reply，只保留 request id 與 timing。這不會讓桌機 ASR 本身大幅變快，但會讓 terminal 乾淨很多，也比較適合現場測。

`--speak-end-reply`

預設說 `byebye / 掰掰 / 拜拜` 後會跳過 TTS，直接回 standby。若你希望 Jarvis 還是念一句告別回覆，再加這個參數。

`--max-speech-seconds 20`

單句最長 20 秒，超過就強制送出。

`--max-recording-seconds 26`

從開始等待你說話算起的硬上限。這個值要比 `--max-speech-seconds` 大，避免環境音或 USB mic 狀態讓單輪卡住。

`--turn-listen-timeout 10`

AI 回覆後等你開始說話的時間。超過就結束這段 session，回到只聽喚醒詞。

`--no-wake-word`

只給測試用。它會跳過第一次 `Hey Jarvis`，直接用音量開始一段 session；這段 session 結束後程式會直接退出，避免你以為它已經回到正式 standby，卻還在不用喚醒詞的模式下聽你說話。正式測試「結束後必須重新喚醒」時不要加這個參數。

`--session-idle-timeout 45`

整段對話閒置多久後自動回 standby。

`--volume-min 700`

最基本的音量門檻。

`--speech-start-margin 350`

背景底噪加多少才算你開始說話。環境吵可調高。

`--silence-duration 1.2`

講話後靜音多久算一句話結束。

`--wake-volume-min 350`

喚醒詞也要有最低音量，避免低音量環境音觸發。

## 調參方向

如果環境音常被當成你講話：

```bash
--speech-start-margin 500 --silence-margin 800
```

如果你講話常常進不了錄音：

```bash
--speech-start-margin 250 --volume-min 500
```

如果 AI 回覆剛播完，麥克風錄到喇叭殘響：

```bash
--post-tts-settle-seconds 0.8
```

## 延遲判斷

看 log 卡在哪一段：

```text
一直停在 phase=speech
```

代表它還沒判斷你停止說話。先用 `--fast-response`，還是太慢就加：

```bash
--silence-duration 0.6 --silence-margin 800
```

```text
Max speech length reached
```

代表背景音或回音讓 silence 沒觸發，最後等到 `max_speech_seconds`。正式互動不要設 20 秒，低延遲建議 5 到 6 秒。

```text
Round trip: 很久
```

代表延遲在桌機 ASR/Ollama，不是在 Jetson 錄音。

```text
TTS started 後很久才下一輪
```

代表正在等 TTS 播完。這是避免 AI 自己的聲音被錄進麥克風；要再快一點可以降 `--post-tts-settle-seconds 0.1`。
