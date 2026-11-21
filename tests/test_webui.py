"""The local web test bench: one turn, with fake components and no models.

The bench exists so a human can hear the real pipeline on a slow laptop; these
tests pin its contract only — what the page sends, what it gets back, and that
a missing transcript, bad audio or a dead llama-server are reported instead of
producing a fake reply.
"""

from __future__ import annotations

import asyncio
import base64
import io
import sys
import threading
import wave
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from server.config import Settings
from server.llm import ChatResult, LlamaServerUnavailable, Timings
from server.webui import create_app


def _timings() -> Timings:
    return Timings(
        wall_ms=12.0,
        total_ms=10.0,
        prompt_eval_ms=2.0,
        generation_ms=8.0,
        prompt_tokens=7,
        completion_tokens=5,
        tokens_per_second=1.5,
    )


class FakeAsr:
    def __init__(self, text: str = "سلام، حال شما چطور است؟") -> None:
        self.text = text
        self.calls: list[bytes] = []
        self.rates: list[int] = []

    def transcribe_pcm(self, pcm: bytes, token: Any = None, job: Any = None, **_: Any) -> str:
        self.calls.append(pcm)
        self.rates.append(_.get("sample_rate", 16000))
        return self.text

    def close(self) -> None:
        return None


class FakeSpeaker:
    def __init__(self) -> None:
        self.synthesized: list[str] = []

    def synthesize_pcm(self, text: str) -> tuple[bytes, int]:
        self.synthesized.append(text)
        return b"\x00\x00" * 800, 16000  # 50 ms of silence at 16 kHz

    def close(self) -> None:
        return None


class FakeClient:
    def __init__(self, reply: str = "سلام! خوبم، ممنون.", unavailable: bool = False) -> None:
        self.reply = reply
        self.unavailable = unavailable
        self.requests: list[list[dict[str, str]]] = []

    async def chat(self, messages: Any, **_: Any) -> ChatResult:
        if self.unavailable:
            raise LlamaServerUnavailable("به llama-server وصل نشدم.")
        self.requests.append([dict(message) for message in messages])
        return ChatResult(
            content=self.reply, model="fake", finish_reason="stop", timings=_timings()
        )

    async def stream_chat(self, messages: Any, **_: Any):
        """Same reply, delivered as deltas then a final result (as llama does)."""
        from server.llm import StreamDelta, StreamDone

        if self.unavailable:
            raise LlamaServerUnavailable("به llama-server وصل نشدم.")
        self.requests.append([dict(message) for message in messages])
        for piece in self.reply.split():
            yield StreamDelta(piece + " ")
        yield StreamDone(
            ChatResult(content=self.reply, model="fake", finish_reason="stop", timings=_timings())
        )

    async def aclose(self) -> None:
        return None


_TEST_DATA_DIR: list[str] = []


@pytest.fixture(scope="session", autouse=True)
def _cleanup_test_data_dirs():
    """Remove only data directories created by this test module."""
    yield
    import shutil

    for data_dir in _TEST_DATA_DIR:
        shutil.rmtree(data_dir)
    _TEST_DATA_DIR.clear()


def make_client(
    *,
    asr: FakeAsr | None = None,
    speaker: FakeSpeaker | None = None,
    client: FakeClient | None = None,
    settings: Settings | None = None,
    models_dir: Any = None,
    env_path: Any = None,
    speaker_factory: Any = None,
) -> tuple[TestClient, FakeAsr, FakeSpeaker, FakeClient]:
    # A fresh data dir per client: the bench now persists its conversation, so
    # tests must never read or write the real ``data/`` folder or each other's log.
    import tempfile

    data_dir = tempfile.mkdtemp(prefix="pvai-webui-")
    _TEST_DATA_DIR.append(data_dir)
    app = create_app(
        settings=settings or Settings(data_dir=data_dir),
        asr=asr or FakeAsr(),
        speaker=speaker or FakeSpeaker(),
        client=client or FakeClient(),
        models_dir=models_dir,
        env_path=env_path,
        speaker_factory=speaker_factory,
        preload=False,
    )
    return (
        TestClient(app, base_url="http://localhost", client=("127.0.0.1", 12345)),
        app.state.webui.asr,
        app.state.webui.speaker,
        app.state.webui.client,
    )


@pytest.mark.parametrize(("peer", "url"), [
    ("192.168.1.42", "http://localhost"),
    ("127.0.0.1", "http://bench.invalid"),
])
def test_bench_rejects_nonlocal_requests(peer: str, url: str) -> None:
    client, asr, speaker, model = make_client()
    with TestClient(client.app, base_url=url, client=(peer, 12345)) as remote:
        assert remote.get("/api/history").status_code == 403
        assert remote.post("/api/chat", json={"text": "سلام"}).status_code == 403
    assert not (asr.calls or speaker.synthesized or model.requests)


