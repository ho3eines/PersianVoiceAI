"""FastAPI service exposing the local Persian LLM (llama.cpp backend).

Endpoints
---------
GET  /health                 service + model runtime status
GET  /ready                  full readiness checklist (models, AEC, audio deps)
GET  /v1/models              available local models
POST /v1/chat/completions    OpenAI-compatible chat (stream or JSON)
POST /api/chat               one-shot chat, optionally bound to a local session
GET  /api/sessions           locally persisted sessions
GET  /api/sessions/{id}      one session with message lifecycle statuses
DEL  /api/sessions/{id}      delete a locally persisted session
GET  /api/protocol           ESP32 wire-protocol description
WS   /ws/voice               LAN device audio transport (opt-in, token required)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from . import __version__
from .config import Settings
from .conversation import STATUS_COMPLETE, Conversation, open_store
from .protocol import describe as describe_protocol
from .ws_voice import build_router as build_ws_router
from .ws_voice import default_bundle as default_voice_bundle
from .readiness import collect as collect_readiness
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
    session_id: str | None = Field(default=None, max_length=64)
    """Optional local session. Without it the request stays stateless (no disk writes)."""


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


def create_app(
    settings: Settings | None = None,
    llm: LlamaServerClient | Any | None = None,
    voice_bundle: Any | None = None,
) -> FastAPI:
    """Application factory.

    ``llm`` and ``voice_bundle`` are injectable so tests need no model and no
    audio device; ``voice_bundle`` builds the per-connection voice dependencies
    for the WebSocket transport.
    """
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
        app.state.store = open_store(resolved)
        app.state.voice_bundle = voice_bundle or default_voice_bundle
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
    if resolved.ws_enabled:
        # Opt-in LAN voice transport; each connection gets its own Pipeline and
        # Conversation.  Refused unless PVAI_WS_TOKEN is configured.
        app.include_router(
            build_ws_router(resolved, bundle_factory=voice_bundle or default_voice_bundle)
        )

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
            "ready": "/ready",
        }

    @app.get("/ready", tags=["meta"])
    async def ready(request: Request) -> JSONResponse:
        """Full readiness checklist; never raises on a broken installation."""
        backend = await request.app.state.llm.health()
        report = collect_readiness(request.app.state.settings, backend=backend)
        status_code = 200 if report["ready"] else 503
        return JSONResponse(
            status_code=status_code,
            content={
                "status": "ready" if report["ready"] else "not_ready",
                "uptime_s": round(time.time() - request.app.state.started_at, 1),
                **report,
            },
        )

    @app.get("/api/protocol", tags=["meta"])
    async def protocol(request: Request) -> dict[str, Any]:
        """Wire protocol that the future ESP32-S3 firmware will speak (design only)."""
        return describe_protocol()

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
            except LlamaServerError as exc:
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

    # ------------------------------------------------------------- sessions
    def _conversation(session_id: str | None) -> Conversation:
        """Load a persisted session, or build a RAM-only conversation."""
        options = {
            "max_messages": resolved.history_max_messages,
            "token_budget": resolved.history_token_budget,
            "include_interrupted": resolved.history_include_interrupted,
        }
        if not session_id:
            return Conversation(store=None, **options)
        try:
            return Conversation.load(request_store(), session_id, **options)
        except ValueError as exc:  # invalid identifier (path traversal etc.)
            raise HTTPException(status_code=400, detail="شناسهٔ نشست نامعتبر است.") from exc

    def request_store():
        return app.state.store

    @app.get("/api/sessions", tags=["sessions"])
    async def list_sessions(request: Request) -> dict[str, Any]:
        store = request_store()
        return {
            "persistence": bool(store.enabled),
            "directory": str(store.root) if store.enabled else None,
            "sessions": store.sessions(),
        }

    @app.get("/api/sessions/{session_id}", tags=["sessions"])
    async def show_session(session_id: str, request: Request) -> dict[str, Any]:
        conversation = _conversation(session_id)
        if not conversation.messages:
            raise HTTPException(status_code=404, detail="نشست پیدا نشد.")
        return conversation.as_dict()

    @app.delete("/api/sessions/{session_id}", tags=["sessions"])
    async def delete_session(session_id: str, request: Request) -> dict[str, Any]:
        deleted = request_store().delete(session_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="نشست پیدا نشد.")
        return {"deleted": True, "session_id": session_id}

    # -------------------------------------------------------- simple endpoint
    @app.post("/api/chat", tags=["simple"])
    async def simple_chat(payload: SimpleChatRequest, request: Request) -> dict[str, Any]:
        conversation = _conversation(payload.session_id)
        turn_id = conversation.new_turn_id()
        conversation.add_user(payload.message, turn_id=turn_id)
        messages = conversation.context_with_system(payload.system or resolved.system_prompt)

        result = await request.app.state.llm.chat(
            messages,
            model=payload.model,
            temperature=payload.temperature,
            max_tokens=payload.max_tokens,
        )
        conversation.finish_assistant(
            result.content, status=STATUS_COMPLETE, turn_id=turn_id
        )
        body: dict[str, Any] = {
            "reply": result.content,
            "model": result.model,
            "finish_reason": result.finish_reason,
            "timings": result.timings.as_dict(),
        }
        if payload.session_id:
            body["session"] = conversation.summary()
        return body

    return app


app = create_app()
