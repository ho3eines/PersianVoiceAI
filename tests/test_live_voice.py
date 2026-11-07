from __future__ import annotations

import threading
from array import array


from server.live_voice import Endpoint, sentences


def pcm(value: int) -> bytes:
    return array('h', [value] * 320).tobytes()


def test_endpoint_onset_silence_and_noise() -> None:
    detector = Endpoint(.01, silence_frames=3)
    for _ in range(10):
        assert detector.feed(pcm(0)) == (False, None)
    assert not detector.feed(pcm(2000))[0]
    assert not detector.feed(pcm(2000))[0]
    assert detector.feed(pcm(2000))[0]
    for _ in range(5):
        detector.feed(pcm(2000))
    detector.feed(pcm(0))
    detector.feed(pcm(0))
    assert detector.feed(pcm(0))[1] is not None
    detector.feed(pcm(2000))
    for _ in range(3):
        result = detector.feed(pcm(0))
    assert result[1] is None


def test_max_utterance_bounded() -> None:
    detector = Endpoint(.01, max_frames=10)
    for _ in range(9):
        assert detector.feed(pcm(2000))[1] is None
    assert len(detector.feed(pcm(2000))[1]) == 6400
    assert detector.frames == []


def test_sentence_boundaries_and_tail() -> None:
    assert sentences('سلام! چطوری؟ باقی') == (['سلام!', 'چطوری؟'], 'باقی')
    assert sentences('باقی', final=True) == (['باقی'], '')
    ready, tail = sentences('واژه ' * 50)
    assert ready and len(tail) < 180


def test_pcm_playback_stops_on_cancel() -> None:
    from types import SimpleNamespace
    from server.live_voice import Speaker
    token = threading.Event()
    writes = []
    aborted = []

    class Output:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def write(self, data):
            writes.append(data)
            token.set()

        def abort(self):
            aborted.append(True)

    speaker = Speaker.__new__(Speaker)
    speaker.sd = SimpleNamespace(RawOutputStream=lambda **kwargs: Output())
    speaker.model = SimpleNamespace(synthesize=lambda text: iter([
        SimpleNamespace(sample_rate=16000, sample_channels=1, audio_int16_bytes=pcm(500) * 10)]))
    speaker.speak('سلام', token)
    assert len(writes) == 1
    assert aborted == [True]
