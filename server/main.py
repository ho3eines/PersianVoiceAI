"""FastAPI service exposing the local Persian LLM (llama.cpp backend).

Endpoints
---------
GET  /health                 service + model runtime status
GET  /v1/models              available local models
POST /v1/chat/completions    OpenAI-compatible chat (stream or JSON)
POST /api/chat               minimal one-shot endpoint for simple clients
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from . import __version__
from .config import Settings
from .llm import (
    ChatResult,
    GenerationError,
    LlamaServerClient,
    LlamaServerError,
    LlamaServerUnavailable,
    ModelNotLoaded,
    StreamDelta,
    StreamDone,
)

logger = logging.getLogger("persian_voice_ai")

Role = Literal["system", "user", "assistant"]


class Message(BaseModel):
    role: Role
    content: str = Field(min_length=1, max_length=64_000)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: str | None = None
    messages: list[Message] = Field(min_length=1, max_length=100)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=4096)
    stream: bool = False


class SimpleChatRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    message: str = Field(min_length=1, max_length=32_000)
    model: str | None = None
    system: str | None = Field(default=None, max_length=16_000)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=4096)


def _completion_id() -> str:
    return f"chatcmpl-{uuid.uuid4().hex[:24]}"


def _usage(result: ChatResult) -> dict[str, int]:
    return {
        "prompt_tokens": result.timings.prompt_tokens,
        "completion_tokens": result.timings.completion_tokens,
        "total_tokens": result.timings.prompt_tokens + result.timings.completion_tokens,
    }


def _sse(data: dict[str, Any]) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def create_app(settings: Settings | None = None, llm: LlamaServerClient | Any | None = None) -> FastAPI:
    """Application factory. ``llm`` is injectable so tests need no model."""
    resolved = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logging.basicConfig(
            level=getattr(logging, resolved.log_level, logging.INFO),
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        app.state.settings = resolved
        app.state.started_at = time.time()
        app.state.llm = llm or LlamaServerClient(resolved)
        logger.info(
            "Persian Voice AI %s | model=%s",
            __version__,
            resolved.model_path,
        )
        yield
        await app.state.llm.aclose()

    app = FastAPI(
        title="Persian Voice AI",
        description="Local Persian LLM core (llama.cpp). Lesson 1: text chat + local API.",
        version=__version__,
        lifespan=lifespan,
    )

    # ---------------------------------------------------------------- errors
    @app.exception_handler(ModelNotLoaded)
    async def _model_not_loaded(_: Request, exc: ModelNotLoaded) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"error": "model_not_loaded", "detail": str(exc)},
        )

    @app.exception_handler(GenerationError)
    async def _generation_error(_: Request, exc: GenerationError) -> JSONResponse:
        return JSONResponse(
            status_code=502,
            content={"error": "generation_error", "detail": str(exc)},
        )

    @app.exception_handler(LlamaServerUnavailable)
    async def _backend_unavailable(_: Request, exc: LlamaServerUnavailable) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"error": "backend_unavailable", "detail": str(exc)},
        )

    @app.exception_handler(LlamaServerError)
    async def _backend_error(_: Request, exc: LlamaServerError) -> JSONResponse:
        return JSONResponse(
            status_code=502,
            content={"error": "backend_error", "detail": str(exc)},
        )

    # ---------------------------------------------------------------- basics
    @app.get("/", tags=["meta"])
    async def root() -> dict[str, Any]:
        return {
            "service": "Persian Voice AI",
            "version": __version__,
            "lesson": 1,
            "docs": "/docs",
            "health": "/health",
        }

    @app.get("/health", tags=["meta"])
    async def health(request: Request) -> dict[str, Any]:
        backend = await request.app.state.llm.health()
        healthy = bool(backend.get("reachable") and backend.get("model_available"))
        return {
            "status": "ok" if healthy else "degraded",
            "service": "persian-voice-ai",
            "version": __version__,
            "model": resolved.model_path.split("\\")[-1].split("/")[-1],
            "uptime_s": round(time.time() - request.app.state.started_at, 1),
            "backend": backend,
        }

    @app.get("/v1/models", tags=["openai"])
    async def list_models(request: Request) -> dict[str, Any]:
        names = await request.app.state.llm.list_models()
        return {
            "object": "list",
            "data": [
                {
                    "id": name,
                    "object": "model",
                    "created": 0,
                    "owned_by": "local",
                }
                for name in names
            ],
        }

    # ------------------------------------------------------- OpenAI-compatible
    @app.post("/v1/chat/completions", tags=["openai"])
    async def chat_completions(payload: ChatRequest, request: Request) -> Any:
        llm_client = request.app.state.llm
        model_name = payload.model or resolved.model_path.split("\\")[-1].split("/")[-1]
        completion_id = _completion_id()
        created = int(time.time())

        if not payload.stream:
            result = await llm_client.chat(
                payload.messages,
                model=payload.model,
                temperature=payload.temperature,
                max_tokens=payload.max_tokens,
            )
            logger.info(
                "chat model=%s prompt_tokens=%s completion_tokens=%s wall_ms=%s tps=%s",
                result.model,
                result.timings.prompt_tokens,
                result.timings.completion_tokens,
                result.timings.wall_ms,
                result.timings.tokens_per_second,
            )
            return {
                "id": completion_id,
                "object": "chat.completion",
                "created": created,
                "model": result.model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": result.content},
                        "finish_reason": result.finish_reason,
                    }
                ],
                "usage": _usage(result),
                "timings": result.timings.as_dict(),
            }

        async def event_stream() -> AsyncIterator[str]:
            started = time.perf_counter()
            first_token_ms: float | None = None
            try:
                async for event in llm_client.stream_chat(
                    payload.messages,
                    model=payload.model,
                    temperature=payload.temperature,
                    max_tokens=payload.max_tokens,
                ):
                    if isinstance(event, StreamDelta):
                        if first_token_ms is None:
                            first_token_ms = round((time.perf_counter() - started) * 1000, 2)
                        chunk = {
                            "id": completion_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model_name,
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {"content": event.content},
                                    "finish_reason": None,
                                }
                            ],
                        }
                        yield _sse(chunk)
                        continue

                    # StreamDone
                    result = event.result
                    timings = result.timings.as_dict()
                    timings["ttft_ms"] = first_token_ms or timings["wall_ms"]
                    final_chunk = {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": result.model,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {},
                                "finish_reason": result.finish_reason,
                            }
                        ],
                        "usage": _usage(result),
                        "timings": timings,
                    }
                    yield _sse(final_chunk)
                    logger.info(
                        "stream model=%s ttft_ms=%s wall_ms=%s tps=%s",
                        result.model,
                        timings["ttft_ms"],
                        result.timings.wall_ms,
                        result.timings.tokens_per_second,
                    )
                yield "data: [DONE]\n\n"
            except LlamaCppError as exc:
                logger.error("stream failed: %s", exc)
                yield _sse(
                    {
                        "error": {
                            "message": str(exc),
                            "type": type(exc).__name__,
                        }
                    }
                )
                yield "data: [DONE]\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # -------------------------------------------------------- simple endpoint
    @app.post("/api/chat", tags=["simple"])
    async def simple_chat(payload: SimpleChatRequest, request: Request) -> dict[str, Any]:
        messages: list[dict[str, str]] = []
        if payload.system:
            messages.append({"role": "system", "content": payload.system})
        messages.append({"role": "user", "content": payload.message})

        result = await request.app.state.llm.chat(
            messages,
            model=payload.model,
            temperature=payload.temperature,
            max_tokens=payload.max_tokens,
        )
        return {
            "reply": result.content,
            "model": result.model,
            "finish_reason": result.finish_reason,
            "timings": result.timings.as_dict(),
        }

    return app


app = create_app()