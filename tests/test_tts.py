"""Piper/TTS layer: voice selection, chunk errors, cancellation, playback metrics."""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from server.config import Settings
from server.live_voice import Speaker, sentences
from server.voice import VoiceError


def pcm_block(value: int = 500, samples: int = 320) -> bytes:
    from array import array

    return array("h", [value] * samples).tobytes()


def chunk(rate: int = 16000, channels: int = 1, samples: int = 320) -> SimpleNamespace:
    return SimpleNamespace(
        sample_rate=rate,
        sample_channels=channels,
        audio_int16_bytes=pcm_block(samples=samples),
    )


# --------------------------------------------------------------------- sentence
def test_sentence_boundaries_and_tail() -> None:
    assert sentences("سلام! چطوری؟ باقی") == (["سلام!", "چطوری؟"], "باقی")
    assert sentences("باقی", final=True) == (["باقی"], "")
    ready, tail = sentences("واژه " * 50)
    assert ready and len(tail) < 180


def test_long_text_is_split_on_a_word_boundary() -> None:
    ready, tail = sentences("کلمه " * 60)
    assert ready
    for piece in ready:
        assert piece and not piece.endswith("کلم")
        assert len(piece) <= 180
    assert "کلمه" in ready[0]


def test_incomplete_sentence_is_not_spoken_early() -> None:
    ready, tail = sentences("این جمله کامل نشده")
    assert ready == []
    assert tail == "این جمله کامل نشده"


# ------------------------------------------------------------------ voice config
def test_voice_selection_uses_configured_paths(tmp_path: Path) -> None:
    female = tmp_path / "fa_female.onnx"
    male = tmp_path / "fa_male.onnx"
    settings = Settings(
        tts_model_path=str(female),
        tts_model_female=str(female),
        tts_model_male=str(male),
        tts_voice="male",
    )
    assert settings.tts_model_for("male") == str(male)
    assert settings.tts_model_for("female") == str(female)
    assert settings.tts_model_for() == str(male)  # configured default voice
    assert Settings(tts_model_path="x.onnx").tts_model_for("male") == "x.onnx"


@pytest.mark.parametrize("voice", ["robot", ""])
def test_invalid_voice_is_a_configuration_error(voice: str) -> None:
    errors = Settings(tts_voice=voice).config_errors()
    assert any("PVAI_TTS_VOICE" in error for error in errors)


def test_missing_piper_model_is_a_user_facing_error(tmp_path: Path) -> None:
    settings = Settings(tts_model_path=str(tmp_path / "absent.onnx"))
    with pytest.raises(VoiceError, match="Piper"):
        Speaker(settings)


def test_missing_piper_config_is_reported(tmp_path: Path) -> None:
    model = tmp_path / "fa.onnx"
    model.write_bytes(b"x")
    with pytest.raises(VoiceError, match=r"\.onnx\.json"):
        Speaker(Settings(tts_model_path=str(model)))


# --------------------------------------------------------------------- playback
def test_playback_stops_within_one_block_and_aborts_the_stream() -> None:
    token = threading.Event()
    writes: list[bytes] = []
    aborted: list[bool] = []

    class Output:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def write(self, data: bytes) -> None:
            writes.append(data)
            token.set()

        def abort(self) -> None:
            aborted.append(True)

    speaker = Speaker.__new__(Speaker)
    speaker.sd = SimpleNamespace(RawOutputStream=lambda **kwargs: Output())
    speaker.model = SimpleNamespace(
        synthesize=lambda text: iter([chunk(samples=320 * 10)])
    )
    speaker.speak("سلام", token)
    assert len(writes) == 1  # exactly one 20 ms block before cancellation
    assert aborted == [True]


