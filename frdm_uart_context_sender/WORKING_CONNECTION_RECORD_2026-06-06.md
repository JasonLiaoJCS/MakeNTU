# MakeNTU Working Connection Record - 2026-06-06

This records the Jetson-to-desktop Qwen flow that was verified on 2026-06-06
at night, Asia/Taipei time.

## Verified Topology

```text
Jetson
-> Windows/WSL desktop_fast_chat_server.py on port 8766
-> desktop Ollama / Qwen model
-> Jetson Piper TTS on port 8777
```

Verified desktop address from Jetson:

```text
http://192.168.1.122:8766
```

Working endpoints:

```text
Voice chat : http://192.168.1.122:8766/voice-chat
Focus check: http://192.168.1.122:8766/focus-check
TTS        : http://127.0.0.1:8777/speak_async
```

Important IP note:

- `192.168.1.122` was the working LAN IP for the desktop from the Jetson.
- `172.22.184.16` was the WSL internal IP shown by Flask. Do not use that from the Jetson.
- `100.108.141.26` timed out during this test and should be treated as stale for this setup.
- `192.168.1.127` is also stale for this run.

Verified model:

```text
ollama_model : qwen3.5:9b
vision_model : qwen3.5:9b
debug_version: 13
```

## Terminal 1: Desktop Server

Run this on the computer side, inside WSL:

```bash
cd /home/ktliu/MakeNTU/emotion_robot_controller/voice_stt_remote/windows_desktop_server_bundle

python3 -m venv .venv
source .venv/bin/activate

python desktop_fast_chat_server.py \
  --host 0.0.0.0 \
  --port 8766 \
  --ollama-url http://127.0.0.1:11434/api/chat \
  --ollama-model qwen3.5:9b \
  --vision-model qwen3.5:9b \
  --no-think
```

Expected healthy lines:

```text
Ollama warm-up done: ...
Vision routing: enabled=True, force=False, model=qwen3.5:9b
torch.cuda.is_available() = True
ASR device=cuda:0, dtype=bfloat16
Fast chat server listening on http://0.0.0.0:8766
```

Keep this terminal open.

## Terminal 2: Jetson Piper TTS

Run this on the Jetson:

```bash
cd /home/asrlab-yian/MakeNTU/jetson_piper_tts
source .venv/bin/activate

python -m jetson_piper_tts.server --host 0.0.0.0 --port 8777
```

Keep this terminal open.

## Terminal 3: Jetson Server Check

Run this on the Jetson:

```bash
cd /home/asrlab-yian/MakeNTU
source emotion_robot_controller/.venv/bin/activate

python emotion_robot_controller/voice_stt_remote/jetson_fast_voice_chat.py \
  --server-url http://192.168.1.122:8766/voice-chat \
  --check-server \
  --timeout 90
```

Expected healthy values:

```text
debug_version: 13
chat_ready   : True
asr_loaded   : True
ollama_model : qwen3.5:9b
TTS ready    : True
audio        : aplay device=plughw:CARD=UACDemoV10,DEV=0
```

The verified smoke test returned:

```text
Reply: 收到啦，我在這裡喔！
Timing total_ms: about 4465
```

## Speaker Test

After TTS health is OK, test actual audio output:

```bash
curl -X POST http://127.0.0.1:8777/speak_async \
  -H "Content-Type: application/json" \
  -d '{"text":"測試成功，我可以說話。","interrupt":true}'
```

If this returns OK but there is no sound, check the USB speaker/UACDemo output
and the TTS terminal logs.

## Full Wake Bridge Demo

Keep the desktop server and Jetson TTS terminals open. Then run:

```bash
cd /home/asrlab-yian/MakeNTU
source emotion_robot_controller/.venv/bin/activate

SERVER_URL=http://192.168.1.122:8766/voice-chat \
FOCUS_SERVER_URL=http://192.168.1.122:8766/focus-check \
./frdm_uart_context_sender/run_wake_bridge_full_demo.sh
```

Use the environment variables above so the demo does not accidentally use old
default IP values.

## Full Voice Endpoint Test Result

The `/voice-chat` path was verified by uploading the local `test.wav` from the
Jetson:

```text
POST http://192.168.1.122:8766/voice-chat audio=@test.wav
ok: true
transcript: 喂喂喂喂喂喂喂。
reply: 喂？怎麼啦？
ollama_model: qwen3.5:9b
asr_ms: about 29099
llm_ms: about 7363
total_ms: about 41771
```

This confirms that the real audio upload path reaches desktop ASR, Qwen, and
returns a usable reply to the Jetson.

## If The IP Changes

1. On Windows, find the active Wi-Fi/Ethernet IPv4 address with PowerShell:

```powershell
ipconfig
```

2. From Jetson, test the new address:

```bash
curl http://NEW_DESKTOP_IP:8766/health
```

3. If SSH from the desktop to Jetson is active, this can also hint at the
desktop LAN IP:

```bash
ss -tn | grep ':22'
```

Use the peer address that connects to Jetson port 22, then verify it with the
`curl /health` command above.

## Troubleshooting

`/health` timeout:

```text
Wrong desktop IP, desktop server not running, Windows firewall blocked, or WSL
server was not started with --host 0.0.0.0.
```

`TTS health check failed: connection refused`:

```text
Jetson Piper TTS is not running. Start Terminal 2.
```

`/text-chat` works but `/voice-chat` fails:

```text
The network and Qwen path are OK. Check ASR loading, microphone recording, WAV
upload, or desktop ASR logs.
```

Reply exists but there is no sound:

```text
Check Jetson TTS health, UACDemo speaker detection, and the TTS server terminal.
```