def test_bench_cli_rejects_a_lan_bind(monkeypatch: pytest.MonkeyPatch) -> None:
    from server import webui

    monkeypatch.setattr(sys, "argv", ["server.webui", "--host", "0.0.0.0"])
    with pytest.raises(SystemExit) as exc:
        webui.main()
    assert exc.value.code == 2


def test_bench_accepts_only_same_origin_browser_requests() -> None:
    client, _, _, model = make_client()
    with client:
        rejected = client.post("/api/chat", json={"text": "سلام"}, headers={"Origin": "http://example.invalid"})
        assert rejected.status_code == 403
        assert not model.requests
        accepted = client.post("/api/chat", json={"text": "سلام"}, headers={"Origin": "http://localhost"})
    assert accepted.status_code == 200


def test_index_page_is_served() -> None:
    client, *_ = make_client()
    with client:
        response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "گفتگو و دیباگ" in response.text


def test_the_page_keeps_the_stages_apart() -> None:
    """The whole point of the rebuild: a failure must be attributable.

    If the page sent one request for the whole chain, a bad transcript and a bad
    reply would look the same. Each stage therefore needs its own control, and
    the retranscribe result needs a way back into the transcript box.
    """
    client, *_ = make_client()
    with client:
        page = client.get("/").text
    for control in ("mic", "transcript", "retranscribe", "applyRetrans", "sendChat", "playTts"):
        assert f'id="{control}"' in page, f"the page lost the {control} control"
    for heading in ("ضبط و رونویسی", "تنظیمات ASR", "مدل", "صدا"):
        assert heading in page, f"the page lost the «{heading}» stage"


def test_the_page_is_one_shell_with_the_panel() -> None:
    """Both surfaces must share the same shell, font and local-only assets."""
    client, *_ = make_client()
    with client:
        page = client.get("/").text
    assert 'href="/assets/theme.css"' in page
    assert 'href="/control"' in page, "the bench must link back to the panel"
    assert 'id="menuToggle"' in page, "the sidebar needs a way back on small screens"
    assert 'dir="rtl"' in page and 'lang="fa"' in page
    for external in ("http://cdn", "https://cdn", "fonts.googleapis", "unpkg.com"):
        assert external not in page, f"the page must stay offline ({external})"


def test_shared_assets_are_served_locally() -> None:
    client, *_ = make_client()
    with client:
        theme = client.get("/assets/theme.css")
        font = client.get("/assets/fonts/Vazirmatn-var.woff2")
        script = client.get("/assets/voice.js")
        escape = client.get("/assets/../../server/webui.py")
    assert theme.status_code == 200 and "text/css" in theme.headers["content-type"]
    assert "Vazirmatn" in theme.text
    assert font.status_code == 200 and font.headers["content-type"].startswith("font/woff2")
    assert font.content[:4] == b"wOF2", "the bundled font must be a real woff2 file"
    assert script.status_code == 200 and "javascript" in script.headers["content-type"]
    assert escape.status_code == 404, "path traversal must not escape web/assets"


def test_streamed_chat_emits_deltas_then_a_final_reply() -> None:
    client, _asr, _speaker, llm = make_client()
    with client:
        response = client.post("/api/chat/stream", json={"text": "سلام!"})
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    events = [
        line[6:]
        for line in response.text.splitlines()
        if line.startswith("data: ") and line[6:] != "[DONE]"
    ]
    import json as _json

    parsed = [_json.loads(event) for event in events]
    deltas = [event for event in parsed if event["type"] == "delta"]
    done = [event for event in parsed if event["type"] == "done"]
    assert deltas, "the stream must deliver text before it finishes"
    assert done and done[0]["reply"] == "سلام! خوبم، ممنون."
    assert done[0]["ttft_ms"] is not None
    assert response.text.rstrip().endswith("[DONE]")
    assert llm.requests, "the model must receive the turn"


def test_streamed_chat_reports_a_dead_backend_instead_of_a_fake_reply() -> None:
    client, *_ = make_client(client=FakeClient(unavailable=True))
    with client:
        response = client.post("/api/chat/stream", json={"text": "سلام!"})
    assert response.status_code == 200
    assert '"type": "error"' in response.text
    assert "خوبم" not in response.text


