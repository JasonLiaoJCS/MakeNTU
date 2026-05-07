# MakeNTU Local Tool Server: Music + Weather

這是一個 Jetson 本地工具 sidecar。新版 `wake_voice_chat_frdm_bridge.py` 已經會自動呼叫它：

```text
/music    使用者說「我想聽告白氣球」後，Wake Bridge 在 TTS 確認句結束後 POST 到 /music
/weather  使用者問「所在地天氣、明天、特定時間天氣」時，Wake Bridge POST 到 /weather
```

一般聊天、vision、FRDM UART、TTS 都照原本流程走；只有 transcript 被判斷成點歌、暫停、停止、換歌或天氣問題時，才會動這個 local tool server。
當 Music Player 正在播歌時，只要 Wake Bridge 偵測到 `Hey Jarvis`，就會先送 `pause`，讓音樂停下來再錄你的下一句話。
如果下一句不是音樂控制，音樂會維持暫停；要恢復請說「繼續播放音樂」。

用途：

```text
語音 transcript -> music intent -> 抽歌名 -> mpv/YouTube 搜尋串流播放
語音 transcript -> weather intent -> 抽時間/地點 -> Open-Meteo 查天氣 -> 回傳自然語句
```

## 最快開始

正式 demo 建議讓它當 Terminal 4 開著：

```bash
cd /home/asrlab-yian/MakeNTU/music_web_player
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate

python3 music_web_player.py \
  --server \
  --host 127.0.0.1 \
  --port 8788 \
  --backend mpv \
  --weather-default-location Taipei
```

開好後測：

```bash
curl http://127.0.0.1:8788/health

curl -X POST http://127.0.0.1:8788/music \
  -H "Content-Type: application/json" \
  -d '{"action":"play","query":"告白氣球","backend":"mpv"}'

curl -X POST http://127.0.0.1:8788/music \
  -H "Content-Type: application/json" \
  -d '{"action":"pause"}'

curl -X POST http://127.0.0.1:8788/music \
  -H "Content-Type: application/json" \
  -d '{"action":"resume"}'

curl -X POST http://127.0.0.1:8788/weather \
  -H "Content-Type: application/json" \
  -d '{"text":"明天下午三點所在地天氣如何","default_location":"Taipei"}'
```

Wake Bridge 端已經會自動呼叫這個 endpoint。你平常不需要手動 POST，除非正在測 Music Player 本身。

`/health` 欄位判讀：

```text
backend=mpv          正式播放模式；browser 只開搜尋頁
mpv_available=true   系統找得到 mpv
yt_dlp_available=true 系統找得到 yt-dlp/youtube-dl
active=true          目前有 mpv process
paused=true          目前暫停中，可以 resume
last_query=...       最近一次點的歌
ipc_path=/tmp/...    mpv IPC socket，pause/resume 需要它
weather_available=true /weather 已載入
weather_source=open-meteo 天氣來源
weather_default_location=Taipei 所在地預設城市
```

## 模式

### weather 模式，查 Open-Meteo 天氣

天氣不靠 LLM 猜，也不需要 API key。流程是：

```text
text='明天下午三點台北天氣如何'
-> detect_weather_intent
-> geocoding API 把 台北 轉成 latitude/longitude/timezone
-> forecast API 取 current/hourly/daily
-> 回傳 reply 給 Wake Bridge TTS
```

一次性測試：

```bash
python3 music_web_player.py \
  --weather "明天下午三點台北天氣如何" \
  --weather-default-location Taipei
```

常用 HTTP 測試：

```bash
curl -X POST http://127.0.0.1:8788/weather \
  -H "Content-Type: application/json" \
  -d '{"text":"所在地天氣如何","default_location":"Taipei"}'

curl -X POST http://127.0.0.1:8788/weather \
  -H "Content-Type: application/json" \
  -d '{"text":"明天會下雨嗎","default_location":"Taipei"}'

curl -X POST http://127.0.0.1:8788/weather \
  -H "Content-Type: application/json" \
  -d '{"text":"weather in Tokyo tomorrow","default_location":"Taipei"}'
```

支援：

```text
所在地 / 這裡 / here -> 使用 --weather-default-location
今天 / 明天 / 後天
早上 / 中午 / 下午 / 晚上
三點 / 15:00 / 3pm
台北、新竹、台中、高雄、東京、英文地名
```

回傳重點欄位：

```text
ok=true
handled=true
reply=給 TTS 念的自然語句
location=解析後地點
target.kind=current/hourly/daily
source=open-meteo
weather=結構化天氣摘要
```

### browser 模式，只開搜尋頁

打開 YouTube Music 搜尋頁，不下載、不儲存音樂。這個模式最不容易壞，也最適合先測流程，但通常不會自動開始播放。

```bash
python3 music_web_player.py --text "幫我播放周杰倫 稻香" --backend browser
```

### mpv 模式，直接播第一個搜尋結果

需要 `mpv` 和 `yt-dlp`。這個模式會用 `mpv` 串流播放 `ytsearch1:<歌名>` 的第一個搜尋結果，不會把音樂存到專案資料夾。

```bash
python3 -m pip install -U yt-dlp
python3 music_web_player.py --text "play never gonna give you up" --backend mpv
```

你的 Jetson 目前已經有 `mpv` 和 `yt-dlp` 時，正式 demo 建議使用 `mpv`。

