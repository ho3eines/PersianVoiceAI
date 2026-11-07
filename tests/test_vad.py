"""VAD acceptance tests: quiet speech, noise, pre-roll, hysteresis, segmentation."""

from __future__ import annotations

from array import array

import pytest

from server.config import Settings
from server.live_voice import Endpoint
from server.vad import EnergyVAD, block_rms, build_vad


def pcm(level: int) -> bytes:
    return array("h", [level] * 320).tobytes()


def segment_frames(segment: bytes) -> list[int]:
    samples = array("h")
    samples.frombytes(segment)
    return [samples[index] for index in range(0, len(samples), 320)]


def test_block_rms_is_normalised_and_ignores_odd_tail() -> None:
    assert block_rms(pcm(0)) == 0.0
    assert 0.05 < block_rms(pcm(2000)) < 0.07
    assert block_rms(b"\x01") == 0.0


def test_quiet_persian_speech_is_detected() -> None:
    # ~0.02 RMS is quiet speech (a soft voice at low input gain), above the
    # configured 0.015 threshold even though it is far below a normal "loud" block.
    vad = EnergyVAD(0.015, silence_frames=5)
    onset = False
    for _ in range(3):
        onset, _ = vad.feed(pcm(650))
    assert onset is True


def test_background_noise_does_not_trigger_speech() -> None:
    vad = EnergyVAD(0.015, silence_frames=5, noise_adapt=True, noise_multiplier=2.5)
    # Stationary fan noise just below the base threshold (rms ~0.012).
    for _ in range(300):
        assert vad.feed(pcm(400))[0] is False
    assert vad.noise_floor > 0
    # A room that is *this* noisy requires louder speech before an onset is claimed.
    for _ in range(100):
        assert vad.feed(pcm(500))[0] is False  # rms ~0.015 < adapted onset level


def test_adaptive_floor_raises_onset_level_but_real_speech_still_triggers() -> None:
    vad = EnergyVAD(0.015, silence_frames=5, noise_adapt=True, noise_multiplier=2.5)
    for _ in range(300):
        vad.feed(pcm(400))  # rms ~0.012: measurable noise, never speech
    assert vad.onset_level > vad.threshold
    onset = False
    for _ in range(3):
        onset, _ = vad.feed(pcm(6000))  # rms ~0.18: clearly speech
    assert onset is True


def test_pre_roll_preserves_initial_consonants() -> None:
    vad = EnergyVAD(0.02, silence_frames=4, onset_frames=2, pre_roll_frames=10)
    for _ in range(10):
        vad.feed(pcm(10))  # 200 ms of near-silence before the first phoneme
    onset = False
    for _ in range(2):
        onset, _ = vad.feed(pcm(3000))
    assert onset is True
    # The captured segment starts with the pre-roll, so the first syllable is intact.
    assert segment_frames(b"".join(vad.frames))[0] == 10
    assert len(vad.frames) == 12


def test_hysteresis_keeps_quiet_syllables_inside_the_utterance() -> None:
    vad = EnergyVAD(0.02, silence_frames=3, onset_frames=1, offset_threshold=0.01,
                    min_voiced_frames=1)
    vad.feed(pcm(3000))
    assert vad.started is True
    # A quieter syllable (rms ~0.012) sits between onset and offset thresholds:
    # it must not count as silence.
    for _ in range(5):
        onset, segment = vad.feed(pcm(400))
        assert segment is None
    assert vad.quiet == 0
    # Below the offset threshold it does count.
    for _ in range(3):
        _, segment = vad.feed(pcm(10))
    assert segment is not None


def test_short_noise_burst_is_rejected_as_an_utterance() -> None:
    vad = EnergyVAD(0.02, silence_frames=2, onset_frames=1, min_voiced_frames=8)
    vad.feed(pcm(3000))
    for _ in range(2):
        _, segment = vad.feed(pcm(0))
    assert segment is None  # a cough is not a sentence


def test_long_speech_is_segmented_without_losing_audio() -> None:
    vad = EnergyVAD(0.02, silence_frames=5, onset_frames=1, max_frames=10, pre_roll_frames=2)
    first = None
    for index in range(1, 11):
        _, first = vad.feed(pcm(1000 + index))  # 1001..1010 identify every frame
    assert first is not None and len(first) == 6400
    assert segment_frames(first) == [1000 + index for index in range(1, 11)]
    # Speech continues: the next segment must start with the very next frame.
    second = None
    for index in range(11, 21):
        _, second = vad.feed(pcm(1000 + index))
    assert second is not None
    assert segment_frames(second) == [1000 + index for index in range(11, 21)]
    assert segment_frames(second)[0] == 1011  # no frame lost at the 10-frame split


def test_reset_clears_state_and_boundaries_are_recorded() -> None:
    vad = EnergyVAD(0.02, silence_frames=2, onset_frames=1)
    vad.feed(pcm(3000))
    assert vad.last_boundary is not None
    assert vad.last_boundary.kind == "onset"
    vad.reset()
    assert vad.frames == [] and vad.started is False
    assert vad.feed(pcm(0)) == (False, None)


def test_endpoint_delay_reporting() -> None:
    vad = EnergyVAD(0.02, silence_frames=30, onset_frames=3)
    assert vad.onset_delay_ms() == pytest.approx(60.0)
    assert vad.endpoint_delay_ms() == pytest.approx(600.0)


def test_build_vad_maps_settings() -> None:
    settings = Settings(
        voice_vad_threshold=0.02,
        voice_vad_onset_ms=80,
        voice_vad_silence_ms=700,
        voice_vad_pre_roll_ms=300,
        voice_vad_min_speech_ms=200,
        voice_vad_max_segment_s=12.0,
        voice_vad_noise_adapt=False,
    )
    vad = build_vad(settings)
    assert vad.threshold == pytest.approx(0.02)
    assert vad.onset_frames == 4
    assert vad.silence_frames == 35
    assert vad.pre_roll_frames == 15
    assert vad.min_voiced_frames == 10
    assert vad.max_frames == 600
    assert vad.offset_threshold == pytest.approx(0.012)


def test_legacy_endpoint_keeps_its_contract() -> None:
    detector = Endpoint(0.01, silence_frames=3)
    for _ in range(10):
        assert detector.feed(pcm(0)) == (False, None)
    assert detector.feed(pcm(2000))[0] is False
    assert detector.feed(pcm(2000))[0] is False
    assert detector.feed(pcm(2000))[0] is True
    for _ in range(5):
        detector.feed(pcm(2000))
    detector.feed(pcm(0))
    detector.feed(pcm(0))
    assert detector.feed(pcm(0))[1] is not None


def test_describe_reports_the_effective_continuation_level() -> None:
    """The diagnostic must show the threshold actually used, not the base one.

    With an adaptive noise floor the continuation threshold is scaled up; the
    old ``describe()`` reported ``offset_threshold`` (the raw base), which hid
    why a quiet syllable ended an utterance in a noisy room.
    """
    from server.vad import EnergyVAD

    vad = EnergyVAD(0.01, offset_threshold=0.006, noise_adapt=True, noise_multiplier=3.0)
    vad.noise_floor = 0.004  # measured background level
    description = vad.describe()
    assert description["offset_level"] == round(vad.continuation_level, 5)
    assert description["offset_level"] > 0.006
    assert description["onset_level"] == 0.012
