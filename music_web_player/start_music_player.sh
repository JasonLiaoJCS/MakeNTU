#!/usr/bin/env bash
set -euo pipefail

cd /home/asrlab-yian/MakeNTU/music_web_player

python3 music_web_player.py \
  --server \
  --host 127.0.0.1 \
  --port 8788 \
  --backend "${MUSIC_PLAYER_BACKEND:-browser}"
