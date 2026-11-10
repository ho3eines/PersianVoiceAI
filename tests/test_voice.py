from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from server.config import Settings
from server.voice import LocalAudio, VoiceError, VoiceSession, local_api_url


class Audio:
    text = "سلام"
    spoken = ""

    def transcribe(self, source: Path) -> str:
        return self.text

    def speak(self, text: str, output: Path) -> None:
        self.spoken = text


def test_voice_turn_and_history() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.read())
        return httpx.Response(200, json={"choices": [{"message": {"content": "درود"}}],
                                        "timings": {"wall_ms": 10}})

    audio = Audio()
    with httpx.Client(transport=httpx.MockTransport(handler), base_url="http://127.0.0.1") as client:
        session = VoiceSession(client, audio)
        for _ in range(8):
            timings = session.turn(Path("in.wav"), Path("out.wav"))
        assert len(session.history) == 12
        assert session.history[0]["role"] == "user"
        assert audio.spoken == "درود"
        assert timings["llm"]["wall_ms"] == 10
        assert len(requests) == 8


@pytest.mark.parametrize("reply", [{}, {"choices": []}, {"choices": [{"message": {"content": ""}}]}])
def test_bad_reply_does_not_update_history(reply: dict) -> None:
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=reply)),
                      base_url="http://127.0.0.1") as client:
        session = VoiceSession(client, Audio())
        with pytest.raises(VoiceError):
            session.turn(Path("in"), Path("out"))
        assert session.history == []


def test_silence_never_calls_api() -> None:
    audio = Audio()
    audio.text = ""
    with pytest.raises(VoiceError, match="گفتاری"):
        VoiceSession(None, audio).turn(Path("in"), Path("out"))


def test_local_models_required(tmp_path: Path) -> None:
    with pytest.raises(VoiceError, match="Whisper"):
        LocalAudio(Settings(asr_model_path=str(tmp_path))).validate()


def test_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PVAI_VOICE_RECORD_SECONDS", "999")
    monkeypatch.setenv("PVAI_ASR_MODEL_PATH", "local/model")
    settings = Settings.from_env()
    assert settings.voice_record_seconds == 30
    assert settings.asr_model_path == "local/model"


@pytest.mark.parametrize("host", ["8.8.8.8", "example.com"])
def test_public_api_rejected(host: str) -> None:
    with pytest.raises(VoiceError):
        local_api_url(Settings(api_host=host))


def test_bind_address_maps_to_loopback() -> None:
    assert local_api_url(Settings(api_host="0.0.0.0")) == "http://127.0.0.1:8000"


def test_whisper_never_downloads(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    options = {}

    def model(path: str, **kwargs):
        options.update(kwargs)
        return SimpleNamespace(transcribe=lambda *a, **k: ([SimpleNamespace(text=" سلام ")], None))

    monkeypatch.setattr("server.voice.dependency", lambda _: SimpleNamespace(WhisperModel=model))
    (tmp_path / "model.bin").touch()
    assert LocalAudio(Settings(asr_model_path=str(tmp_path)))._transcribe("stub") == "سلام"
    assert options["local_files_only"] is True


def test_module_startup_missing_model_has_no_traceback(tmp_path: Path) -> None:
    import os
    import subprocess
    import sys
    environment = dict(os.environ, PVAI_ASR_MODEL_PATH=str(tmp_path / 'absent'))
    result = subprocess.run([sys.executable, '-m', 'server.voice'], env=environment,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 1
    assert 'Whisper' in result.stderr
    assert 'Traceback' not in result.stderr
