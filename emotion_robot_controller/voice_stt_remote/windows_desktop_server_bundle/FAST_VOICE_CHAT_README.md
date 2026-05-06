# Fast Voice Chat README

最新完整操作文件請看 Jetson 專案根目錄：

```text
/home/asrlab-yian/MakeNTU/emotion_robot_controller/voice_stt_remote/FAST_VOICE_CHAT_README.md
```

Windows 桌機最短啟動指令：

```powershell
cd C:\Users\User\Desktop\windows_desktop_server_bundle
.\.venv\Scripts\Activate.ps1
python desktop_fast_chat_server.py --host 0.0.0.0 --port 8766 --ollama-model qwen35-fast:latest --vision-model qwen35-fast:latest --no-think
```

Jetson 最短啟動指令：

```bash
cd /home/asrlab-yian/MakeNTU/emotion_robot_controller/voice_stt_remote
source .venv/bin/activate
python jetson_fast_voice_chat.py --server-url http://100.108.141.26:8766/voice-chat --device 0
```
