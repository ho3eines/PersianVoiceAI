"""Minimal local web UI for one-turn voice/text testing.

The page (``web/index.html``) talks to JSON endpoints here; the server reuses
the same components as the live pipeline (``LocalAudio``, ``Speaker``,
``LlamaServerClient``) but drives exactly one turn at a time.  On the two-core
target laptop Whisper needs tens of seconds per decode, so the continuous live
loop floods its final-ASR queue; this bench gives a clean "record → transcribe
→ answer → hear" cycle for testing, with editable transcripts when the ASR
mishears.

It is a loopback-only developer test bench, not an authenticated product
surface.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import dataclasses
import io
import json
import logging
import math
import platform
import time
import wave
from array import array
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from .config import Settings, is_loopback_host
from .conversation import create_conversation
from .live_voice import Speaker
from .llm import LlamaServerClient, LlamaServerError, StreamDelta, StreamDone
from .manage_api import install_management
from .manage_models import (
    CATALOG,
    _activation_value,
    _active_id,
    installed_models,
)
from .manage_common import persist_env_updates
from .voice import LocalAudio, VoiceError

logger = logging.getLogger("persian_voice_ai.webui")

WEB_DIR = Path(__file__).resolve().parents[1] / "web"
ASSETS_DIR = WEB_DIR / "assets"


class TurnRequest(BaseModel):
    text: str = ""
    pcm_b64: str = ""
    sample_rate: int = Field(default=16000, ge=8000, le=48000)


class TranscribeRequest(BaseModel):
    """Transcription only: no LLM, no TTS — the step-by-step mode."""

    pcm_b64: str = ""
    use_last: bool = False
    sample_rate: int = Field(default=16000, ge=8000, le=48000)
    """Re-run ASR on the last recording with different settings."""
    beam_size: int | None = Field(default=None, ge=1, le=5)
    language: str | None = None
    initial_prompt: str | None = None


class ChatRequest(BaseModel):
    text: str


class TtsRequest(BaseModel):
    text: str


class VoiceSelectRequest(BaseModel):
    id: str


def pcm_stats(pcm: bytes, sample_rate: int = 16000) -> dict[str, float]:
    """Loudness facts about a capture: a quiet or clipped clip is a capture bug.

    Pure Python on purpose: the web bench may run without numpy (it is part of
    the optional voice stack), and a 4 s clip is cheap to scan.
    """
    samples = array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    if not samples:
        return {"seconds": 0.0, "rms_dbfs": -120.0, "peak_dbfs": -120.0, "speech_ratio": 0.0}
    peak = max(max(samples), -min(samples)) / 32768.0
    mean_square = sum(float(value) * float(value) for value in samples) / len(samples)
    rms = math.sqrt(mean_square) / 32768.0
    window = max(1, sample_rate // 50)
    loud_windows = 0
    windows = 0
    for start in range(0, len(samples) - window + 1, window):
        chunk = samples[start:start + window]
        level = math.sqrt(sum(float(value) * float(value) for value in chunk) / window) / 32768.0
        windows += 1
        if level > 0.01:
            loud_windows += 1
    return {
        "seconds": round(len(samples) / sample_rate, 2),
        "rms_dbfs": round(20 * math.log10(rms + 1e-9), 1),
        "peak_dbfs": round(20 * math.log10(peak + 1e-9), 1),
        "speech_ratio": round(loud_windows / max(1, windows), 2),
    }


def wav_bytes(pcm: bytes, rate: int) -> bytes:
    """Wrap mono PCM16 in a WAV container the browser can play directly."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(pcm)
    return buffer.getvalue()


def decode_pcm(value: str) -> bytes:
    """Decode a base64 PCM16 payload or raise a clear 400."""
    try:
        pcm = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="دادهٔ صوتی معتبر نیست.") from exc
    if not pcm or len(pcm) % 2:
        raise HTTPException(status_code=400, detail="دادهٔ صوتی خالی یا ناقص است.")
    return pcm


