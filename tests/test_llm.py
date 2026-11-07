from __future__ import annotations

import asyncio

from server.config import Settings
from server.llm import OllamaClient, _ms_from_ns


def _client() -> OllamaClient:
    return OllamaClient(Settings(model="test:model"))


def test_ms_from_ns() -> None:
    assert _ms_from_ns(1_500_000_000) == 1500.0
    assert _ms_from_ns(None) == 0.0
    assert _ms_from_ns("bad") == 0.0


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
        assert payload["options"]["temperature"] == 0.2
        assert payload["options"]["num_predict"] == 32
        assert payload["options"]["num_ctx"] == Settings().num_ctx
        assert payload["think"] is False
    finally:
        asyncio.run(client.aclose())


def test_result_parses_ollama_timings() -> None:
    client = _client()
    try:
        data = {
            "model": "test:model",
            "message": {"role": "assistant", "content": "پاسخ"},
            "done_reason": "stop",
            "total_duration": 2_000_000_000,
            "load_duration": 100_000_000,
            "prompt_eval_count": 12,
            "prompt_eval_duration": 500_000_000,
            "eval_count": 20,
            "eval_duration": 1_400_000_000,
        }
        result = client._result(data, wall_ms=2100.0)
        assert result.content == "پاسخ"
        assert result.timings.total_ms == 2000.0
        assert result.timings.load_ms == 100.0
        assert result.timings.prompt_tokens == 12
        assert result.timings.completion_tokens == 20
        assert result.timings.tokens_per_second == 14.29
        assert result.finish_reason == "stop"
    finally:
        asyncio.run(client.aclose())