請只播放你有權播放的內容，正式產品建議接 YouTube/Spotify/Apple Music 的官方 API 或使用者已登入的串流服務。

## 先跑 Self-Test

```bash
cd /home/asrlab-yian/MakeNTU/music_web_player
python3 music_web_player.py --self-test
```

成功：

```text
music_web_player self-test OK
```

## 一次性文字測試

只判斷，不真的打開播放器：

```bash
python3 music_web_player.py \
  --text "Hey Jarvis 幫我播放周杰倫 稻香" \
  --dry-run
```

預期：

```json
{
  "intent": true,
  "action": "play",
  "query": "周杰倫 稻香",
  "handled": true
}
```

實際開瀏覽器搜尋：

```bash
python3 music_web_player.py \
  --text "我想聽告白氣球這首歌" \
  --backend browser
```

實際用 mpv 播放：

```bash
python3 music_web_player.py \
  --text "我想聽告白氣球這首歌" \
  --backend mpv
```

## 跑成 HTTP Sidecar

開新的 Terminal，例如 Terminal 4。Wake Bridge 也會在需要時自動嘗試啟動 sidecar，但正式 demo 建議手動開著看 log。

```bash
cd /home/asrlab-yian/MakeNTU/music_web_player
source /home/asrlab-yian/MakeNTU/emotion_robot_controller/.venv/bin/activate

python3 music_web_player.py \
  --server \
  --host 127.0.0.1 \
  --port 8788 \
  --backend mpv \
  --weather-default-location Taipei
```

測 health：

```bash
curl http://127.0.0.1:8788/health
```

測播放意圖：

```bash
curl -X POST http://127.0.0.1:8788/music \
  -H "Content-Type: application/json" \
  -d '{"text":"Hey Jarvis 幫我播放周杰倫 稻香"}'
```

測天氣意圖：

```bash
curl -X POST http://127.0.0.1:8788/weather \
  -H "Content-Type: application/json" \
  -d '{"text":"明天下午三點所在地天氣如何","default_location":"Taipei"}'
```

強制指定 query，Wake Bridge 目前就是用這種方式把已解析好的歌名送過來：

```bash
curl -X POST http://127.0.0.1:8788/music \
  -H "Content-Type: application/json" \
  -d '{"action":"play","query":"周杰倫 稻香","backend":"mpv"}'
```

暫停 / 繼續 / 停止 mpv：

```bash
curl -X POST http://127.0.0.1:8788/music \
  -H "Content-Type: application/json" \
  -d '{"action":"pause"}'

curl -X POST http://127.0.0.1:8788/music \
  -H "Content-Type: application/json" \
  -d '{"action":"resume"}'

curl -X POST http://127.0.0.1:8788/music \
  -H "Content-Type: application/json" \
  -d '{"action":"stop"}'
```

`pause` / `resume` 透過 mpv IPC socket 控制，所以會保留播放位置。`stop` 才會結束 mpv process；stop 之後不能從同一秒 resume，只能重新點歌。

Action 對照：

```text
play   : 需要 query；會播放 ytdl://ytsearch1:<query>，並取代上一首
pause  : 不需要 query；保留播放位置
resume : 不需要 query；從暫停位置繼續
stop   : 不需要 query；結束 mpv process，不能從原秒數繼續
```

## 已接進 Wake Bridge 的流程

目前 Jetson Wake Bridge 已經內建 music routing：

```text
transcript = ASR 結果
-> detect_weather_intent(transcript)
-> if weather: POST http://127.0.0.1:8788/weather, replace AI reply with weather reply
-> detect_music_intent(transcript)
-> wake 一偵測到先 POST {"action": "pause"}，避免音樂進錄音
-> action=play 時，先讓 TTS 說完「好，我幫你放」
-> action=resume 時，先讓 TTS 說完「好，繼續播放」
-> POST http://127.0.0.1:8788/music {"action": "play", "query": "歌名", "backend": "mpv"}
-> 或 POST http://127.0.0.1:8788/music {"action": "resume"}
-> mpv 串流播放第一個搜尋結果
-> bridge 回 standby，繼續聽下一次 Hey Jarvis
```

Wake Bridge 相關參數：

```text
--music-backend mpv
--music-url http://127.0.0.1:8788/music
--music-timeout 5
--music-wake-pause-timeout 0.6
--music-debug
--no-music
--no-music-autostart
--no-music-pause-on-wake
--weather-url http://127.0.0.1:8788/weather
--weather-default-location Taipei
--weather-timeout 6
--weather-api-timeout 5
--weather-debug
--no-weather
```

## 支援語句

會判斷為播放：

```text
幫我播放周杰倫 稻香
我想聽告白氣球這首歌
我想要听《告白气球》
幫我波 稻香
換成 七里香
改播 告白氣球
放一下 lofi music
來一首
play never gonna give you up
put on some jazz
```

會判斷為播放控制：

```text
暫停音樂
先暫停
繼續播放音樂
恢復播放
停止音樂
不要播了
resume music
stop music
```

不會判斷為播放：

```text
今天幾號
講個笑話
解釋 PID 控制
```

## 建議流程

1. 先用 `--dry-run` 確認歌名抽取正確。
2. 再用 `--backend browser` 確認網路搜尋能打開。
3. 正式 demo 要真的開始播放，請用 `--backend mpv`。
4. 最後用 Hey Jarvis 測：「我想要聽告白氣球」。