class WebUIState:
    """Models and conversation for the bench, created once per app."""

    def __init__(
        self,
        settings: Settings,
        *,
        asr: Any = None,
        speaker: Any = None,
        client: Any = None,
        speaker_factory: Callable[[Settings], Any] | None = None,
        models_dir: Path | None = None,
        env_path: Path | None = None,
    ) -> None:
        self.settings = settings
        self.asr = asr
        self.speaker = speaker
        self.speaker_factory = speaker_factory or Speaker
        self.models_dir = models_dir or (Path(__file__).resolve().parents[1] / "models")
        self.env_path = env_path or (Path(__file__).resolve().parents[1] / ".env")
        self.client = client if client is not None else LlamaServerClient(settings)
        # One persistent conversation per bench: every message is appended to the
        # local JSONL store (PVAI_HISTORY_PERSIST) and reloaded on restart, so the
        # model keeps earlier facts such as the user's name across sessions.
        self.conversation = create_conversation(settings, local=True, exclusive=True)
        self.turn_lock = asyncio.Lock()
        self.last_pcm = b""
        self.last_rate = 16000
        """The previous recording, kept so ASR settings can be compared on the
        exact same audio instead of asking the speaker to repeat themselves."""
        self.status: dict[str, str] = {"asr": "ready" if asr else "loading", "tts": "ready" if speaker else "loading", "error": ""}
        self._loader: asyncio.Task | None = None

    # ----------------------------------------------------------- conversation
    def model_context(self) -> list[dict[str, str]]:
        """The window the model sees: the stored conversation, token-budgeted."""
        return self.conversation.context()

    async def run_turn(self, text: str) -> tuple[str, dict[str, float]]:
        """One model turn: record the user line, ask the model, record the reply.

        The user message is stored *before* the model call so a failed llama-server
        request still leaves the question on disk; the reply is stored only when
        the model actually answered, so a failure never produces a fake answer.
        Caller must hold ``turn_lock``.
        """
        conversation = self.conversation
        turn_id = conversation.new_turn_id()
        conversation.add_user(text, turn_id=turn_id)
        messages = self.model_context()
        started = time.perf_counter()
        try:
            result = await self.client.chat(messages)
        except LlamaServerError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        reply = (result.content or "").strip()
        timings = {
            "llm_ms": round((time.perf_counter() - started) * 1000, 1),
            "prompt_tokens": float(result.timings.prompt_tokens),
            "completion_tokens": float(result.timings.completion_tokens),
        }
        conversation.finish_assistant(reply, turn_id=turn_id)
        return reply, timings

    async def stream_turn(self, text: str) -> AsyncIterator[dict[str, Any]]:
        """One streamed model turn, reported as events for the browser.

        The user line is stored before the request (a failed call still leaves
        the question on disk) and the reply only after the stream finished, so
        an aborted turn never becomes a fake answer in the history.
        Caller must hold ``turn_lock``.
        """
        conversation = self.conversation
        turn_id = conversation.new_turn_id()
        conversation.add_user(text, turn_id=turn_id)
        messages = self.model_context()
        started = time.perf_counter()
        first_ms: float | None = None
        pieces: list[str] = []
        result = None
        try:
            async for event in self.client.stream_chat(messages):
                if isinstance(event, StreamDelta):
                    if first_ms is None:
                        first_ms = round((time.perf_counter() - started) * 1000, 1)
                    pieces.append(event.content)
                    yield {"type": "delta", "text": event.content}
                elif isinstance(event, StreamDone):
                    result = event.result
        except LlamaServerError as exc:
            yield {"type": "error", "detail": str(exc)}
            return
        reply = "".join(pieces).strip()
        timings = {
            "llm_ms": round((time.perf_counter() - started) * 1000, 1),
            "prompt_tokens": float(getattr(result.timings, "prompt_tokens", 0) if result else 0),
            "completion_tokens": float(
                getattr(result.timings, "completion_tokens", 0) if result else len(pieces)
            ),
        }
        conversation.finish_assistant(reply, turn_id=turn_id)
        yield {
            "type": "done",
            "reply": reply,
            "timings": timings,
            "ttft_ms": first_ms,
        }

    # ----------------------------------------------------------- lifecycle
    def start_loading(self) -> None:
        """Load Whisper and Piper in the background; the server stays responsive."""
        if self._loader is None and (self.asr is None or self.speaker is None):
            self._loader = asyncio.create_task(self._load_models())

    async def _load_models(self) -> None:
        if self.asr is None:
            try:
                self.asr = await asyncio.to_thread(self._build_asr)
                self.status["asr"] = "ready"
            except Exception as exc:  # noqa: BLE001 - reported through /api/status
                self.status["asr"] = "failed"
                self.status["error"] = f"ASR: {exc}"
                logger.warning("web UI ASR load failed: %s", exc)
        if self.speaker is None:
            try:
                self.speaker = await asyncio.to_thread(Speaker, self.settings)
                self.status["tts"] = "ready"
            except Exception as exc:  # noqa: BLE001
                self.status["tts"] = "failed"
                self.status["error"] = (self.status["error"] + f" TTS: {exc}").strip()
                logger.warning("web UI TTS load failed: %s", exc)

    def _build_asr(self) -> LocalAudio:
        audio = LocalAudio(self.settings)
        try:
            audio.validate_asr()
            audio.warmup()
        except Exception:
            audio.close()
            raise
        return audio

    def transcribe_with(
        self, pcm: bytes, overrides: dict[str, Any] | None = None, *, sample_rate: int = 16000,
    ) -> str:
        """Transcribe one clip, optionally overriding the ASR settings for it.

        The model stays loaded; only the per-decode options change, so the same
        recording can be compared under different beams/prompts without asking
        the speaker to repeat and without paying the model-load cost again.
        """
        if self.asr is None:
            raise VoiceError("رونویسی هنوز آماده نیست.")
        rate_options = {"sample_rate": sample_rate} if sample_rate != 16000 else {}
        if not overrides or not hasattr(self.asr, "settings"):
            return self.asr.transcribe_pcm(pcm, **rate_options)
        previous = self.asr.settings
        self.asr.settings = dataclasses.replace(previous, **overrides)
        try:
            return self.asr.transcribe_pcm(pcm, **rate_options)
        finally:
            self.asr.settings = previous

    async def run_model_worker(self, worker: Callable[..., Any], *args: Any, **options: Any) -> Any:
        """Keep the caller's turn lock until a cancelled worker has actually stopped."""
        task = asyncio.create_task(asyncio.to_thread(worker, *args, **options))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await asyncio.gather(task, return_exceptions=True)
            raise

    def voices(self) -> dict[str, Any]:
        """Installed Piper voices plus the one the bench is currently using."""
        installed = installed_models(self.models_dir, "tts")
        active = _active_id("tts", self.settings, self.models_dir)
        labels: dict[str, str] = {}
        for entry in CATALOG:
            if entry.kind != "tts":
                continue
            for file in entry.files:
                labels[Path(file.name).stem] = entry.label_fa
            for alias in entry.aliases:
                labels[alias] = entry.label_fa
        voices = [
            {
                "id": item["id"],
                "name": item["name"],
                "path": item["path"],
                "valid": item["valid"],
                "active": item["id"] == active,
                "label_fa": labels.get(item["id"], item["id"]),
            }
            for item in installed
        ]
        return {"active_id": active, "voices": voices}

    async def select_voice(self, model_id: str) -> dict[str, Any]:
        """Persist the chosen voice and hot-swap the running Piper model.

        The bench owns its own ``Speaker``; rewriting ``.env`` alone would only
        take effect after a restart.  Building the replacement first keeps the
        old voice usable when the new file is broken.
        """
        installed = installed_models(self.models_dir, "tts")
        item = next((entry for entry in installed if entry["id"] == model_id), None)
        if item is None:
            raise HTTPException(status_code=404, detail="این صدا نصب نشده است.")
        if not item["valid"]:
            raise HTTPException(status_code=409, detail="فایل تنظیمات این صدا کامل نیست.")
        value = _activation_value("tts", Path(item["path"]), self.models_dir)
        updates = {"PVAI_TTS_MODEL_PATH": value}
        if (self.settings.tts_voice or "").strip().lower() == "male":
            updates["PVAI_TTS_MODEL_MALE"] = ""
        else:
            updates["PVAI_TTS_MODEL_FEMALE"] = ""
        try:
            persist_env_updates(self.env_path, updates)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        new_settings = dataclasses.replace(
            self.settings,
            tts_model_path=value,
            tts_model_male="" if "PVAI_TTS_MODEL_MALE" in updates else self.settings.tts_model_male,
            tts_model_female="" if "PVAI_TTS_MODEL_FEMALE" in updates else self.settings.tts_model_female,
        )
        try:
            replacement = await asyncio.to_thread(self.speaker_factory, new_settings)
        except Exception as exc:  # noqa: BLE001 - reported to the page
            raise HTTPException(status_code=500, detail=f"بارگذاری صدای جدید ناموفق بود: {exc}") from exc
        previous = self.speaker
        self.speaker = replacement
        self.settings = new_settings
        self.status["tts"] = "ready"
        closer = getattr(previous, "close", None)
        if callable(closer):
            try:
                await asyncio.to_thread(closer)
            except Exception:  # noqa: BLE001 - teardown must not fail
                pass
        return {"active_id": model_id, "path": value, "label_fa": next(
            (voice["label_fa"] for voice in self.voices()["voices"] if voice["id"] == model_id),
            model_id,
        )}

    async def transcribe_recording(
        self, pcm: bytes, sample_rate: int, overrides: dict[str, Any] | None = None,
    ) -> str:
        """Run just ASR; the caller owns the turn lock and recording selection."""
        try:
            text = await self.run_model_worker(
                self.transcribe_with, pcm, overrides, sample_rate=sample_rate
            )
        except (VoiceError, ValueError) as exc:
            raise HTTPException(status_code=500, detail=f"رونویسی ناموفق بود: {exc}") from exc
        return (text or "").strip()

    async def aclose(self) -> None:
        self.conversation.close()
        if self._loader is not None and not self._loader.done():
            self._loader.cancel()
        for component in (self.asr, self.speaker):
            closer = getattr(component, "close", None)
            if callable(closer):
                try:
                    await asyncio.to_thread(closer)
                except Exception:  # noqa: BLE001 - teardown must not fail
                    pass
        closer = getattr(self.client, "aclose", None)
        if callable(closer):
            try:
                await closer()
            except Exception:  # noqa: BLE001
                pass


