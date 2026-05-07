from .intent_router import IntentRouter
from .settings import MusicAgentSettings


def build_music_agent() -> dict:
    settings = MusicAgentSettings()
    router = IntentRouter()

    return {
        "settings": settings,
        "router": router,
    }


if __name__ == "__main__":
    agent = build_music_agent()
    print("Music agent scaffold loaded.")
    print(agent)
