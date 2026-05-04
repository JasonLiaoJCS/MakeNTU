from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ..models import EmotionDecision
from .checksum import add_checksum


def _clean_field(value: object) -> str:
    text = str(value)
    if any(ch in text for ch in [",", "*", "$", "\n", "\r"]):
        raise ValueError(f"Invalid packet field: {text!r}")
    return text


@dataclass
class BuiltPacket:
    line: str
    seq: int
    command: str


class PacketBuilder:
    def __init__(self, start_seq: int = 1) -> None:
        self._seq = start_seq - 1

    def next_seq(self) -> int:
        self._seq = (self._seq + 1) % 100000
        if self._seq == 0:
            self._seq = 1
        return self._seq

    def build(self, command: str, fields: Iterable[object], seq: int | None = None) -> BuiltPacket:
        packet_seq = self.next_seq() if seq is None else seq
        payload_fields = [command, packet_seq, *fields]
        payload = ",".join(_clean_field(field) for field in payload_fields)
        return BuiltPacket(add_checksum(payload), packet_seq, command)

    def act(self, decision: EmotionDecision, mode: str = "DIALOGUE", seq: int | None = None) -> BuiltPacket:
        return self.build(
            "ACT",
            [
                mode,
                decision.face_id,
                decision.motion_id,
                decision.roll_bias,
                decision.pitch_bias,
                decision.speed,
                decision.hold_ms,
            ],
            seq=seq,
        )

    def emo(self, emotion: str, seq: int | None = None) -> BuiltPacket:
        return self.build("EMO", [emotion], seq=seq)

    def test(self, motion_id: str, seq: int | None = None) -> BuiltPacket:
        return self.build("TEST", [motion_id], seq=seq)

    def reset(self, seq: int | None = None) -> BuiltPacket:
        return self.build("RESET", [], seq=seq)

    def status(self, seq: int | None = None) -> BuiltPacket:
        return self.build("STATUS", [], seq=seq)

    def ping(self, seq: int | None = None) -> BuiltPacket:
        return self.build("PING", [], seq=seq)