def create_app(
    settings: Settings | None = None,
    *,
    asr: Any = None,
    speaker: Any = None,
    client: Any = None,
    speaker_factory: Callable[[Settings], Any] | None = None,
    models_dir: Path | None = None,
    env_path: Path | None = None,
    preload: bool = True,
    panel_host: str = "127.0.0.1",
    panel_port: int = 8100,
) -> FastAPI:
    """Build the bench app; tests inject fakes through the keyword arguments."""
    resolved = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # noqa: ANN202
        state: WebUIState = app.state.webui
        if preload:
            state.start_loading()
        try:
            yield
        finally:
            await state.aclose()
            # Only children the panel started; a llama-server launched outside
            # the panel is deliberately left running.
            await asyncio.to_thread(panel_manager.shutdown)

    app = FastAPI(title="Persian Voice AI — Test Bench", lifespan=lifespan)
    app.state.started_at = time.time()
    app.state.webui = WebUIState(
        resolved,
        asr=asr,
        speaker=speaker,
        client=client,
        speaker_factory=speaker_factory,
        models_dir=models_dir,
        env_path=env_path,
    )
    panel_manager = install_management(
        app,
        resolved,
        env_path=env_path,
        models_dir=models_dir,
        host=panel_host,
        port=panel_port,
    )

    @app.middleware("http")
    async def restrict_to_loopback(request: Request, call_next: Callable[..., Any]) -> Response:
        peer = request.client.host if request.client else None
        try:
            host = request.url.hostname
        except ValueError:
            host = None
        origin = request.headers.get("origin")
        expected_origin = f"{request.url.scheme}://{request.url.netloc}"
        if (
            not peer or not is_loopback_host(peer)
            or not host or not is_loopback_host(host)
            or (origin is not None and origin != expected_origin)
        ):
            return JSONResponse(
                status_code=403, content={"detail": "میز تست فقط از همین رایانه در دسترس است."}
            )
        return await call_next(request)

    @app.get("/")
    async def index() -> FileResponse:
        page = WEB_DIR / "index.html"
        if not page.is_file():
            raise HTTPException(status_code=500, detail="web/index.html missing")
        return FileResponse(
            page, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-cache"}
        )

    @app.get("/assets/{asset_path:path}")
    async def asset(asset_path: str) -> FileResponse:
        """Self-hosted CSS/JS/font.  Nothing here is fetched from a CDN."""
        target = (ASSETS_DIR / asset_path).resolve()
        try:
            target.relative_to(ASSETS_DIR.resolve())
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="فایل پیدا نشد.") from exc
        if not target.is_file():
            raise HTTPException(status_code=404, detail="فایل پیدا نشد.")
        media = {
            ".css": "text/css; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".woff2": "font/woff2",
            ".svg": "image/svg+xml",
        }.get(target.suffix.lower(), "application/octet-stream")
        return FileResponse(target, media_type=media, headers={"Cache-Control": "no-cache"})

    @app.get("/api/status")
    async def status() -> dict[str, Any]:
        state: WebUIState = app.state.webui
        return {
            **state.status,
            "messages": len(state.conversation.messages),
            "persisted": bool(state.conversation.store.enabled),
            "store_error": getattr(state.conversation.store, "error", None),
            "thinking": bool(getattr(resolved, "llm_enable_thinking", False)),
        }

    @app.post("/api/reset")
    async def reset() -> dict[str, str]:
        """Clear the in-memory window only; the JSONL log on disk is kept."""
        state: WebUIState = app.state.webui
        async with state.turn_lock:
            state.conversation.clear()
        return {"status": "ok"}

    @app.get("/api/history")
    async def history() -> dict[str, Any]:
        state: WebUIState = app.state.webui
        return {
            "messages": [
                {"role": message.role, "content": message.content}
                for message in state.conversation.messages
            ]
        }

    @app.get("/api/last.wav")
    async def last_recording() -> Response:
        state: WebUIState = app.state.webui
        if not state.last_pcm:
            raise HTTPException(status_code=404, detail="هنوز ضبطی وجود ندارد.")
        return Response(
            content=wav_bytes(state.last_pcm, state.last_rate),
            media_type="audio/wav",
            headers={"Content-Disposition": 'attachment; filename="last-recording.wav"'},
        )

    @app.post("/api/transcribe")
    async def transcribe(payload: TranscribeRequest) -> dict[str, Any]:
        """Only ASR: the recording never reaches the LLM or the speaker."""
        state: WebUIState = app.state.webui
        if state.asr is None:
            raise HTTPException(status_code=503, detail="رونویسی هنوز آماده نیست؛ چند لحظه صبر کنید.")
        overrides: dict[str, Any] = {}
        if payload.beam_size is not None:
            overrides["asr_beam_size"] = payload.beam_size
        if payload.language:
            overrides["asr_language"] = payload.language
        if payload.initial_prompt is not None:
            overrides["asr_initial_prompt"] = payload.initial_prompt.strip()
        started = time.perf_counter()
        async with state.turn_lock:
            if payload.use_last:
                if not state.last_pcm:
                    raise HTTPException(status_code=404, detail="هنوز ضبطی وجود ندارد.")
                pcm, rate = state.last_pcm, state.last_rate
            else:
                pcm, rate = decode_pcm(payload.pcm_b64), payload.sample_rate
                state.last_pcm, state.last_rate = pcm, rate
            text = await state.transcribe_recording(pcm, rate, overrides)
        return {
            "transcript": text,
            "timings": {"asr_ms": round((time.perf_counter() - started) * 1000, 1)},
            "audio": pcm_stats(pcm, rate),
        }

    @app.post("/api/chat")
    async def chat(payload: ChatRequest) -> dict[str, Any]:
        """Only the model: text in, text out, no audio."""
        state: WebUIState = app.state.webui
        text = payload.text.strip()
        if not text:
            raise HTTPException(status_code=400, detail="متن ورودی خالی است.")
        async with state.turn_lock:
            reply, timings = await state.run_turn(text)
        return {"reply": reply, "timings": timings}

    @app.post("/api/chat/stream")
    async def chat_stream(payload: ChatRequest) -> StreamingResponse:
        """Same turn as ``/api/chat``, streamed as Server-Sent Events.

        Events: ``delta`` (text piece), ``done`` (full reply + timings) and
        ``error`` (Persian detail).  The stream always ends with ``[DONE]``.
        """
        state: WebUIState = app.state.webui
        text = payload.text.strip()
        if not text:
            raise HTTPException(status_code=400, detail="متن ورودی خالی است.")

        async def events() -> AsyncIterator[str]:
            try:
                async with state.turn_lock:
                    async for event in state.stream_turn(text):
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - reported to the page
                logger.warning("web UI stream failed: %s", exc)
                detail = f"خطا در پاسخ مدل: {exc}"
                yield "data: " + json.dumps({"type": "error", "detail": detail}, ensure_ascii=False) + "\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/diagnostics")
    async def diagnostics() -> dict[str, Any]:
        """Everything the debug tab needs, in one read-only call."""
        state: WebUIState = app.state.webui
        try:
            backend = await state.client.health()
        except Exception as exc:  # noqa: BLE001 - diagnostics must never raise
            backend = {"reachable": False, "detail": f"بررسی مدل ناموفق بود: {exc}"}
        return {
            "server": {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "uptime_s": round(time.time() - app.state.started_at, 1),
            },
            "asr": {
                "status": state.status["asr"],
                "model": str(resolved.asr_model_path),
                "device": resolved.asr_device or "auto",
                "compute_type": resolved.asr_compute_type,
                "threads": resolved.asr_threads,
                "beam_size": resolved.asr_beam_size,
                "language": resolved.asr_language,
            },
            "tts": {"status": state.status["tts"], "model": str(state.settings.tts_model_path)},
            "llm": backend,
            "conversation": {
                "messages": len(state.conversation.messages),
                "persisted": bool(state.conversation.store.enabled),
                "store_error": getattr(state.conversation.store, "error", None),
            },
            "audio": {
                "last_seconds": round(len(state.last_pcm) / 2 / max(1, state.last_rate), 2),
                "last_rate": state.last_rate,
            },
            "thinking": bool(getattr(resolved, "llm_enable_thinking", False)),
        }

    @app.post("/api/tts")
    async def tts(payload: TtsRequest) -> dict[str, Any]:
        """Only the voice: text in, playable WAV out, no model call."""
        state: WebUIState = app.state.webui
        text = payload.text.strip()
        if not text:
            raise HTTPException(status_code=400, detail="متن ورودی خالی است.")
        if state.speaker is None:
            raise HTTPException(status_code=503, detail="صدا هنوز آماده نیست؛ چند لحظه صبر کنید.")
        started = time.perf_counter()
        try:
            async with state.turn_lock:
                pcm, rate = await state.run_model_worker(state.speaker.synthesize_pcm, text)
        except VoiceError as exc:
            raise HTTPException(status_code=500, detail=f"تولید صدا ناموفق بود: {exc}") from exc
        return {
            "tts_wav_b64": base64.b64encode(wav_bytes(pcm, rate)).decode("ascii") if pcm else "",
            "timings": {"tts_ms": round((time.perf_counter() - started) * 1000, 1)},
        }

    @app.get("/api/voices")
    async def voices() -> dict[str, Any]:
        """Piper voices installed under ``models/`` and the active one."""
        state: WebUIState = app.state.webui
        return state.voices()

    @app.post("/api/voices/select")
    async def select_voice(payload: VoiceSelectRequest) -> dict[str, Any]:
        """Switch the bench voice now; the choice is also saved to ``.env``."""
        state: WebUIState = app.state.webui
        async with state.turn_lock:
            return await state.select_voice(payload.id.strip())

    @app.post("/api/turn")
    async def turn(payload: TurnRequest) -> dict[str, Any]:
        state: WebUIState = app.state.webui
        timings: dict[str, float] = {}
        transcript = payload.text.strip()

        async with state.turn_lock:
            if payload.pcm_b64:
                if state.asr is None:
                    raise HTTPException(status_code=503, detail="رونویسی هنوز آماده نیست؛ چند لحظه صبر کنید.")
                pcm = decode_pcm(payload.pcm_b64)
                state.last_pcm, state.last_rate = pcm, payload.sample_rate
                started = time.perf_counter()
                transcript = await state.transcribe_recording(pcm, payload.sample_rate)
                timings["asr_ms"] = round((time.perf_counter() - started) * 1000, 1)

            if not transcript:
                return {
                    "transcript": "",
                    "reply": "",
                    "tts_wav_b64": "",
                    "timings": timings,
                    "notice": "چیزی شنیده نشد؛ دوباره و واضح‌تر صحبت کنید.",
                }

            reply, model_timings = await state.run_turn(transcript)
            timings.update(model_timings)

            audio_b64 = ""
            if reply and state.speaker is not None:
                started = time.perf_counter()
                try:
                    pcm, rate = await state.run_model_worker(state.speaker.synthesize_pcm, reply)
                    if pcm:
                        audio_b64 = base64.b64encode(wav_bytes(pcm, rate)).decode("ascii")
                except VoiceError as exc:
                    logger.warning("web UI TTS failed: %s", exc)
                timings["tts_ms"] = round((time.perf_counter() - started) * 1000, 1)

        return {
            "transcript": transcript,
            "reply": reply,
            "tts_wav_b64": audio_b64,
            "timings": timings,
        }

    return app


def main() -> int:
    """Run the bench: ``python -m server.webui [--port N]``."""
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="محیط تست وب یک‌نوبتی (فقط localhost)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8100)
    args = parser.parse_args()
    if not args.host or not is_loopback_host(args.host):
        parser.error("میز تست فقط روی نشانی loopback اجرا می‌شود.")
    uvicorn.run(
        create_app(panel_host=args.host, panel_port=args.port),
        host=args.host,
        port=args.port,
        log_level="info",
        proxy_headers=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
