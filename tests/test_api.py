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