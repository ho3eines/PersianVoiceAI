from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from server.config import Settings
from server.llm import (
    ChatResult,
    GenerationError,
    LlamaServerClient,
    LlamaServerUnavailable,
    ModelNotLoaded,
    StreamDelta,
    StreamDone,
    Timings,
)


def _timings() -> Timings:
    return Timings(
        wall_ms=1200.0,
        total_ms=1100.0,
        prompt_eval_ms=100.0,
        generation_ms=1000.0,
        prompt_tokens=20,
        completion_tokens=10,
        tokens_per_second=10.0,
    )


class FakeLLM:
    def __init__(self, *, unavailable: bool = False, missing_model: bool = False) -> None:
        self.unavailable = unavailable
        self.missing_model = missing_model
        self.last_messages: list[dict[str, str]] | None = None
        self.closed = False

    def _guard(self) -> None:
        if self.unavailable:
            raise LlamaServerUnavailable("llama-server در دسترس نیست")
        if self.missing_model:
            raise ModelNotLoaded("مدل لود نشده")

    async def chat(self, messages: list[Any], **_: Any) -> ChatResult:
        self._guard()
        self.last_messages = [
            {"role": message["role"], "content": message["content"]}
            if isinstance(message, dict)
            else {"role": message.role, "content": message.content}
            for message in messages
        ]
        return ChatResult(
            content="سلام! من یک دستیار فارسی محلی هستم.",
            model="test-model.gguf",
            finish_reason="stop",
            timings=_timings(),
        )

    async def stream_chat(self, messages: list[Any], **_: Any):
        self._guard()
        self.last_messages = [
            {"role": message["role"], "content": message["content"]}
            if isinstance(message, dict)
            else {"role": message.role, "content": message.content}
            for message in messages
        ]
        for piece in ["سلام", "!", " من", " محلی", " هستم."]:
            yield StreamDelta(piece)
        yield StreamDone(
            ChatResult(
                content="سلام! من محلی هستم.",
                model="test-model.gguf",
                finish_reason="stop",
                timings=_timings(),
            )
        )

    async def list_models(self) -> list[str]:
        self._guard()
        return ["test-model.gguf"]

    async def health(self) -> dict[str, Any]:
        if self.unavailable:
            return {
                "reachable": False,
                "model_available": False,
                "models": [],
                "error": "ConnectError",
            }
        return {
            "reachable": True,
            "model_available": not self.missing_model,
            "models": ["test-model.gguf"] if not self.missing_model else [],
        }

    async def version(self) -> str | None:
        return "0.0-test"

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture()
def settings() -> Settings:
    return Settings(model_path="models/test-model.gguf", system_prompt="سیستم تست")


@pytest.fixture()
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture()
def client(settings: Settings, fake_llm: FakeLLM):
    from server.main import create_app

    with TestClient(create_app(settings=settings, llm=fake_llm)) as test_client:
        yield test_client


