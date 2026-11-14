from __future__ import annotations

import ctypes
import os
import threading
from array import array
from types import SimpleNamespace

import pytest

from server.aec import DuplexAudio, FRAME_BYTES, SpeexAEC
from server.config import Settings
from server.voice import VoiceError


def frame(value=500):
    return array('h', [value] * 320).tobytes()


def test_missing_library_is_explicit_not_silent_bypass(monkeypatch):
    monkeypatch.setattr('ctypes.util.find_library', lambda name: None)
    with pytest.raises(VoiceError, match='SpeexDSP'):
        SpeexAEC()


def test_invalid_library_is_user_facing(tmp_path):
    with pytest.raises(VoiceError, match='SpeexDSP'):
        SpeexAEC(str(tmp_path / 'missing.dll'))


def test_aec_reference_delay_and_reset():
    observed = []

    def cancellation(state, mic, reference, out):
        observed.append(bytes(reference))
        ctypes.memmove(out, mic, FRAME_BYTES)

    aec = SpeexAEC.__new__(SpeexAEC)
    aec.lib = SimpleNamespace(speex_echo_cancellation=cancellation,
                              speex_echo_state_reset=lambda state: None,
                              speex_echo_state_destroy=lambda state: None)
    aec.state = 1
    aec.delay_frames = 2
    aec.reset_delay()
    assert aec.process(frame(3), frame(1)) == frame(3)
    aec.process(frame(3), frame(2))
    aec.process(frame(3), frame(4))
    assert observed == [bytes(FRAME_BYTES), bytes(FRAME_BYTES), frame(1)]
    with pytest.raises(ValueError):
        aec.process(b'', frame())
    aec.reset()
    aec.process(frame(), frame())
    assert observed[-1] == bytes(FRAME_BYTES)
    aec.close()
    aec.close()
    assert aec.state is None


def test_duplex_reference_is_exact_rendered_pcm_and_cancelled_frames_are_silent():
    duplex = DuplexAudio()
    token = threading.Event()
    submitted = threading.Event()
    duplex.render.put((frame(123), token, submitted))
    output = bytearray(FRAME_BYTES)
    duplex.callback(frame(999), output, 320, None, False)
    assert output == frame(123)
    assert duplex.capture.get_nowait() == (frame(999), bytes(output))
    assert submitted.is_set()
    token.set()
    duplex.render.put((frame(321), token, threading.Event()))
    duplex.callback(frame(999), output, 320, None, False)
    assert output == bytes(FRAME_BYTES)
    assert duplex.capture.get_nowait()[1] == bytes(FRAME_BYTES)


def test_capture_overflow_and_invalid_frame_size():
    duplex = DuplexAudio()
    output = bytearray(FRAME_BYTES)
    for _ in range(101):
        duplex.callback(frame(), output, 320, None, False)
    assert duplex.capture.qsize() == 100
    assert duplex.overflow.is_set()
    duplex.overflow.clear()
    duplex.callback(frame(), output, 123, None, False)
    assert duplex.overflow.is_set()
    assert output == bytes(FRAME_BYTES)


def test_close_unblocks_writer_and_padding_is_zero():
    duplex = DuplexAudio()
    thread = threading.Thread(target=duplex.write, args=(b'\x01\x02', threading.Event()))
    thread.start()
    output = bytearray(FRAME_BYTES)
    # Wait for producer without relying on a timing-sensitive sleep.
    pcm, token, submitted = duplex.render.get(timeout=1)
    assert pcm == b'\x01\x02' + bytes(638)
    duplex.render.put((pcm, token, submitted))
    duplex.callback(frame(), output, 320, None, False)
    thread.join(timeout=1)
    assert not thread.is_alive()
    thread = threading.Thread(target=duplex.write, args=(frame() * 200, threading.Event()))
    thread.start()
    duplex.close()
    thread.join(timeout=1)
    assert not thread.is_alive()


def test_aec_config(monkeypatch):
    monkeypatch.setenv('PVAI_AEC_ENABLED', 'false')
    monkeypatch.setenv('PVAI_AEC_DELAY_MS', '999')
    monkeypatch.setenv('PVAI_AEC_FILTER_MS', '10')
    settings = Settings.from_env()
    assert not settings.aec_enabled
    assert settings.aec_delay_ms == 500
    assert settings.aec_filter_ms == 100


