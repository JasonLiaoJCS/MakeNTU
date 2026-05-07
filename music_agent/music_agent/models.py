from dataclasses import dataclass
from typing import Optional


@dataclass
class MusicIntent:
    action: str
    query: Optional[str] = None
    artist: Optional[str] = None


@dataclass
class SongCandidate:
    title: str
    artist: str
    source_url: str
    duration_sec: Optional[int] = None
    confidence: float = 0.0