def test_diagnostics_describes_every_component() -> None:
    client, *_ = make_client()
    with client:
        data = client.get("/api/diagnostics").json()
    for key in ("server", "asr", "tts", "llm", "conversation", "audio", "thinking"):
        assert key in data, f"diagnostics lost {key}"
    assert data["asr"]["status"] == "ready"
    assert data["tts"]["status"] == "ready"
    assert "model" in data["asr"] and "device" in data["asr"]


def test_transcribe_overrides_the_settings_for_one_decode_only() -> None:
    """The ASR settings are swapped in place, so they must come back afterwards.

    A leak here would silently apply one experiment's beam_size to every later
    turn — exactly the confusion this bench is meant to remove.
    """
    import dataclasses

    base = Settings()
    asr = FakeAsr("یک بار")
    asr.settings = base
    client, *_ = make_client(asr=asr)
    with client:
        data = client.post(
            "/api/transcribe",
            json={"pcm_b64": base64.b64encode(bytes(3200)).decode("ascii"), "beam_size": 5},
        ).json()
    assert data["transcript"] == "یک بار"
    assert asr.settings is base, "the override must be restored, not left behind"
    assert asr.settings.asr_beam_size == base.asr_beam_size


def test_status_reports_injected_components_as_ready() -> None:
    client, *_ = make_client()
    with client:
        data = client.get("/api/status").json()
    assert data["asr"] == "ready"
    assert data["tts"] == "ready"
    assert data["messages"] == 0


def test_text_turn_returns_reply_and_playable_audio() -> None:
    client, asr, speaker, llm = make_client()
    with client:
        response = client.post("/api/turn", json={"text": "سلام!"})
    assert response.status_code == 200
    data = response.json()
    assert data["transcript"] == "سلام!"
    assert data["reply"] == "سلام! خوبم، ممنون."
    assert speaker.synthesized == [data["reply"]]
    assert llm.requests and llm.requests[-1][-1] == {"role": "user", "content": "سلام!"}

    wav = base64.b64decode(data["tts_wav_b64"])
    with wave.open(io.BytesIO(wav), "rb") as parsed:
        assert parsed.getframerate() == 16000
        assert parsed.getnchannels() == 1
        assert parsed.getsampwidth() == 2
        assert parsed.getnframes() == 800
    assert data["timings"]["llm_ms"] >= 0


def test_voice_turn_runs_the_injected_asr_and_keeps_the_transcript() -> None:
    client, asr, _, _ = make_client(asr=FakeAsr("دو به‌علاوه دو چند است؟"))
    pcm = bytes(3200)  # 100 ms of silence; the fake ignores the content
    with client:
        response = client.post(
            "/api/turn", json={"pcm_b64": base64.b64encode(pcm).decode("ascii")}
        )
    assert response.status_code == 200
    data = response.json()
    assert data["transcript"] == "دو به‌علاوه دو چند است؟"
    assert asr.calls == [pcm]
    assert data["timings"]["asr_ms"] >= 0


def test_the_last_recording_is_kept_for_offline_asr_comparison() -> None:
    """ASR tuning must be repeatable on the exact same audio.

    Asking the speaker to repeat a sentence for every parameter change makes
    comparisons meaningless, so the bench keeps the last upload and serves it
    back as a WAV.
    """
    client, *_ = make_client()
    pcm = bytes(3200)
    with client:
        assert client.get("/api/last.wav").status_code == 404
        client.post("/api/turn", json={"pcm_b64": base64.b64encode(pcm).decode("ascii")})
        response = client.get("/api/last.wav")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/wav")
    with wave.open(io.BytesIO(response.content), "rb") as parsed:
        assert parsed.getframerate() == 16000
        assert parsed.getnframes() == 1600


def test_transcribe_endpoint_runs_asr_only() -> None:
    """The step-by-step bench: transcription must not touch LLM or TTS."""
    client, asr, speaker, llm = make_client(asr=FakeAsr("سلام دنیا"))
    pcm = bytes(3200)  # 100 ms
    with client:
        response = client.post(
            "/api/transcribe", json={"pcm_b64": base64.b64encode(pcm).decode("ascii")}
        )
    data = response.json()
    assert response.status_code == 200
    assert data["transcript"] == "سلام دنیا"
    assert asr.calls == [pcm]
    assert llm.requests == []
    assert speaker.synthesized == []
    assert data["audio"]["seconds"] == 0.1
    assert "rms_dbfs" in data["audio"]


def test_retranscribe_reuses_the_saved_recording_with_overrides() -> None:
    client, asr, speaker, llm = make_client(asr=FakeAsr("یک بار"))
    pcm = bytes(6400)
    with client:
        client.post("/api/transcribe", json={"pcm_b64": base64.b64encode(pcm).decode("ascii")})
        data = client.post(
            "/api/transcribe",
            json={
                "use_last": True,
                "beam_size": 5,
                "language": "auto",
                "initial_prompt": "این یک گفتگوی فارسی است.",
            },
        ).json()
    assert data["transcript"] == "یک بار"
    assert asr.calls == [pcm, pcm], "the saved capture must be re-decoded, not the client's"
    assert speaker.synthesized == []


