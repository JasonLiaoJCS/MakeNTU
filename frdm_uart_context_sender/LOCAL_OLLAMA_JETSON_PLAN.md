# Jetson Local Ollama Voice Mode

This is the v1 Jetson-only AI path. It keeps the existing desktop-PC flow
unchanged and adds a local sidecar that exposes the same `/voice-chat` API.

```text
Jetson wake bridge
-> http://127.0.0.1:8766/voice-chat
-> whisper.cpp local STT
-> Ollama local qwen3 text model / qwen2.5vl vision model
-> Jetson Piper TTS
-> FRDM UART / screen / head motor
```

The local sidecar now supports voice-turn camera vision with `qwen2.5vl`.
Text chat stays on the smaller `qwen3:1.7b-q4_K_M` by default because
`qwen2.5vl:3b-q4_K_M` is not stable as the primary Jetson model on Orin Nano
8GB. In local tests, Ollama reported that `qwen2.5vl:3b-q4_K_M` required about
8.4 GiB system memory, which is above the usable memory on this board during
the robot pipeline. The code keeps the camera upload and vision routing enabled,
but falls back to text response if the vision model cannot load. Focus vision
remains disabled by the local launcher to avoid periodic image analysis load.

## Difference From The Desktop-PC Flow

The original desktop-PC flow is not removed. This local mode is an additional
Jetson-only launcher and sidecar. Use the desktop-PC flow when you want the
larger PC model, PC-side ASR, or vision behavior that has already been verified.

Local v1 keeps these parts of the robot flow:

- Wake word standby and one-wake conversation mode.
- Jetson microphone recording.
- Jetson Piper TTS on `127.0.0.1:8777`.
- FRDM UART screen updates and head motor control.
- The existing bridge/tool routing that is still enabled by
  `run_wake_bridge_full_demo.sh`, such as local music/weather/to-do support.

Local v1 intentionally changes or disables these parts:

- No desktop PC server, no `192.168.x.x` server, and no Tailscale server.
  `SERVER_URL` is forced to `http://127.0.0.1:8766/voice-chat`.
- No PC-side Qwen ASR. Speech-to-text uses `whisper.cpp` on the Jetson.
- No local Qwen ASR in v1. Running Qwen ASR plus Ollama plus TTS on Jetson
  Orin Nano 8GB is too memory-risky for the first local version.
- No large desktop Ollama model by default. Local mode uses
  `qwen3:1.7b-q4_K_M` for text chat and `qwen2.5vl:3b-q4_K_M` for voice-turn
  image understanding, with `qwen3:0.6b` as the optional text fallback.
- Local voice-turn vision is enabled by default. The wake bridge captures a
  JPEG after speech ends and uploads it with `/voice-chat`; the sidecar only
  calls the vision path when the transcript or metadata asks for vision.
- Vision failure is non-fatal. If Ollama cannot load the local vision model,
  `/voice-chat` returns a normal text answer and includes `vision_error` in
  debug output.
- Focus vision remains disabled by default. The launcher passes
  `--no-focus-mode`, and `/focus-check` returns a disabled response.
- No periodic room-temperature UART by default. The launcher passes
  `--no-temp-room-uart`.
- ESP32 BLE is disabled by default to keep the local voice path quiet and
  deterministic. Enable it explicitly with `ESP32_BLE=1` only when the ESP32
  peripheral is powered and nearby.

In short: local v1 is meant to prove the full offline voice loop first. It is
not yet a feature-complete replacement for the desktop-PC flow.

## Required Local Assets

Ollama must be installed and running on the Jetson:

```bash
ollama --version
ollama serve
```

Pull the default local text LLM:

```bash
ollama pull qwen3:1.7b-q4_K_M
```

Pull the local vision model:

```bash
ollama pull qwen2.5vl:3b-q4_K_M
```

Optional lower-latency text fallback:

```bash
ollama pull qwen3:0.6b
```

Build or install whisper.cpp so `whisper-cli` exists:

```bash
command -v whisper-cli
```

Download a whisper.cpp model. Recommended first target:

```text
ggml-base.bin
```

If latency is too high, switch to:

```text
ggml-tiny.bin
```

Set the model path when launching:

```bash
export WHISPER_CPP_MODEL=/path/to/ggml-base.bin
```

