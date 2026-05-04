from jetson_piper_tts.text_normalizer import normalize_text, split_chunks


def test_normalize_removes_emoji_and_spaces() -> None:
    result = normalize_text(
        "  你好   世界 😃\n再見  ",
        enable_traditional_to_simplified=False,
        max_chunk_chars=20,
    )

    assert result.normalized_text == "你好 世界 再見"
    assert "😃" not in result.normalized_text
    assert result.chunks == ["你好 世界 再見"]


def test_split_chunks_prefers_punctuation() -> None:
    chunks = split_chunks("第一句很短。第二句也很短！第三句稍微長一點點。", max_chunk_chars=8)

    assert chunks
    assert all(len(chunk) <= 8 for chunk in chunks)
    assert chunks[0].endswith("。")


def test_split_chunks_does_not_split_short_text_on_commas() -> None:
    chunks = split_chunks("你好，我現在回來了，我會用比較自然的節奏說話。", max_chunk_chars=70)

    assert chunks == ["你好，我現在回來了，我會用比較自然的節奏說話。"]


def test_text_truncation_warning() -> None:
    result = normalize_text("a" * 20, enable_traditional_to_simplified=False, max_text_chars=10)

    assert result.normalized_text == "a" * 10
    assert result.warnings
