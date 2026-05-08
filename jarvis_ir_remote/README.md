# Jarvis IR Remote

這個資料夾是 Jetson 上的紅外線遙控學習/發射工具，獨立於 `frdm_uart_context_sender`。它可以先把遙控器按鈕的 IR raw timing 存起來，之後用語音文字找回同一組訊號並發射出去。

正式接進 JAVIS 時，請用 `wake_voice_chat_ir_bridge.py`。它以 `/home/asrlab-yian/MakeNTU/frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py` 為主幹，保留原本 wake word、錄音、ASR、TTS、FRDM UART、music/weather/focus/to-do 流程，只是在本機工具層多加 IR learning/sending routing。原始 `frdm_uart_context_sender` 檔案不會被修改。

## 硬體概念

```text
IR 接收模組 OUT -> Jetson RX GPIO
IR 發射模組 IN  -> Jetson TX GPIO
VCC/GND          -> 依模組規格接 3.3V 或 5V/GND
```

預設 pin 使用 Jetson.GPIO 的 `BOARD` 編號：

```text
RX pin: 18
TX pin: 32
carrier: 38 kHz
receiver: active-low
transmitter: active-high
```

如果你的接收模組 idle 是低電位，用 `--rx-active-high`。如果你的發射模組低電位才點亮，用 `--tx-active-low`。

## 安裝

```bash
cd /home/asrlab-yian/MakeNTU/jarvis_ir_remote
python3 -m pip install -r requirements.txt
```

Jetson GPIO 有時需要 root 或 GPIO 權限。第一次測試若遇到 permission error，可以先用 `sudo -E python3 ...` 驗證硬體。

## 先跑不碰硬體的測試

```bash
python3 jarvis_ir_remote.py self-test
python3 wake_voice_chat_ir_bridge.py --self-test
```

第二個指令會先跑原 Wake Bridge self-test，再跑 IR parser/store/routing self-test。

## 學習一個按鈕

對 Jarvis 說的句子可以直接拿來當 label 來源：

```bash
python3 jarvis_ir_remote.py learn "這個按鈕是控制電風扇的" --overwrite
```

流程：

```text
1. Jetson 逼一聲
2. 把原本遙控器對準 IR 接收模組
3. 按下遙控器按鈕
4. 工具把 timing 存進 jarvis_ir_remote/ir_codes.json
```

也可以手動指定 label 和 alias：

```bash
python3 jarvis_ir_remote.py learn --label "電風扇" --alias "風扇" --alias "開風扇" --overwrite
```

## 發射已學過的按鈕

```bash
python3 jarvis_ir_remote.py send "幫我開電風扇"
```

先只確認會配對到哪個按鈕，不真的發射：

```bash
python3 jarvis_ir_remote.py send "幫我開電風扇" --dry-run
```

列出目前學過的按鈕：

```bash
python3 jarvis_ir_remote.py list
```

刪除一個按鈕：

```bash
python3 jarvis_ir_remote.py delete "電風扇"
```

## 給 JAVIS 串接的 HTTP 工具

這個 HTTP sidecar 仍可單獨測試，但正式 JAVIS 建議直接使用 `wake_voice_chat_ir_bridge.py`，不必多開 IR sidecar。

啟動本機 sidecar：

```bash
python3 jarvis_ir_remote.py server --host 127.0.0.1 --port 8790
```

健康檢查：

```bash
curl http://127.0.0.1:8790/health
```

學習：

```bash
curl -X POST http://127.0.0.1:8790/text \
  -H 'Content-Type: application/json' \
  -d '{"text":"這個按鈕是控制電風扇的","overwrite":true}'
```

發射：

```bash
curl -X POST http://127.0.0.1:8790/text \
  -H 'Content-Type: application/json' \
  -d '{"text":"幫我開電風扇"}'
```

如果要從其他程式接進來，建議只在 transcript 明確含有「這個按鈕/學習/記住/紅外線」或已學過設備名稱時，POST 到 `http://127.0.0.1:8790/text`。

## 以 Wake Bridge 為主幹的正式用法

啟動順序照 `frdm_uart_context_sender/QUICK_START.md`：

```text
Terminal 1 on Windows : desktop_fast_chat_server.py
Terminal 2 on Jetson  : jetson_piper_tts.server
Terminal 4 on Jetson  : music_web_player.py
Terminal 3 on Jetson  : wake_voice_chat_ir_bridge.py
```

Terminal 3 只要把原本這行：

```bash
python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py \
```

改成：

```bash
python3 jarvis_ir_remote/wake_voice_chat_ir_bridge.py \
```

其餘 Wake Bridge 參數照原本貼上。IR 預設會啟用：

```text
codes path : jarvis_ir_remote/ir_codes.json
RX pin     : BOARD 18
TX pin     : BOARD 32
carrier    : 38 kHz
```

常用 IR 參數：

```bash
  --ir-codes-path /home/asrlab-yian/MakeNTU/jarvis_ir_remote/ir_codes.json \
  --ir-rx-pin 18 \
  --ir-tx-pin 32 \
  --ir-pin-mode BOARD \
  --ir-learn-overwrite \
  --ir-debug
```

如果只想先驗證語音會配對到已學過的按鈕，但不要真的發射 GPIO，加：

```bash
  --ir-dry-run
```

語音流程：

```text
Hey Jarvis，這個按鈕是控制電風扇的
-> Windows ASR 回 transcript
-> Jetson IR local routing 命中 learn
-> Jetson 逼一聲
-> 按下實體遙控器按鈕
-> IR timing 存進 ir_codes.json
-> TTS 回覆已記住

Hey Jarvis，幫我開電風扇
-> Windows ASR 回 transcript
-> Jetson IR local routing 找到電風扇
-> IR TX 發射同一組 timing
-> TTS 回覆已送出
```

IR routing 的優先序放在 to-do / focus 後面、weather/music/general AI 前面。一般聊天例如「講個笑話」不會被 IR 接走；只有明確學習語句，或已學過 label/alias 能配對到的控制語句，才會進 IR。

## 注意事項

這版使用 Python 在 GPIO 上產生 38 kHz carrier，對常見 NEC 類遙控器通常夠用，但不是硬即時。若某些冷氣或複雜遙控器不穩，之後可以把同一份 JSON timing 改接到 LIRC、Arduino、FRDM 或其他 MCU 產生更穩的 carrier。

接收端要用常見的 38 kHz IR receiver module，輸出是 demodulated digital pulse。單純 photodiode/raw sensor 不適合這個腳本。
