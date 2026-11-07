"""Event-driven PC pipeline: rolling ASR, cancellable SSE, and PCM playback.

Whisper is adapted with growing overlapping snapshots, not a native streaming model.
Only endpoint-final text triggers an LLM request; partial text is observable while speaking.
"""
from __future__ import annotations

import asyncio
import json
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from .config import Settings
from .live_voice import Endpoint, Speaker, sentences
from .voice import LocalAudio, VoiceError, dependency, local_api_url


@dataclass(frozen=True)
class Snapshot:
    generation: int
    pcm: bytes
    final: bool


def agreement(previous: str, current: str) -> str:
    """Two-pass local agreement; provisional prefix may still change on final decode."""
    words = []
    for left, right in zip(previous.split(), current.split()):
        if left != right:
            break
        words.append(right)
    return " ".join(words)


class Pipeline:
    def __init__(self, settings: Settings, audio: Any, speaker: Any, client: Any,
                 emit: Callable[[dict[str, Any]], None]) -> None:
        self.settings, self.audio, self.speaker, self.client, self.emit = settings, audio, speaker, client, emit
        self.detector = Endpoint(settings.voice_vad_threshold)
        self.generation = 0
        self.cancel = threading.Event()
        self.snapshots: asyncio.Queue[Snapshot] = asyncio.Queue(maxsize=1)
        self.speech: asyncio.Queue[tuple[int, threading.Event, str]] = asyncio.Queue(maxsize=8)
        self.reply: asyncio.Task[None] | None = None
        self.actors: list[asyncio.Task[None]] = []
        self.history: list[dict[str, str]] = []
        self.last_snapshot_frames = 0
        self.previous = ""
        self.endpoint_at = 0.0

    def event(self, kind: str, **values: Any) -> None:
        self.emit({"type": kind, "generation": self.generation, **values})

    def start(self) -> None:
        self.actors = [asyncio.create_task(self.recognize()), asyncio.create_task(self.play())]

    def interrupt(self) -> None:
        self.cancel.set()
        if self.reply is not None:
            self.reply.cancel()  # async SSE response context closes even while waiting for a token
        self.cancel = threading.Event()
        self.generation += 1
        self.previous = ""
        self.last_snapshot_frames = 0
        while not self.snapshots.empty():
            self.snapshots.get_nowait()
        while not self.speech.empty():
            self.speech.get_nowait()
        self.event("interrupted")

    def offer(self, pcm: bytes, final: bool) -> None:
        if not self.snapshots.empty():
            self.snapshots.get_nowait()  # latest snapshot wins; ASR never builds an unbounded backlog
        self.snapshots.put_nowait(Snapshot(self.generation, pcm, final))

    def feed(self, pcm: bytes) -> None:
        onset, final = self.detector.feed(pcm)
        if onset:
            self.interrupt()
            self.event("speech_start")
        if final:
            self.endpoint_at = time.perf_counter()
            self.offer(final, True)
            self.event("speech_end")
        elif self.detector.started:
            count = len(self.detector.frames)
            if count - self.last_snapshot_frames >= int(self.settings.voice_asr_interval_s / .02):
                self.last_snapshot_frames = count
                self.offer(b"".join(self.detector.frames), False)

    def overflow(self) -> None:
        self.interrupt()
        self.detector = Endpoint(self.settings.voice_vad_threshold)
        self.event("error", detail="ورودی صوت عقب افتاد؛ نوبت جاری حذف شد.")

    async def recognize(self) -> None:
        # One actor owns Whisper; no concurrent native model calls on overload or cancellation.
        while True:
            item = await self.snapshots.get()
            if item.generation != self.generation:
                continue
            try:
                text = await asyncio.to_thread(self.audio.transcribe_pcm, item.pcm)
            except Exception:
                if item.generation == self.generation:
                    self.event("error", detail="تشخیص گفتار ناموفق بود؛ مدل محلی را بررسی کنید.")
                continue
            if item.generation != self.generation:
                continue
            stable = agreement(self.previous, text)
            self.previous = text
            self.event("transcript_final" if item.final else "transcript_partial",
                       text=text, agreed_prefix=stable)
            if item.final and text:
                if self.reply is not None:
                    self.reply.cancel()
                self.reply = asyncio.create_task(self.respond(text, item.generation, self.cancel))

    async def respond(self, text: str, generation: int, token: threading.Event) -> None:
        messages = self.history[-10:] + [{"role": "user", "content": text}]
        buffer, pieces = "", []
        completed = False
        first = True
        try:
            async with self.client.stream("POST", "/v1/chat/completions",
                                          json={"messages": messages, "stream": True}) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if generation != self.generation or token.is_set():
                        return
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        completed = True
                        break
                    data = json.loads(raw)
                    if data.get("error"):
                        raise VoiceError("خطای استریم پاسخ")
                    if "timings" in data:
                        self.event("llm_timings", timings=data["timings"])
                    for choice in data.get("choices", []):
                        piece = choice.get("delta", {}).get("content") or ""
                        if not piece:
                            continue
                        if first:
                            self.event("first_token", endpoint_ms=(time.perf_counter() - self.endpoint_at) * 1000)
                            first = False
                        pieces.append(piece)
                        self.event("response_delta", text=piece)
                        ready, buffer = sentences(buffer + piece)
                        for sentence in ready:
                            await self.speech.put((generation, token, sentence))
            if not completed:
                raise VoiceError("استریم پاسخ ناقص بود.")
            for sentence in sentences(buffer, final=True)[0]:
                await self.speech.put((generation, token, sentence))
            # Final marker commits only once queued audio has actually finished.
            self.pending_history = (generation, messages, "".join(pieces))
            await self.speech.put((generation, token, ""))
        except asyncio.CancelledError:
            token.set()
            raise
        except Exception:
            token.set()
            if generation == self.generation:
                self.event("error", detail="پاسخ استریم ناموفق بود؛ اتصال مدل را بررسی کنید.")

    async def play(self) -> None:
        # Single actor owns Piper/output. Cancelled native synthesis must return before next call.
        while True:
            generation, token, text = await self.speech.get()
            if generation != self.generation or token.is_set():
                continue
            if not text:
                pending = getattr(self, "pending_history", None)
                if pending and pending[0] == generation:
                    self.history = (pending[1] + [{"role": "assistant", "content": pending[2]}])[-12:]
                    self.event("response_end")
                continue
            try:
                self.event("tts_start", text=text)
                await asyncio.to_thread(self.speaker.speak, text, token)
                if not token.is_set():
                    self.event("tts_end")
            except Exception:
                token.set()
                if generation == self.generation:
                    self.event("error", detail="تولید یا پخش صدا ناموفق بود.")

    async def close(self) -> None:
        self.cancel.set()
        tasks = self.actors + ([self.reply] if self.reply is not None else [])
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def run_live(settings: Settings) -> None:
    from .aec import DuplexAudio, SpeexAEC

    audio = LocalAudio(settings)
    audio.validate()
    duplex = DuplexAudio()
    aec = SpeexAEC(settings.aec_library, filter_ms=settings.aec_filter_ms,
                   delay_ms=settings.aec_delay_ms) if settings.aec_enabled else None
    try:
        try:
            speaker = Speaker(settings, duplex=duplex)
        except Exception as exc:
            raise VoiceError("راه‌اندازی Piper ناموفق بود؛ مدل صوتی را بررسی کنید.") from exc
        sd = dependency("sounddevice")
        async with httpx.AsyncClient(base_url=local_api_url(settings), timeout=settings.request_timeout_s,
                                     trust_env=False, follow_redirects=False) as client:
            pipeline = Pipeline(settings, audio, speaker, client,
                                lambda event: print(json.dumps(event, ensure_ascii=False), flush=True))
            pipeline.start()
            print("شنیدن زنده فعال است؛ AEC " + ("فعال" if aec else "خاموش؛ از هدفون استفاده کنید") + ". خروج: Ctrl+C")
            try:
                # Same clock/stream for render reference and microphone capture.
                with sd.RawStream(samplerate=16000, blocksize=320, channels=1,
                                  dtype="int16", callback=duplex.callback):
                    while True:
                        if duplex.overflow.is_set():
                            pipeline.overflow()
                            if aec:
                                aec.reset()
                            while not duplex.capture.empty():
                                duplex.capture.get_nowait()
                            duplex.overflow.clear()
                        try:
                            microphone, reference = duplex.capture.get_nowait()
                        except queue.Empty:
                            await asyncio.sleep(.01)
                        else:
                            cleaned = aec.process(microphone, reference) if aec else microphone
                            pipeline.feed(cleaned)  # AEC always precedes VAD, ASR and barge-in.
                            await asyncio.sleep(0)
            finally:
                duplex.close()
                await pipeline.close()
    finally:
        duplex.close()
        if aec:
            aec.close()
