from pathlib import Path

from jetson_piper_tts.cache import AudioCache


def test_cache_put_and_get(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache = AudioCache(cache_dir)
    source = tmp_path / "source.wav"
    source.write_bytes(b"RIFF" + b"\0" * 80)

    key = cache.key_for(
        text="系统启动完成。",
        model_path=tmp_path / "voice.onnx",
        length_scale=0.9,
        noise_scale=0.667,
        noise_w=0.8,
    )

    assert cache.get_cached_wav(key) is None
    cached = cache.put_cached_wav(key, source)

    assert cached.exists()
    assert cache.get_cached_wav(key) == cached
    assert cache.stats()["wav_files"] == 1
    assert cache.clear_cache() == 1
    assert cache.stats()["wav_files"] == 0