def test_turn_and_repeat_reuse_the_native_recording_rate() -> None:
    client, asr, *_ = make_client()
    pcm = base64.b64encode(bytes(32000)).decode("ascii")
    with client:
        assert client.post("/api/turn", json={"pcm_b64": pcm, "sample_rate": 8000}).status_code == 200
        repeated = client.post("/api/transcribe", json={"use_last": True}).json()
        recording = client.get("/api/last.wav").content
    assert asr.rates == [8000, 8000]
    assert repeated["audio"]["seconds"] == 2.0
    with wave.open(io.BytesIO(recording), "rb") as parsed:
        assert parsed.getframerate() == 8000


def test_asr_startup_does_not_require_piper(tmp_path, monkeypatch) -> None:
    from server.voice import LocalAudio

    (tmp_path / "model.bin").touch()
    warmed = []
    monkeypatch.setattr(LocalAudio, "warmup", lambda audio: warmed.append(audio))
    settings = Settings(
        asr_model_path=str(tmp_path), tts_model_path=str(tmp_path / "missing.onnx"),
        data_dir=str(tmp_path), history_persist=False,
    )
    app = create_app(settings, client=FakeClient(), preload=False)
    with TestClient(app):
        audio = app.state.webui._build_asr()
        assert warmed == [audio]
        audio.close()


def test_tts_waits_for_in_flight_turn_asr_even_if_request_is_cancelled(tmp_path) -> None:
    started, release = threading.Event(), threading.Event()

    class BlockingAsr(FakeAsr):
        def transcribe_pcm(self, pcm: bytes, **options: Any) -> str:
            started.set()
            if not release.wait(3):
                raise TimeoutError
            return super().transcribe_pcm(pcm, **options)

    async def run():
        speaker = FakeSpeaker()
        settings = Settings(data_dir=str(tmp_path), history_persist=False)
        app = create_app(settings, asr=BlockingAsr(), speaker=speaker, client=FakeClient(), preload=False)
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 12345))
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
            pcm = base64.b64encode(bytes(3200)).decode("ascii")
            turn = asyncio.create_task(client.post("/api/turn", json={"pcm_b64": pcm}))
            speech = None
            try:
                assert await asyncio.to_thread(started.wait, 1)
                turn.cancel()
                speech = asyncio.create_task(client.post("/api/tts", json={"text": "تست"}))
                await asyncio.sleep(0)
                assert app.state.webui.turn_lock.locked()
                assert not turn.done()
                assert not speech.done()
            finally:
                release.set()
                await asyncio.gather(turn, return_exceptions=True)
                if speech is not None:
                    assert (await speech).status_code == 200
            assert speaker.synthesized == ["تست"]
        await app.state.webui.aclose()

    asyncio.run(run())


def test_chat_endpoint_returns_text_without_synthesizing_audio() -> None:
    client, asr, speaker, llm = make_client(client=FakeClient(reply="پاسخ متنی"))
    with client:
        data = client.post("/api/chat", json={"text": "سلام"}).json()
    assert data["reply"] == "پاسخ متنی"
    assert speaker.synthesized == []
    assert llm.requests[-1][-1] == {"role": "user", "content": "سلام"}


def test_tts_endpoint_synthesizes_without_calling_the_model() -> None:
    client, asr, speaker, llm = make_client()
    with client:
        data = client.post("/api/tts", json={"text": "سلام، این یک آزمایش است."}).json()
    assert speaker.synthesized == ["سلام، این یک آزمایش است."]
    assert llm.requests == []
    with wave.open(io.BytesIO(base64.b64decode(data["tts_wav_b64"])), "rb") as parsed:
        assert parsed.getframerate() == 16000
    assert data["timings"]["tts_ms"] >= 0


def test_text_endpoints_reject_an_empty_prompt() -> None:
    client, _, speaker, llm = make_client()
    with client:
        assert client.post("/api/chat", json={"text": "   "}).status_code == 400
        assert client.post("/api/tts", json={"text": ""}).status_code == 400
    assert speaker.synthesized == []
    assert llm.requests == []


def test_invalid_audio_is_rejected_before_any_model_runs() -> None:
    client, asr, _, llm = make_client()
    with client:
        bad = client.post("/api/turn", json={"pcm_b64": "!!!not-base64"})
        odd = client.post("/api/turn", json={"pcm_b64": base64.b64encode(b"\x01").decode()})
    assert bad.status_code == 400
    assert odd.status_code == 400
    assert asr.calls == []
    assert llm.requests == []


