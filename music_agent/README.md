# Music Agent

This folder contains a standalone music search and playback agent for Jetson Nano voice workflows.

## Goal
- Detect music-related intents from existing LLM/STT output
- Search song candidates from online sources
- Control playback with a local player backend

## Folder Layout
- `music_agent/main.py`: entrypoint for wiring
- `music_agent/intent_router.py`: maps text to intents
- `music_agent/search_provider.py`: song search interface
- `music_agent/player_controller.py`: playback control interface
- `music_agent/models.py`: shared data models
- `music_agent/settings.py`: runtime settings
- `tests/test_smoke.py`: basic import and wiring test

## Next Step
Implement provider and player adapters, then integrate with your existing voice loop.

## Wake Bridge Integration
`frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py` can now call this music agent.

Example (using playerctl as backend):

```bash
python3 frdm_uart_context_sender/wake_voice_chat_frdm_bridge.py \
	--server-url http://DESKTOP_IP:8766/voice-chat \
	--enable-music-agent \
	--music-play-cmd "python3 /home/asrlab-yian/MakeNTU/music_agent/play_youtube_music.py {query}" \
	--music-pause-cmd "playerctl pause" \
	--music-resume-cmd "playerctl play" \
	--music-next-cmd "playerctl next" \
	--music-stop-cmd "playerctl stop"
```

Notes:
- `--music-play-cmd` must be provided for play requests.
- The `{query}` placeholder will be replaced by the spoken transcript query.
- Music control is optional and disabled by default.
- `play_youtube_music.py` requires `yt-dlp` and either `mpv` or `cvlc`.
