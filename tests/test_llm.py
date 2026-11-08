from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from server.config import Settings
from server.llm import LlamaServerClient, LlamaServerUnavailable


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


def test_default_model_path_points_to_repository_models_directory() -> None:
    from pathlib import Path

    from server.config import DEFAULT_MODEL_PATH

    expected = (
        Path(__file__).resolve().parents[1]
        / "models"
        / "qwen3.5-4b"
        / "Qwen3.5-4B-Q4_K_M.gguf"
    )
    assert Path(DEFAULT_MODEL_PATH) == expected


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


def test_non_streaming_result_does_not_expose_reasoning_content() -> None:
    client = _client()
    try:
        result = client._result(
            {
                "choices": [
                    {
                        "message": {
                            "content": "<think>استدلال داخلی محرمانه</think>پاسخ نهایی",
                            "reasoning_content": "استدلال داخلی محرمانه",
                        },
                        "finish_reason": "stop",
                    }
                ]
            },
            wall_ms=1.0,
        )
        assert result.content == "پاسخ نهایی"

        reasoning_only = client._result(
            {
                "choices": [
                    {
                        "message": {"reasoning_content": "استدلال داخلی محرمانه"},
                        "finish_reason": "stop",
                    }
                ]
            },
            wall_ms=1.0,
        )
        assert reasoning_only.content == ""
    finally:
        asyncio.run(client.aclose())


def test_streaming_result_does_not_emit_reasoning_content() -> None:
    client = _client()
    events = [
        {
            "choices": [
                {"delta": {"reasoning_content": "استدلال داخلی محرمانه"}, "finish_reason": None}
            ]
        },
        {"choices": [{"delta": {"content": "<thi"}, "finish_reason": None}]},
        {"choices": [{"delta": {"content": "nk>استدلال داخلی"}, "finish_reason": None}]},
        {"choices": [{"delta": {"content": " محرمانه</th"}, "finish_reason": None}]},
        {"choices": [{"delta": {"content": "ink>پاسخ نهایی"}, "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
    ]
    body = "".join(f"data: {json.dumps(event, ensure_ascii=False)}\n\n" for event in events)
    body += "data: [DONE]\n\n"

    def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    async def replace_transport() -> None:
        await client.aclose()
        client._client = httpx.AsyncClient(
            base_url=client.settings.llama_server_url,
            transport=httpx.MockTransport(respond),
        )

    async def collect() -> list[object]:
        return [event async for event in client.stream_chat([{"role": "user", "content": "سلام"}])]

    asyncio.run(replace_transport())
    try:
        result = asyncio.run(collect())
        assert len(result) == 2
        assert result[0].content == "پاسخ نهایی"
        assert result[1].result.content == "پاسخ نهایی"
    finally:
        asyncio.run(client.aclose())


def test_list_models_maps_transport_errors_to_domain_error() -> None:
    client = _client()

    def fail_request(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("private transport detail")

    async def replace_transport() -> None:
        await client.aclose()
        client._client = httpx.AsyncClient(
            base_url=client.settings.llama_server_url,
            transport=httpx.MockTransport(fail_request),
        )

    asyncio.run(replace_transport())
    try:
        with pytest.raises(LlamaServerUnavailable) as error:
            asyncio.run(client.list_models())
        assert "private transport detail" not in str(error.value)
        assert "llama-server" in str(error.value)
    finally:
        asyncio.run(client.aclose())
