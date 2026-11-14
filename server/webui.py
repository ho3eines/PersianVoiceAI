"""Minimal local web UI for one-turn voice/text testing.

The page (``web/index.html``) talks to JSON endpoints here; the server reuses
the same components as the live pipeline (``LocalAudio``, ``Speaker``,
``LlamaServerClient``) but drives exactly one turn at a time.  On the two-core
target laptop Whisper needs tens of seconds per decode, so the continuous live
loop floods its final-ASR queue; this bench gives a clean "record → transcribe
→ answer → hear" cycle for testing, with editable transcripts when the ASR
mishears.

It binds to loopback by default and has no authentication: it is a developer
test bench, not a product surface.  Do not expose it on the LAN as-is.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import dataclasses
import io
import logging
import math
import time
import wave
from array import array
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from .config import Settings
from .conversation import create_conversation
from .live_voice import Speaker
from .llm import LlamaServerClient, LlamaServerError
from .voice import LocalAudio, VoiceError

logger = logging.getLogger("persian_voice_ai.webui")

WEB_DIR = Path(__file__).resolve().parents[1] / "web"


class TurnRequest(BaseModel):
    text: str = ""
    pcm_b64: str = ""
    sample_rate: int = Field(default=16000, ge=8000, le=48000)


class TranscribeRequest(BaseModel):
    """Transcription only: no LLM, no TTS — the step-by-step mode."""

    pcm_b64: str = ""
    use_last: bool = False
    """Re-run ASR on the last recording with different settings."""
    beam_size: int | None = Field(default=None, ge=1, le=5)
    language: str | None = None
    initial_prompt: str | None = None


class ChatRequest(BaseModel):
    text: str


class TtsRequest(BaseModel):
    text: str


def pcm_stats(pcm: bytes) -> dict[str, float]:
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
    window = 320  # 20 ms at 16 kHz
    loud_windows = 0
    windows = 0
    for start in range(0, len(samples) - window, window):
        chunk = samples[start:start + window]
        level = math.sqrt(sum(float(value) * float(value) for value in chunk) / window) / 32768.0
        windows += 1
        if level > 0.01:
            loud_windows += 1
    return {
        "seconds": round(len(samples) / 16000, 2),
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
    ) -> None:
        self.settings = settings
        self.asr = asr
        self.speaker = speaker
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
            audio.validate()
            audio.warmup()
        except Exception:
            audio.close()
            raise
        return audio

    def transcribe_with(self, pcm: bytes, overrides: dict[str, Any] | None = None) -> str:
        """Transcribe one clip, optionally overriding the ASR settings for it.

        The model stays loaded; only the per-decode options change, so the same
        recording can be compared under different beams/prompts without asking
        the speaker to repeat and without paying the model-load cost again.
        """
        if self.asr is None:
            raise VoiceError("رونویسی هنوز آماده نیست.")
        if not overrides or not hasattr(self.asr, "settings"):
            return self.asr.transcribe_pcm(pcm)
        previous = self.asr.settings
        self.asr.settings = dataclasses.replace(previous, **overrides)
        try:
            return self.asr.transcribe_pcm(pcm)
        finally:
            self.asr.settings = previous

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
    preload: bool = True,
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

    app = FastAPI(title="Persian Voice AI — Test Bench", lifespan=lifespan)
    app.state.webui = WebUIState(resolved, asr=asr, speaker=speaker, client=client)

    @app.get("/")
    async def index() -> FileResponse:
        page = WEB_DIR / "index.html"
        if not page.is_file():
            raise HTTPException(status_code=500, detail="web/index.html missing")
        return FileResponse(page, media_type="text/html; charset=utf-8")

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
        if payload.use_last:
            if not state.last_pcm:
                raise HTTPException(status_code=404, detail="هنوز ضبطی وجود ندارد.")
            pcm = state.last_pcm
        else:
            pcm = decode_pcm(payload.pcm_b64)
            state.last_pcm = pcm
            state.last_rate = 16000
        overrides: dict[str, Any] = {}
        if payload.beam_size is not None:
            overrides["asr_beam_size"] = payload.beam_size
        if payload.language:
            overrides["asr_language"] = payload.language
        if payload.initial_prompt is not None:
            overrides["asr_initial_prompt"] = payload.initial_prompt.strip()
        started = time.perf_counter()
        try:
            # transcribe_with swaps the ASR settings in place, so two concurrent
            # decodes would corrupt each other; the bench is one-turn-at-a-time.
            async with state.turn_lock:
                text = await asyncio.to_thread(state.transcribe_with, pcm, overrides)
        except (VoiceError, ValueError) as exc:
            raise HTTPException(status_code=500, detail=f"رونویسی ناموفق بود: {exc}") from exc
        return {
            "transcript": (text or "").strip(),
            "timings": {"asr_ms": round((time.perf_counter() - started) * 1000, 1)},
            "audio": pcm_stats(pcm),
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
            pcm, rate = await asyncio.to_thread(state.speaker.synthesize_pcm, text)
        except VoiceError as exc:
            raise HTTPException(status_code=500, detail=f"تولید صدا ناموفق بود: {exc}") from exc
        return {
            "tts_wav_b64": base64.b64encode(wav_bytes(pcm, rate)).decode("ascii") if pcm else "",
            "timings": {"tts_ms": round((time.perf_counter() - started) * 1000, 1)},
        }

    @app.post("/api/turn")
    async def turn(payload: TurnRequest) -> dict[str, Any]:
        state: WebUIState = app.state.webui
        timings: dict[str, float] = {}
        transcript = payload.text.strip()

        if payload.pcm_b64:
            if state.asr is None:
                raise HTTPException(status_code=503, detail="رونویسی هنوز آماده نیست؛ چند لحظه صبر کنید.")
            pcm = decode_pcm(payload.pcm_b64)
            state.last_pcm = pcm
            state.last_rate = payload.sample_rate
            started = time.perf_counter()
            try:
                transcript = await asyncio.to_thread(state.asr.transcribe_pcm, pcm)
            except (VoiceError, ValueError) as exc:
                raise HTTPException(status_code=500, detail=f"رونویسی ناموفق بود: {exc}") from exc
            transcript = (transcript or "").strip()
            timings["asr_ms"] = round((time.perf_counter() - started) * 1000, 1)

        if not transcript:
            return {
                "transcript": "",
                "reply": "",
                "tts_wav_b64": "",
                "timings": timings,
                "notice": "چیزی شنیده نشد؛ دوباره و واضح‌تر صحبت کنید.",
            }

        async with state.turn_lock:
            reply, model_timings = await state.run_turn(transcript)
            timings.update(model_timings)

            audio_b64 = ""
            if reply and state.speaker is not None:
                started = time.perf_counter()
                try:
                    pcm, rate = await asyncio.to_thread(state.speaker.synthesize_pcm, reply)
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
    if args.host not in {"127.0.0.1", "localhost"}:
        print("هشدار: این محیط احراز هویت ندارد؛ فقط روی localhost اجرا کنید.")
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
