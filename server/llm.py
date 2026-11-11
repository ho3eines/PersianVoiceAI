"""Async HTTP client for local llama.cpp server (llama-server).

The llama-server (built from llama.cpp) exposes an OpenAI-compatible API.
We use the same client structure as before but target llama-server endpoints.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from typing import Any, AsyncIterator, Mapping, Sequence

import httpx

from .config import Settings


class LlamaServerError(RuntimeError):
    """Base error for llama-server runtime failures."""


class LlamaServerUnavailable(LlamaServerError):
    """llama-server is not running or cannot be reached."""


class ModelNotLoaded(LlamaServerError):
    """The model failed to load in llama-server."""


class GenerationError(LlamaServerError):
    """Error during token generation."""


@dataclass(slots=True)
class Timings:
    """Measured latency and throughput for one completion."""

    wall_ms: float
    total_ms: float
    prompt_eval_ms: float
    generation_ms: float
    prompt_tokens: int
    completion_tokens: int
    tokens_per_second: float

    def as_dict(self) -> dict[str, float | int]:
        return asdict(self)


@dataclass(slots=True)
class ChatResult:
    content: str
    model: str
    finish_reason: str
    timings: Timings


@dataclass(slots=True)
class StreamDelta:
    content: str


@dataclass(slots=True)
class StreamDone:
    result: ChatResult


StreamEvent = StreamDelta | StreamDone


def _ms_from_ns(value: Any) -> float:
    try:
        return round(float(value or 0) / 1_000_000, 2)
    except (TypeError, ValueError):
        return 0.0


class LlamaServerClient:
    """Small wrapper around llama-server's OpenAI-compatible API.

    llama-server endpoints (OpenAI-compatible):
    - GET  /health                  -> server health
    - GET  /v1/models               -> list models
    - POST /v1/chat/completions     -> chat (stream or JSON)
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.llama_server_url,
            timeout=httpx.Timeout(settings.request_timeout_s, connect=5.0),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _messages_with_system(
        self,
        messages: Sequence[Mapping[str, str] | Any],
        system_prompt: str | None = None,
    ) -> list[dict[str, str]]:
        prepared: list[dict[str, str]] = []
        for message in messages:
            if isinstance(message, Mapping):
                prepared.append({"role": str(message["role"]), "content": str(message["content"])})
            else:  # pydantic model
                prepared.append({"role": message.role, "content": message.content})

        prompt = self.settings.system_prompt if system_prompt is None else system_prompt
        if prompt and not any(item["role"] == "system" for item in prepared):
            prepared.insert(0, {"role": "system", "content": prompt})
        return prepared

    def _payload(
        self,
        messages: Sequence[Mapping[str, str] | Any],
        *,
        model: str | None,
        temperature: float | None,
        max_tokens: int | None,
        stream: bool,
        system_prompt: str | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model or self.settings.model_path.split("\\")[-1].split("/")[-1],
            "messages": self._messages_with_system(messages, system_prompt),
            "stream": stream,
            "chat_template_kwargs": {"enable_thinking": self.settings.llm_enable_thinking},
            "temperature": (
                self.settings.temperature if temperature is None else temperature
            ),
            "max_tokens": self.settings.max_tokens if max_tokens is None else max_tokens,
        }
        return payload

    @staticmethod
    def _raise_for_response(response: httpx.Response, body: str = "") -> None:
        if response.status_code == 404:
            raise ModelNotLoaded(
                "مدل در llama-server لود نشده است. مطمئن شوید llama-server با فایل مدل صحیح اجرا شده."
            )
        if response.status_code >= 400:
            detail = body.strip() or response.text.strip()
            raise GenerationError(f"llama-server error {response.status_code}: {detail[:500]}")

    def _result(self, data: Mapping[str, Any], wall_ms: float) -> ChatResult:
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        usage = data.get("usage") or {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)

        # llama-server provides timings in response (if available)
        # Some versions include: prompt_eval_ms, generation_ms, tokens_per_second
        timings_data = data.get("timings", {}) if isinstance(data.get("timings"), dict) else {}
        generation_ms = timings_data.get("predicted_ms", 0)
        prompt_eval_ms = timings_data.get("prompt_ms", 0)
        tokens_per_second = timings_data.get("predicted_per_second", 0)

        # Fallback: calculate from completion_tokens and generation_ms
        if tokens_per_second == 0 and generation_ms > 0 and completion_tokens > 0:
            tokens_per_second = round(completion_tokens / (generation_ms / 1000), 2)

        timings = Timings(
            wall_ms=round(wall_ms, 2),
            total_ms=_ms_from_ns(data.get("total_duration")) or round(wall_ms, 2),
            prompt_eval_ms=prompt_eval_ms,
            generation_ms=generation_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            tokens_per_second=tokens_per_second,
        )
        # Only user-facing content is returned, never reasoning.
        content = message.get("content") or ""
        return ChatResult(
            content=str(content),
            model=str(data.get("model") or self.settings.model_path.split("\\")[-1].split("/")[-1]),
            finish_reason=str(choice.get("finish_reason") or "stop"),
            timings=timings,
        )

    async def chat(
        self,
        messages: Sequence[Mapping[str, str] | Any],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        system_prompt: str | None = None,
    ) -> ChatResult:
        payload = self._payload(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=False,
            system_prompt=system_prompt,
        )
        started = time.perf_counter()
        try:
            response = await self._client.post("/v1/chat/completions", json=payload)
        except httpx.ConnectError as exc:
            raise LlamaServerUnavailable(
                "به llama-server وصل نشدم. مطمئن شوید llama-server در حال اجراست."
            ) from exc
        except httpx.TimeoutException as exc:
            raise LlamaServerUnavailable("پاسخ llama-server در زمان مجاز دریافت نشد.") from exc
        except httpx.HTTPError as exc:
            raise LlamaServerUnavailable(f"خطای ارتباط با llama-server: {exc}") from exc

        self._raise_for_response(response)
        wall_ms = (time.perf_counter() - started) * 1000
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise GenerationError("پاسخ llama-server قابل خواندن نبود.") from exc
        return self._result(data, wall_ms)

    async def stream_chat(
        self,
        messages: Sequence[Mapping[str, str] | Any],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        system_prompt: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        payload = self._payload(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
            system_prompt=system_prompt,
        )
        started = time.perf_counter()
        chunks: list[str] = []
        final: dict[str, Any] = {}

        try:
            async with self._client.stream("POST", "/v1/chat/completions", json=payload) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", errors="replace")
                    self._raise_for_response(response, body)

                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    if not line.startswith("data: "):
                        continue
                    raw = line[6:].strip()
                    if raw == "[DONE]":
                        break
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if data.get("error"):
                        raise GenerationError(str(data["error"]))
                    choice = (data.get("choices") or [{}])[0]
                    delta = choice.get("delta") or {}
                    # Only speak user-facing content, not reasoning deltas.
                    piece = str(delta.get("content") or "")
                    if piece:
                        chunks.append(piece)
                        yield StreamDelta(piece)
                    if choice.get("finish_reason") is not None:
                        final = data
                        break
        except httpx.ConnectError as exc:
            raise LlamaServerUnavailable(
                "به llama-server وصل نشدم. مطمئن شوید llama-server در حال اجراست."
            ) from exc
        except httpx.TimeoutException as exc:
            raise LlamaServerUnavailable("پاسخ llama-server در زمان مجاز دریافت نشد.") from exc
        except httpx.HTTPError as exc:
            raise LlamaServerUnavailable(f"خطای ارتباط با llama-server: {exc}") from exc

        wall_ms = (time.perf_counter() - started) * 1000
        result = self._result(final, wall_ms)
        result.content = "".join(chunks)
        yield StreamDone(result)

    async def list_models(self) -> list[str]:
        try:
            response = await self._client.get("/v1/models")
        except httpx.ConnectError as exc:
            raise LlamaServerUnavailable(
                "به llama-server وصل نشدم. مطمئن شوید llama-server در حال اجراست."
            ) from exc
        self._raise_for_response(response)
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise GenerationError("فهرست مدل‌های llama-server قابل خواندن نبود.") from exc
        return [str(item.get("id", "")) for item in data.get("data", []) if item.get("id")]

    async def health(self) -> dict[str, Any]:
        """Never raises: health must work even when the backend is down."""
        try:
            response = await self._client.get("/health", timeout=3.0)
            response.raise_for_status()
            data = response.json()
            models = await self.list_models()
            # llama-server returns full paths as IDs; extract basenames for comparison
            model_basenames = [m.split("\\")[-1].split("/")[-1] for m in models]
            wanted = self.settings.model_path.split("\\")[-1].split("/")[-1]
            available = wanted in model_basenames
            return {
                "reachable": True,
                "model_available": available,
                "models": models,
                "llama_server": data,
                # ``/props`` reports the chat template the server is actually
                # using.  Readiness needs it to tell whether
                # ``chat_template_kwargs.enable_thinking`` can have any effect;
                # a server that does not expose it yields ``{}`` and readiness
                # reports NOT MEASURED instead of guessing.
                "props": await self.props(),
            }
        except Exception as exc:  # health endpoint must never crash
            return {
                "reachable": False,
                "model_available": False,
                "models": [],
                "error": f"{type(exc).__name__}: {exc}",
            }

    async def props(self) -> dict[str, Any]:
        """The server's ``/props`` document, or ``{}`` when unavailable."""
        try:
            response = await self._client.get("/props", timeout=3.0)
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, dict) else {}
        except Exception:  # diagnostics only: never break health or readiness
            return {}

    async def probe_no_thinking(self) -> dict[str, Any]:
        """Smoke-test the actual template, not merely the requested option.

        No reasoning or generated text is returned in readiness diagnostics.
        """
        try:
            response = await self._client.post(
                "/v1/chat/completions",
                json=self._payload([{"role": "user", "content": "سلام"}],
                                   model=None, temperature=0, max_tokens=12,
                                   stream=False, system_prompt=None),
                timeout=10.0,
            )
            response.raise_for_status()
            data = response.json()
            message = data["choices"][0]["message"]
            content = str(message.get("content") or "")
            if message.get("reasoning_content"):
                return {"ok": False, "detail": "reasoning_content در خروجی مدل دیده شد؛ --jinja و enable_thinking را بررسی کنید."}
            if "<think>" in content.lower() or "</think>" in content.lower():
                return {"ok": False, "detail": "<think> در خروجی مدل دیده شد؛ --jinja و enable_thinking را بررسی کنید."}
            if not content.strip():
                return {"ok": False, "detail": "آزمایش قالب چت خروجی قابل بررسی نداد؛ NOT MEASURED."}
            return {"ok": True, "detail": "آزمایش کوتاه قالب چت بدون reasoning_content/<think> بود."}
        except (httpx.HTTPError, KeyError, ValueError, IndexError, TypeError) as exc:
            return {"ok": False, "detail": f"آزمایش قالب چت ناموفق بود ({type(exc).__name__})."}

    async def version(self) -> str | None:
        try:
            response = await self._client.get("/v1/models", timeout=3.0)
            response.raise_for_status()
            data = response.json()
            if data.get("data"):
                return str(data["data"][0].get("id", ""))
        except Exception:
            pass
        return None