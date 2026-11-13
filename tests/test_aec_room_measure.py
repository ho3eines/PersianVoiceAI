"""The real-room AEC tool must not turn a noisy measurement into a hardware claim.

``tools/aec_room_measure.py`` is the only tool that measures echo cancellation
on a real microphone/speaker pair.  Its numbers end up in
``docs/HARDWARE_VALIDATION.md`` as ``MEASURED``, so the arithmetic and the
labelling are what these tests pin — the audio hardware itself is not needed
(and is not present in CI).

Two mistakes this file exists to prevent:

* averaging ERLE over the pauses of a sentence, which divides room noise by
  room noise and reports a meaningless number (often negative);
* a frame-shift convention that reports the echo delay in the wrong direction,
  which would tell an operator to set ``PVAI_AEC_DELAY_MS`` to a value that
  makes cancellation worse.
"""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def room():
    np = pytest.importorskip("numpy")
    pytest.importorskip("scipy")
    assert np is not None
    return load_module("pvai_aec_room_measure", "tools/aec_room_measure.py")


# --------------------------------------------------------------------- parsing
def test_delay_spec_is_sorted_deduplicated_and_bounded(room) -> None:
    assert room._parse_delays("40, 0,20,20,60") == [0, 20, 40, 60]
    assert room._parse_delays("") == []
    with pytest.raises(SystemExit):
        room._parse_delays("-20")
    with pytest.raises(SystemExit):
        room._parse_delays("520")


def test_cli_defaults_use_speech_and_stay_local(room) -> None:
    args = room._arguments([])
    assert args.stimulus == "speech"
    assert args.filter_ms == 250
    assert args.delay_ms == 40
    assert args.list_devices is False
    assert 0 < args.amplitude <= 0.9


# --------------------------------------------------------------------- signals
def test_band_power_measures_only_the_band_it_names(room) -> None:
    np = pytest.importorskip("numpy")
    time_axis = np.arange(room.RATE) / room.RATE
    tone = 0.5 * np.sin(2 * math.pi * 1000 * time_axis)
    in_band = room._band_rms(tone, 800.0, 1200.0)
    out_of_band = room._band_rms(tone, 2000.0, 3000.0)
    assert in_band == pytest.approx(0.5 / math.sqrt(2), rel=0.05)
    assert out_of_band < 0.01


def test_line_amplitude_reads_one_frequency(room) -> None:
    np = pytest.importorskip("numpy")
    time_axis = np.arange(room.RATE) / room.RATE
    tone = 0.4 * np.sin(2 * math.pi * 2500 * time_axis)
    assert room._line_amplitude(tone, 2500.0) == pytest.approx(0.4, rel=0.02)
    assert room._line_amplitude(tone, 700.0) < 0.02


def test_erle_reports_the_energy_ratio_in_db(room) -> None:
    np = pytest.importorskip("numpy")
    rng = np.random.default_rng(1)
    mic = rng.normal(0, 0.1, room.RATE).astype("float32")
    cancelled = mic * 0.1
    assert room._erle(mic, cancelled, 100.0, 7000.0) == pytest.approx(20.0, abs=0.3)


def test_active_frames_exclude_pauses(room) -> None:
    """Silence must not enter the ERLE window: it only dilutes the number."""
    np = pytest.importorskip("numpy")
    rng = np.random.default_rng(2)
    quiet = rng.normal(0, 0.001, room.RATE).astype("float32")
    loud = rng.normal(0, 0.1, room.RATE * 3).astype("float32")
    mask, threshold = room._active_mask(np.concatenate([quiet, loud]))
    assert threshold > 0
    assert not mask[: room.RATE].any(), "the quiet part must not be counted"
    assert mask[room.RATE :].all(), "the loud part must be counted"


def test_acoustic_delay_finds_a_known_shift_and_its_direction(room) -> None:
    """The microphone echo must be reported as *later* than the played reference."""
    np = pytest.importorskip("numpy")
    rng = np.random.default_rng(3)
    reference = rng.normal(0, 0.2, room.RATE * 2).astype("float32")
    shift = 10 * room.FRAME_SAMPLES  # 200 ms
    microphone = np.concatenate([np.zeros(shift, dtype="float32"), reference[:-shift]])
    delay_ms, prominence = room._acoustic_delay_ms(microphone, reference, start_s=0.0)
    assert delay_ms == pytest.approx(200, abs=20)
    assert prominence > 1.0


def test_frames_are_exact_twenty_millisecond_blocks(room) -> None:
    pcm = bytes(room.FRAME_BYTES * 3 + 17)
    frames = room._frames(pcm)
    assert len(frames) == 3
    assert all(len(frame) == room.FRAME_BYTES for frame in frames)


def test_tool_is_importable_without_audio_libraries(room) -> None:
    """``sounddevice`` may only be imported inside the functions that use a device."""
    source = (ROOT / "tools/aec_room_measure.py").read_text(encoding="utf-8")
    top_level = source.split("def _arguments(", 1)[0]
    assert "\nimport sounddevice" not in top_level
    assert "sounddevice" in source.split("def record_duplex(", 1)[1]
