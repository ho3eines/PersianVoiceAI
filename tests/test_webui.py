"""The local web test bench: one turn, with fake components and no models.

The bench exists so a human can hear the real pipeline on a slow laptop; these
tests pin its contract only — what the page sends, what it gets back, and that
a missing transcript, bad audio or a dead llama-server are reported instead of
producing a fake reply.
"""

from __future__ import annotations

import base64
import io
import wave
from typing import Any

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

    def transcribe_pcm(self, pcm: bytes, token: Any = None, job: Any = None, **_: Any) -> str:
        self.calls.append(pcm)
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

    async def aclose(self) -> None:
        return None


_TEST_DATA_DIR: list[str] = []


def make_client(
    *,
    asr: FakeAsr | None = None,
    speaker: FakeSpeaker | None = None,
    client: FakeClient | None = None,
) -> tuple[TestClient, FakeAsr, FakeSpeaker, FakeClient]:
    # A fresh data dir per client: the bench now persists its conversation, so
    # tests must never read or write the real ``data/`` folder or each other's log.
    import tempfile

    data_dir = tempfile.mkdtemp(prefix="pvai-webui-")
    _TEST_DATA_DIR.append(data_dir)
    app = create_app(
        settings=Settings(data_dir=data_dir),
        asr=asr or FakeAsr(),
        speaker=speaker or FakeSpeaker(),
        client=client or FakeClient(),
        preload=False,
    )
    return (
        TestClient(app),
        app.state.webui.asr,
        app.state.webui.speaker,
        app.state.webui.client,
    )


def test_index_page_is_served() -> None:
    client, *_ = make_client()
    with client:
        response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "تست مرحله‌به‌مرحله" in response.text


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