If `whisper-cli` is not in `PATH`:

```bash
export WHISPER_CPP_BIN=/path/to/whisper-cli
```

## Check Mode

Run this before the full demo:

```bash
cd /home/asrlab-yian/MakeNTU
./frdm_uart_context_sender/run_jetson_local_ollama_pipeline.sh check
```

This verifies:

- Ollama is reachable on `127.0.0.1:11434`.
- `qwen3:1.7b-q4_K_M` exists in `ollama list`.
- `qwen2.5vl:3b-q4_K_M` exists in `ollama list` when local vision is enabled.
- `whisper-cli` exists.
- the whisper.cpp model file exists.
- Jetson Piper TTS is ready on `127.0.0.1:8777`.
- Jetson local AI sidecar is ready on `127.0.0.1:8766`.

## Start Full Local Demo

```bash
cd /home/asrlab-yian/MakeNTU
./frdm_uart_context_sender/run_jetson_local_ollama_pipeline.sh start
```

The launcher forces:

```text
SERVER_URL=http://127.0.0.1:8766/voice-chat
FOCUS_SERVER_URL=http://127.0.0.1:8766/focus-check
--no-focus-mode
--no-temp-room-uart
```

This prevents accidental calls to an external desktop IP.

## 本地瑞踏得啟動紀錄

Current local version startup command:

```bash
cd /home/asrlab-yian/MakeNTU
./frdm_uart_context_sender/run_jetson_local_ollama_pipeline.sh start \
  --session-idle-timeout 10 \
  --standby-progress-interval 0.5 \
  --idle-volume-print-min 0
```

Use this for the Jetson-only local Ollama flow. One `Hey Jarvis` starts
conversation follow-up, goodbye or 10 seconds without valid follow-up speech
returns to wake-only standby, and successful music play/resume exits follow-up
mode so the next command must start with the wake word again. On the next wake,
the bridge pauses active music before recording.

ESP32 BLE and room-temperature UART are disabled by default in this local v1
launcher. If the ESP32 peripheral is powered and you want to include it in the
demo, opt in explicitly:

```bash
ESP32_BLE=1 ./frdm_uart_context_sender/run_jetson_local_ollama_pipeline.sh start
```

## Local AI Sidecar Only

For narrow debugging, start the local AI server manually:

```bash
cd /home/asrlab-yian/MakeNTU
source emotion_robot_controller/.venv/bin/activate

python emotion_robot_controller/voice_stt_remote/jetson_local_ai_server.py \
  --host 127.0.0.1 \
  --port 8766 \
  --ollama-url http://127.0.0.1:11434/api/chat \
  --ollama-model qwen3:1.7b-q4_K_M \
  --fallback-ollama-model qwen3:0.6b \
  --vision-model qwen2.5vl:3b-q4_K_M \
  --stt-bin "${WHISPER_CPP_BIN:-whisper-cli}" \
  --stt-model "$WHISPER_CPP_MODEL"
```

Health:

```bash
curl http://127.0.0.1:8766/health | python -m json.tool
```

Text smoke test:

```bash
curl -X POST http://127.0.0.1:8766/text-chat \
  -H "Content-Type: application/json" \
  -d '{"text":"自然回我一句話"}' | python -m json.tool
```

Voice endpoint test with the existing local WAV:

```bash
curl -X POST http://127.0.0.1:8766/voice-chat \
  -F audio=@test.wav | python -m json.tool
```

Expected response fields:

```text
ok
request_id
transcript
reply
control
emotion
timing.asr_ms
timing.llm_ms
timing.total_ms
debug
```

## TTS Test

```bash
curl -X POST http://127.0.0.1:8777/speak_async \
  -H "Content-Type: application/json" \
  -d '{"text":"Jetson 本地模式測試成功。","interrupt":true}'
```

## Performance Tuning

Default local LLM options are intentionally conservative:

```text
memory_turns=8
num_ctx=2048
num_predict=120
temperature=0.5
keep_alive=10m
```

`memory_turns` controls how many recent user/assistant turns are included in
the next prompt. Each remembered turn includes both what the user said and what
the robot already replied, so the next answer can continue the thread instead
of repeating the same response. `num_ctx` controls the Ollama context window.
Larger values make the local model remember more, but they also increase RAM
use and latency on the Jetson.

