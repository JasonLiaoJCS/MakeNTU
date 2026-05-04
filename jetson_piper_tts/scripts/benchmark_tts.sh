#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if [[ -f .venv/bin/activate ]]; then
  source .venv/bin/activate
fi

python - <<'PY'
from jetson_piper_tts.config import get_settings
from jetson_piper_tts.piper_engine import TTSService

phrases = [
    "你好，我是桌面助手。",
    "我侦测到你正在工作，我会安静一点。",
    "抱歉，我刚刚没有成功处理这个指令，请再说一次。",
    "现在开始播放一段稍微长一点的测试句子，用来观察分句、合成时间和快取效果。",
]

service = TTSService(get_settings())

print(f"{'case':<6} {'chars':>5} {'chunks':>6} {'cache':>6} {'synth_ms':>9} {'total_ms':>9}")
for round_name in ("cold", "cache"):
    for text in phrases:
        result = service.speak(text, play=False, blocking=True)
        print(
            f"{round_name:<6} {len(text):>5} {len(result['chunks']):>6} "
            f"{result['cache_hits']:>6} {result['synth_ms']:>9} {result['total_ms']:>9}"
        )
PY
