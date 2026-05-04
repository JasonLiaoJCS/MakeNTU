from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import serial

from .checksum import PacketError, split_packet
from .packet_builder import BuiltPacket


LOGGER = logging.getLogger(__name__)


@dataclass
class SerialResponse:
    ok: bool
    packet_type: str
    seq: int
    line: str
    fields: list[str]
    error_code: str | None = None
    message: str | None = None


class SerialBridge:
    def __init__(self, config: dict[str, Any]) -> None:
        serial_cfg = config.get("serial", {})
        self.port = serial_cfg.get("port", "COM5")
        self.baudrate = int(serial_cfg.get("baudrate", 115200))
        self.timeout_sec = float(serial_cfg.get("timeout_sec", 1.0))
        self.ack_timeout_sec = float(serial_cfg.get("ack_timeout_sec", 1.0))
        self.retry_count = int(serial_cfg.get("retry_count", 2))
        self.line_ending = serial_cfg.get("line_ending", "\n").encode("ascii")
        self.command_prefix = str(serial_cfg.get("command_prefix", "")).strip()
        self._serial: serial.Serial | None = None

    def open(self) -> None:
        if self._serial and self._serial.is_open:
            return
        self._serial = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            timeout=self.timeout_sec,
            write_timeout=self.timeout_sec,
        )
        LOGGER.info("Opened serial port %s at %s baud", self.port, self.baudrate)

    def close(self) -> None:
        if self._serial:
            self._serial.close()
            LOGGER.info("Closed serial port %s", self.port)

    def __enter__(self) -> "SerialBridge":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def send_and_wait(self, packet: BuiltPacket) -> SerialResponse:
        last_response: SerialResponse | None = None
        for attempt in range(self.retry_count + 1):
            self._write_line(packet.line)
            response = self._read_matching_response(packet.seq, self.ack_timeout_sec)
            if response:
                return response
            last_response = SerialResponse(
                ok=False,
                packet_type="TIMEOUT",
                seq=packet.seq,
                line="",
                fields=[],
                error_code="TIMEOUT",
                message=f"No ACK/PONG/STATUS before timeout on attempt {attempt + 1}",
            )
            LOGGER.warning("%s", last_response.message)
        assert last_response is not None
        return last_response

    def _write_line(self, line: str) -> None:
        if not self._serial or not self._serial.is_open:
            self.open()
        assert self._serial is not None
        wire_line = f"{self.command_prefix} {line}" if self.command_prefix else line
        data = wire_line.encode("utf-8") + self.line_ending
        LOGGER.info("TX %s", wire_line)
        self._serial.write(data)
        self._serial.flush()

    def _read_matching_response(self, seq: int, timeout_sec: float) -> SerialResponse | None:
        assert self._serial is not None
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            raw = self._serial.readline()
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            LOGGER.info("RX %s", line)
            try:
                _, fields = split_packet(line)
            except PacketError as exc:
                LOGGER.warning("Ignoring malformed RX packet: %s", exc)
                continue

            if len(fields) < 2:
                continue
            packet_type = fields[0]
            try:
                rx_seq = int(fields[1])
            except ValueError:
                continue
            if rx_seq != seq:
                LOGGER.info("Ignoring response for seq %s while waiting for %s", rx_seq, seq)
                continue

            if packet_type == "ACK":
                return SerialResponse(True, packet_type, rx_seq, line, fields, message="OK")
            if packet_type == "PONG":
                ok = len(fields) >= 3 and fields[2] == "OK"
                return SerialResponse(ok, packet_type, rx_seq, line, fields, message="OK" if ok else "Unexpected PONG")
            if packet_type == "STATUS":
                return SerialResponse(True, packet_type, rx_seq, line, fields, message=",".join(fields[2:]))
            if packet_type == "NACK":
                code = fields[2] if len(fields) > 2 else "UNKNOWN"
                msg = fields[3] if len(fields) > 3 else ""
                return SerialResponse(False, packet_type, rx_seq, line, fields, error_code=code, message=msg)
        return None
