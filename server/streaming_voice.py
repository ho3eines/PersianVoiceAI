"""Event-driven PC conversation engine.

Design contract (see ``docs/STREAMING_ARCHITECTURE.md``):

* **UserUtterance** — one detected speech segment. Identifiers increase
  monotonically and are *never* reused. An utterance owns its audio and its
  transcript; nothing about the assistant can invalidate it. An utterance the
  VAD throws away (shorter than ``PVAI_VOICE_VAD_MIN_SPEECH_MS``) is reported
  explicitly (``utterance_rejected``) and never counts as "newer speech", so it
  cannot make an earlier, still unanswered sentence look stale.
* **PartialASR** — replaceable, display-only. A partial result may be dropped
  at any time (bounded tail window, latest-only mailbox) without affecting the
  conversation.
* **FinalASR** — lossless FIFO. Every final job is transcribed, and its result
  is committed to the conversation exactly once, even if the user has already
  started the next sentence, the assistant was interrupted, or the audio input
  overflowed. Only shutdown discards a queued final, and it says so. A committed
  user message gets an answer unless a newer one already has one: an empty or
  failed transcript of the *next* sentence never deletes the answer the user is
  still waiting for.
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
import inspect
import json
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

import httpx

from .broker import CommandBroker
from .config import Settings
from .conversation import (
    ROLE_ASSISTANT,
    STATUS_COMPLETE,
    STATUS_FAILED,
    STATUS_INTERRUPTED,
    Conversation,
    create_conversation,
    purge_expired,
)
from .live_voice import Speaker, sentences
from .persian import prepare_for_tts
from .metrics import Metrics
from .vad import ONSET, REJECTED, SEGMENT, build_vad
from .voice import LocalAudio, VoiceError, dependency, local_api_url, local_client_headers, assert_local_api_authorized

BLOCK_SECONDS = 0.02
TRAILING_SILENCE_KEEP_FRAMES = 5
"""Trailing silence kept in the final ASR audio (100 ms at 20 ms blocks)."""

logger = logging.getLogger(__name__)
"""Module logger.

