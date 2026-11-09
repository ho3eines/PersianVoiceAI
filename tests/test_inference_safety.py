from __future__ import annotations

from types import SimpleNamespace
import threading

import numpy as np
import pytest
import soundfile as sf

from server.config import Settings
from server.voice import (LocalAudio, ModelLoadError, AudioValidationError,
                          AsrPreempted, asr_device_options, load_asr_audio,
                          release_inference_memory)


@pytest.mark.parametrize('requested,count,expected', [
    ('cpu', 1, 'cpu'), ('auto', 1, 'cuda'), ('cuda', 0, 'cpu'),
    ('mps', 1, 'cpu'), ('auto', 0, 'cpu')])
def test_device_detection(monkeypatch, requested, count, expected):
    monkeypatch.setattr('server.voice.dependency', lambda _: SimpleNamespace(
        get_cuda_device_count=lambda: count))
    device, precision = asr_device_options(requested, 'float16')
    assert device == expected
    if expected == 'cpu' and requested != 'cpu':
        assert precision == 'int8'


def test_detection_failure(monkeypatch, caplog):
    def missing(_):
        raise OSError('driver missing')
    monkeypatch.setattr('server.voice.dependency', missing)
    assert asr_device_options('cuda', 'float16') == ('cpu', 'int8')
    assert 'falling back to CPU' in caplog.text


def test_audio_stereo_resampling(tmp_path):
    path = tmp_path / 'stereo.wav'
    sf.write(path, np.full((4800, 2), .25), 48000)
    samples = load_asr_audio(path)
    assert samples.shape == (1600,)
    assert samples.dtype == np.float32
    assert samples.flags.c_contiguous
    assert np.mean(samples[20:-20]) == pytest.approx(.25, abs=.001)


@pytest.mark.parametrize('kind', ['missing', 'directory', 'corrupt', 'empty'])
def test_invalid_audio(tmp_path, kind):
    path = tmp_path / 'input.wav'
    if kind == 'directory':
        path.mkdir()
    elif kind == 'corrupt':
        path.write_bytes(b'not audio')
    elif kind == 'empty':
        sf.write(path, np.zeros(0), 16000)
    with pytest.raises(AudioValidationError):
        LocalAudio(Settings()).transcribe(path)


def test_missing_weights(tmp_path):
    with pytest.raises(ModelLoadError, match='model.bin'):
        LocalAudio(Settings(asr_model_path=str(tmp_path)))._ensure_model()


def test_cuda_initialization_retry(tmp_path, monkeypatch):
    (tmp_path / 'model.bin').touch()
    calls = []
    sentinel = object()
    def load(path, **options):
        calls.append(options)
        if options['device'] == 'cuda':
            raise RuntimeError('CUDA library unavailable')
        return sentinel
    monkeypatch.setattr('server.voice.asr_device_options', lambda *a: ('cuda', 'float16'))
    monkeypatch.setattr('server.voice.dependency', lambda _: SimpleNamespace(WhisperModel=load))
    audio = LocalAudio(Settings(asr_model_path=str(tmp_path)))
    assert audio._ensure_model() is sentinel
    assert audio._ensure_model() is sentinel
    assert len(calls) == 2
    assert audio.last_options['device'] == 'cpu'


@pytest.mark.parametrize('pcm', [b'', b'1', b'123'])
def test_invalid_pcm(pcm):
    with pytest.raises(AudioValidationError):
        LocalAudio(Settings()).transcribe_pcm(pcm)


def test_lazy_decode_closed_on_preemption(monkeypatch):
    audio = LocalAudio(Settings())
    token = threading.Event()
    closed = []
    def segments():
        try:
            token.set()
            yield SimpleNamespace(text='hello')
        finally:
            closed.append(True)
    audio.model = SimpleNamespace(transcribe=lambda *a, **k: (segments(), None))
    with pytest.raises(AsrPreempted):
        audio._transcribe(np.zeros(160), token)
    assert closed == [True]


# ------------------------------------------------------- resource lifecycle
def test_close_releases_the_whisper_model_and_empties_caches(monkeypatch) -> None:
    """``close()`` must drop the weights *and* reclaim the allocator's memory.

    The LAN transport shares one ``LocalAudio`` per process (``MODEL_POOL``);
    resetting the pool without this left the ~500 MB model resident, so a model
    reload after a settings change double-counted RAM on a 16 GB laptop.
    """
    called: list[bool] = []
    monkeypatch.setattr('server.voice.release_inference_memory', lambda: called.append(True))
    audio = LocalAudio(Settings())
    audio.model = object()
    audio.loaded_at = 1.0
    audio.close()
    assert audio.model is None and audio.loaded_at is None
    assert called == [True]
    audio.close()  # idempotent: a second close must not raise
    assert called == [True, True]


