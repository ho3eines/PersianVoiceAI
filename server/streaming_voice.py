"""Event-driven PC conversation engine.

Design contract (see ``docs/STREAMING_ARCHITECTURE.md``):

* **UserUtterance** — one detected speech segment. Identifiers increase
  monotonically and are *never* reused. An utterance owns its audio and its
  transcript; nothing about the assistant can invalidate it.
* **PartialASR** — replaceable, display-only. A partial result may be dropped
  at any time (bounded tail window, latest-only mailbox) without affecting the
  conversation.
* **FinalASR** — lossless FIFO. Every final job is transcribed, and its result
  is committed to the conversation exactly once, even if the user has already
  started the next sentence, the assistant was interrupted, or the audio input
  overflowed. Only shutdown discards a queued final, and it says so.
* **AssistantGeneration** — one LLM reply attempt tied to one utterance. A new
  user utterance cancels the *generation* (barge-in) and its queued speech, but
  never touches ASR jobs.

Whisper is not a streaming model: partials are produced with bounded
overlapping windows (``PVAI_ASR_PARTIAL_WINDOW_S``) so the per-decode cost does
not grow with utterance length, while the final decode always receives the
complete utterance audio.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

import httpx

from .config import Settings
from .conversation import (
    STATUS_COMPLETE,
    STATUS_FAILED,
    STATUS_INTERRUPTED,
    Conversation,
    create_conversation,
)
from .live_voice import Speaker, sentences
from .metrics import Metrics
from .vad import EnergyVAD, build_vad
from .voice import LocalAudio, VoiceError, dependency, local_api_url

BLOCK_SECONDS = 0.02
TRAILING_SILENCE_KEEP_FRAMES = 5
"""Trailing silence kept in the final ASR audio (100 ms at 20 ms blocks)."""


# --------------------------------------------------------------------------- state
class VoiceState(str, Enum):
    """Explicit conversational state; transitions live in ``ALLOWED_TRANSITIONS``."""

    IDLE = "idle"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    THINKING = "thinking"
    SPEAKING = "speaking"


ALLOWED_TRANSITIONS: dict[VoiceState, frozenset[VoiceState]] = {
    VoiceState.IDLE: frozenset({VoiceState.LISTENING}),
    VoiceState.LISTENING: frozenset(
        {VoiceState.IDLE, VoiceState.TRANSCRIBING, VoiceState.THINKING}
    ),
    VoiceState.TRANSCRIBING: frozenset(
        {VoiceState.IDLE, VoiceState.LISTENING, VoiceState.THINKING}
    ),
    VoiceState.THINKING: frozenset(
        {VoiceState.IDLE, VoiceState.LISTENING, VoiceState.SPEAKING}
    ),
    VoiceState.SPEAKING: frozenset(
        {VoiceState.IDLE, VoiceState.LISTENING, VoiceState.THINKING}
    ),
}
"""Allowed state transitions. Anything else is reported (``state_violation``)
instead of crashing the audio thread — a wrong transition must never be silent."""


class StateMachine:
    def __init__(self, on_event: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.state = VoiceState.IDLE
        self.on_event = on_event
        self.violations = 0

    def transition(self, target: VoiceState, *, reason: str = "", force: bool = False) -> bool:
        if target is self.state:
            return False
        allowed = target in ALLOWED_TRANSITIONS[self.state]
        if not allowed and not force:
            self.violations += 1
            if self.on_event is not None:
                self.on_event(
                    {
                        "type": "state_violation",
                        "state": self.state.value,
                        "target": target.value,
                        "reason": reason,
                    }
                )
        previous, self.state = self.state, target
        if self.on_event is not None:
            self.on_event(
                {
                    "type": "state",
                    "state": target.value,
                    "previous": previous.value,
                    "reason": reason,
                    "forced": bool(not allowed),
                }
            )
        return True


# ------------------------------------------------------------------------ records
@dataclass(frozen=True, slots=True)
class AsrJob:
    """One ASR request. ``final`` jobs are lossless; partials are replaceable."""

    utterance_id: int
    pcm: bytes
    final: bool
    submitted_at: float
    total_frames: int
    window_start_frame: int = 0
    block_ms: int = 20

    @property
    def audio_ms(self) -> float:
        return self.total_frames * self.block_ms

    @property
    def decoded_ms(self) -> float:
        return max(1, self.total_frames - self.window_start_frame) * self.block_ms


@dataclass(slots=True)
class Utterance:
    """A detected user speech segment, independent of assistant lifecycle."""

    id: int
    started_at: float
    status: str = "recording"  # recording | ended | transcribed | empty | failed
    ended_at: float | None = None
    text: str = ""
    partial_text: str = ""
    partial_anchor: int = 0
    partial_windowed: bool = False


@dataclass(slots=True)
class AssistantTurn:
    """One assistant reply attempt, cancellable independently of ASR."""

    turn_id: int
    utterance_id: int
    generation: int
    endpoint_at: float
    text: str = ""
    status: str = "streaming"  # streaming | speaking | complete | interrupted | failed
    first_token_at: float | None = None
    spoken: list[str] = field(default_factory=list)
    current: str | None = None
    """Sentence currently handed to synthesis/playback (not yet completed)."""
    committed: bool = False
    token: threading.Event = field(default_factory=threading.Event)


@dataclass(frozen=True, slots=True)
class SpeechItem:
    turn: AssistantTurn
    text: str
    token: threading.Event


class AsrScheduler:
    """Final ASR FIFO (lossless) + single latest-only partial slot.

    The separation is the core fix for P0-01: cancellation of an assistant
    generation cannot remove a final job, and a new user utterance cannot
    replace one. Finals are always served before partials so a short partial
    subscription can never delay the transcript that unblocks the conversation.
    """

    def __init__(self, *, max_finals: int = 16) -> None:
        self.finals: deque[AsrJob] = deque()
        self.partial: AsrJob | None = None
        self.max_finals = max(1, int(max_finals))
        self.dropped_partial = 0
        self.received_final = 0
        self.started_final = 0
        self.backlog_warnings = 0
        self._wakeup = asyncio.Event()

    # ------------------------------------------------------------------ submit
    def submit_final(self, job: AsrJob) -> None:
        assert job.final
        self.finals.append(job)
        self.received_final += 1
        if len(self.finals) > self.max_finals:
            self.backlog_warnings += 1
        # A pending partial for this (or an older) utterance is now obsolete.
        if self.partial is not None and self.partial.utterance_id <= job.utterance_id:
            self.partial = None
            self.dropped_partial += 1
        self._wakeup.set()

    def submit_partial(self, job: AsrJob) -> bool:
        """Store a partial; return ``False`` when a previous one was replaced."""
        assert not job.final
        replaced = self.partial is not None
        if replaced:
            self.dropped_partial += 1
        self.partial = job
        self._wakeup.set()
        return not replaced

    def drop_partial(self, utterance_id: int | None = None) -> bool:
        if self.partial is None:
            return False
        if utterance_id is None or self.partial.utterance_id <= utterance_id:
            self.partial = None
            self.dropped_partial += 1
            return True
        return False

    # -------------------------------------------------------------------- take
    async def next(self) -> AsrJob:
        """Return the next job; finals always win over a waiting partial.

        The wake-up flag is cleared *before* re-checking the queues.  Clearing
        it after the check (the previous order) loses a job that is submitted
        in the window between "queues are empty" and ``await wait()``: the
        submit sets the flag, the actor clears it, and no further submit ever
        arrives — a final transcript would sit in the queue forever.  This is a
        lost-wakeup bug, not a theoretical one, and it is the exact shape of
        P0-01 (a final that silently disappears).

        ``submit_*`` must be called on the event-loop thread (the audio
        coordinator calls ``Pipeline.feed`` there); finals submitted from
        another thread would need ``loop.call_soon_threadsafe``.
        """
        while True:
            if self.finals:
                self.started_final += 1
                return self.finals.popleft()
            if self.partial is not None:
                job, self.partial = self.partial, None
                return job
            self._wakeup.clear()
            if self.finals or self.partial is not None:  # re-check after clear
                continue
            await self._wakeup.wait()

    @property
    def depth(self) -> int:
        return len(self.finals) + (1 if self.partial is not None else 0)

    @property
    def pending_finals(self) -> int:
        return len(self.finals)


def agreement(previous: str, current: str) -> str:
    """Two-pass local agreement; provisional prefix may still change on final decode."""
    words = []
    for left, right in zip(previous.split(), current.split()):
        if left != right:
            break
        words.append(right)
    return " ".join(words)


# ---------------------------------------------------------------------- pipeline
class Pipeline:
    """Owns the conversation loop: audio in, transcripts, replies, speech out."""

    def __init__(
        self,
        settings: Settings,
        audio: Any,
        speaker: Any,
        client: Any,
        emit: Callable[[dict[str, Any]], None],
        *,
        conversation: Conversation | None = None,
        metrics: Metrics | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.settings = settings
        self.audio = audio
        self.speaker = speaker
        self.client = client
        self.emit = emit
        self.clock = clock
        self.metrics = metrics or Metrics(enabled=settings.metrics_enabled)
        self.conversation = conversation or Conversation(
            max_messages=settings.history_max_messages,
            token_budget=settings.history_token_budget,
            max_stored_messages=settings.history_max_stored_messages,
            include_interrupted=settings.history_include_interrupted,
        )
        self.states = StateMachine(self._emit_state)
        self.detector = build_vad(settings)
        self.scheduler = AsrScheduler(max_finals=settings.asr_max_final_jobs)
        self.speech: asyncio.Queue[SpeechItem] = asyncio.Queue(
            maxsize=settings.voice_speech_queue
        )
        self.reply: asyncio.Task[None] | None = None
        self.actors: list[asyncio.Task[None]] = []
        self.pending_turn: AssistantTurn | None = None
        self.history: list[dict[str, str]] = []  # legacy read-only mirror
        # Cancellation gate for playback/synthesis; replaced on every barge-in.
        self.cancel = threading.Event()
        self.generation = 0
        self.utterance: Utterance | None = None
        self.endpoint_at = 0.0
        self._latest_utterance_id = 0
        self._next_utterance_id = 1
        self._last_partial_frames = 0
        self._barged_in_utterance = 0
        self._finalized: deque[int] = deque(maxlen=512)
        self._finalized_ids: set[int] = set()
        self._closed = False
        self._partial_interval_frames = max(
            1, round(settings.voice_asr_interval_s * 1000 / settings.audio_block_ms)
        )
        self._partial_window_frames = max(
            1, round(settings.asr_partial_window_s * 1000 / settings.audio_block_ms)
        )
        self._barge_in_frames = max(
            1, round(settings.voice_barge_in_ms / settings.audio_block_ms)
        )
        self._block_ms = settings.audio_block_ms
        self._base_threshold = settings.voice_vad_threshold

    # --------------------------------------------------------------- utilities
    def _emit_state(self, payload: dict[str, Any]) -> None:
        self.emit({**payload, "generation": self.generation})

    def event(self, kind: str, **values: Any) -> None:
        payload = {
            "type": kind,
            "generation": self.generation,
            "state": self.states.state.value,
        }
        payload.update(values)
        try:
            self.emit(payload)
        except Exception:  # an emitter failure must never break the audio loop
            pass

    @property
    def state(self) -> VoiceState:
        return self.states.state

    def start(self) -> None:
        if self.actors:
            return
        self.actors = [asyncio.create_task(self.recognize()), asyncio.create_task(self.play())]
        self.event("ready", conversation_id=self.conversation.conversation_id)

    # ------------------------------------------------------------------ audio in
    def feed(self, pcm: bytes) -> None:
        """Consume one PCM16/16k mono block (called from the audio coordinator)."""
        if self._closed:
            return
        now = self.clock()
        onset, segment = self.detector.feed(pcm, at=now)
        if onset:
            self._begin_utterance(now)
        if segment is not None:
            self._end_utterance(segment, now)
        elif self.detector.started:
            self._maybe_offer_partial(now)
        self._maybe_barge_in()

    def _begin_utterance(self, now: float) -> None:
        self.utterance = Utterance(id=self._next_utterance_id, started_at=now)
        self._next_utterance_id += 1
        self._latest_utterance_id = self.utterance.id
        self._last_partial_frames = 0
        self._barged_in_utterance = 0
        self.metrics.increment("utterances")
        self.metrics.observe("vad_start_ms", self.detector.onset_delay_ms())
        self.metrics.set("vad_noise_floor", self.detector.noise_floor)
        self.event(
            "speech_start",
            utterance_id=self.utterance.id,
            rms=round(self.detector.last_rms, 5),
            noise_floor=round(self.detector.noise_floor, 5),
            onset_ms=self.detector.onset_delay_ms(),
        )
        if self.states.state in (VoiceState.IDLE, VoiceState.TRANSCRIBING):
            self.states.transition(VoiceState.LISTENING, reason="speech_onset")

    def _end_utterance(self, pcm: bytes, now: float) -> None:
        if self.utterance is None:  # defensive: a segment always follows an onset
            self._begin_utterance(now)
        utterance = self.utterance
        assert utterance is not None
        utterance.status = "ended"
        utterance.ended_at = now
        block_bytes = self.settings.block_frames() * 2
        pcm = self._trim_trailing_silence(pcm, block_bytes)
        frames = len(pcm) // block_bytes
        job = AsrJob(
            utterance_id=utterance.id,
            pcm=pcm,
            final=True,
            submitted_at=now,
            total_frames=frames,
            block_ms=self._block_ms,
        )
        self.scheduler.submit_final(job)
        self.metrics.increment("asr_final_job_count")
        self.metrics.set("asr_queue_depth", self.scheduler.depth)
        self.endpoint_at = now
        self.metrics.observe("vad_end_ms", self.detector.endpoint_delay_ms())
        boundary = self.detector.last_boundary
        self.event(
            "speech_end",
            utterance_id=utterance.id,
            audio_ms=frames * self._block_ms,
            boundary=boundary.kind if boundary else "endpoint",
            rms=round(self.detector.last_rms, 5),
            noise_floor=round(self.detector.noise_floor, 5),
            endpoint_ms=self.detector.endpoint_delay_ms(),
        )
        self.utterance = None
        if self.states.state == VoiceState.LISTENING:
            self.states.transition(VoiceState.TRANSCRIBING, reason="final_asr_queued")

    def _trim_trailing_silence(self, pcm: bytes, block_bytes: int) -> bytes:
        """Drop the endpoint silence that closed the utterance (keep 100 ms).

        The detector needs ~600 ms of silence to decide the user finished; that
        silence is not speech and decoding it wastes CPU that the LLM and TTS
        need on a two-core laptop.
        """
        quiet = int(getattr(self.detector, "last_trailing_quiet", 0))
        trim = quiet - TRAILING_SILENCE_KEEP_FRAMES
        if trim <= 0:
            return pcm
        total = len(pcm) // block_bytes
        voiced = int(getattr(self.detector, "last_voiced_frames", 0)) or total
        keep = max(voiced, 1)
        trim = min(trim, max(0, total - keep))
        if trim <= 0:
            return pcm
        return pcm[: (total - trim) * block_bytes]

    def _maybe_offer_partial(self, now: float) -> None:
        utterance = self.utterance
        if utterance is None:
            return
        frames = self.detector.frames
        total = len(frames)
        if total - self._last_partial_frames < self._partial_interval_frames:
            return
        if total < 1:
            return
        self._last_partial_frames = total
        start = max(0, total - self._partial_window_frames)
        pcm = b"".join(frames[start:])
        if not pcm:
            return
        job = AsrJob(
            utterance_id=utterance.id,
            pcm=pcm,
            final=False,
            submitted_at=now,
            total_frames=total,
            window_start_frame=start,
            block_ms=self._block_ms,
        )
        replaced = not self.scheduler.submit_partial(job)
        self.metrics.increment("asr_partial_jobs_submitted")
        if replaced:
            self.metrics.set(
                "asr_dropped_partial_count", self.scheduler.dropped_partial
            )
        self.metrics.set("asr_queue_depth", self.scheduler.depth)

    def _maybe_barge_in(self) -> None:
        if self.states.state not in (VoiceState.THINKING, VoiceState.SPEAKING):
            return
        utterance = self.utterance
        if utterance is None or not self.detector.started:
            return
        if self._barged_in_utterance == utterance.id:
            return
        if self.detector.voiced < self._barge_in_frames:
            return
        self.barge_in(reason="user_speech")

    # ------------------------------------------------------------- cancellation
    def _assistant_active(self) -> bool:
        return (
            (self.reply is not None and not self.reply.done())
            or not self.speech.empty()
            or self.pending_turn is not None
            or self.states.state in (VoiceState.THINKING, VoiceState.SPEAKING)
        )

    def interrupt(self) -> bool:
        """Public barge-in entry point (keyboard/API interrupt)."""
        return self.barge_in(reason="manual")

    def barge_in(self, *, reason: str = "speech", force: bool = False) -> bool:
        """Stop the assistant, never the ASR queue."""
        active = self._assistant_active()
        if not active and not force:
            return False
        started = self.clock()
        self.cancel.set()  # stops Piper chunks and duplex playback immediately
        turn = self.pending_turn
        if turn is not None:
            turn.token.set()
        if self.reply is not None and not self.reply.done():
            self.reply.cancel()
        self.reply = None
        self.generation += 1
        dropped = self._drain_speech()
        if turn is not None:
            self._commit_turn(turn, STATUS_INTERRUPTED, reason=reason)
        self.pending_turn = None
        self.cancel = threading.Event()
        # Restore normal VAD sensitivity after playback stopped.
        self._set_vad_margin(1.0)
        if self.utterance is not None:
            self._barged_in_utterance = self.utterance.id
        if active:
            latency = (self.clock() - started) * 1000
            self.metrics.increment("barge_in_count")
            self.metrics.observe("barge_in_latency_ms", latency)
            self.event(
                "interrupted",
                reason=reason,
                dropped_sentences=dropped,
                teardown_ms=round(latency, 2),
            )
        target = VoiceState.LISTENING if self.utterance is not None else VoiceState.IDLE
        self.states.transition(target, reason=f"barge_in:{reason}", force=True)
        return True

    def _drain_speech(self) -> int:
        dropped = 0
        while not self.speech.empty():
            try:
                self.speech.get_nowait()
                dropped += 1
            except asyncio.QueueEmpty:  # pragma: no cover - defensive
                break
        self.metrics.set("speech_queue_depth", self.speech.qsize())
        return dropped

    def _set_vad_margin(self, margin: float) -> None:
        """Raise/lower the VAD threshold while the assistant is speaking.

        Residual echo after AEC can still trip an energy detector. Requiring a
        louder-onset while playback is active is a *mitigation*, not a proof of
        echo-free operation; real-room validation is still required.
        """
        try:
            self.detector.threshold = self._base_threshold * max(1.0, margin)
        except AttributeError:  # custom detector in tests
            pass

    # ------------------------------------------------------------------ ASR actor
    async def recognize(self) -> None:
        """Single owner of Whisper: finals first, partials replaceable."""
        while True:
            job = await self.scheduler.next()
            self.metrics.set("asr_queue_depth", self.scheduler.depth)
            started = self.clock()
            try:
                text = await asyncio.to_thread(self.audio.transcribe_pcm, job.pcm)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.metrics.increment("asr_errors")
                if job.final:
                    self._on_final_failure(job)
                continue
            elapsed = (self.clock() - started) * 1000
            self.metrics.observe("asr_ms", elapsed)
            self.metrics.observe("asr_rtf", elapsed / job.decoded_ms)
            if job.final:
                await self._on_final(job, text)
            else:
                self._on_partial(job, text, elapsed)

    def _on_partial(self, job: AsrJob, text: str, elapsed: float) -> None:
        self.metrics.observe(
            "asr_partial_latency_ms", (self.clock() - job.submitted_at) * 1000
        )
        utterance = self.utterance
        if utterance is None or utterance.id != job.utterance_id or not self.detector.started:
            self.metrics.increment("asr_partial_late")
            return  # the final decode owns this utterance now
        windowed = job.window_start_frame > 0
        if windowed and job.window_start_frame != utterance.partial_anchor:
            stable = ""  # window moved: a prefix claim would be dishonest
        else:
            stable = agreement(utterance.partial_text, text)
        utterance.partial_text = text
        utterance.partial_anchor = job.window_start_frame
        utterance.partial_windowed = windowed
        self.event(
            "transcript_partial",
            utterance_id=job.utterance_id,
            text=text,
            agreed_prefix=stable,
            windowed=windowed,
            audio_ms=job.audio_ms,
            decoded_ms=job.decoded_ms,
            asr_ms=round(elapsed, 2),
        )

    def _on_final_failure(self, job: AsrJob) -> None:
        if self.utterance is None and self.states.state == VoiceState.TRANSCRIBING:
            self.states.transition(VoiceState.IDLE, reason="asr_failed")
        self.event(
            "error",
            detail="تشخیص گفتار ناموفق بود؛ مدل محلی Whisper را بررسی کنید.",
            utterance_id=job.utterance_id,
        )

    async def _on_final(self, job: AsrJob, text: str) -> None:
        self.metrics.observe(
            "asr_final_latency_ms", (self.clock() - job.submitted_at) * 1000
        )
        if job.utterance_id in self._finalized_ids:
            self.metrics.increment("asr_duplicate_final")
            return
        self._remember_final(job.utterance_id)
        clean = (text or "").strip()
        self.metrics.set("asr_queue_depth", self.scheduler.depth)
        if not clean:
            self.metrics.increment("utterances_empty")
            self.event(
                "transcript_final",
                utterance_id=job.utterance_id,
                text="",
                empty=True,
                audio_ms=job.audio_ms,
            )
            if self.utterance is None and self.states.state == VoiceState.TRANSCRIBING:
                self.states.transition(VoiceState.IDLE, reason="empty_transcript")
            return
        self.event(
            "transcript_final",
            utterance_id=job.utterance_id,
            text=clean,
            audio_ms=job.audio_ms,
            rtf=round(self.metrics.summary("asr_rtf").last, 3),
        )
        message = self.conversation.add_user(clean, utterance_id=job.utterance_id)
        if message is None:  # duplicate utterance id — already committed
            return
        self.history = list(self._history_view())
        self.event(
            "user_message",
            utterance_id=job.utterance_id,
            message_id=message.id,
            text=clean,
        )
        if self._latest_utterance_id > job.utterance_id:
            self.metrics.increment("responses_skipped")
            self.event(
                "response_skipped",
                utterance_id=job.utterance_id,
                reason="newer_utterance_started",
            )
            if self.utterance is None and self.states.state == VoiceState.TRANSCRIBING:
                self.states.transition(VoiceState.IDLE, reason="superseded")
            return
        if self._assistant_active():  # a previous reply must not continue
            self.barge_in(reason="superseded")
        self.start_reply(job.utterance_id, endpoint_at=job.submitted_at)

    def _remember_final(self, utterance_id: int) -> None:
        if len(self._finalized) == self._finalized.maxlen:
            oldest = self._finalized[0]
            self._finalized_ids.discard(oldest)
        self._finalized.append(utterance_id)
        self._finalized_ids.add(utterance_id)

    # ----------------------------------------------------------- assistant actor
    def start_reply(self, utterance_id: int, *, endpoint_at: float | None = None) -> AssistantTurn:
        turn = AssistantTurn(
            turn_id=self.conversation.new_turn_id(),
            utterance_id=utterance_id,
            generation=self.generation,
            endpoint_at=endpoint_at if endpoint_at is not None else self.clock(),
        )
        self.pending_turn = turn
        self.metrics.increment("llm_requests")
        self.states.transition(VoiceState.THINKING, reason="final_transcript", force=True)
        token = self.cancel
        self.reply = asyncio.create_task(self.respond(turn, token))
        return turn

    async def respond(self, turn: AssistantTurn, token: threading.Event) -> None:
        messages = self.conversation.context_with_system(self.settings.system_prompt)
        if not messages:
            return
        payload = {
            "messages": messages,
            "stream": True,
            "temperature": self.settings.temperature,
            "max_tokens": self.settings.max_tokens,
        }
        buffer, pieces = "", []
        completed = False
        try:
            async with self.client.stream(
                "POST", "/v1/chat/completions", json=payload
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if token.is_set() or turn.generation != self.generation or turn.token.is_set():
                        return
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        completed = True
                        break
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if data.get("error"):
                        raise VoiceError("خطای استریم پاسخ")
                    if "timings" in data:
                        timings = data["timings"]
                        self.event("llm_timings", timings=timings)
                        if isinstance(timings, dict) and timings.get("predicted_per_second"):
                            self.metrics.observe(
                                "llm_tokens_per_second", timings["predicted_per_second"]
                            )
                    for choice in data.get("choices", []):
                        piece = choice.get("delta", {}).get("content") or ""
                        if not piece:
                            continue
                        if turn.first_token_at is None:
                            turn.first_token_at = self.clock()
                            ttft = (turn.first_token_at - turn.endpoint_at) * 1000
                            self.metrics.observe("llm_ttft_ms", ttft)
                            self.event(
                                "first_token",
                                utterance_id=turn.utterance_id,
                                turn_id=turn.turn_id,
                                endpoint_ms=round(ttft, 2),
                            )
                        pieces.append(piece)
                        turn.text += piece
                        self.event("response_delta", turn_id=turn.turn_id, text=piece)
                        ready, buffer = sentences(buffer + piece)
                        for sentence in ready:
                            await self._enqueue_speech(turn, sentence, token)
            if not completed and not token.is_set():
                raise VoiceError("استریم پاسخ ناقص بود.")
            if token.is_set() or turn.generation != self.generation:
                return
            for sentence in sentences(buffer, final=True)[0]:
                await self._enqueue_speech(turn, sentence, token)
            # The commit marker is consumed only after queued audio finished.
            await self._put_speech(SpeechItem(turn, "", token))
        except asyncio.CancelledError:
            raise  # barge_in already committed the turn as interrupted
        except Exception:
            self.metrics.increment("llm_failures")
            if turn.generation == self.generation and not turn.committed:
                self._commit_turn(turn, STATUS_FAILED, reason="llm_error")
                self.event(
                    "error",
                    detail="پاسخ استریم ناموفق بود؛ اتصال مدل و llama-server را بررسی کنید.",
                    turn_id=turn.turn_id,
                )
            # A failed reply produces no commit marker in the play queue, so
            # nothing else would leave THINKING: without this the next user
            # utterance was treated as a barge-in and the reported state lied
            # about a turn that no longer existed.
            if self.pending_turn is None and self.speech.empty():
                self._set_vad_margin(1.0)
                target = (
                    VoiceState.LISTENING if self.utterance is not None else VoiceState.IDLE
                )
                self.states.transition(target, reason="llm_error", force=True)

    async def _enqueue_speech(
        self, turn: AssistantTurn, text: str, token: threading.Event
    ) -> None:
        if not text:
            return
        if token.is_set() or turn.generation != self.generation or turn.token.is_set():
            return
        words = text.split()
        self.event("tts_enqueue", turn_id=turn.turn_id, text=text, words=len(words))
        await self._put_speech(SpeechItem(turn, text, token))

    async def _put_speech(self, item: SpeechItem) -> None:
        """Bounded enqueue: backpressure to SSE, abort when the turn is cancelled."""
        while True:
            if item.token.is_set() or item.turn.generation != self.generation:
                return
            try:
                await asyncio.wait_for(self.speech.put(item), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            self.metrics.set("speech_queue_depth", self.speech.qsize())
            return

    # ---------------------------------------------------------------- play actor
    async def play(self) -> None:
        """Single owner of Piper/output; cancelled audio is never played later."""
        while True:
            item = await self.speech.get()
            self.metrics.set("speech_queue_depth", self.speech.qsize())
            turn, text, token = item.turn, item.text, item.token
            stale = (
                token.is_set()
                or turn.token.is_set()
                or turn.generation != self.generation
            )
            if stale:
                continue
            if not text:
                # Commit marker: every queued sentence for this turn was played.
                self._commit_turn(turn, STATUS_COMPLETE, reason="playback_done")
                # The last real sentence could not leave SPEAKING because the
                # commit marker was still queued behind it.  Without this the
                # machine stayed in SPEAKING after the reply ended, so the next
                # utterance was mis-labelled as barge-in and the assistant
                # looked permanently busy.
                # No turn is pending once the marker is committed, and the
                # marker is always enqueued *after* the last sentence, so an
                # empty queue here means the reply is fully played.
                if self.speech.empty() and self.pending_turn is None:
                    self._set_vad_margin(1.0)
                    target = (
                        VoiceState.LISTENING
                        if self.utterance is not None
                        else VoiceState.IDLE
                    )
                    self.states.transition(target, reason="playback_done", force=True)
                continue
            started = self.clock()

            def on_first_chunk(at: float) -> None:
                self.metrics.observe("tts_first_chunk_ms", (at - started) * 1000)

            def on_first_frame(at: float) -> None:
                self.metrics.observe("first_audible_ms", (at - turn.endpoint_at) * 1000)
                self.event(
                    "first_audible",
                    turn_id=turn.turn_id,
                    utterance_id=turn.utterance_id,
                    ms=round((at - turn.endpoint_at) * 1000, 2),
                )

            def on_complete(at: float, played_ms: float) -> None:
                self.metrics.observe("playback_duration_ms", played_ms)
                self.metrics.set("last_playback_ms", played_ms)

            self.states.transition(VoiceState.SPEAKING, reason="tts_start", force=True)
            self._set_vad_margin(self.settings.voice_barge_in_margin)
            turn.current = text  # a barge-in now records the sentence that was heard
            self.event("tts_start", turn_id=turn.turn_id, text=text)
            try:
                await asyncio.to_thread(
                    self.speaker.speak,
                    text,
                    token,
                    on_first_chunk=on_first_chunk,
                    on_first_frame=on_first_frame,
                    on_complete=on_complete,
                )
                if not token.is_set() and not turn.token.is_set():
                    turn.spoken.append(text)
                turn.current = None
            except Exception:
                self.metrics.increment("tts_failures")
                token.set()
                if turn.generation == self.generation and not turn.committed:
                    self._commit_turn(turn, STATUS_FAILED, reason="tts_error")
                    self.event(
                        "error",
                        detail="تولید یا پخش صدا ناموفق بود؛ مدل Piper و دستگاه خروجی را بررسی کنید.",
                        turn_id=turn.turn_id,
                    )
                self._set_vad_margin(1.0)
                continue
            if token.is_set() or turn.token.is_set():
                self.metrics.observe(
                    "barge_in_playback_ms", (self.clock() - started) * 1000
                )
                self._set_vad_margin(1.0)
                target = (
                    VoiceState.LISTENING if self.utterance is not None else VoiceState.IDLE
                )
                self.states.transition(target, reason="playback_stopped", force=True)
                continue
            self.event("tts_end", turn_id=turn.turn_id, text=text)
            if self.speech.empty() and (self.reply is None or self.reply.done()):
                self._set_vad_margin(1.0)
                target = (
                    VoiceState.LISTENING if self.utterance is not None else VoiceState.IDLE
                )
                self.states.transition(target, reason="playback_done", force=True)

    def _commit_turn(self, turn: AssistantTurn, status: str, *, reason: str = "") -> None:
        """Record an assistant reply exactly once, with an explicit lifecycle status."""
        if turn.committed:
            return
        turn.committed = True
        turn.status = status
        if self.pending_turn is turn:
            self.pending_turn = None
        text = turn.text.strip()
        if status == STATUS_INTERRUPTED:
            # Record what the user actually heard, not what the model produced.
            # Three distinct cases:
            #   * at least one sentence finished -> those sentences;
            #   * the first sentence was started but not finished -> that
            #     sentence (the user heard part of it);
            #   * nothing was audible yet (interrupted while thinking) -> keep
            #     the generated text for inspection only; it stays out of the
            #     model context unless PVAI_HISTORY_INCLUDE_INTERRUPTED=true.
            if turn.spoken:
                text = " ".join(turn.spoken).strip()
            elif turn.current:
                text = turn.current.strip()
        message = None
        if text:
            message = self.conversation.finish_assistant(
                text,
                status=status,
                turn_id=turn.turn_id,
                utterance_id=turn.utterance_id,
            )
        self.history = list(self._history_view())
        if status == STATUS_COMPLETE:
            self.metrics.set("last_response_chars", len(text))
        self.event(
            "response_end",
            turn_id=turn.turn_id,
            utterance_id=turn.utterance_id,
            status=status,
            reason=reason,
            text=text,
            message_id=message.id if message else None,
            chars=len(text),
        )

    def _history_view(self) -> list[dict[str, str]]:
        return [
            {"role": message.role, "content": message.content}
            for message in self.conversation.eligible()
        ]

    # ------------------------------------------------------------------- control
    def overflow(self, detail: str = "ورودی صوت عقب افتاد؛ نوبت جاری حذف شد.") -> None:
        """Input backlog: drop the unfinished utterance, keep every final job."""
        self.metrics.increment("audio_overflow_count")
        self.barge_in(reason="input_overflow", force=True)
        self.detector.reset()
        self.utterance = None
        self._last_partial_frames = 0
        self.scheduler.drop_partial()
        self.event("error", detail=detail)

    def metrics_snapshot(self) -> dict[str, Any]:
        return self.metrics.snapshot()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        turn = self.pending_turn
        if turn is not None and not turn.committed:
            self._commit_turn(turn, STATUS_INTERRUPTED, reason="shutdown")
        self.cancel.set()
        tasks = list(self.actors)
        if self.reply is not None:
            tasks.append(self.reply)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.actors = []
        self.reply = None


# ------------------------------------------------------------------- live runner
def create_pipeline(
    settings: Settings,
    audio: Any,
    speaker: Any,
    client: Any,
    emit: Callable[[dict[str, Any]], None],
    *,
    metrics: Metrics | None = None,
) -> Pipeline:
    """Build a pipeline with the configured persisted conversation."""
    conversation = create_conversation(settings)
    return Pipeline(
        settings,
        audio,
        speaker,
        client,
        emit,
        conversation=conversation,
        metrics=metrics,
    )


async def run_live(settings: Settings) -> None:
    """Start the offline PC voice loop (microphone → conversation → speaker)."""
    from . import audio_io
    from .aec import DuplexAudio, SpeexAEC

    problems = settings.config_errors()
    if problems:
        raise VoiceError(" ".join(problems))

    metrics = Metrics(enabled=settings.metrics_enabled)
    audio = LocalAudio(settings, metrics=metrics)
    audio.validate()  # missing/mismatched models first: the most actionable failure
    duplex = DuplexAudio()
    aec = (
        SpeexAEC(
            settings.aec_library,
            filter_ms=settings.aec_filter_ms,
            delay_ms=settings.aec_delay_ms,
        )
        if settings.aec_enabled
        else None
    )
    try:
        try:
            speaker = Speaker(settings, duplex=duplex)
        except VoiceError:
            raise
        except Exception as exc:
            raise VoiceError("راه‌اندازی Piper ناموفق بود؛ مدل صوتی را بررسی کنید.") from exc
        sd = dependency("sounddevice")
        warmup = getattr(audio, "warmup", None)
        if settings.asr_warmup and callable(warmup):
            warmup()
        async with httpx.AsyncClient(
            base_url=local_api_url(settings),
            timeout=settings.request_timeout_s,
            trust_env=False,
            follow_redirects=False,
        ) as client:
            pipeline = create_pipeline(
                settings,
                audio,
                speaker,
                client,
                lambda event: print(json.dumps(event, ensure_ascii=False), flush=True),
                metrics=metrics,
            )
            pipeline.start()

            def opened(plan: audio_io.StreamPlan) -> None:
                print(
                    "دستگاه صوتی آماده است: "
                    f"ورودی «{plan.input_name}» / خروجی «{plan.output_name}» "
                    f"@ {plan.samplerate}Hz، بلوک {plan.blocksize} نمونه",
                    flush=True,
                )
                print(
                    "شنیدن زنده فعال است؛ AEC "
                    + ("فعال" if aec else "خاموش؛ از هدفون استفاده کنید")
                    + f"، صدا: {settings.tts_voice}. خروج: Ctrl+C",
                    flush=True,
                )

            try:
                # Same clock/stream for render reference and microphone capture.
                await audio_io.stream_forever(
                    sd,
                    duplex,
                    settings,
                    pipeline,
                    aec=aec,
                    metrics=metrics,
                    on_open=opened,
                )
            finally:
                await asyncio.gather(pipeline.close(), return_exceptions=True)
    finally:
        duplex.close()
        if aec:
            aec.close()