@pytest.mark.skipif(not os.getenv('PVAI_TEST_AEC_LIBRARY'), reason='Native SpeexDSP test library not supplied')
def test_native_speex_reduces_synthetic_delayed_echo():
    np = pytest.importorskip('numpy')
    rng = np.random.default_rng(42)
    reference = rng.normal(0, 3000, 16000 * 12).astype('<i2')
    echo = np.zeros_like(reference)
    echo[640:] = (reference[:-640].astype(float) * .55).astype('<i2')
    aec = SpeexAEC(os.environ['PVAI_TEST_AEC_LIBRARY'], delay_ms=40)
    try:
        output = b''.join(aec.process(echo[i:i+320].tobytes(), reference[i:i+320].tobytes())
                          for i in range(0, len(echo), 320))
    finally:
        aec.close()
    residual = np.frombuffer(output, dtype='<i2').astype(float)[-32000:]
    original = echo.astype(float)[-32000:]
    erle_db = 10 * np.log10(np.mean(original ** 2) / max(1, np.mean(residual ** 2)))
    print(f'Synthetic echo reduction: {erle_db:.2f} dB')
    assert erle_db > 10


def test_piper_resamples_into_duplex_reference_transport():
    np = pytest.importorskip('numpy')
    pytest.importorskip('scipy')
    from server.live_voice import Speaker
    output = []
    speaker = Speaker.__new__(Speaker)
    speaker.duplex = SimpleNamespace(write=lambda pcm, token: output.append(pcm))
    speaker.model = SimpleNamespace(synthesize=lambda text: iter([
        SimpleNamespace(sample_rate=24000, sample_channels=1,
                        audio_int16_bytes=np.full(480, 3000, dtype='<i2').tobytes())]))
    speaker.speak('سلام', threading.Event())
    assert len(output[0]) == FRAME_BYTES
    assert abs(float(np.frombuffer(output[0], dtype='<i2').mean()) - 3000) < 20


def test_runtime_aec_precedes_pipeline_and_resources_close(monkeypatch, tmp_path):
    import asyncio
    from server.streaming_voice import run_live
    order = []

    class AEC:
        def __init__(self, *args, **kwargs):
            pass

        def process(self, microphone, reference):
            order.append('aec')
            return frame(12)

        def close(self):
            order.append('aec_close')

    class Pipeline:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

        def feed(self, pcm):
            assert pcm == frame(12)
            order.append('vad_asr')
            raise RuntimeError('stop test')

        async def close(self):
            order.append('pipeline_close')

    class Stream:
        def __init__(self, **kwargs):
            self.callback = kwargs['callback']

        def __enter__(self):
            self.callback(frame(99), bytearray(FRAME_BYTES), 320, None, False)

        def __exit__(self, *args):
            order.append('stream_close')

    class FakeSd:
        """Minimal sounddevice stand-in: one duplex device, no capability checks."""

        @staticmethod
        def query_devices():
            return [{'name': 'test duplex', 'max_input_channels': 1,
                     'max_output_channels': 1, 'default_samplerate': 16000}]

    FakeSd.RawStream = Stream
    monkeypatch.setattr('server.aec.SpeexAEC', AEC)
    monkeypatch.setattr('server.streaming_voice.LocalAudio',
                        lambda s, metrics=None: SimpleNamespace(validate=lambda: None))
    monkeypatch.setattr('server.streaming_voice.Speaker', lambda s, duplex: object())
    monkeypatch.setattr('server.streaming_voice.Pipeline', Pipeline)
    monkeypatch.setattr('server.streaming_voice.dependency', lambda name: FakeSd)
    with pytest.raises(RuntimeError, match='stop test'):
        asyncio.run(run_live(Settings(data_dir=str(tmp_path / "data"))))
    assert order == ['aec', 'vad_asr', 'stream_close', 'pipeline_close', 'aec_close']


@pytest.mark.skipif(not os.getenv('PVAI_TEST_AEC_LIBRARY'), reason='Native SpeexDSP test library not supplied')
def test_native_double_talk_preserves_independent_near_end_signal():
    np = pytest.importorskip('numpy')
    rng = np.random.default_rng(24)
    count = 16000 * 12
    reference = rng.normal(0, 2500, count).astype('<i2')
    near = np.zeros(count)
    t = np.arange(16000 * 4) / 16000
    near[-len(t):] = 1100 * np.sin(2 * np.pi * 220 * t) + 700 * np.sin(2 * np.pi * 370 * t)
    echo = np.zeros(count)
    echo[640:] = .55 * reference[:-640]
    microphone = (echo + near).astype('<i2')
    aec = SpeexAEC(os.environ['PVAI_TEST_AEC_LIBRARY'])
    try:
        output = b''.join(aec.process(microphone[i:i+320].tobytes(), reference[i:i+320].tobytes())
                          for i in range(0, count, 320))
    finally:
        aec.close()
    cleaned = np.frombuffer(output, dtype='<i2')[-16000:].astype(float)
    original = near[-16000:]
    correlation = np.corrcoef(original, cleaned)[0, 1]
    retention = np.sqrt(np.mean(cleaned ** 2) / np.mean(original ** 2))
    print(f'Synthetic double-talk correlation={correlation:.3f}, RMS retention={retention:.3f}')
    assert correlation > .7
    assert .6 < retention < 1.4
