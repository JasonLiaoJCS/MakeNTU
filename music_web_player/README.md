# MakeNTU Music Web Player

這是一個全新的旁路工具，不會修改現有 wake / TTS / UART bridge。

用途：

```text
語音 transcript
-> 判斷是不是「播放音樂」意圖
-> 抽出歌名 / 歌手 / 關鍵字
-> 用網路串流/搜尋服務找歌
-> 播放或打開搜尋頁
```

## 模式

### browser 模式，預設、最穩

打開 YouTube Music 搜尋頁，不下載、不儲存音樂。這個模式最不容易壞，也最適合先測流程。

```bash
python3 music_web_player.py --text "幫我播放周杰倫 稻香" --backend browser
```

### mpv 模式，直接播第一個搜尋結果

需要 `mpv` 和 `yt-dlp`。這個模式會用 `mpv` 串流播放 `ytsearch1:<歌名>` 的第一個搜尋結果，不會把音樂存到專案資料夾。

```bash
python3 -m pip install -U yt-dlp
python3 music_web_player.py --text "play never gonna give you up" --backend mpv
```

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

開新的 Terminal，例如 Terminal 4：

```bash
cd /home/asrlab-yian/MakeNTU/music_web_player
python3 music_web_player.py \
  --server \
  --host 127.0.0.1 \
  --port 8788 \
  --backend browser
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

強制指定 query：

```bash
curl -X POST http://127.0.0.1:8788/music \
  -H "Content-Type: application/json" \
  -d '{"query":"周杰倫 稻香","backend":"browser"}'
```

停止 mpv：

```bash
curl -X POST http://127.0.0.1:8788/music \
  -H "Content-Type: application/json" \
  -d '{"action":"stop"}'
```

## 要怎麼接進你的本地 AI

你現在前面的本地 AI 已經會聽、會回、會 TTS。下一步最乾淨的接法是讓 Windows server 或 Jetson 中間層多一個 tool routing：

```text
transcript = ASR 結果
if music_web_player 判斷 intent=True:
    POST http://127.0.0.1:8788/music {"text": transcript}
    reply = "好，我幫你找這首。"
else:
    原本聊天流程
```

如果不想讓 rule-based 判斷，也可以要求本地 AI 在需要播歌時輸出 tool call：

```json
{
  "reply": "好，我幫你找這首。",
  "tool": {
    "name": "music_player",
    "text": "幫我播放周杰倫 稻香"
  }
}
```

然後 server 看到 `tool.name == "music_player"` 就呼叫：

```bash
POST http://127.0.0.1:8788/music
```

## 支援語句

會判斷為播放：

```text
幫我播放周杰倫 稻香
我想聽告白氣球這首歌
放一下 lofi music
來一首
play never gonna give you up
put on some jazz
```

會判斷為停止：

```text
停止音樂
不要播了
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
3. 若你要完全自動播，安裝 `yt-dlp`，改用 `--backend mpv`。
4. 最後再把 Windows server 或 Jetson bridge 接到 `POST /music`。