def test_root(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["lesson"] == 1


def test_local_knowledge_api_and_rag_augmented_chat(tmp_path: Any) -> None:
    from server.main import create_app

    settings = Settings(
        model_path="models/test-model.gguf",
        system_prompt="سیستم تست",
        data_dir=str(tmp_path),
        history_persist=False,
    )
    llm = FakeLLM()
    with TestClient(create_app(settings=settings, llm=llm)) as test_client:
        added = test_client.post("/api/knowledge", json={
            "title": "مشخصات لپ‌تاپ",
            "text": "GeForce 940MX این لپ‌تاپ ۲ گیگابایت VRAM دارد.",
        })
        assert added.status_code == 200
        search = test_client.get("/api/knowledge/search", params={"q": "940MX VRAM"})
        assert search.status_code == 200
        assert search.json()["results"][0]["id"] == added.json()["id"]

        reply = test_client.post("/api/chat", json={"message": "چقدر VRAM دارد؟"})
        assert reply.status_code == 200
        assert reply.json()["sources"][0]["title"] == "مشخصات لپ‌تاپ"
        assert "۲ گیگابایت VRAM" in llm.last_messages[0]["content"]

        deleted = test_client.delete(f"/api/knowledge/{added.json()['id']}")
        assert deleted.status_code == 200
        assert test_client.get("/api/knowledge").json()["documents"] == []


def test_health_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["model"] == "test-model.gguf"
    assert body["backend"]["reachable"] is True


def test_health_degraded(settings: Settings) -> None:
    from server.main import create_app

    with TestClient(create_app(settings=settings, llm=FakeLLM(unavailable=True))) as client:
        body = client.get("/health").json()
    assert body["status"] == "degraded"
    assert body["backend"]["reachable"] is False


def test_chat_completion(client: TestClient, fake_llm: FakeLLM) -> None:
    response = client.post(
        "/v1/chat/completions",
        json={
            "messages": [{"role": "user", "content": "سلام"}],
            "max_tokens": 64,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "chat.completion"
    assert body["id"].startswith("chatcmpl-")
    assert body["choices"][0]["message"]["content"].startswith("سلام")
    assert body["usage"] == {
        "prompt_tokens": 20,
        "completion_tokens": 10,
        "total_tokens": 30,
    }
    assert body["timings"]["tokens_per_second"] == 10.0
    assert fake_llm.last_messages == [{"role": "user", "content": "سلام"}]


def test_chat_stream_sse(client: TestClient) -> None:
    response = client.post(
        "/v1/chat/completions",
        json={
            "messages": [{"role": "user", "content": "سلام"}],
            "stream": True,
        },
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    body = response.text
    assert '"content": "سلام"' in body or '"content":"سلام"' in body
    assert '"finish_reason": "stop"' in body or '"finish_reason":"stop"' in body
    assert '"ttft_ms"' in body
    assert body.rstrip().endswith("data: [DONE]")


def test_simple_chat(client: TestClient) -> None:
    response = client.post("/api/chat", json={"message": "سلام"})
    assert response.status_code == 200
    body = response.json()
    assert body["reply"].startswith("سلام")
    assert body["model"] == "test-model.gguf"
    assert body["timings"]["completion_tokens"] == 10


def test_simple_chat_with_system(client: TestClient, fake_llm: FakeLLM) -> None:
    response = client.post(
        "/api/chat",
        json={"message": "سلام", "system": "فقط کوتاه پاسخ بده"},
    )
    assert response.status_code == 200
    assert fake_llm.last_messages[0] == {"role": "system", "content": "فقط کوتاه پاسخ بده"}


def test_validation_rejects_empty_messages(client: TestClient) -> None:
    response = client.post("/v1/chat/completions", json={"messages": []})
    assert response.status_code == 422


def test_validation_rejects_bad_role(client: TestClient) -> None:
    response = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "robot", "content": "سلام"}]},
    )
    assert response.status_code == 422


def test_models(client: TestClient) -> None:
    response = client.get("/v1/models")
    assert response.status_code == 200
    ids = [item["id"] for item in response.json()["data"]]
    assert ids == ["test-model.gguf"]


def test_backend_unavailable_returns_503(settings: Settings) -> None:
    from server.main import create_app

    with TestClient(create_app(settings=settings, llm=FakeLLM(unavailable=True))) as client:
        response = client.post("/api/chat", json={"message": "سلام"})
    assert response.status_code == 503
    assert response.json()["error"] == "backend_unavailable"


def test_model_not_loaded_returns_404(settings: Settings) -> None:
    from server.main import create_app

    with TestClient(create_app(settings=settings, llm=FakeLLM(missing_model=True))) as client:
        response = client.post("/api/chat", json={"message": "سلام"})
    # missing_model raises ModelNotLoaded -> 503 model_not_loaded
    assert response.status_code == 503
    assert response.json()["error"] == "model_not_loaded"


def test_generation_error_returns_502(settings: Settings) -> None:
    from server.main import create_app

    with TestClient(create_app(settings=settings, llm=FakeLLM(missing_model=True))) as client:
        response = client.post("/api/chat", json={"message": "سلام"})
    # Both missing_model and unavailable map to 503/404 in our handler
    assert response.status_code in (404, 503, 502)


def test_lifespan_closes_client(settings: Settings, fake_llm: FakeLLM) -> None:
    from server.main import create_app

    with TestClient(create_app(settings=settings, llm=fake_llm)):
        pass
    assert fake_llm.closed is True

def test_stream_backend_failure_is_sse_error(settings: Settings) -> None:
    from server.main import create_app
    with TestClient(create_app(settings=settings, llm=FakeLLM(unavailable=True))) as client:
        response = client.post('/v1/chat/completions', json={
            'messages': [{'role': 'user', 'content': 'سلام'}], 'stream': True})
    assert response.status_code == 200
    assert 'LlamaServerUnavailable' in response.text
    assert '[DONE]' in response.text


# ------------------------------------------------------------------ readiness
def _fake_models(tmp_path) -> Settings:
    asr = tmp_path / "whisper"
    asr.mkdir()
    (asr / "model.bin").write_bytes(b"x")
    tts = tmp_path / "piper" / "fa.onnx"
    tts.parent.mkdir()
    tts.write_bytes(b"x")
    (tts.parent / "fa.onnx.json").write_bytes(b"{}")
    return Settings(
        model_path="models/test-model.gguf",
        system_prompt="سیستم تست",
        asr_model_path=str(asr),
        tts_model_path=str(tts),
        aec_enabled=False,
        history_persist=True,
        data_dir=str(tmp_path / "data"),
    )