def test_duplex_path_resamples_to_16k_mono_and_reports_frames() -> None:
    pytest.importorskip("numpy")
    pytest.importorskip("scipy")
    import numpy as np

    writes: list[bytes] = []
    chunk_times: list[float] = []
    frame_times: list[float] = []
    completed: list[tuple[float, float]] = []
    speaker = Speaker.__new__(Speaker)
    speaker.duplex = SimpleNamespace(write=lambda pcm, token: writes.append(pcm))
    speaker.model = SimpleNamespace(
        synthesize=lambda text: iter(
            [SimpleNamespace(sample_rate=24000, sample_channels=1,
                             audio_int16_bytes=np.full(480 * 2, 3000, dtype="<i2").tobytes())]
        )
    )
    speaker.speak(
        "سلام",
        threading.Event(),
        on_first_chunk=chunk_times.append,
        on_first_frame=frame_times.append,
        on_complete=lambda at, played: completed.append((at, played)),
    )
    assert writes and len(writes[0]) == 640  # 20 ms at 16 kHz mono
    assert abs(float(np.frombuffer(writes[0], dtype="<i2").mean()) - 3000) < 30
    assert len(chunk_times) == 1 and len(frame_times) == 1
    assert completed and completed[0][1] > 0  # played duration in milliseconds


def test_empty_text_does_not_touch_the_model() -> None:
    def explode(text):  # pragma: no cover - must not be called
        raise AssertionError("synthesis must not run for empty text")

    speaker = Speaker.__new__(Speaker)
    speaker.model = SimpleNamespace(synthesize=explode)
    speaker.sd = SimpleNamespace()
    completed: list[float] = []
    speaker.speak("   ", threading.Event(), on_complete=lambda at, played: completed.append(played))
    assert completed == [0.0]


def test_incompatible_piper_chunk_is_a_user_facing_error() -> None:
    class BrokenChunk:
        pass

    class Output:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def write(self, data: bytes) -> None:
            pass

        def abort(self) -> None:
            pass

    speaker = Speaker.__new__(Speaker)
    speaker.sd = SimpleNamespace(RawOutputStream=lambda **kwargs: Output())
    speaker.model = SimpleNamespace(synthesize=lambda text: iter([BrokenChunk()]))
    with pytest.raises(VoiceError, match="AudioChunk"):
        speaker.speak("سلام", threading.Event())


def test_speaker_only_path_resamples_piper_rate_to_the_output_stream() -> None:
    """Regression: 22 050 Hz Piper audio used to be written into a 16 kHz stream.

    Persian Piper voices are commonly 22.05 kHz.  The speaker-only path opened
    the output device at ``PVAI_AUDIO_SAMPLE_RATE`` (16 kHz) but wrote the
    chunk's native samples unchanged, so playback ran ~27 % fast at the wrong
    pitch while every duplex (AEC) run sounded correct.
    """
    pytest.importorskip("numpy")
    pytest.importorskip("scipy")
    import numpy as np

    opened: list[dict] = []
    writes: list[bytes] = []

    class Output:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def write(self, data: bytes) -> None:
            writes.append(data)

        def abort(self) -> None:  # pragma: no cover - not cancelled here
            pass

    def open_stream(**kwargs):
        opened.append(kwargs)
        return Output()

    speaker = Speaker.__new__(Speaker)
    speaker.settings = Settings(audio_sample_rate=16000)
    speaker.sd = SimpleNamespace(RawOutputStream=open_stream)
    speaker.model = SimpleNamespace(
        synthesize=lambda text: iter(
            [
                SimpleNamespace(
                    sample_rate=22050,
                    sample_channels=1,
                    audio_int16_bytes=np.full(441, 1200, dtype="<i2").tobytes(),
                )
            ]
        )
    )
    speaker.speak("سلام", threading.Event())

    assert opened and opened[0]["samplerate"] == 16000 and opened[0]["channels"] == 1
    assert writes and all(len(piece) == 640 for piece in writes)
    samples = np.frombuffer(b"".join(writes), dtype="<i2")
    assert len(samples) == 320  # 20 ms at 16 kHz, not 441 samples
    assert abs(float(samples.mean()) - 1200) < 60  # amplitude preserved


def test_native_rate_chunk_is_written_verbatim_without_resampling() -> None:
    speaker = Speaker.__new__(Speaker)
    speaker.settings = Settings(audio_sample_rate=16000)
    payload = pcm_block(samples=320)
    pcm, rate, channels = speaker._chunk_pcm(chunk(rate=16000, channels=1, samples=320))
    assert (pcm, rate, channels) == (payload, 16000, 1)


def test_non_16k_capture_rate_is_a_configuration_error() -> None:
    problems = Settings(audio_sample_rate=48000).config_errors()
    assert any("16000" in problem for problem in problems)
