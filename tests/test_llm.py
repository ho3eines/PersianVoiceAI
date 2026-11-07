from __future__ import annotations

import asyncio

from server.config import Settings
from server.llm import LlamaServerClient


def _client() -> LlamaServerClient:
    return LlamaServerClient(Settings(model_path="models/test.gguf"))


def test_system_prompt_is_injected_once() -> None:
    client = _client()
    try:
        messages = client._messages_with_system([{"role": "user", "content": "سلام"}])
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == Settings().system_prompt
        assert len(messages) == 2

        custom = client._messages_with_system(
            [{"role": "user", "content": "سلام"}], system_prompt="دستور ویژه"
        )
        assert custom[0]["content"] == "دستور ویژه"

        already_has_system = client._messages_with_system(
            [
                {"role": "system", "content": "دستور کاربر"},
                {"role": "user", "content": "سلام"},
            ]
        )
        assert already_has_system[0]["content"] == "دستور کاربر"
        assert len(already_has_system) == 2
    finally:
        asyncio.run(client.aclose())


def test_payload_contains_runtime_options() -> None:
    client = _client()
    try:
        payload = client._payload(
            [{"role": "user", "content": "سلام"}],
            model="other:1b",
            temperature=0.2,
            max_tokens=32,
            stream=False,
            system_prompt=None,
        )
        assert payload["model"] == "other:1b"
        assert payload["stream"] is False
        assert payload["temperature"] == 0.2
        assert payload["max_tokens"] == 32
    finally:
        asyncio.run(client.aclose())


def test_result_parses_llama_server_timings() -> None:
    client = _client()
    try:
        # Simulate llama-server/OpenAI-compatible response with usage and timings
        data = {
            "model": "test-model.gguf",
            "choices": [
                {
                    "message": {"role": "assistant", "content": "پاسخ"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 20,
            },
            "timings": {
                "prompt_ms": 500.0,
                "predicted_ms": 1400.0,
                "predicted_per_second": 14.29,
            },
        }
        result = client._result(data, wall_ms=2100.0)
        assert result.content == "پاسخ"
        assert result.timings.prompt_tokens == 12
        assert result.timings.completion_tokens == 20
        assert result.timings.prompt_eval_ms == 500.0
        assert result.timings.generation_ms == 1400.0
        assert result.timings.tokens_per_second == 14.29
        assert result.finish_reason == "stop"
    finally:
        asyncio.run(client.aclose())