The local sidecar now follows the desktop server's simpler prompt structure:

```text
最近對話（僅供理解代名詞與延續語意；本輪使用者原話優先）
使用者原話：本輪 STT 文字
```

This keeps the local behavior close to `desktop_fast_chat_server.py`, including
remembering both user turns and assistant replies. Vision turns are marked as
`含畫面` in memory.

When the Wake Bridge detects a conversation-end keyword such as `掰掰`,
`拜拜`, `再見`, or `bye`, it exits follow-up mode and calls the local sidecar:

```text
POST http://127.0.0.1:8766/memory/clear
```

That clears all previous local conversation memory, so the next wake word starts
a fresh context. The local sidecar also has a fallback safeguard that clears
memory if it directly receives a goodbye transcript.

For a longer-context local run:

```bash
JETSON_LOCAL_MEMORY_TURNS=12 \
JETSON_LOCAL_OLLAMA_NUM_CTX=4096 \
./frdm_uart_context_sender/run_jetson_local_ollama_pipeline.sh start
```

For a lower-latency local run:

```bash
JETSON_LOCAL_MEMORY_TURNS=4 \
JETSON_LOCAL_OLLAMA_NUM_CTX=2048 \
./frdm_uart_context_sender/run_jetson_local_ollama_pipeline.sh start
```

After changing memory/context settings, restart the local AI sidecar:

```bash
./frdm_uart_context_sender/run_jetson_local_ollama_pipeline.sh stop
./frdm_uart_context_sender/run_jetson_local_ollama_pipeline.sh start
```

If the complete round trip is slower than 15-25 seconds:

1. Switch STT from `ggml-base.bin` to `ggml-tiny.bin`.
2. Pull and use the smaller text-only LLM, with local vision disabled:

```bash
ollama pull qwen3:0.6b
JETSON_LOCAL_OLLAMA_MODEL=qwen3:0.6b \
JETSON_LOCAL_VISION=0 \
./frdm_uart_context_sender/run_jetson_local_ollama_pipeline.sh start
```

3. Reduce generated reply length:

```bash
JETSON_LOCAL_OLLAMA_NUM_PREDICT=80 \
./frdm_uart_context_sender/run_jetson_local_ollama_pipeline.sh start
```

## Troubleshooting

`ERROR: local Ollama model is missing`

```bash
ollama pull qwen3:1.7b-q4_K_M
```

`ERROR: local Ollama vision model is missing`

```bash
ollama pull qwen2.5vl:3b-q4_K_M
```

`vision_error` shows `model requires more system memory`

`qwen2.5vl:3b-q4_K_M` has been tested on this Jetson Orin Nano 8GB and can
fail before inference with an Ollama memory error. The voice/text path should
still continue. For a stable demo, either disable local vision:

```bash
JETSON_LOCAL_VISION=0 ./frdm_uart_context_sender/run_jetson_local_ollama_pipeline.sh start
```

or switch `JETSON_LOCAL_VISION_MODEL` to a smaller Ollama vision model after
pulling and testing one.

`ERROR: whisper.cpp binary not found`

Build whisper.cpp and export:

```bash
export WHISPER_CPP_BIN=/path/to/whisper-cli
```

`ERROR: whisper.cpp model not found`

Download a ggml model and export:

```bash
export WHISPER_CPP_MODEL=/path/to/ggml-base.bin
```

`port 8766 is occupied`

Another server is already using the local AI port. Stop the old process or run
the local AI server on another port and pass the matching `SERVER_URL`.

`/text-chat works but /voice-chat fails`

The LLM path is OK. Check `WHISPER_CPP_BIN`, `WHISPER_CPP_MODEL`, and the
`stt_stderr_preview` field in `/debug`.

`Reply exists but no sound`

Check Jetson Piper TTS:

```bash
curl http://127.0.0.1:8777/health | python -m json.tool
```

Repeated `ESP32 BLE unavailable` warnings

The local launcher should suppress this by default. If you explicitly enabled
ESP32, make sure the ESP32 peripheral is powered and advertising, or start the
local demo without `ESP32_BLE=1`.

## References

- Ollama Qwen3 model tags: https://ollama.com/library/qwen3/tags
- whisper.cpp: https://github.com/ggml-org/whisper.cpp
