from .models import MusicIntent


class IntentRouter:
    def _extract_play_query(self, text: str) -> str:
        query = text.strip()
        prefixes = [
            "幫我",
            "帮我",
            "請",
            "请",
            "我要",
            "我想",
            "播放",
            "撥放",
            "放歌",
            "來一首",
            "来一首",
            "聽歌",
            "听歌",
            "play",
        ]
        changed = True
        while changed:
            changed = False
            compact = query.strip()
            for prefix in prefixes:
                if compact.lower().startswith(prefix.lower()):
                    query = compact[len(prefix) :].strip()
                    changed = True
                    break
        return query or text.strip()

    def parse(self, text: str) -> MusicIntent:
        lowered = text.lower().strip()

        if any(k in lowered for k in ["pause", "暫停", "暂停"]):
            return MusicIntent(action="pause")
        if any(k in lowered for k in ["stop", "停止播放", "停止音樂", "停止音乐", "停播"]):
            return MusicIntent(action="stop")
        if any(k in lowered for k in ["resume", "continue", "繼續", "继续", "恢復", "恢复"]):
            return MusicIntent(action="resume")
        if any(k in lowered for k in ["next", "下一首", "換一首", "换一首"]):
            return MusicIntent(action="next")
        if any(
            k in lowered
            for k in [
                "play",
                "music",
                "song",
                "播放",
                "撥放",
                "放歌",
                "聽歌",
                "听歌",
                "來一首",
                "来一首",
                "播",
            ]
        ):
            return MusicIntent(action="play", query=self._extract_play_query(text))

        return MusicIntent(action="unknown")
