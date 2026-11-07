"""Thin async client for the local Ollama runtime.

The service deliberately talks to Ollama's native API (not its OpenAI shim)
because the native API returns precise timing data we need for lesson 1
benchmarks: model load time, prompt evaluation time, generation time and
token counts.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from typing import Any, AsyncIterator, Mapping, Sequence

import httpx

from .config import Settings


class OllamaError(RuntimeError):
    """Base error for local model runtime failures."""


class OllamaUnavailable(OllamaError):
    """Ollama is not running or cannot be reached."""


class ModelNotFound(OllamaError):
    """The configured model is not available in the local runtime."""


@dataclass(slots=True)
class Timings:
    """Measured latency and throughput for one completion."""

    wall_ms: float
    total_ms: float
    load_ms: float
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


class OllamaClient:
    """Small, dependency-light wrapper around ``/api/chat`` and ``/api/tags``."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.ollama_base_url,
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
            "model": model or self.settings.model,
            "messages": self._messages_with_system(messages, system_prompt),
            "stream": stream,
            "options": {
                "temperature": (
                    self.settings.temperature if temperature is None else temperature
                ),
                "num_predict": self.settings.max_tokens if max_tokens is None else max_tokens,
                "num_ctx": self.settings.num_ctx,
            },
        }
        if self.settings.think is not None:
            payload["think"] = self.settings.think
        return payload

    @staticmethod
    def _raise_for_response(response: httpx.Response, body: str = "") -> None:
        if response.status_code == 404:
            raise ModelNotFound(
                "مدل در Ollama پیدا نشد. با دستور «ollama pull <model>» آن را دانلود کنید."
            )
        if response.status_code >= 400:
            detail = body.strip() or response.text.strip()
            raise OllamaError(f"Ollama error {response.status_code}: {detail[:500]}")

    def _result(self, data: Mapping[str, Any], wall_ms: float) -> ChatResult:
        message = data.get("message") or {}
        prompt_tokens = int(data.get("prompt_eval_count") or 0)
        completion_tokens = int(data.get("eval_count") or 0)
        generation_ns = data.get("eval_duration") or 0
        tokens_per_second = (
            round(completion_tokens / (float(generation_ns) / 1e9), 2)
            if generation_ns
            else 0.0
        )
        timings = Timings(
            wall_ms=round(wall_ms, 2),
            total_ms=_ms_from_ns(data.get("total_duration")),
            load_ms=_ms_from_ns(data.get("load_duration")),
            prompt_eval_ms=_ms_from_ns(data.get("prompt_eval_duration")),
            generation_ms=_ms_from_ns(generation_ns),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            tokens_per_second=tokens_per_second,
        )
        return ChatResult(
            content=str(message.get("content") or ""),
            model=str(data.get("model") or self.settings.model),
            finish_reason=str(data.get("done_reason") or "stop"),
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
            response = await self._client.post("/api/chat", json=payload)
        except httpx.ConnectError as exc:
            raise OllamaUnavailable(
                "به Ollama وصل نشدم. مطمئن شوید «ollama serve» در حال اجراست."
            ) from exc
        except httpx.TimeoutException as exc:
            raise OllamaUnavailable("پاسخ Ollama در زمان مجاز دریافت نشد.") from exc
        except httpx.HTTPError as exc:
            raise OllamaUnavailable(f"خطای ارتباط با Ollama: {exc}") from exc

        self._raise_for_response(response)
        wall_ms = (time.perf_counter() - started) * 1000
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise OllamaError("پاسخ Ollama قابل خواندن نبود.") from exc
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
            async with self._client.stream("POST", "/api/chat", json=payload) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", errors="replace")
                    self._raise_for_response(response, body)

                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if data.get("error"):
                        raise OllamaError(str(data["error"]))
                    piece = str((data.get("message") or {}).get("content") or "")
                    if piece:
                        chunks.append(piece)
                        yield StreamDelta(piece)
                    if data.get("done"):
                        final = data
                        break
        except httpx.ConnectError as exc:
            raise OllamaUnavailable(
                "به Ollama وصل نشدم. مطمئن شوید «ollama serve» در حال اجراست."
            ) from exc
        except httpx.TimeoutException as exc:
            raise OllamaUnavailable("پاسخ Ollama در زمان مجاز دریافت نشد.") from exc
        except httpx.HTTPError as exc:
            raise OllamaUnavailable(f"خطای ارتباط با Ollama: {exc}") from exc

        wall_ms = (time.perf_counter() - started) * 1000
        result = self._result(final, wall_ms)
        result.content = "".join(chunks)
        yield StreamDone(result)

    async def list_models(self) -> list[str]:
        try:
            response = await self._client.get("/api/tags")
        except httpx.ConnectError as exc:
            raise OllamaUnavailable(
                "به Ollama وصل نشدم. مطمئن شوید «ollama serve» در حال اجراست."
            ) from exc
        self._raise_for_response(response)
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise OllamaError("فهرست مدل‌های Ollama قابل خواندن نبود.") from exc
        return [str(item.get("name", "")) for item in data.get("models", []) if item.get("name")]

    async def health(self) -> dict[str, Any]:
        """Never raises: health must work even when the backend is down."""
        try:
            response = await self._client.get("/api/tags", timeout=3.0)
            response.raise_for_status()
            models = [
                str(item.get("name", ""))
                for item in response.json().get("models", [])
                if item.get("name")
            ]
            wanted = self.settings.model
            wanted_base = wanted.removesuffix(":latest")
            available = any(
                name.removesuffix(":latest") == wanted_base or name.split(":")[0] == wanted_base
                for name in models
            )
            return {
                "reachable": True,
                "model_available": available,
                "models": models,
            }
        except Exception as exc:  # health endpoint must never crash
            return {
                "reachable": False,
                "model_available": False,
                "models": [],
                "error": f"{type(exc).__name__}: {exc}",
            }

    async def version(self) -> str | None:
        try:
            response = await self._client.get("/api/version", timeout=3.0)
            response.raise_for_status()
            return str(response.json().get("version") or "")
        except Exception:
            return None
