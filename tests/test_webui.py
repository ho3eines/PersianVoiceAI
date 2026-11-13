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
from server.webui import MAX_HISTORY_MESSAGES, create_app


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


def make_client(
    *,
    asr: FakeAsr | None = None,
    speaker: FakeSpeaker | None = None,
    client: FakeClient | None = None,
) -> tuple[TestClient, FakeAsr, FakeSpeaker, FakeClient]:
    app = create_app(
        settings=Settings(),
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
    assert "محیط تست" in response.text


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


def test_reset_clears_history_and_history_never_grows_unbounded() -> None:
    client, *_ = make_client()
    with client:
        for index in range(MAX_HISTORY_MESSAGES):
            assert client.post("/api/turn", json={"text": f"پیام {index}"}).status_code == 200
        history = client.get("/api/history").json()["messages"]
        assert len(history) == MAX_HISTORY_MESSAGES
        # The oldest turns fall out; the newest user message survives.
        assert history[-2]["content"] == f"پیام {MAX_HISTORY_MESSAGES - 1}"

        assert client.post("/api/reset").json()["status"] == "ok"
        assert client.get("/api/history").json()["messages"] == []
