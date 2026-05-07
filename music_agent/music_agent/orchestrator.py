from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from typing import Optional

from .intent_router import IntentRouter


@dataclass
class MusicActionResult:
    handled: bool
    action: str
    message: str


class CommandMusicOrchestrator:
    """Execute music actions using external commands provided by runtime args."""

    def __init__(
        self,
        *,
        play_cmd: str = "",
        pause_cmd: str = "",
        resume_cmd: str = "",
        next_cmd: str = "",
        stop_cmd: str = "",
        command_timeout_sec: float = 8.0,
    ) -> None:
        self.router = IntentRouter()
        self.play_cmd = play_cmd.strip()
        self.pause_cmd = pause_cmd.strip()
        self.resume_cmd = resume_cmd.strip()
        self.next_cmd = next_cmd.strip()
        self.stop_cmd = stop_cmd.strip()
        self.command_timeout_sec = max(1.0, float(command_timeout_sec))

    def handle_text(self, text: str) -> MusicActionResult:
        intent = self.router.parse(text)
        action = intent.action

        if action == "unknown":
            return MusicActionResult(handled=False, action=action, message="")

        if action == "play":
            cmd_template = self.play_cmd
            query = (intent.query or text).strip()
            if not cmd_template:
                return MusicActionResult(handled=False, action=action, message="")
            return self._run_command(
                cmd_template,
                query=query,
                success_message=f"好，幫你播放：{query}",
                background=True,
            )

        if action == "pause":
            if not self.pause_cmd:
                return MusicActionResult(handled=False, action=action, message="")
            return self._run_command(self.pause_cmd, success_message="已幫你暫停播放。")

        if action == "resume":
            if not self.resume_cmd:
                return MusicActionResult(handled=False, action=action, message="")
            return self._run_command(self.resume_cmd, success_message="已繼續播放。")

        if action == "next":
            if not self.next_cmd:
                return MusicActionResult(handled=False, action=action, message="")
            return self._run_command(self.next_cmd, success_message="已切到下一首。")

        if action == "stop":
            if not self.stop_cmd:
                return MusicActionResult(handled=False, action=action, message="")
            return self._run_command(self.stop_cmd, success_message="已停止播放。")

        return MusicActionResult(handled=False, action=action, message="")

    def _run_command(
        self,
        cmd_template: str,
        *,
        query: Optional[str] = None,
        success_message: str,
        background: bool = False,
    ) -> MusicActionResult:
        expanded = cmd_template.format(query=(query or ""))
        try:
            argv = shlex.split(expanded)
            if not argv:
                return MusicActionResult(handled=True, action="error", message="音樂命令是空的，請重新設定。")
            if background:
                subprocess.Popen(
                    argv,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            else:
                subprocess.run(
                    argv,
                    check=True,
                    timeout=self.command_timeout_sec,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            return MusicActionResult(handled=True, action="ok", message=success_message)
        except subprocess.TimeoutExpired:
            return MusicActionResult(handled=True, action="error", message="音樂命令逾時，請檢查播放器是否正常。")
        except Exception:
            return MusicActionResult(handled=True, action="error", message="音樂命令執行失敗，請檢查命令設定。")
