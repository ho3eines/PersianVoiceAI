"""AEC readiness probes: they must describe the state without allocating a filter."""

from __future__ import annotations

from types import SimpleNamespace

from server import aec


def _settings(**overrides):
    base = {"aec_enabled": True, "aec_library": "", "audio_sample_rate": 16000, "audio_block_ms": 20}
    base.update(overrides)
    return SimpleNamespace(**base)


def test_probe_reports_a_missing_library_with_an_actionable_farsi_message(monkeypatch):
    monkeypatch.setattr(aec.ctypes.util, "find_library", lambda name: None)
    status = aec.probe_aec("")
    assert status["available"] is False
    assert "PVAI_AEC_LIBRARY" in status["detail"]


def test_probe_reports_a_configured_path_that_is_not_a_file(tmp_path):
    status = aec.probe_aec(str(tmp_path / "absent.so"))
    assert status["available"] is False
    assert status["library"] == str(tmp_path / "absent.so")


def test_probe_reports_a_file_that_is_not_a_loadable_library(tmp_path):
    fake = tmp_path / "speexdsp.dll"
    fake.write_bytes(b"not a real shared library")
    status = aec.probe_aec(str(fake))
    assert status["available"] is False
    assert "بارگذاری" in status["detail"]


def test_status_is_disabled_without_touching_the_library_when_aec_is_off():
    status = aec.aec_status(_settings(aec_enabled=False))
    assert status["enabled"] is False
    assert status["available"] is None


def test_status_refuses_a_non_16khz_or_non_20ms_configuration(monkeypatch):
    monkeypatch.setattr(aec.ctypes.util, "find_library", lambda name: "/fake/speexdsp")
    for overrides in ({"audio_sample_rate": 48000}, {"audio_block_ms": 40}):
        status = aec.aec_status(_settings(**overrides))
        assert status["enabled"] is True
        assert status["available"] is False
        assert "16kHz" in status["detail"]


def test_status_never_raises_for_a_bare_object():
    status = aec.aec_status(SimpleNamespace())
    assert status["enabled"] is False
