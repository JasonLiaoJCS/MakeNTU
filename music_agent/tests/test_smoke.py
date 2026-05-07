from music_agent.main import build_music_agent
from music_agent.intent_router import IntentRouter
from music_agent.orchestrator import CommandMusicOrchestrator


def test_build_music_agent_returns_router() -> None:
    agent = build_music_agent()
    assert "router" in agent


def test_router_supports_chinese_play_command() -> None:
    router = IntentRouter()
    intent = router.parse("幫我播放周杰倫 晴天")
    assert intent.action == "play"
    assert intent.query == "周杰倫 晴天"


def test_router_supports_chinese_control_commands() -> None:
    router = IntentRouter()
    assert router.parse("暫停一下").action == "pause"
    assert router.parse("繼續播放").action == "resume"
    assert router.parse("下一首").action == "next"
    assert router.parse("停止播放").action == "stop"


def test_orchestrator_skips_unknown_without_error() -> None:
    orchestrator = CommandMusicOrchestrator()
    result = orchestrator.handle_text("今天天氣如何")
    assert result.handled is False
