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
import io
import logging
import time
import wave
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .config import Settings
from .live_voice import Speaker
from .llm import LlamaServerClient, LlamaServerError
from .voice import LocalAudio, VoiceError

logger = logging.getLogger("persian_voice_ai.webui")

WEB_DIR = Path(__file__).resolve().parents[1] / "web"
MAX_HISTORY_MESSAGES = 12
"""Messages kept in RAM and sent to the model: the bench is not a chat archive."""


class TurnRequest(BaseModel):
    text: str = ""
    pcm_b64: str = ""
    sample_rate: int = Field(default=16000, ge=8000, le=48000)


def wav_bytes(pcm: bytes, rate: int) -> bytes:
    """Wrap mono PCM16 in a WAV container the browser can play directly."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(pcm)
    return buffer.getvalue()


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
        self.history: list[dict[str, str]] = []
        self.turn_lock = asyncio.Lock()
        self.status: dict[str, str] = {"asr": "ready" if asr else "loading", "tts": "ready" if speaker else "loading", "error": ""}
        self._loader: asyncio.Task | None = None

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

    async def aclose(self) -> None:
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
            "messages": len(state.history),
            "thinking": bool(getattr(resolved, "llm_enable_thinking", False)),
        }

    @app.post("/api/reset")
    async def reset() -> dict[str, str]:
        state: WebUIState = app.state.webui
        state.history.clear()
        return {"status": "ok"}

    @app.get("/api/history")
    async def history() -> dict[str, Any]:
        state: WebUIState = app.state.webui
        return {"messages": list(state.history)}

    @app.post("/api/turn")
    async def turn(payload: TurnRequest) -> dict[str, Any]:
        state: WebUIState = app.state.webui
        timings: dict[str, float] = {}
        transcript = payload.text.strip()

        if payload.pcm_b64:
            if state.asr is None:
                raise HTTPException(status_code=503, detail="رونویسی هنوز آماده نیست؛ چند لحظه صبر کنید.")
            try:
                pcm = base64.b64decode(payload.pcm_b64, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise HTTPException(status_code=400, detail="دادهٔ صوتی معتبر نیست.") from exc
            if not pcm or len(pcm) % 2:
                raise HTTPException(status_code=400, detail="دادهٔ صوتی خالی یا ناقص است.")
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
            messages = state.history[-MAX_HISTORY_MESSAGES:] + [
                {"role": "user", "content": transcript}
            ]
            started = time.perf_counter()
            try:
                result = await state.client.chat(messages)
            except LlamaServerError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            reply = (result.content or "").strip()
            timings["llm_ms"] = round((time.perf_counter() - started) * 1000, 1)
            timings["prompt_tokens"] = float(result.timings.prompt_tokens)
            timings["completion_tokens"] = float(result.timings.completion_tokens)

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

            state.history.extend(
                [
                    {"role": "user", "content": transcript},
                    {"role": "assistant", "content": reply},
                ]
            )
            del state.history[:-MAX_HISTORY_MESSAGES]

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
