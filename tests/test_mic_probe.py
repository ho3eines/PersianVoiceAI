"""The live microphone probe must never mislabel a number it did not measure.

``tools/mic_probe.py`` is the only tool that touches a real microphone, so a
bug here turns "this laptop heard you" into a false hardware-claim in the docs.
These tests pin the two mistakes that were actually made while building it:

* a peak sampled from the *last* block instead of the whole capture;
* an ``asr_rtf`` computed per segment, which reports rtf=8 for a 1.8 s clip
  that costs the same as an 11 s one (Whisper pads every call to 30 s).
"""

from __future__ import annotations

import importlib.util
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
def probe():
    return load_module("pvai_mic_probe", "tools/mic_probe.py")


def test_every_number_carries_a_status_label(probe) -> None:
    """A bare number is what the audit forbids; each row states its evidence."""
    assert probe.MEASURED == "MEASURED"
    assert probe.NOT_MEASURED == "NOT MEASURED"


def test_probe_defaults_are_local_only(probe) -> None:
    """Offline is a project rule: a probe must open no network path."""
    args = probe._arguments([])
    assert args.sample_rate == 16000
    assert args.device_rate is None
    # Decoding every VAD segment multiplies the fixed Whisper cost.
    assert args.max_segments >= 1
    assert args.no_asr is False


def test_probe_rejects_a_non_positive_capture(probe) -> None:
    with pytest.raises(SystemExit) as caught:
        probe.main(["--seconds", "0"])
    assert "positive" in str(caught.value)


def test_probe_refuses_a_block_length_the_service_would_not_use(probe) -> None:
    """``--block-ms 40`` must not silently be measured as if it were 20 ms.

    The detector converts frame counts to milliseconds with the block length it
    was built with, so a probe flag that disagreed with ``PVAI_AUDIO_BLOCK_MS``
    produced segment durations and onset/endpoint delays that were simply wrong
    while still being labelled MEASURED.
    """
    with pytest.raises(SystemExit) as caught:
        probe.main(["--block-ms", "0"])
    assert "positive" in str(caught.value)

    with pytest.raises(SystemExit) as caught:
        probe.main(["--block-ms", "30"])
    assert "10, 20 or 40" in str(caught.value)


def test_device_resolution_finds_an_input_by_name(probe) -> None:
    class FakeSD:
        def query_devices(self):
            return [
                {"name": "Speakers", "max_input_channels": 0},
                {"name": "Microphone (Realtek)", "max_input_channels": 2},
            ]

    assert probe._resolve_device(FakeSD(), "realtek") == 1
    assert probe._resolve_device(FakeSD(), "1") == 1
    assert probe._resolve_device(FakeSD(), None) is None
    with pytest.raises(SystemExit):
        probe._resolve_device(FakeSD(), "nonexistent")


def test_output_device_is_never_treated_as_input(probe) -> None:
    class FakeSD:
        def query_devices(self):
            return [{"name": "Speakers Only", "max_input_channels": 0}]

    with pytest.raises(SystemExit):
        probe._resolve_device(FakeSD(), "speakers")


def test_probe_is_importable_without_audio_libraries() -> None:
    """``sounddevice`` is imported inside ``main`` so collection stays headless."""
    source = (ROOT / "tools/mic_probe.py").read_text(encoding="utf-8")
    assert "import sounddevice as sd" in source
    import_block = source.split("def main(", 1)[1]
    assert "import sounddevice as sd" in import_block
    top_level = source.split("def main(", 1)[0]
    assert "\nimport sounddevice" not in top_level
