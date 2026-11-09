from __future__ import annotations

from types import SimpleNamespace
import threading

import numpy as np
import pytest
import soundfile as sf

from server.config import Settings
from server.voice import (LocalAudio, ModelLoadError, AudioValidationError,
                          AsrPreempted, asr_device_options, load_asr_audio)


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
