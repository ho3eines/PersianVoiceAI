from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from server.config import Settings
from server.llm import GenerationError, LlamaServerClient, StreamDelta


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

def test_reasoning_is_not_a_spoken_reply() -> None:
    client = _client()
    try:
        result = client._result({'choices': [{'message': {'reasoning_content': 'internal'}}]}, 1)
        assert result.content == ''
        inline = client._result({"choices": [{"message": {"content": "<think>hidden</think>visible"}}]}, 1)
        assert inline.content == "visible"
        payload = client._payload([], model=None, temperature=None, max_tokens=None,
                                  stream=True, system_prompt=None)
        assert payload['chat_template_kwargs'] == {'enable_thinking': False}
    finally:
        asyncio.run(client.aclose())


def test_thinking_filter_strips_inline_blocks_across_chunks() -> None:
    """`benchmarks/compare_models.py` imports this helper; keep it working.

    Reasoning must never be counted as an answer, even when the `` thinking``
    markers arrive split across stream chunks.
    """
    from server.llm import _ThinkingFilter, _strip_thinking_blocks

    content_filter = _ThinkingFilter()
    assert content_filter.feed("<thi") == ""
    assert content_filter.feed("nk>hidden</thi") == ""
    assert content_filter.feed("nk>visible") == "visible"
    assert content_filter.finish() == ""

    assert _strip_thinking_blocks("ab") == "ab"
    assert _strip_thinking_blocks("plain answer") == "plain answer"
    assert _strip_thinking_blocks("<think>only reasoning") == ""


def test_llm_transport_refuses_public_url_and_does_not_use_proxies_or_redirects(monkeypatch) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.test:8080")
    with pytest.raises(ValueError, match="PVAI_LLAMA_SERVER_URL"):
        LlamaServerClient(Settings(llama_server_url="http://outside.test:8080"))
    client = LlamaServerClient(Settings(llama_server_url="http://192.168.0.2:8080"))
    try:
        assert client._client.trust_env is False
        assert client._client.follow_redirects is False
    finally:
        asyncio.run(client.aclose())


@pytest.mark.parametrize("finish", [True, False])
def test_stream_only_yields_visible_text_and_requires_finish_reason(finish: bool) -> None:
    async def run() -> None:
        client = _client()
        await client.aclose()
        choices = [{"delta": {"content": chunk}} for chunk in ("<thi", "nk>hidden", "</think>visible")]
        if finish:
            choices.append({"delta": {}, "finish_reason": "stop"})
        body = "".join(f"data: {json.dumps({'choices': [choice]})}\n\n" for choice in choices)
        client._client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, text=body)),
            base_url="http://localhost",
        )
        events = []
        try:
            if finish:
                async for event in client.stream_chat([]):
                    events.append(event)
                assert events[-1].result.content == "visible"
            else:
                with pytest.raises(GenerationError):
                    async for event in client.stream_chat([]):
                        events.append(event)
            assert [event.content for event in events if isinstance(event, StreamDelta)] == ["visible"]
        finally:
            await client.aclose()

    asyncio.run(run())