def test_release_inference_memory_tolerates_a_broken_cache(monkeypatch) -> None:
    """A ``torch`` cache that refuses to empty must never break cleanup."""
    import sys
    from types import SimpleNamespace

    def explode() -> None:
        raise RuntimeError('driver reset')

    fake_torch = SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: True, empty_cache=explode),
        mps=SimpleNamespace(empty_cache=explode),
    )
    monkeypatch.setitem(sys.modules, 'torch', fake_torch)
    release_inference_memory()  # must not raise


def test_mps_falls_back_to_cpu_with_a_reason(caplog) -> None:
    assert asr_device_options('mps', 'float16') == ('cpu', 'int8')
    assert 'unsupported' in caplog.text


@pytest.mark.parametrize('requested', ['', '   ', 'gpu', 'CPU '])
def test_unknown_device_is_rejected_or_normalised(requested) -> None:
    if requested.strip().upper() == 'CPU':
        assert asr_device_options(requested, 'int8') == ('cpu', 'int8')
        return
    with pytest.raises(ModelLoadError):
        asr_device_options(requested, 'int8')


def test_piper_config_must_be_a_json_object(tmp_path) -> None:
    model = tmp_path / 'voice.onnx'
    model.write_bytes(b'')
    (tmp_path / 'voice.onnx.json').write_text('[1, 2]', encoding='utf-8')
    (tmp_path / 'model.bin').touch()  # a 0-byte stub is enough for validate()
    audio = LocalAudio(Settings(asr_model_path=str(tmp_path), tts_model_path=str(model)))
    with pytest.raises(ModelLoadError, match='Piper'):
        audio.validate()


def test_tts_readiness_rejects_a_corrupt_config(tmp_path) -> None:
    from server.readiness import check_tts

    model = tmp_path / 'voice.onnx'
    model.write_bytes(b'')
    config = tmp_path / 'voice.onnx.json'
    config.write_text('{ "audio": ', encoding='utf-8')
    report = check_tts(Settings(tts_model_path=str(model), tts_voice='female'))
    assert report['ok'] is False
    assert 'JSON' in report['detail'] or 'اعتبار' in report['detail']
    config.write_text('{}', encoding='utf-8')  # the minimal real config is valid
    assert check_tts(Settings(tts_model_path=str(model), tts_voice='female'))['ok'] is True


def test_rate_limiter_bucket_map_is_bounded() -> None:
    """A scanner walking the network must not grow an unbounded dict."""
    from server.security import RateLimiter

    limiter = RateLimiter(rate=1.0, burst=2, now=lambda: 1000.0, max_keys=8)
    for index in range(200):
        limiter.check(f'10.0.0.{index}')
    assert len(limiter._buckets) <= 8


@pytest.mark.parametrize('kwargs', [
    {'sample_rate': 0}, {'sample_rate': -16000}, {'channels': 0},
    {'sample_rate': 'fast'}, {'channels': None},
])
def test_invalid_pcm_metadata_is_a_validation_error(kwargs) -> None:
    """A bad rate/channel argument must not become a ``TypeError``/``ZeroDivisionError``."""
    pcm = np.zeros(320, dtype='<i2').tobytes()
    with pytest.raises(AudioValidationError):
        LocalAudio(Settings()).transcribe_pcm(pcm, **kwargs)


def test_pcm_16k_mono_bypasses_resampling(monkeypatch) -> None:
    """The default path must not pull librosa/scipy in, nor require extra NumPy APIs.

    Live microphone audio always arrives as PCM16 mono 16 kHz, so a decode must
    not pay for a resampler (or fail on a NumPy stub that only provides
    ``frombuffer``/``float32`` — exactly what ``tests/test_requested_regressions.py``
    passes in).
    """
    import server.voice as voice_module

    real_dependency = voice_module.dependency

    def guarded(name: str):
        if name in {'librosa', 'scipy', 'scipy.signal'}:
            raise AssertionError('16 kHz mono PCM must not be resampled')
        return real_dependency(name)

    monkeypatch.setattr(voice_module, 'dependency', guarded)
    audio = LocalAudio(Settings())
    seen: list[Any] = []
    audio._transcribe = lambda samples, token=None: (seen.append(samples), 'متن')[1]
    result = audio.transcribe_pcm(np.zeros(320, dtype='<i2').tobytes())
    assert result == 'متن'
    assert seen and seen[0].shape == (320,)
    assert seen[0].dtype == np.float32
