from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)
_OPENCC = None
_OPENCC_UNAVAILABLE = False

_SPACE_RE = re.compile(r"\s+")
_SENTENCE_SPLIT_RE = re.compile(r"([^。！？!?；;\n\r]+[。！？!?；;]?)")
_QUOTE_MAP = str.maketrans(
    {
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
        "（": "(",
        "）": ")",
        "【": "[",
        "】": "]",
        "《": "",
        "》": "",
        "…": "。",
        "—": "-",
        "～": "~",
    }
)


@dataclass(slots=True)
class NormalizedText:
    original_text: str
    normalized_text: str
    chunks: list[str]
    warnings: list[str] = field(default_factory=list)


def _remove_control_and_emoji(text: str) -> str:
    kept: list[str] = []
    for char in text:
        category = unicodedata.category(char)
        if category in {"Cc", "Cf", "Cs"}:
            if char in "\n\r\t":
                kept.append(" ")
            continue
        if category == "So":
            continue
        kept.append(char)
    return "".join(kept)


def _simplify(text: str, enabled: bool, warnings_out: list[str]) -> str:
    global _OPENCC, _OPENCC_UNAVAILABLE
    if not enabled:
        return text
    if _OPENCC_UNAVAILABLE:
        warnings_out.append("OpenCC unavailable; keeping original Chinese text")
        return text
    if _OPENCC is None:
        try:
            from opencc import OpenCC

            _OPENCC = OpenCC("t2s")
        except Exception as exc:  # pragma: no cover - depends on optional runtime package
            _OPENCC_UNAVAILABLE = True
            message = f"OpenCC unavailable; keeping original Chinese text: {exc}"
            warnings_out.append(message)
            logger.warning(message)
            return text
    try:
        return _OPENCC.convert(text)
    except Exception as exc:  # pragma: no cover - defensive
        message = f"OpenCC conversion failed; keeping original Chinese text: {exc}"
        warnings_out.append(message)
        logger.warning(message)
        return text


def _hard_wrap(sentence: str, max_chars: int) -> list[str]:
    if len(sentence) <= max_chars:
        return [sentence]

    pieces: list[str] = []
    current = sentence
    soft_breaks = "，,、；;：: "
    while len(current) > max_chars:
        split_at = -1
        for index in range(min(len(current), max_chars), max(0, max_chars // 2), -1):
            if current[index - 1] in soft_breaks:
                split_at = index
                break
        if split_at <= 0:
            split_at = max_chars
        pieces.append(current[:split_at].strip())
        current = current[split_at:].strip()
    if current:
        pieces.append(current)
    return [piece for piece in pieces if piece]


def split_chunks(text: str, max_chunk_chars: int = 70) -> list[str]:
    chunks: list[str] = []
    for match in _SENTENCE_SPLIT_RE.finditer(text):
        sentence = match.group(0).strip()
        if not sentence:
            continue
        chunks.extend(_hard_wrap(sentence, max_chunk_chars))

    if not chunks and text.strip():
        chunks = _hard_wrap(text.strip(), max_chunk_chars)

    return [chunk for chunk in chunks if chunk.strip()]


def normalize_text(
    text: str,
    *,
    enable_traditional_to_simplified: bool = True,
    max_text_chars: int = 600,
    max_chunk_chars: int = 70,
) -> NormalizedText:
    original = text if text is not None else ""
    warnings_out: list[str] = []

    cleaned = original.translate(_QUOTE_MAP)
    cleaned = _remove_control_and_emoji(cleaned)
    cleaned = _SPACE_RE.sub(" ", cleaned).strip()

    if len(cleaned) > max_text_chars:
        warnings_out.append(f"text truncated from {len(cleaned)} to {max_text_chars} chars")
        cleaned = cleaned[:max_text_chars].rstrip()

    simplified = _simplify(cleaned, enable_traditional_to_simplified, warnings_out)
    simplified = _SPACE_RE.sub(" ", simplified).strip()
    chunks = split_chunks(simplified, max_chunk_chars=max_chunk_chars)
    normalized = " ".join(chunks).strip()

    return NormalizedText(
        original_text=original,
        normalized_text=normalized,
        chunks=chunks,
        warnings=warnings_out,
    )