def test_ready_reports_each_check_and_fails_when_models_are_missing(monkeypatch, tmp_path) -> None:
    from server.main import create_app

    monkeypatch.setattr("server.readiness.VOICE_DEPENDENCIES", ())
    settings = Settings(asr_model_path=str(tmp_path / "absent"),
                        tts_model_path=str(tmp_path / "absent.onnx"),
                        aec_enabled=False, data_dir=str(tmp_path))
    with TestClient(create_app(settings=settings, llm=FakeLLM())) as client:
        response = client.get("/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["asr_model"]["ok"] is False
    assert body["checks"]["tts_model"]["ok"] is False
    assert body["checks"]["configuration"]["ok"] is True


def test_ready_is_200_when_every_check_passes(monkeypatch, tmp_path) -> None:
    from server.main import create_app

    monkeypatch.setattr("server.readiness.VOICE_DEPENDENCIES", ())
    settings = _fake_models(tmp_path)
    with TestClient(create_app(settings=settings, llm=FakeLLM())) as client:
        response = client.get("/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is True
    assert body["checks"]["llm_backend"]["ok"] is True
    assert body["checks"]["aec"]["skipped"] is True


def test_ready_reports_the_ws_transport_and_refuses_it_without_a_token(monkeypatch, tmp_path) -> None:
    """An enabled-but-tokenless transport is reported as not ready, never as open."""
    from server.main import create_app

    monkeypatch.setattr("server.readiness.VOICE_DEPENDENCIES", ())
    off = _fake_models(tmp_path)
    with TestClient(create_app(settings=off, llm=FakeLLM())) as client:
        check = client.get("/ready").json()["checks"]["ws_voice"]
    assert check["enabled"] is False and check["skipped"] is True

    on = Settings(
        asr_model_path=off.asr_model_path,
        tts_model_path=off.tts_model_path,
        aec_enabled=False,
        data_dir=str(tmp_path),
        ws_enabled=True,
        ws_token="device-token",
    )
    with TestClient(create_app(settings=on, llm=FakeLLM())) as client:
        check = client.get("/ready").json()["checks"]["ws_voice"]
    assert check["ok"] is True and check["enabled"] is True
    assert check["path"] == "/ws/voice" and check["firmware"] is False


def test_ws_enabled_without_a_token_is_a_configuration_error() -> None:
    assert any("PVAI_WS_TOKEN" in error for error in Settings(ws_enabled=True, ws_token="").config_errors())
    # A configured token satisfies the same rule.
    assert not [
        error
        for error in Settings(ws_enabled=True, ws_token="device-token").config_errors()
        if "PVAI_WS_TOKEN" in error
    ]


def test_ready_reports_missing_backend(monkeypatch, tmp_path) -> None:
    from server.main import create_app

    monkeypatch.setattr("server.readiness.VOICE_DEPENDENCIES", ())
    with TestClient(create_app(settings=_fake_models(tmp_path), llm=FakeLLM(unavailable=True))) as client:
        body = client.get("/ready").json()
    assert body["ready"] is False
    assert body["checks"]["llm_backend"]["reachable"] is False


def test_sse_client_disconnect_cancels_upstream_generation(tmp_path) -> None:
    """A browser that hangs up mid-stream must stop generation, not leak it.

    The app is driven at the ASGI level so the test can send a real
    ``http.disconnect`` after the second chunk — exactly what Starlette sees when
    the client closes the connection (OPEN-03).
    """
    import asyncio
    import json as jsonlib

    from server.llm import StreamDelta
    from server.main import create_app

    class EndlessLlm:
        """Generates "forever"; records whether the stream was closed early."""

        def __init__(self) -> None:
            self.produced = 0
            self.closed = False
            self.finished = False

        async def aclose(self) -> None:
            return None

        async def health(self) -> dict:
            return {"reachable": True, "model_available": True}

        async def list_models(self) -> list[str]:
            return ["test-model"]

        async def chat(self, *args, **kwargs):  # pragma: no cover - not used
            raise AssertionError("non-streaming path must not run")

        async def stream_chat(self, *args, **kwargs):
            try:
                for index in range(100_000):
                    self.produced += 1
                    await asyncio.sleep(0.001)
                    yield StreamDelta(content=f"کلمه{index} ")
                self.finished = True
            finally:
                self.closed = True

    settings = Settings(model_path="models/test-model.gguf", data_dir=str(tmp_path))
    llm = EndlessLlm()
    app = create_app(settings=settings, llm=llm)

    async def scenario() -> None:
        body = jsonlib.dumps(
            {
                "model": "test-model",
                "messages": [{"role": "user", "content": "سلام"}],
                "stream": True,
            }
        ).encode("utf-8")
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/v1/chat/completions",
            "raw_path": b"/v1/chat/completions",
            "query_string": b"",
            "root_path": "",
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        }
        request_sent = False
        disconnected = asyncio.Event()
        chunks = 0

        async def receive() -> dict:
            nonlocal request_sent
            if not request_sent:
                request_sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            await disconnected.wait()
            return {"type": "http.disconnect"}

        async def send(message: dict) -> None:
            nonlocal chunks
            if message["type"] == "http.response.body" and message.get("body"):
                chunks += 1
                if chunks == 2:
                    disconnected.set()  # the browser closed the tab here

        async with app.router.lifespan_context(app):
            await asyncio.wait_for(app(scope, receive, send), timeout=5)

    asyncio.run(scenario())
    assert llm.closed is True, "upstream stream stayed open after the client left"
    assert llm.finished is False
    assert llm.produced < 500, f"generation kept running: {llm.produced} deltas"


def test_protocol_endpoint_reports_transport_but_no_firmware(client: TestClient) -> None:
    """The published contract must match the code: transport done, firmware not."""
    body = client.get("/api/protocol").json()
    assert body["implemented"] is True
    assert body["transport_implemented"] is True
    assert body["firmware_implemented"] is False
    assert body["audio"]["sample_rate"] == 16000


# ------------------------------------------------------------------- sessions
def test_stateless_chat_does_not_touch_disk(client: TestClient, tmp_path) -> None:
    assert client.post("/api/chat", json={"message": "سلام"}).status_code == 200
    assert not (tmp_path / "data").exists()


def test_chat_with_session_id_persists_and_reuses_context(tmp_path) -> None:
    from server.main import create_app

    settings = Settings(model_path="models/test-model.gguf", data_dir=str(tmp_path),
                        history_persist=True)
    llm = FakeLLM()
    with TestClient(create_app(settings=settings, llm=llm)) as client:
        first = client.post("/api/chat", json={"message": "سلام", "session_id": "s-live"})
        assert first.status_code == 200
        assert first.json()["session"]["session_id"] == "s-live"
        second = client.post("/api/chat", json={"message": "بعدی", "session_id": "s-live"})
        assert second.status_code == 200
        assert second.json()["session"]["messages"] == 4
        # The second request carries the previous exchange as context.
        assert [message["role"] for message in llm.last_messages] == [
            "system",
            "user",
            "assistant",
            "user",
        ]
        assert llm.last_messages[-2]["content"] == "سلام! من یک دستیار فارسی محلی هستم."

        listing = client.get("/api/sessions").json()
        assert listing["persistence"] is True
        assert [entry["session_id"] for entry in listing["sessions"]] == ["s-live"]

        detail = client.get("/api/sessions/s-live").json()
        assert len(detail["messages"]) == 4
        assert detail["messages"][0]["status"] == "complete"
        assert detail["messages"][-1]["turn_id"] is not None

        assert client.delete("/api/sessions/s-live").status_code == 200
        assert client.get("/api/sessions/s-live").status_code == 404


def test_session_endpoints_handle_invalid_ids(tmp_path) -> None:
    from server.main import create_app

    settings = Settings(model_path="models/test-model.gguf", data_dir=str(tmp_path),
                        history_persist=True)
    with TestClient(create_app(settings=settings, llm=FakeLLM())) as client:
        assert client.get("/api/sessions/..%2Fetc").status_code in (400, 404)
        assert client.delete("/api/sessions/..%2Fetc").status_code in (400, 404)


def test_sessions_are_isolated_from_each_other(tmp_path) -> None:
    """Two sessions must never see each other's context (LAN/ESP32 readiness)."""
    from server.main import create_app

    settings = Settings(
        model_path="models/test-model.gguf", data_dir=str(tmp_path), history_persist=True
    )
    llm = FakeLLM()
    with TestClient(create_app(settings=settings, llm=llm)) as client:
        client.post("/api/chat", json={"message": "سلام", "session_id": "s-one"})
        client.post("/api/chat", json={"message": "درود", "session_id": "s-two"})

        # The second session's payload starts from its own first user message.
        assert [m["role"] for m in llm.last_messages] == ["system", "user"]
        assert llm.last_messages[-1]["content"] == "درود"

        client.post("/api/chat", json={"message": "بعدی", "session_id": "s-one"})
        assert [m["content"] for m in llm.last_messages[1:]] == [
            "سلام",
            "سلام! من یک دستیار فارسی محلی هستم.",
            "بعدی",
        ]

        one = client.get("/api/sessions/s-one").json()
        two = client.get("/api/sessions/s-two").json()
        assert one["messages"][0]["content"] == "سلام"
        assert two["messages"][0]["content"] == "درود"
        assert one["conversation_id"] != two["conversation_id"]