def test_empty_transcript_returns_a_notice_without_calling_the_model() -> None:
    client, _, speaker, llm = make_client(asr=FakeAsr(""))
    pcm = base64.b64encode(bytes(3200)).decode("ascii")
    with client:
        data = client.post("/api/turn", json={"pcm_b64": pcm}).json()
    assert data["reply"] == ""
    assert data["tts_wav_b64"] == ""
    assert "دوباره" in data["notice"]
    assert llm.requests == []
    assert speaker.synthesized == []


def test_dead_llama_server_is_an_explicit_502() -> None:
    client, *_ = make_client(client=FakeClient(unavailable=True))
    with client:
        response = client.post("/api/turn", json={"text": "سلام"})
    assert response.status_code == 502
    assert "llama-server" in response.json()["detail"]


def test_reset_clears_the_window_but_keeps_the_log_on_disk() -> None:
    """Reset empties the RAM window only: the JSONL history is never deleted here.

    The user may ask the model about facts from earlier turns (their name, for
    example), so forgetting must be an explicit, separate action.
    """
    client, *_ = make_client()
    with client:
        assert client.post("/api/turn", json={"text": "اسم من آرش است"}).status_code == 200
        history = client.get("/api/history").json()["messages"]
        assert [message["role"] for message in history] == ["user", "assistant"]
        assert history[0]["content"] == "اسم من آرش است"

        assert client.post("/api/reset").json()["status"] == "ok"
        assert client.get("/api/history").json()["messages"] == []


def test_the_model_sees_earlier_turns_from_the_conversation() -> None:
    """The bench must not forget the user's name between two messages."""
    client, _, _, llm = make_client()
    with client:
        client.post("/api/chat", json={"text": "اسم من آرش است"})
        client.post("/api/chat", json={"text": "اسم من چیست؟"})
    last_request = llm.requests[-1]
    contents = [message["content"] for message in last_request]
    assert "اسم من آرش است" in contents, "the earlier fact must reach the model"
    assert contents[-1] == "اسم من چیست؟"


def test_voice_selection_persists_and_hot_swaps_the_speaker(tmp_path: Path) -> None:
    """Picking a voice must take effect now, not only after a restart."""
    models_dir = tmp_path / "models"
    for name in ("fa_IR-amir-medium", "fa_IR-gyro-medium"):
        folder = models_dir / name.split("-")[1]
        folder.mkdir(parents=True)
        (folder / f"{name}.onnx").write_bytes(b"\x00" * 8)
        (folder / f"{name}.onnx.json").write_text("{}", encoding="utf-8")
    env_path = tmp_path / ".env"
    env_path.write_text("PVAI_TTS_MODEL_PATH=models/piper/fa_IR.onnx\n", encoding="utf-8")
    settings = Settings(
        data_dir=str(tmp_path / "data"),
        tts_model_path=str(models_dir / "amir" / "fa_IR-amir-medium.onnx"),
    )
    built: list[tuple[Settings, FakeSpeaker]] = []

    def factory(new_settings: Settings) -> FakeSpeaker:
        speaker = FakeSpeaker()
        built.append((new_settings, speaker))
        return speaker

    old_speaker = FakeSpeaker()
    client, *_ = make_client(
        settings=settings,
        speaker=old_speaker,
        models_dir=models_dir,
        env_path=env_path,
        speaker_factory=factory,
    )
    with client:
        body = client.get("/api/voices").json()
        assert {voice["id"] for voice in body["voices"]} == {
            "fa_IR-amir-medium",
            "fa_IR-gyro-medium",
        }
        assert body["active_id"] == "fa_IR-amir-medium"

        response = client.post("/api/voices/select", json={"id": "fa_IR-gyro-medium"})
        assert response.status_code == 200, response.text
        assert response.json()["active_id"] == "fa_IR-gyro-medium"

        assert client.get("/api/voices").json()["active_id"] == "fa_IR-gyro-medium"

    assert "gyro" in env_path.read_text(encoding="utf-8")
    assert built and built[-1][0].tts_model_path.endswith("fa_IR-gyro-medium.onnx")
    assert client.app.state.webui.speaker is built[-1][1]


def test_voice_selection_rejects_an_unknown_voice(tmp_path: Path) -> None:
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    client, *_ = make_client(models_dir=models_dir, env_path=tmp_path / ".env")
    with client:
        response = client.post("/api/voices/select", json={"id": "ghost"})
    assert response.status_code == 404
