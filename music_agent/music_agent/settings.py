from dataclasses import dataclass


@dataclass
class MusicAgentSettings:
    search_timeout_sec: int = 8
    max_candidates: int = 3
    default_volume: int = 70
