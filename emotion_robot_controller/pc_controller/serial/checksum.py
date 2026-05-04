from __future__ import annotations


class PacketError(ValueError):
    """Raised when a text packet is malformed or has a bad checksum."""


def checksum_payload(payload: str) -> str:
    value = 0
    for byte in payload.encode("utf-8"):
        value ^= byte
    return f"{value:02X}"


def add_checksum(payload: str) -> str:
    if payload.startswith("$"):
        raise PacketError("payload must not include '$'")
    if "*" in payload or "\n" in payload or "\r" in payload:
        raise PacketError("payload contains invalid packet characters")
    return f"${payload}*{checksum_payload(payload)}"


def split_packet(line: str) -> tuple[str, list[str]]:
    text = line.strip()
    if not text.startswith("$") or "*" not in text:
        raise PacketError("packet must look like $PAYLOAD*CS")
    payload, checksum = text[1:].rsplit("*", 1)
    checksum = checksum.strip().upper()
    if len(checksum) != 2:
        raise PacketError("checksum must be two hex characters")
    expected = checksum_payload(payload)
    if checksum != expected:
        raise PacketError(f"bad checksum: got {checksum}, expected {expected}")
    fields = payload.split(",")
    if not fields or not fields[0]:
        raise PacketError("empty command")
    return payload, fields