``_dispatch_command``'s except-branch called ``logger.exception`` long before
this name existed, so a failing intent source raised ``NameError`` inside the
handler and the real error vanished.  Nothing caught it either: the call sits in
a plain function, not a task, so it surfaced as an unhandled exception in
``_on_final``.
"""


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
    started_at: float | None = None
    """Clock reading when the single ASR actor began decoding this job.

    Filled in by :meth:`AsrScheduler.begin`.  ``None`` means the job waited in
    a queue; the gap between ``submitted_at`` and ``started_at`` is the latency
    the final job never had a chance to avoid (backlog, or a partial decode
    that was still running).
    """

    @property
    def audio_ms(self) -> float:
        return self.total_frames * self.block_ms

    @property
    def decoded_ms(self) -> float:
        return max(1, self.total_frames - self.window_start_frame) * self.block_ms

    def queued(self, at: float) -> "AsrJob":
        """This job, marked as started at ``at`` (frozen dataclass: a copy)."""
        return AsrJob(
            utterance_id=self.utterance_id,
            pcm=self.pcm,
            final=self.final,
            submitted_at=self.submitted_at,
            total_frames=self.total_frames,
            window_start_frame=self.window_start_frame,
            block_ms=self.block_ms,
            started_at=at,
        )


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

    One decode at a time is a hard constraint (Whisper on two cores), so a
    partial that is *already decoding* when the final arrives is the one place
    where the lossless queue still costs latency.  ``next()`` therefore
    announces what it is about to decode through ``_in_flight``; the pipeline
    calls :meth:`preempt` as soon as a final is queued and stops that partial
    instead of waiting for it to finish (up to a 6 s window = seconds spent on
    a transcript nobody will ever see).
    """

    def __init__(self, *, max_finals: int = 16) -> None:
        self.finals: deque[AsrJob] = deque()
        self.partial: AsrJob | None = None
        self.max_finals = max(1, int(max_finals))
        self.dropped_partial = 0
        self.received_final = 0
        self.started_final = 0
        self.preempted_partial = 0
        self.backlog_warnings = 0
        self._in_flight: AsrJob | None = None
        self._preempted: AsrJob | None = None
        self._preempt = threading.Event()
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
        # Wake the actor so it picks the final up as soon as it is free.  The
        # cancellation token is *not* touched here: it means "abandon the job in
        # flight" and only ``preempt()`` may say that, otherwise a backend that
        # polls the token would stop partials even with preemption switched off.
        self._wakeup.set()

    def submit_partial(self, job: AsrJob) -> bool:
        """Store a partial; return ``False`` when a previous one was replaced."""
        assert not job.final
        if job.utterance_id <= self._latest_final_id():
            # A final for this (or a newer) utterance is already waiting, so
            # this partial describes audio the final decode already owns.
            self.dropped_partial += 1
            return False
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

    # --------------------------------------------------------------- preemption
    def preempt(self) -> bool:
        """Request cancellation of the partial decode currently in flight."""
        if self._in_flight is None or self._in_flight.final:
            return False
        self._preempted = self._in_flight
        self._preempt.set()
        return True

    @property
    def in_flight(self) -> AsrJob | None:
        return self._in_flight

    def begin(self, job: AsrJob) -> threading.Event | None:
        """Take ownership of a job before decoding it.

        Returns the cancellation token a partial decode must pass to its worker
        (``None`` for finals, which are never preempted).
        """
        self._in_flight = job
        self._preempted = None
        if job.final:
            return None
        self._preempt.clear()
        return self._preempt

    def end(self, job: AsrJob) -> bool:
        """Release a job after decoding; ``True`` when it was preempted.

        A partial counts as preempted only when *this* job is the one a final
        asked to stop.  A flag that is merely set (``submit_final`` also wakes
        the actor) must never discard a partial that finished normally.
        """
        preempted = self._preempted is job
        self._in_flight = None
        self._preempted = None
        self._preempt.clear()
        if preempted:
            self.preempted_partial += 1
            self.dropped_partial += 1
        return preempted

    def _latest_final_id(self) -> int:
        return self.finals[-1].utterance_id if self.finals else 0

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
        broker: CommandBroker | None = None,
        intent: Callable[[str], dict[str, Any] | None] | None = None,
    ) -> None:
        self.settings = settings
        self.audio = audio
        self.speaker = speaker
        self.client = client
        self.emit = emit
        self.clock = clock
        # Device commands are opt-in: without both a broker and an intent source
        # a final transcript never reaches any device (AGENTS.md §2.3).
        self.broker = broker
        self.intent = intent
        self._command_tasks: set[asyncio.Task[None]] = set()
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
        # Highest utterance id that produced a *valid* (non-empty) final.  It is
        # raised only by ``_on_final``: an utterance the VAD rejected, or one
        # whose transcript came back empty, must never make an earlier answer
        # look superseded.  Kept as the reported "newest transcript" marker;
        # the decision itself is made from the conversation (see ``_has_answer``)
        # because an answer may have been committed by a barge-in or shutdown.
        # ``next_utterance_id`` is the conversation's, not ``1``: after a resume
        # the stored turns already own ids 1..N, and a fresh counter would hand
        # the first new utterance a duplicate that dedupe silently drops.
        self._latest_utterance_id = self.conversation.next_utterance_id() - 1
        self._next_utterance_id = self.conversation.next_utterance_id()
        self._last_partial_frames = 0
        self._barged_in_utterance = 0
        # Only utterances with a persisted user message can own an answer.
        # Empty/failed ASR ids never enter this bounded queue.
        self._pending_answers: deque[int] = deque(maxlen=16)
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
        # Wall-clock start of the partial decode in flight and the instant the
        # actor became free again: together they attribute the final's wait.
        self._partial_started_at: float | None = None
        self._last_partial_end: float | None = None
        self.preempt_partials = True
        """Cancel an in-flight partial when a final is queued (bug 4).

        Partials are display-only, so stopping one costs nothing but a stale
        on-screen hint; leaving it running costs the final transcript (and the
        reply) up to one partial window of extra latency.  Kept switchable so
        the delay it removes can be measured instead of assumed.
        """

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
        boundary, segment = self.detector.feed_event(pcm, at=now)
        if boundary == ONSET:
            self._begin_utterance(now)
        if boundary == SEGMENT and segment is not None:
            self._end_utterance(segment, now)
        elif boundary == REJECTED:
            self._reject_utterance(now)
        elif self.detector.started:
            self._maybe_offer_partial(now)
        self._maybe_barge_in()

    def _begin_utterance(self, now: float) -> None:
        self.utterance = Utterance(id=self._next_utterance_id, started_at=now)
        self._next_utterance_id += 1
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

    def _reject_utterance(self, now: float) -> None:
        """Close an utterance the detector threw away (cough, click, door slam).

        The damage of ignoring this is not the lost cough, it is everything
        downstream of it: the utterance had already been announced and had
        raised ``self.utterance``, so leaving it open made the *next* valid
        final look stale (``response_skipped``, no reply at all) and kept the
        state machine in ``LISTENING`` with an utterance that could never end.
        """
        utterance = self.utterance
        if utterance is not None:
            utterance.status = "rejected"
            utterance.ended_at = now
        self.utterance = None
        self._last_partial_frames = 0
        self.scheduler.drop_partial()
        self.metrics.increment("utterances_rejected")
        self.metrics.set(
            "vad_rejected_utterances", int(getattr(self.detector, "rejected_utterances", 0))
        )
        self.event(
            "utterance_rejected",
            utterance_id=utterance.id if utterance is not None else 0,
            reason="too_short",
            voiced_frames=int(getattr(self.detector, "last_voiced_frames", 0)),
            min_voiced_frames=int(getattr(self.detector, "min_voiced_frames", 0)),
            audio_ms=int(getattr(self.detector, "last_voiced_frames", 0)) * self._block_ms,
        )
        self._settle_after_utterance(reason="utterance_rejected")

    def _settle_after_utterance(self, *, reason: str) -> None:
        """Return to a truthful state once no utterance is open.

        ``TRANSCRIBING`` if a final transcript is still owed, otherwise
        ``IDLE`` — the assistant is not busy and nothing is being recorded.
        Speaking/thinking states are left alone: a reply owns them.
        """
        if self.utterance is not None:
            return
        state = self.states.state
        if state not in (
            VoiceState.LISTENING,
            VoiceState.TRANSCRIBING,
            VoiceState.IDLE,
        ):
            return
        target = (
            VoiceState.TRANSCRIBING
            if self.scheduler.pending_finals
            else VoiceState.IDLE
        )
        self.states.transition(target, reason=reason)

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
        # A final was queued while a partial was decoding: stop that partial
        # instead of making the transcript (and the reply) wait for a decode
        # window nobody will ever see.
        if self.preempt_partials and self.scheduler.preempt():
            in_flight = self.scheduler.in_flight
            started = getattr(in_flight, "started_at", None)
            # Work already spent on a partial nobody will see: the measured
            # cost of one Whisper owner on a two-core machine.
            wasted_ms = max(0.0, (now - started) * 1000) if started is not None else 0.0
            self.metrics.observe("asr_partial_wasted_ms", wasted_ms)
            self.metrics.increment("asr_partial_preempted")
            self.event(
                "asr_partial_preempted",
                utterance_id=in_flight.utterance_id if in_flight is not None else 0,
                final_utterance_id=utterance.id,
                in_flight_ms=round(wasted_ms, 2),
            )

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
                turn_id=turn.turn_id if turn is not None else 0,
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
        """Single owner of Whisper: finals first, partials replaceable.

        One decode at a time is deliberate (two cores).  The cost of that is
        paid by whatever is *already decoding* when a final arrives, so every
        job records when the actor actually picked it up: the final latency
        reported as ``asr_final_latency_ms`` is split into "waited behind a
        partial" (``asr_final_wait_behind_partial_ms``) and "the final decode
        itself" (``asr_final_decode_ms``).  Without that split the delay is a
        number that cannot be acted on.
        """
        while True:
            dequeued = None
            job = None
            token = None
            try:
                dequeued = await self.scheduler.next()
                started_at = self.clock()
                job = dequeued.queued(started_at)
                dequeued = None
                token = self.scheduler.begin(job)
                self.metrics.set("asr_queue_depth", self.scheduler.depth)
                if job.final:
                    self._observe_final_wait(job, started_at)
                else:
                    self._partial_started_at = started_at
                try:
                    text = await self._transcribe(job, token)
                except asyncio.CancelledError:
                    self.scheduler.end(job)
                    raise
                except Exception as exc:  # noqa: BLE001 - a decode failure is a metric
                    if not job.final:
                        self._last_partial_end = self.clock()
                    preempted = self.scheduler.end(job)
                    if preempted:
                        # Expected: the worker stopped a partial because a final
                        # was waiting.  Not an ASR error, and definitely not a
                        # reason to report a broken Whisper model.
                        self.metrics.increment("asr_partial_cancelled")
                        self.event(
                            "asr_partial_cancelled",
                            utterance_id=job.utterance_id,
                            reason="final_waiting",
                            exception=type(exc).__name__,
                        )
                        continue
                    self.metrics.increment("asr_errors")
                    if job.final:
                        self._on_final_failure(job, exc)
                    continue
                elapsed = (self.clock() - started_at) * 1000
                if not job.final:
                    self._last_partial_end = self.clock()
                if self.scheduler.end(job):
                    self.metrics.increment("asr_partial_cancelled")
                    self.event(
                        "asr_partial_cancelled",
                        utterance_id=job.utterance_id,
                        reason="final_waiting",
                        asr_ms=round(elapsed, 2),
                    )
                    continue
                self.metrics.observe("asr_ms", elapsed)
                self.metrics.observe("asr_rtf", elapsed / job.decoded_ms)
                if job.final:
                    self.metrics.observe("asr_final_decode_ms", elapsed)
                    await self._on_final(job, text)
                else:
                    self._on_partial(job, text, elapsed)
            finally:
                # Release references (including raw PCM bytes) before suspending idle.
                dequeued = None
                job = None
                token = None

    @staticmethod
    def _accepts_cancellation(func: Any) -> bool:
        """Whether the ASR backend accepts ``(pcm, token, job)``."""
        try:
            parameters = inspect.signature(func).parameters
        except (TypeError, ValueError):  # builtins / C callables
            return False
        positional = [
            parameter
            for parameter in parameters.values()
            if parameter.kind
            in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
        ]
        return len(positional) >= 3 or any(
            parameter.kind is parameter.VAR_POSITIONAL
            for parameter in parameters.values()
        )

    async def _transcribe(self, job: AsrJob, token: threading.Event | None) -> str:
        """Decode one job off the event loop, optionally cancellable.

        A backend that understands the optional ``token`` stops generating as
        soon as it is set, so a partial that a final has to wait for does not
        keep a core busy.  Backends with the historical ``transcribe_pcm(pcm)``
        signature keep working; the token then only stops *dispatch* of the
        result (``AsrScheduler.end``), which is still the correct behaviour for
        the conversation — a partial is display-only.
        """
        func = self.audio.transcribe_pcm
        if token is None or not self._accepts_cancellation(func):
            return await asyncio.to_thread(func, job.pcm)
        return await asyncio.to_thread(func, job.pcm, token, job)

    def _observe_final_wait(self, job: AsrJob, started_at: float) -> None:
        """Attribute the final's queue wait: partial decode vs. final backlog.

        The actor is single, so the interesting number is not "how long did the
        final sit in the queue" but "how much of that time was a partial that
        nobody needed".  ``_last_partial_end`` is the instant the actor became
        free; if the final was submitted before that, the difference is exactly
        the time it spent behind a partial.  With preemption on it is a few
        milliseconds; with a backend that cannot be cancelled it is the whole
        leftover decode, which is the number that justifies (or refutes)
        preemption on real hardware.
        """
        waited = max(0.0, (started_at - job.submitted_at) * 1000)
        self.metrics.observe("asr_final_dispatch_wait_ms", waited)
        behind = 0.0
        last_end = self._last_partial_end
        if last_end is not None and job.submitted_at < last_end:
            behind = max(0.0, (last_end - job.submitted_at) * 1000)
        self.metrics.observe("asr_final_wait_behind_partial_ms", behind)
        if behind <= 0:
            return
        self.metrics.increment("asr_final_waited_behind_partial")
        self.event(
            "asr_final_waited_behind_partial",
            utterance_id=job.utterance_id,
            wait_ms=round(behind, 2),
            dispatch_wait_ms=round(waited, 2),
            partial_preempted=bool(self.scheduler.preempted_partial),
        )

    def _on_partial(self, job: AsrJob, text: str, elapsed: float) -> None:
        self.metrics.observe(
            "asr_partial_latency_ms", (self.clock() - job.submitted_at) * 1000
        )
        partial_ms = self._partial_elapsed_ms(elapsed)
        self.metrics.observe("asr_partial_decode_ms", partial_ms)
        # True when the final of this (or a newer) utterance was already queued
        # while this partial decoded: the case that costs the answer latency and
        # that ``asr_final_wait_behind_partial_ms`` attributes to the partial.
        final_waiting = bool(self.scheduler.pending_finals)
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
            partial_decode_ms=round(partial_ms, 2),
            final_was_waiting=final_waiting,
        )

    def _partial_elapsed_ms(self, elapsed: float) -> float:
        """Wall-clock duration of the partial decode that just finished."""
        started = self._partial_started_at
        self._partial_started_at = None
        if started is None:
            return elapsed
        return max(0.0, (self.clock() - started) * 1000)

    def _on_final_failure(self, job: AsrJob, error: BaseException | None = None) -> None:
        self._remember_final(job.utterance_id)
        self.event(
            "error",
            detail="تشخیص گفتار ناموفق بود؛ مدل محلی Whisper را بررسی کنید.",
            utterance_id=job.utterance_id,
        )
        self._settle_after_utterance(reason="asr_failed")

    def _dispatch_command(self, utterance_id: int, text: str) -> None:
        """Run the intent source and hand its command to the broker in the background.

        The reply path never waits for the device: a slow or silent device must
        not delay speech, and a broker refusal is reported as an event only.
        """
        if self.broker is None or self.intent is None:
            return
        try:
            raw = self.intent(text)
        except Exception:
            logger.exception("intent extraction failed")
            return
        if raw is None:
            return
        task = asyncio.create_task(self._run_command(utterance_id, raw))
        self._command_tasks.add(task)
        task.add_done_callback(self._command_tasks.discard)

    async def _run_command(self, utterance_id: int, raw: dict[str, Any]) -> None:
        assert self.broker is not None
        result = await self.broker.submit(raw)
        self.event(
            "device_command",
            utterance_id=utterance_id,
            status=result.status,
            message=result.message,
            code=result.detail.get("code"),
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
            # Empty ASR has no committed user message; never attach the next
            # utterance's assistant reply to this id.
            self._settle_after_utterance(reason="empty_transcript")
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
        self._latest_utterance_id = max(self._latest_utterance_id, job.utterance_id)
        self._pending_answers.append(job.utterance_id)
        self.history = list(self._history_view())
        self.event(
            "user_message",
            utterance_id=job.utterance_id,
            message_id=message.id,
            text=clean,
        )
        self._dispatch_command(job.utterance_id, clean)
        newer_answered = self._newest_answered()
        if newer_answered > job.utterance_id:
            # A *newer* user turn already got its reply, so this one is
            # deliberately skipped: that is the documented barge-in behaviour,
            # and answering it now would talk over the answer the user just got.
            self.metrics.increment("responses_skipped")
            self.event(
                "response_skipped",
                utterance_id=job.utterance_id,
                reason="newer_final_answered",
                answered_utterance_id=newer_answered,
            )
            self._settle_after_utterance(reason="superseded")
            return
        if not self.start_next_pending(job.utterance_id, endpoint_at=job.submitted_at):
            self._settle_after_utterance(reason="nothing_to_answer")

    def _newest_answered(self) -> int:
        """Highest utterance id that already has an assistant message."""
        newest = 0
        for message in self.conversation.messages:
            if (
                message.role == ROLE_ASSISTANT
                and message.utterance_id is not None
                and message.utterance_id > newest
            ):
                newest = message.utterance_id
        return newest

    def _has_answer(self, utterance_id: int) -> bool:
        """Whether this exact user turn was already answered.

        The conversation is the source of truth, not a counter in the pipeline:
        an answer may have been committed by a barge-in, by shutdown, or by the
        play actor, and answering the same turn twice would be worse than
        skipping one.
        """
        return any(
            message.utterance_id == utterance_id
            for message in self.conversation.messages
            if message.role == ROLE_ASSISTANT
        )

    def start_next_pending(
        self, utterance_id: int, *, endpoint_at: float | None = None
    ) -> AssistantTurn | None:
        """Answer the newest unanswered user message instead of dropping it.

        Every committed transcript needs a decision, and "the user did start a
        new sentence, but that sentence produced no text (or a failed decode)
        and therefore no reply" is not a reason to delete the answer the user
        is still waiting for.  That is the rule this implements: a user message
        gets an answer unless it already has one.
        """
        newest = utterance_id
        behind = 0
        while self._pending_answers:
            candidate = self._pending_answers[-1]
            self._pending_answers.pop()
            if candidate <= newest or self._has_answer(candidate):
                continue
            newest = candidate
            behind = len(self._pending_answers) + 1
            break
        if self._has_answer(newest):
            return None
        if behind:
            self.metrics.increment("responses_recovered")
            self.event(
                "response_recovered",
                utterance_id=newest,
                trigger_utterance_id=utterance_id,
                skipped_turns=behind,
            )
        if self._assistant_active():  # a previous reply must not continue
            self.barge_in(reason="superseded")
        return self.start_reply(newest, endpoint_at=endpoint_at)

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
        try:
            messages = self.conversation.context_with_system(self.settings.system_prompt)
        except Exception as exc:  # noqa: BLE001 - history failures must not wedge a turn
            self._fail_turn(
                turn,
                detail="ساخت تاریخچهٔ مکالمه ناموفق بود.",
                error=exc,
                reason="history_error",
            )
            return
        if not messages:
            # Nothing to send.  Returning here used to leave ``pending_turn``
            # set and the state machine in THINKING for good: the next user
            # utterance was then mis-read as a barge-in, and no ``response_end``
            # was ever emitted for a turn the caller had already announced.
            self._fail_turn(
                turn,
                detail="تاریخچهٔ مکالمه خالی است؛ پاسخی ارسال نشد.",
                reason="empty_context",
            )
            return
        payload = {
            "messages": messages,
            "stream": True,
            "use_rag": True,
            "temperature": self.settings.temperature,
            "max_tokens": self.settings.max_tokens,
        }
        buffer = ""
        completed = False
        try:
            async with self.client.stream(
                "POST", "/v1/chat/completions", json=payload
            ) as response:
                assert_local_api_authorized(response)
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
        except Exception as exc:  # noqa: BLE001 - reported as a failed turn
            self._fail_turn(
                turn,
                detail=(str(exc) if isinstance(exc, VoiceError)
                        and "PVAI_API_TOKEN برای کلاینت داخلی تنظیم نشده" in str(exc)
                        else "پاسخ استریم ناموفق بود؛ اتصال مدل و llama-server را بررسی کنید."),
                error=exc,
                reason="llm_error",
            )

    def _fail_turn(
        self,
        turn: AssistantTurn,
        *,
        detail: str,
        reason: str = "llm_error",
        error: BaseException | None = None,
    ) -> None:
        """Commit a failed turn *and* release the state it was holding.

        A failed reply never enqueues a commit marker, so nothing downstream
        would ever leave THINKING/SPEAKING: the next user utterance was then
        treated as a barge-in for a turn that no longer existed, and the
        reported state lied.  Both failure paths (LLM and TTS) go through here
        so the release cannot be forgotten in one of them.
        """
        self.metrics.increment("tts_failures" if reason == "tts_error" else "llm_failures")
        if turn.generation != self.generation or turn.committed:
            return
        self._commit_turn(turn, STATUS_FAILED, reason=reason)
        self.event(
            "error",
            detail=detail,
            turn_id=turn.turn_id,
            utterance_id=turn.utterance_id,
            exception=type(error).__name__ if error is not None else None,
        )
        # Drop whatever this turn still had queued for playback.
        turn.token.set()
        if self.cancel.is_set():
            # ``self.cancel`` is the *pipeline-wide* gate handed to every reply;
            # a set gate makes the next reply abort on its first line.  barge_in
            # replaces it, and a failed turn must do the same, otherwise one TTS
            # error silently muted the assistant for the rest of the session.
            self.cancel = threading.Event()
        self._release_after_failure(reason)

    def _release_after_failure(self, reason: str) -> None:
        """Leave THINKING/SPEAKING once nothing is left to play for a turn.

        The queue must be *empty*, not merely "our turn is gone": a marker or a
        sentence of a newer turn may still be waiting, and the play actor owns
        that transition.  A leftover marker whose turn was already committed as
        failed is committed-and-dropped here, otherwise it kept the pipeline in
        SPEAKING forever.
        """
        while not self.speech.empty():
            try:
                pending = self.speech.get_nowait()
            except asyncio.QueueEmpty:  # pragma: no cover - defensive
                break
            if pending.turn.committed:
                continue  # stale item of the failed turn: drop it
            try:
                self.speech.put_nowait(pending)
            except asyncio.QueueFull:  # pragma: no cover - defensive
                pass
            self.metrics.set("speech_queue_depth", self.speech.qsize())
            return  # a live item of another turn still owns the state
        self.metrics.set("speech_queue_depth", self.speech.qsize())
        if self.pending_turn is not None:
            return
        self._set_vad_margin(1.0)
        if self.states.state not in (VoiceState.THINKING, VoiceState.SPEAKING):
            self._settle_after_utterance(reason=reason)
            return
        target = VoiceState.LISTENING if self.utterance is not None else VoiceState.IDLE
        self.states.transition(target, reason=reason, force=True)

    async def _enqueue_speech(
        self, turn: AssistantTurn, text: str, token: threading.Event
    ) -> None:
        if not text:
            return
        if token.is_set() or turn.generation != self.generation or turn.token.is_set():
            return
        # The text that reaches Piper is *prepared*, not the raw model output:
        # Markdown, list markers, links and emoji are removed and numbers are
        # spelled out (see ``persian.prepare_for_tts``).  The raw sentence is
        # still what the device is shown and recorded, so nothing is lost from
        # the transcript.
        spoken = prepare_for_tts(text)
        if not spoken:
            # Nothing pronounceable left (a bare "*" or an emoji-only line).
            return
        words = spoken.split()
        self.event("tts_enqueue", turn_id=turn.turn_id, text=text, words=len(words))
        await self._put_speech(SpeechItem(turn, spoken, token))

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
            except Exception as exc:  # noqa: BLE001 - reported as a failed turn
                # Cancelling the token stops the rest of *this* turn.  The turn
                # is committed as failed and, crucially, the state is released:
                # the old code returned to the top of the loop with the machine
                # still in SPEAKING and ``pending_turn`` already cleared, so the
                # next thing the user said was counted as a barge-in against a
                # reply that no longer existed (the P1-11 shape, TTS branch).
                self._set_vad_margin(1.0)
                turn.current = None  # nothing of this sentence was heard
                self._fail_turn(
                    turn,
                    detail="تولید یا پخش صدا ناموفق بود؛ مدل Piper و دستگاه خروجی را بررسی کنید.",
                    reason="tts_error",
                    error=exc,
                )
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

    def reset(self, *, reason: str = "audio_stream_reopened") -> None:
        """Forget device-local state after the capture stream was reopened.

        Called by ``server.audio_io.reset_stream_state`` on every reopen.  The
        detector is reset so a half-recorded utterance from before the gap is
        never completed with post-gap audio (the same reason ``overflow`` does
        it), and an announcement is emitted so a transcript that suddenly stops
        is explainable instead of mysterious.

        A *final* transcript is never touched: like overflow, this only drops an
        unfinished utterance, and the ASR queue is left intact.
        """
        abandoned = self.utterance
        self.detector.reset()
        self.utterance = None
        self._last_partial_frames = 0
        self.scheduler.drop_partial()
        self.metrics.increment("audio_reopen_reset_count")
        self.event(
            "input_reset",
            reason=reason,
            abandoned_utterance_id=abandoned.id if abandoned is not None else 0,
        )
        self._settle_after_utterance(reason=reason)

    def metrics_snapshot(self) -> dict[str, Any]:
        return self.metrics.snapshot()

    def finalize_pending_turn(self) -> None:
        """Commit the in-flight reply to the JSONL now, without awaiting.

        Called by the transport *before* it releases the session's OS lease.
        That ordering is what makes an immediate reconnect safe: the old
        session's last record is on disk before another connection is allowed
        to resume and load the same file.  Idempotent — ``_commit_turn``
        ignores a turn that is already committed.
        """
        turn = self.pending_turn
        if turn is not None and not turn.committed:
            self._commit_turn(turn, STATUS_INTERRUPTED, reason="shutdown")

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.finalize_pending_turn()
        self.cancel.set()
        tasks = list(self.actors)
        if self.reply is not None:
            tasks.append(self.reply)
        tasks.extend(self._command_tasks)
        for task in tasks:
            task.cancel()
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            self.actors = []
            self.reply = None
            self.scheduler.finals.clear()
            self.scheduler.partial = None
            self._drain_speech()
            self.detector.reset()
            self.utterance = None
            self.conversation.close()


# ------------------------------------------------------------------- live runner
def create_pipeline(
    settings: Settings,
    audio: Any,
    speaker: Any,
    client: Any,
    emit: Callable[[dict[str, Any]], None],
    *,
    metrics: Metrics | None = None,
    session_id: str | None = None,
) -> Pipeline:
    """Build a pipeline with the configured persisted conversation.

    ``session_id`` selects the session to resume; ``None`` follows the configured
    resume policy (``PVAI_HISTORY_RESUME``), which for the local loop means the
    last session instead of a new one on every restart.
    """
    conversation = create_conversation(settings, session_id=session_id, local=True, exclusive=True)
    try:
        return Pipeline(
            settings,
            audio,
            speaker,
            client,
            emit,
            conversation=conversation,
            metrics=metrics,
        )
    except BaseException:
        # The lease is taken before the pipeline exists: a constructor failure
        # (a bad VAD setting, a missing device) would otherwise hold the local
        # session's lock until the process exits, and the next `run_live` would
        # fail with session_in_use.
        conversation.close()
        raise


async def run_live(settings: Settings) -> None:
    """Start the offline PC voice loop (microphone → conversation → speaker)."""
    from . import audio_io
    from .aec import DuplexAudio, SpeexAEC

    problems = settings.config_errors()
    if problems:
        raise VoiceError(" ".join(problems))

    # Retention must also apply to a PC that only ever runs the live loop and
    # never starts the API: transcripts are plain text on disk.
    removed = purge_expired(settings)
    if removed:
        print(
            f"{len(removed)} نشست قدیمی‌تر از {settings.history_retention_days} روز حذف شد.",
            flush=True,
        )

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
    speaker: Any = None
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
            headers=local_client_headers(settings),
            timeout=settings.request_timeout_s,
            trust_env=False,
            follow_redirects=False,
        ) as client:
            try:
                assert_local_api_authorized(await client.get("/ready"))
            except httpx.HTTPError:
                pass  # unreachable API: the normal stream error will report it
            pipeline = create_pipeline(
                settings,
                audio,
                speaker,
                client,
                lambda event: print(json.dumps(event, ensure_ascii=False), flush=True),
                metrics=metrics,
            )
            pipeline.start()
            resumed = getattr(pipeline, "conversation", None)
            if resumed is not None and resumed.messages:
                # Say it out loud: a resumed conversation looks like a fresh one
                # until the model answers with context the user did not type.
                print(
                    "ادامهٔ نشست «"
                    f"{resumed.session_id}» با {len(resumed.messages)} پیام قبلی "
                    "(خاموش: PVAI_HISTORY_RESUME=off)",
                    flush=True,
                )

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
        if speaker is not None:
            closer = getattr(speaker, "close", None)
            if callable(closer):
                closer()
        closer = getattr(audio, "close", None)
        if callable(closer):
            closer()
        duplex.close()
        if aec:
            aec.close()
