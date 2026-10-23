from __future__ import annotations

from pipecat.frames.frames import (
    InterruptionFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)

from app.pipeline.tts_cache import GreetingCache, _Capture, replay_frames


def _audio(n=640, rate=8000, ch=1):
    return TTSAudioRawFrame(audio=b"\x01\x00" * (n // 2), sample_rate=rate, num_channels=ch)


def test_cache_roundtrip_and_key_isolation(tmp_path):
    a = GreetingCache(tmp_path, "rumik|v1", "Hello")
    assert a.load(8000) is None
    a.save(8000, b"\x01\x00" * 10)
    assert a.load(8000) == b"\x01\x00" * 10
    assert a.load(16000) is None  # other sample rate
    assert GreetingCache(tmp_path, "rumik|v2", "Hello").load(8000) is None  # voice version bumped
    assert GreetingCache(tmp_path, "rumik|v1", "Hello!").load(8000) is None  # text changed


def test_replay_frames_shape_and_no_split_samples():
    pcm = b"\x02\x00" * 5000
    frames = replay_frames(pcm, 8000)
    assert isinstance(frames[0], TTSStartedFrame) and isinstance(frames[-1], TTSStoppedFrame)
    audio = [f for f in frames if isinstance(f, TTSAudioRawFrame)]
    assert b"".join(f.audio for f in audio) == pcm
    assert all(len(f.audio) % 2 == 0 for f in audio)
    assert len({f.context_id for f in frames}) == 1


def test_capture_records_full_utterance_only_when_armed():
    c = _Capture()
    assert c.feed(TTSStartedFrame()) is None
    c.arm()
    c.feed(TTSStartedFrame())
    c.feed(_audio(4))
    c.feed(_audio(6))
    rate, pcm = c.feed(TTSStoppedFrame())
    assert rate == 8000 and len(pcm) == 10
    assert c.feed(TTSStoppedFrame()) is None  # disarmed after one capture


def test_capture_discards_interrupted_audio():
    c = _Capture()
    c.arm()
    c.feed(TTSStartedFrame())
    c.feed(_audio())
    c.feed(InterruptionFrame())
    assert c.feed(TTSStoppedFrame()) is None


def test_capture_ignores_non_mono():
    c = _Capture()
    c.arm()
    c.feed(TTSStartedFrame())
    c.feed(_audio(ch=2))
    assert c.feed(TTSStoppedFrame()) is None


def test_warm_greeting_skips_when_already_cached(tmp_path):
    import asyncio

    from pipecat.pipeline.worker import PipelineParams

    from app.pipeline.tts_cache import GreetingCache, warm_greeting

    cache = GreetingCache(tmp_path, "ns", "hello")
    cache.save(PipelineParams().audio_out_sample_rate, b"\x01\x00" * 10)
    # tts=None would blow up if a pipeline were built -- proves it short-circuits.
    assert asyncio.run(warm_greeting(None, cache)) is True


def test_recorder_calls_on_stored(tmp_path):
    from app.pipeline.tts_cache import GreetingCache, GreetingRecorder

    seen = []
    rec = GreetingRecorder(GreetingCache(tmp_path, "ns", "hi"), on_stored=seen.append)
    assert rec._on_stored is not None
    rec._on_stored(8000)
    assert seen == [8000]
