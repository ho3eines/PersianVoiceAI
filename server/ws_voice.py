"""Server-side WebSocket voice transport for LAN terminals (e.g. ESP32-S3).

What this is
------------
The implementation of the wire contract in :mod:`server.protocol`: one
``/ws/voice`` endpoint that authenticates a device, gives it a session, accepts
binary capture frames, feeds them into the **same** :class:`Pipeline
<server.streaming_voice.Pipeline>` the microphone path uses, and streams the
assistant's speech back as playback frames.

What this is not
----------------
There is still no firmware in this repository, and nothing here talks to the
cloud: the endpoint binds to the configured LAN address and every audio byte
stays on the machine.  Until a device exists, this transport is exercised by
tests with an in-process WebSocket client.

Rules that are deliberate
-------------------------
* **Authentication first.**  No audio or control message is accepted before a
  valid ``hello``.  If no token is configured the endpoint refuses every
  connection with ``auth_not_configured`` instead of silently becoming an open
  voice API — "never expose an unauthenticated voice API".
* **One pipeline per connection.**  Sessions are isolated: separate
  ``Conversation``, separate ASR queue, separate playback queue.  A device can
  resume a session by sending its ``session_id`` in ``hello`` (or
  ``session_start``) when ``PVAI_HISTORY_PERSIST`` is on; the stored messages
  are loaded before the conversation is used, so ids and the dedupe window
  continue instead of restarting.  A device that sends *no* id always gets a
  fresh session — the local loop's "resume the last session" default
  (``PVAI_HISTORY_RESUME``) deliberately does not apply here, or one device
  could inherit another's history.
* **Barge-in is a protocol event.**  ``interrupt`` from the device cancels the
  assistant turn *and* drops queued playback frames; the server answers with
  ``play_stop`` and never sends a frame that belongs to the cancelled turn.
* **The server's VAD is authoritative.**  Device ``speech_start``/``speech_end``
  are accepted (and acknowledged) as informational hints; they do not create or
  end turns, because the PC already owns endpointing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import APIRouter, WebSocket

from .config import Settings
from .conversation import create_conversation, SessionInUse
from .metrics import Metrics
from .protocol import (
    AUDIO_KIND_CAPTURE,
    AUDIO_KIND_PLAYBACK,
    MessageType,
    ProtocolError,
    ControlMessage,
    VALID_VOICES,
    check_token,
    decode_audio,
    encode_audio,
    hello_ok,
    require_authenticated,
)
from .security import ConnectionLimiter, SessionRegistry
from .streaming_voice import Pipeline

logger = logging.getLogger("persian_voice_ai.ws")

CLOSE_UNAUTHORIZED = 1008
CLOSE_UNAVAILABLE = 1011
CLOSE_TRY_AGAIN = 1013
CLOSE_GOING_AWAY = 1001
HANDSHAKE_TIMEOUT_S = 3.0
PLAYBACK_FRAME_BYTES = 640  # 20 ms of PCM16 mono at 16 kHz
SINK_QUEUE_FRAMES = 250  # ~5 s of playback backpressure before synthesis blocks
PLAYBACK_FRAME_MS = 20.0

# The pipeline's VAD counts *frames*, and its endpoint/max-segment timers are
# ``frames * FRAME_SECONDS`` (server/vad.py).  A single 100 ms block therefore
# makes endpointing 5x too slow, silently.  One capture frame is one pipeline
# block; longer blocks are re-chunked, malformed ones are refused.
CAPTURE_BLOCK_SAMPLES = 320
CAPTURE_BLOCK_BYTES = CAPTURE_BLOCK_SAMPLES * 2

_CONTROL_QUEUE_SIZE = 64
_CRITICAL_TYPES = frozenset(
    {
        MessageType.PLAY_STOP.value,
        MessageType.PLAY_END.value,
        MessageType.ASR_FINAL.value,
        MessageType.ERROR.value,
        MessageType.HELLO_OK.value,
        MessageType.SESSION_ACK.value,
    }
)
"""Messages a device must never miss.

A dropped ``play_stop`` is audible (cancelled audio keeps playing) and a
dropped ``play_end``/``asr_final`` breaks the firmware state machine, so these
are delivered even when the control queue is full.  ``asr_partial`` is a display
hint and is the only thing allowed to be dropped.
"""

_PLAY_STOP_REASONS = {
    "user_speech": "barge_in",
    "speech": "barge_in",
    "manual": "barge_in",
    "superseded": "new_turn",
    "shutdown": "shutdown",
    "input_overflow": "barge_in",
}
_SPEECH_END_REASONS = {"max_segment", "overflow"}


def _message_type(raw: str) -> str:
    """Cheap peek at an encoded control message (the sender needs the type)."""
    try:
        return str(json.loads(raw).get("type") or "")
    except (ValueError, AttributeError, TypeError):
        return ""


@dataclass
class VoiceBundle:
    """Per-connection dependencies (real ones, or fakes in tests)."""

    audio: Any
    client: Any
    speaker_for: Callable[[str | None], Any]

    async def aclose(self) -> None:
        closer = getattr(self.client, "aclose", None)
        if closer is not None:
            await closer()


class PlaybackSink:
    """Duplex-compatible sink that queues Piper PCM for the WebSocket sender.

    ``Speaker`` writes 20 ms blocks and checks the cancel token before every
    block, so cancellation still costs at most one block.  The queue is bounded:
    a slow client applies backpressure to synthesis instead of growing memory.
    """

    def __init__(self, max_frames: int = SINK_QUEUE_FRAMES) -> None:
        self.frames: queue.Queue[bytes] = queue.Queue(maxsize=max_frames)
        self.closed = threading.Event()
        self.written = 0
        self.dropped = 0

    def write(self, pcm: bytes, cancel: threading.Event) -> None:
        for start in range(0, len(pcm), PLAYBACK_FRAME_BYTES):
            if cancel.is_set() or self.closed.is_set():
                return
            frame = pcm[start : start + PLAYBACK_FRAME_BYTES].ljust(
                PLAYBACK_FRAME_BYTES, b"\0"
            )
            while not cancel.is_set() and not self.closed.is_set():
                try:
                    self.frames.put(frame, timeout=0.05)
                    self.written += 1
                    break
                except queue.Full:
                    continue

    def drain(self) -> int:
        dropped = 0
        while True:
            try:
                self.frames.get_nowait()
                dropped += 1
            except queue.Empty:
                self.dropped += dropped
                return dropped

    def close(self) -> None:
        self.closed.set()
        self.drain()


class ModelPool:
    """Process-wide Whisper/Piper instances shared by every device connection.

    Each connection used to build its own ``LocalAudio`` (a complete
    ``WhisperModel``) and its own ``Speaker`` (a Piper ONNX session).  Two
    phones plus the local loop meant three copies of the same ~500 MB model in
    RAM, and every new connection paid the load time again.

    Sharing a model is only safe if its *use* is serialised.  Neither library
    is thread-safe: CTranslate2 decoding and Piper's lazy ``synthesize``
    generator both drive mutable session state.  ``LocalAudio`` serialises its
    decodes with ``_decode_lock`` (the "one ASR actor" contract the pipeline
    needs) and each pooled Piper voice is paired with a lock that ``Speaker``
    holds around every model step.

    ``Speaker`` itself is *not* shareable because it writes to a
    per-connection sink, so only the loaded Piper voice is pooled; the wrapper
    stays per connection.
    """

    def __init__(self) -> None:
        self._audio: Any = None
        self.asr_metrics: Metrics | None = None  # process-wide, never first-socket owned
        self._voices: dict[str, Any] = {}
        self._voice_locks: dict[str, Any] = {}
        self._lock = threading.Lock()
        self.telemetry: dict[str, int] = {"asr_loads": 0, "voice_loads": 0, "hits": 0}

    def audio(self, settings: Settings, metrics: Metrics) -> Any:
        with self._lock:
            if self._audio is None:
                from .voice import LocalAudio

                self.asr_metrics = Metrics(enabled=settings.metrics_enabled)
                self._audio = LocalAudio(settings, metrics=self.asr_metrics)
                self.telemetry["asr_loads"] += 1
            else:
                self.telemetry["hits"] += 1
                # Use the process-wide metrics of the pool rather than a first
                # connection that may already have gone away.
            return self._audio

    def preload(self, settings: Settings) -> None:
        """Load and warm the shared Whisper model before the first device connects.

        Without this the first ESP32 connection pays the full model load
        (tens of seconds on a two-core CPU) and its first utterance times out.
        """
        audio = self.audio(settings, Metrics(enabled=settings.metrics_enabled))
        warmup = getattr(audio, "warmup", None)
        if callable(warmup):
            warmup()
            logger.info("Whisper model preloaded for the LAN voice transport")

    def piper_voice(self, settings: Settings, voice: str) -> Any:
        """The loaded Piper model for ``voice`` (``male``/``female`` only)."""
        key = (voice or settings.tts_voice or "female").strip().lower()
        if key not in VALID_VOICES:
            raise ValueError(f"voice must be one of {sorted(VALID_VOICES)}")
        with self._lock:
            cached = self._voices.get(key)
            if cached is not None:
                self.telemetry["hits"] += 1
                return cached
            from .live_voice import load_piper_voice

            self._voices[key] = load_piper_voice(settings, key)
            self._voice_locks[key] = threading.Lock()
            self.telemetry["voice_loads"] += 1
            return self._voices[key]

    def piper_lock(self, voice: str) -> Any:
        """The lock that serialises synthesis steps on one shared Piper model.

        ``Speaker`` holds it around ``next()`` on ``synthesize()``, so two
        devices sharing a voice cannot advance one ONNX session concurrently
        while their playback still overlaps.
        """
        key = (voice or "").strip().lower()
        with self._lock:
            lock = self._voice_locks.get(key)
            if lock is None:
                lock = self._voice_locks[key] = threading.Lock()
            return lock

    def reset(self) -> None:
        with self._lock:
            audio = self._audio
            self._audio = None
            self.asr_metrics = None
            self._voices.clear()
            self._voice_locks.clear()
            self.telemetry = {"asr_loads": 0, "voice_loads": 0, "hits": 0}
        if audio is not None:
            closer = getattr(audio, "close", None)
            if callable(closer):
                closer()
        from .voice import release_inference_memory

        release_inference_memory()


MODEL_POOL = ModelPool()
"""One pool per process; tests reset it via :func:`reset_model_pool`."""


def reset_model_pool() -> None:
    MODEL_POOL.reset()


def default_bundle(settings: Settings, sink: PlaybackSink, metrics: Metrics) -> VoiceBundle:
    """Build the pipeline dependencies for one connection.

    The heavy models come from :data:`MODEL_POOL`, so opening a second
    connection is cheap.  Call this through ``asyncio.to_thread``: the first
    call still loads Whisper and Piper and would otherwise stall the loop.
    """
    import httpx

    from .live_voice import Speaker
    from .voice import local_api_url, local_client_headers

    audio = MODEL_POOL.audio(settings, metrics)

    def speaker_for(voice: str | None) -> Any:
        key = (voice or settings.tts_voice or "female").strip().lower()
        # The Piper model is shared; playback still targets this connection's
        # sink, so the Speaker wrapper stays per-connection.
        return Speaker(
            settings,
            duplex=sink,
            voice=key,
            model=MODEL_POOL.piper_voice(settings, key),
            model_lock=MODEL_POOL.piper_lock(key),
        )

    client = httpx.AsyncClient(
        base_url=local_api_url(settings),
        headers=local_client_headers(settings),
        timeout=settings.request_timeout_s,
        trust_env=False,
        follow_redirects=False,
    )
    return VoiceBundle(audio=audio, client=client, speaker_for=speaker_for)


class WsCounters:
    """Process-wide counters for the transport (cheap, bounded, observable).

    ``rejected`` (handshake/capacity refusals) and ``control_dropped`` (a full
    queue losing a *partial*) used to share one field, so an auth failure and a
    slow client were indistinguishable in the metrics.
    """

    def __init__(self) -> None:
        self.active = 0
        self.total = 0
        self.rejected = 0  # handshake or capacity refusal
        self.control_dropped = 0  # asr_partial dropped by a full queue
        self.critical_reordered = 0  # critical message that had to wait in _overflow
        self.frames_in = 0
        self.frames_out = 0
        self.frames_out_of_order = 0  # capture sequence gaps/duplicates seen
        self.frames_bad_size = 0  # capture frames that were not 320 samples
        self.voice_rejected = 0  # set_voice with an undocumented name
        self.frames_rate_limited = 0  # capture frames above PVAI_WS_FRAME_RATE
        self.outgoing_full = 0  # bounded control queue hit its limit
        self.rejected_capacity = 0  # conn limit / connect rate
        self.session_conflicts = 0  # two devices tried to own one session
        self.registry = SessionRegistry()
        self.lock = threading.Lock()
        self._limiter: Any = None
        self._limiter_settings: Any = None
        self._handshakes: dict[str, int] = {}
        self._failures: dict[str, deque[float]] = {}
        self._handshake_total = 0

    def handshake_enter(self, peer: str) -> bool:
        """Bound untrusted memory without consuming model capacity or connect rate."""
        with self.lock:
            now = time.monotonic()
            failures = self._failures.get(peer)
            if failures is not None:
                while failures and now - failures[0] > 60:
                    failures.popleft()
                if len(failures) >= 3 and now - failures[-1] < 10:
                    return False
            if self._handshake_total >= 16 or self._handshakes.get(peer, 0) >= 8:
                return False
            self._handshakes[peer] = self._handshakes.get(peer, 0) + 1
            self._handshake_total += 1
            return True

    def handshake_leave(self, peer: str) -> None:
        with self.lock:
            count = self._handshakes.get(peer, 0)
            if count:
                self._handshake_total -= 1
                if count == 1:
                    del self._handshakes[peer]
                else:
                    self._handshakes[peer] = count - 1

    def auth_failed(self, peer: str) -> None:
        with self.lock:
            failures = self._failures.setdefault(peer, deque(maxlen=8))
            failures.append(time.monotonic())
            # Keep the table bounded even for many distinct spoofed peers.
            if len(self._failures) > 1024:
                self._failures.pop(next(iter(self._failures)))

    def limiter(self, settings: Settings) -> "ConnectionLimiter":
        with self.lock:
            if self._limiter is None or self._limiter_settings is not settings:
                self._limiter = ConnectionLimiter(
                    settings.ws_max_connections, settings.ws_connect_rate_per_min
                )
                self._limiter_settings = settings
            return self._limiter

    def bump(self, name: str, amount: int = 1) -> None:
        with self.lock:
            setattr(self, name, getattr(self, name) + amount)

    def snapshot(self) -> dict[str, int]:
        with self.lock:
            return {
                "active": self.active,
                "total": self.total,
                "rejected": self.rejected,
                "control_dropped": self.control_dropped,
                "critical_reordered": self.critical_reordered,
                "frames_in": self.frames_in,
                "frames_out": self.frames_out,
                "frames_out_of_order": self.frames_out_of_order,
                "frames_bad_size": self.frames_bad_size,
            }


class VoiceWebSocket:
    """One connected device: handshake, capture in, control, playback out."""

    def __init__(
        self,
        websocket: Any,
        settings: Settings,
        *,
        bundle_factory: Callable[[Settings, PlaybackSink, Metrics], VoiceBundle] = default_bundle,
        counters: WsCounters | None = None,
        metrics: Metrics | None = None,
        session_registry: Any | None = None,
    ) -> None:
        self.websocket = websocket
        self.settings = settings
        self.bundle_factory = bundle_factory
        self.counters = counters or WsCounters()
        self.metrics = metrics or Metrics(enabled=settings.metrics_enabled)
        self.sink = PlaybackSink()
        self.pipeline: Pipeline | None = None
        self._leased_conversation: Any = None
        self.bundle: VoiceBundle | None = None
        self.session_id: str | None = None
        self.device_id: str = ""
        self.voice: str = settings.tts_voice
        self.authenticated = False
        self.claim_capacity: Callable[[], tuple[bool, str]] | None = None
        self.release_capacity: Callable[[], None] | None = None
        self.handshake_done: Callable[[], None] | None = None
        self.capacity_owned = False
        self.peer = getattr(getattr(websocket, "client", None), "host", "unknown")
        self.playback_sequence = 0
        # Playback accounting for ``play_end``: frames actually handed to this
        # device, measured per turn (a turn starts where the previous one ended).
        self.frames_sent = 0
        self._frames_sent_at_turn_start = 0
        self._play_start_turn: int | None = None  # last turn that got play_start
        self._frame_stamps: deque[float] = deque()  # capture arrivals, for the rate cap
        self._serve_task: asyncio.Task[Any] | None = None
        try:
            self._loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:  # constructed outside a loop (unit tests)
            self._loop = None
        self._closing_reason: str | None = None
        self.outgoing: asyncio.Queue[str] = asyncio.Queue(maxsize=_CONTROL_QUEUE_SIZE)
        self._send_task: asyncio.Task[None] | None = None
        # Critical messages that found the queue full and nothing evictable.
        # Bounded too: a device that cannot drain this is dying, and unbounded
        # memory in a transport is exactly the class of bug being fixed.
        self._overflow: deque[ControlMessage] = deque(maxlen=_CONTROL_QUEUE_SIZE)
        # Capture sequence tracking (WiFi reordering/loss detection).
        self._last_capture_seq: int | None = None
        self._capture_out_of_order = 0
        # Playback pacing: monotonic deadline of the next frame we may send,
        # so audio goes out at real-time rate instead of as fast as it is made.
        self._next_frame_at: float | None = None
        self._last_activity = time.monotonic()
        self.credit_ms = 0.0
        self.session_registry = session_registry
        self._owned_session: str | None = None
        self._session_owner: str = ""

    # ------------------------------------------------------------------ output
    def _queue_control(self, message: ControlMessage) -> None:
        """Queue a control message without ever dropping a critical one.

        ``self.outgoing`` is bounded, and the old code dropped whatever arrived
        when it was full — including ``play_stop``, ``play_end``, ``asr_final``
        and ``error``.  A dropped ``play_stop`` means cancelled audio keeps
        playing on the device; a dropped ``play_end`` stalls its state machine.
        Now only ``asr_partial`` may be dropped (a newer partial is about to
        replace it anyway).  Everything critical — ``play_stop``, ``play_end``,
        ``asr_final``, ``error`` — waits in ``_overflow`` and is sent before
        anything still queued, so it is *delayed*, never lost.

        Thread-safe: pipeline events are not all raised on the event loop —
        ``Speaker`` runs in ``asyncio.to_thread`` and calls back from that worker
        on the first audible frame.  ``asyncio.Queue`` is not safe to touch from
        another thread, so the append is marshalled onto the loop.
        """
        if not self._on_loop_thread():
            loop = self._loop
            if loop is None:  # pragma: no cover - no loop means no connection
                return
            loop.call_soon_threadsafe(self._queue_control, message)
            return
        encoded = message.encode()
        try:
            self.outgoing.put_nowait(encoded)
            return
        except asyncio.QueueFull:
            self.counters.bump("outgoing_full")
        critical = message.type.value in _CRITICAL_TYPES
        if not critical:
            self.counters.bump("control_dropped")
            self.metrics.increment("ws_control_dropped")
            return
        self._overflow.append(message)
        self.counters.bump("critical_reordered")

    def _on_loop_thread(self) -> bool:
        """True when the caller is on the event loop that serves this session.

        The loop is captured on first use, not in ``__init__``: a session can be
        built by a test (or a thread) before the loop that serves it exists.

        A worker thread has *no running loop* (``get_running_loop`` raises), and
        that is precisely the case this has to catch — returning ``True`` there
        would leave the guard doing nothing.  Only a context with no loop at all
        and no loop ever captured (a plain synchronous test) is treated as "same
        thread".
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Called from a worker thread, or from synchronous test code.
            return self._loop is None
        if self._loop is None:
            self._loop = loop
        return loop is self._loop


    def _notify(self, code: str, detail: str) -> None:
        self._queue_control(
            ControlMessage(MessageType.ERROR, {"code": code, "detail": detail})
        )

    def _reset_playback_pacing(self) -> None:
        """Let the next turn start with a full credit, not last turn's debt."""
        self._next_frame_at = None
        self._play_start_turn = None

    def _take_played_ms(self) -> float:
        """Milliseconds of audio *sent to this device* since the last boundary.

        Counted from ``frames_sent``, not from the sink: the sink is what
        synthesis produced, which with an unpaced sender ran ahead of playback
        by seconds, so ``played_ms`` reported audio the device had not played.
        With pacing on, frames sent track frames heard within
        ``PVAI_WS_PLAYBACK_CREDIT_MS``; ``play_end`` is only emitted after the
        playback queue is empty, so every counted frame has been delivered.
        """
        frames = self.frames_sent - self._frames_sent_at_turn_start
        self._frames_sent_at_turn_start = self.frames_sent
        return max(0, frames) * PLAYBACK_FRAME_MS

    async def _reject(self, code: str, detail: str, close_code: int = CLOSE_UNAUTHORIZED) -> None:
        """Send a terminal error frame, then close.

        Used before the sender task exists: a queued message would be dropped
        when the socket closes, leaving the device with a bare close code and no
        reason.  The frame goes out directly so the device can log *why*.
        """
        message = ControlMessage(MessageType.ERROR, {"code": code, "detail": detail})
        try:
            await self.websocket.send_text(message.encode())
        except Exception:  # device already gone; the close below is still correct
            pass
        await self.websocket.close(code=close_code)

    def _on_pipeline_event(self, event: dict[str, Any]) -> None:
        """Translate pipeline events into protocol messages (called in-loop)."""
        kind = event.get("type")
        if kind == "speech_start":
            self._queue_control(
                ControlMessage(
                    MessageType.SPEECH_START, {"utterance_id": int(event["utterance_id"])}
                )
            )
        elif kind == "speech_end":
            reason = event.get("boundary")
            self._queue_control(
                ControlMessage(
                    MessageType.SPEECH_END,
                    {
                        "utterance_id": int(event["utterance_id"]),
                        "reason": reason if reason in _SPEECH_END_REASONS else "endpoint",
                    },
                )
            )
        elif kind == "transcript_partial":
            text = str(event.get("text") or "")
            if text:
                self._queue_control(
                    ControlMessage(
                        MessageType.ASR_PARTIAL,
                        {"utterance_id": int(event["utterance_id"]), "text": text},
                    )
                )
        elif kind == "transcript_final":
            self._queue_control(
                ControlMessage(
                    MessageType.ASR_FINAL,
                    {
                        "utterance_id": int(event["utterance_id"]),
                        "text": str(event.get("text") or ""),
                    },
                )
            )
        elif kind == "tts_start":
            # One ``play_start`` per *turn*, matching the single ``play_end``.
            # ``tts_start`` fires once per sentence, so the old mapping sent a
            # second ``play_start`` mid-turn: a device that resets its buffer
            # or its state machine on that message re-announced playback while
            # the first sentence was still playing.
            turn_id = int(event.get("turn_id") or 0)
            if turn_id != self._play_start_turn:
                self._play_start_turn = turn_id
                self._queue_control(
                    ControlMessage(
                        MessageType.PLAY_START,
                        {
                            "turn_id": turn_id,
                            "sample_rate": self.settings.audio_sample_rate,
                        },
                    )
                )
        elif kind == "response_end":
            # ``play_end`` closes a *turn*, not a sentence: a multi-sentence reply
            # emits one ``tts_end`` per sentence, so mapping those to play_end
            # told the device the turn was over while audio was still queued.
            if str(event.get("status")) == "complete":
                turn_id = int(event.get("turn_id") or 0)
                # ``played_ms`` is filled in by the sender, not here: with
                # pacing the frames of this turn are still going out when
                # ``response_end`` arrives, so counting now would report audio
                # that has not reached the device yet.
                self._queue_control(
                    ControlMessage(
                        MessageType.PLAY_END,
                        {"turn_id": turn_id, "played_ms": None, "_final": True},
                    )
                )
        elif kind == "interrupted":
            self.sink.drain()  # nothing from the cancelled turn may be sent later
            self._reset_playback_pacing()  # the next turn starts with full credit
            self._queue_control(
                ControlMessage(
                    MessageType.PLAY_STOP,
                    {
                        "turn_id": int(event.get("turn_id") or 0),
                        "reason": _PLAY_STOP_REASONS.get(str(event.get("reason")), "barge_in"),
                    },
                )
            )
        elif kind == "error":
            self._notify("pipeline", str(event.get("detail") or "خطای خط لوله"))

    def _finalize_play_end(self, pending: str) -> str:
        """Fill ``played_ms`` into a queued ``play_end`` right before it is sent."""
        try:
            body = json.loads(pending)
        except ValueError:  # pragma: no cover - we produced it ourselves
            return pending
        body.pop("_final", None)
        body["played_ms"] = round(self._take_played_ms(), 1)
        return ControlMessage(
            MessageType.PLAY_END, {k: v for k, v in body.items() if k != "type"}
        ).encode()

    def _playback_ready_at(self) -> float | None:
        """When the next playback frame may go out, or ``None`` if now.

        Without pacing the sender pushed every synthesised block as fast as
        Piper produced it — up to ~5 s of audio ahead of real time (the sink
        holds 250 frames).  The device then buffered five seconds it could not
        use: after a barge-in those blocks were already on the wire, so they
        played anyway until ``play_stop`` arrived, and ``played_ms`` described
        queued audio rather than heard audio.  Sending at real-time rate keeps
        the device's buffer at ``PVAI_WS_PLAYBACK_CREDIT_MS`` (default 200 ms).
        """
        if not self.settings.ws_playback_pacing:
            return None
        now = time.monotonic()
        credit_s = max(0.0, self.settings.ws_playback_credit_ms) / 1000.0
        if self._next_frame_at is None:
            # New turn (or first frame): the device may start this much ahead
            # so it never underruns between synthesis bursts.
            self._next_frame_at = now - credit_s
        else:
            self._next_frame_at = max(self._next_frame_at, now - credit_s)
        return self._next_frame_at if self._next_frame_at > now else None

    def _account_frame_sent(self) -> None:
        if not self.settings.ws_playback_pacing:
            return
        base = self._next_frame_at if self._next_frame_at is not None else time.monotonic()
        self._next_frame_at = base + PLAYBACK_FRAME_MS / 1000.0

    async def _send_playback_frame(self) -> bool:
        """Send one queued 20 ms block; ``False`` when none may go out now.

        Pacing is enforced *here*, not in the caller: every path that writes
        playback audio has to honour the device's buffer credit, and a second
        caller that forgot to would silently reintroduce the burst.
        """
        ready_at = self._playback_ready_at()
        if ready_at is not None and ready_at > time.monotonic():
            await asyncio.sleep(min(PLAYBACK_FRAME_MS / 1000.0, ready_at - time.monotonic()))
            return False
        try:
            pcm = self.sink.frames.get_nowait()
        except queue.Empty:
            return False
        frame = encode_audio(
            AUDIO_KIND_PLAYBACK,
            self.playback_sequence,
            int(time.monotonic() * 1000) & 0xFFFFFFFF,
            pcm,
        )
        self.playback_sequence = (self.playback_sequence + 1) & 0xFFFF
        await self.websocket.send_bytes(frame)
        self._account_frame_sent()
        self.counters.bump("frames_out")
        self.metrics.increment("ws_frames_out")
        self.frames_sent += 1
        return True

    async def _sender(self) -> None:
        """Multiplex control messages and playback frames onto one socket.

        Control frames normally win, but a queued ``play_end`` waits until the
        playback queue is empty: the device may only be told the turn finished
        once it has actually received every frame of that turn.
        """
        pending: str | None = None
        try:
            while True:
                # Critical messages that overflowed the bounded queue go first:
                # they were produced later than everything already queued, but
                # for control frames "latest wins" is the correct order.
                if self._overflow and pending is None:
                    # A turn boundary may never overtake its queued play_start.
                    # Other critical controls (especially play_stop) retain priority.
                    if (self._overflow[0].type is MessageType.PLAY_END
                            and not self.outgoing.empty()):
                        pending = self.outgoing.get_nowait()
                    else:
                        pending = self._overflow.popleft().encode()
                if pending is None:
                    try:
                        pending = self.outgoing.get_nowait()
                    except asyncio.QueueEmpty:
                        pending = None
                if pending is not None:
                    if _message_type(pending) == MessageType.PLAY_END.value:
                        # The turn is announced as finished only after every
                        # frame of it has been written to the socket; the count
                        # is taken at that moment, so it describes audio the
                        # device actually received.
                        if not self.sink.frames.empty():
                            if await self._send_playback_frame():
                                continue
                            await asyncio.sleep(0.005)
                            continue
                        pending = self._finalize_play_end(pending)
                    await self.websocket.send_text(pending)
                    pending = None
                    continue
                if await self._send_playback_frame():
                    continue
                await asyncio.sleep(0.005)
        except asyncio.CancelledError:
            raise
        except Exception:  # socket closed: the receive loop will finish the session
            return

    # ---------------------------------------------------------------- handshake
    def _authenticate(self, provided: str | None) -> tuple[bool, str]:
        """Check a device token; returns ``(ok, device_id)``.

        Per-device tokens (``PVAI_WS_TOKENS``) are tried first: they carry the
        device identity, so a stolen token can be revoked for exactly one device
        without reflashing the others.  A single shared ``PVAI_WS_TOKEN`` is
        still accepted so existing setups keep working.
        """
        device_tokens = self.settings.device_tokens()
        if device_tokens:
            for token, device_id in device_tokens.items():
                if provided and check_token(provided, token):
                    return True, device_id
        shared = getattr(self.settings, "ws_token", "") or ""
        if shared and check_token(provided, shared):
            return True, ""
        return False, ""

    async def _handshake(self) -> bool:
        expected = getattr(self.settings, "ws_token", "") or ""
        per_device = bool(self.settings.device_tokens())
        try:
            raw = await asyncio.wait_for(self.websocket.receive_text(), HANDSHAKE_TIMEOUT_S)
        except asyncio.TimeoutError:
            await self._reject("handshake_timeout", "پیام hello در زمان مجاز دریافت نشد.")
            return False
        except Exception:
            return False
        try:
            message = ControlMessage.decode(raw)
        except ProtocolError as exc:
            await self._reject("protocol_error", str(exc))
            return False
        if message.type is not MessageType.HELLO:
            await self._reject("handshake_required", "نخستین پیام باید hello باشد.")
            return False
        if not expected and not per_device:
            self.counters.bump("rejected")
            self.metrics.increment("ws_auth_failures")
            self.counters.auth_failed(self.peer)
            await self._reject(
                "auth_not_configured",
                "احراز هویت دستگاه تنظیم نشده است؛ PVAI_WS_TOKENS (یا PVAI_WS_TOKEN) را "
                "در .env قرار دهید. این API بدون توکن عمداً بسته است.",
            )
            return False
        ok, token_device = self._authenticate(message.payload.get("token"))
        if not ok:
            self.counters.bump("rejected")
            self.metrics.increment("ws_auth_failures")
            self.counters.auth_failed(self.peer)
            await self._reject("auth_failed", "توکن دستگاه نامعتبر است.")
            return False

        claimed = str(message.payload.get("device_id") or "")
        if token_device and claimed and claimed != token_device:
            # The token identifies the device; a token for device A must not be
            # usable while claiming to be device B.
            self.counters.bump("rejected")
            self.metrics.increment("ws_auth_failures")
            self.counters.auth_failed(self.peer)
            await self._reject(
                "device_mismatch",
                f"این توکن به دستگاه «{token_device}» تعلق دارد، نه «{claimed}».",
            )
            return False
        self.device_id = token_device or claimed
        requested = message.payload.get("session_id")
        if requested is not None:
            try:
                self.session_id = str(requested)
                self._validate_session_id(self.session_id)
            except ValueError:
                await self._reject("invalid_session", "شناسهٔ نشست مجاز نیست.")
                return False
        self.authenticated = True
        self.counters.bump("active")
        self.counters.bump("total")
        self.metrics.increment("ws_connections")
        logger.info("ws device=%s session=%s connected", self.device_id, self.session_id)
        return True

    async def _refuse_shared_session(self) -> None:
        """Refuse a second live connection for one session, with a reason."""
        self._notify(
            "session_in_use",
            "این نشست هم‌اکنون در اتصال دیگری باز است؛ دو دستگاه نباید یک "
            "فایل مکالمه را هم‌زمان بنویسند.",
        )
        await self._reject(
            "session_in_use",
            "این نشست هم‌اکنون در اتصال دیگری باز است.",
            CLOSE_UNAVAILABLE,
        )

    @staticmethod
    def _validate_session_id(session_id: str) -> None:
        from .conversation import ConversationStore

        ConversationStore.sanitize(session_id)

    async def _open_pipeline(self) -> bool:
        # ``resume=False`` only disables the *implicit* default: a device that
        # asked for a ``session_id`` still resumes that session, while a device
        # that asked for nothing gets its own new one instead of joining
        # whatever the last session on this PC was.
        #
        # Claim order is registry → OS lease, and release order is lease →
        # registry (see :meth:`close`).  The registry is therefore the *gate*:
        # while it names an owner, the lease is held or about to be; once it is
        # free, nothing of the old session is left.  Claiming in the other order
        # left a window where a session held its lock but not its registry entry,
        # and a reconnecting device was refused with ``session_in_use`` for a
        # session that looked free.
        owner = f"{self.device_id or 'device'}#{id(self):x}"
        requested = self.session_id
        if self.session_registry is not None and requested is not None:
            if not self.session_registry.claim(requested, owner):
                await self._refuse_shared_session()
                return False
            self._owned_session = requested
            self._session_owner = owner
        try:
            conversation = create_conversation(
                self.settings, session_id=self.session_id, resume=False, exclusive=True
            )
        except SessionInUse:
            self._release_claims()
            await self._reject("session_in_use", "این نشست هم‌اکنون در فرایند دیگری باز است.", CLOSE_UNAVAILABLE)
            return False
        self._leased_conversation = conversation
        self.session_id = conversation.session_id
        if self.session_registry is not None and self._owned_session is None:
            # A session id the server generated: nobody can be holding it, but
            # the claim still has to happen so that *both* claims exist.
            if not self.session_registry.claim(self.session_id, owner):
                self._release_claims()
                await self._refuse_shared_session()
                return False
            self._owned_session = self.session_id
            self._session_owner = owner
        from .voice import VoiceError
        try:
            # ``default_bundle`` builds LocalAudio (Whisper) and the first
            # Speaker (Piper ONNX); both load multi-hundred-MB models.  Doing
            # that inline froze the event loop — every other connection *and*
            # the REST API stalled for seconds.
            self.bundle = await asyncio.to_thread(
                self.bundle_factory, self.settings, self.sink, self.metrics
            )
            speaker = await asyncio.to_thread(self.bundle.speaker_for, self.voice)
            if self.bundle_factory is default_bundle:
                from .voice import assert_local_api_authorized
                try:
                    assert_local_api_authorized(await self.bundle.client.get("/ready"))
                except httpx.HTTPError:
                    pass  # no server: stream failure is reported by the pipeline
        except VoiceError as exc:
            await self._reject("api_auth_failed", str(exc), CLOSE_UNAVAILABLE)
            return False
        except Exception as exc:
            logger.warning("ws voice bundle failed: %s", exc)
            await self._reject(
                "voice_unavailable",
                "موتور صوتی آماده نیست؛ مدل‌های Whisper/Piper و /ready را بررسی کنید.",
                CLOSE_UNAVAILABLE,
            )
            return False
        self.pipeline = Pipeline(
            self.settings,
            self.bundle.audio,
            speaker,
            self.bundle.client,
            self._on_pipeline_event,
            conversation=conversation,
            metrics=self.metrics,
        )
        self.pipeline.start()
        self._send_task = asyncio.create_task(self._sender())
        self._queue_control(
            hello_ok(
                self.session_id,
                server_time_ms=int(time.time() * 1000),
            )
        )
        return True

    # ----------------------------------------------------------------- dispatch
    async def _handle_control(self, raw: str) -> bool:
        try:
            message = ControlMessage.decode(raw)
            require_authenticated(message, authenticated=self.authenticated)
        except ProtocolError as exc:
            self._notify("protocol_error", str(exc))
            return True
        kind = message.type
        if kind is MessageType.PING:
            self._queue_control(
                ControlMessage(MessageType.PONG, {"nonce": message.payload.get("nonce")})
            )
        elif kind is MessageType.SESSION_START:
            requested = str(message.payload.get("session_id"))
            if self.session_id and requested != self.session_id:
                self._notify(
                    "session_locked",
                    "نشست این اتصال تغییرپذیر نیست؛ برای نشست دیگر اتصال تازه باز کنید.",
                )
            else:
                self.session_id = requested
                self._queue_control(
                    ControlMessage(MessageType.SESSION_ACK, {"session_id": requested})
                )
        elif kind is MessageType.INTERRUPT:
            if self.pipeline is not None:
                # The pipeline cancels the turn; the sink drain happens in the
                # interrupted event so cancelled audio is never sent afterwards.
                self.pipeline.barge_in(reason=str(message.payload.get("reason") or "manual"))
        elif kind is MessageType.SET_VOICE:
            if self.bundle is not None and self.pipeline is not None:
                await self._set_voice(message.payload.get("voice"))
        elif kind in (MessageType.SPEECH_START, MessageType.SPEECH_END):
            # Informational: the server owns endpointing.  Acknowledged so the
            # firmware state machine does not stall.
            pass
        else:
            self._notify(
                "unsupported_message",
                f"پیام {kind.value} از سمت دستگاه در این نسخه پشتیبانی نمی‌شود.",
            )
        return True

    async def _set_voice(self, requested: Any) -> bool:
        """Apply a ``set_voice`` request; ``False`` when the name is refused.

        Only the two documented voices are accepted.  ``speaker_for`` used to
        cache one loaded Piper model per *distinct string*, and
        ``tts_model_for`` silently returns the default model for an unknown
        name — so any authenticated device could grow the cache without limit
        by sending random strings (memory DoS).  The wire parser already
        rejects these, but the check is repeated here because
        ``ControlMessage`` does not validate its payload and the cache is what
        actually blows up.
        """
        voice = str(requested or "").strip().lower()
        if voice not in VALID_VOICES:
            self.metrics.increment("ws_voice_rejected")
            self.counters.bump("voice_rejected")
            self._notify(
                "invalid_voice",
                f"صدا باید یکی از {sorted(VALID_VOICES)} باشد.",
            )
            return False
        try:
            # Model load is blocking and must not run in the loop.
            self.pipeline.speaker = await asyncio.to_thread(
                self.bundle.speaker_for, voice
            )
        except Exception as exc:  # missing weights for that voice
            logger.warning("ws set_voice failed for %s: %s", voice, exc)
            self._notify(
                "voice_unavailable",
                f"مدل صدای «{voice}» آماده نیست؛ PVAI_TTS_MODEL_MALE/FEMALE را بررسی کنید.",
            )
            return False
        self.voice = voice  # applies from the next sentence onwards
        logger.info("ws device=%s voice=%s", self.device_id, voice)
        return True

    def _track_capture_sequence(self, sequence: int) -> None:
        """Detect lost/duplicated/reordered capture frames (WiFi is lossy).

        The old code never looked at ``sequence``, so a dropped packet simply
        shortened the utterance with no trace anywhere — VAD endpointing then
        measured a gap that never existed.  Any discontinuity is counted and
        logged once per occurrence; it is not fatal, because a single lost 20 ms
        block must not kill an otherwise working session.
        """
        previous = self._last_capture_seq
        expected = None if previous is None else (previous + 1) & 0xFFFF
        self._last_capture_seq = sequence
        if expected is None or sequence == expected:
            return
        delta = (sequence - expected) & 0xFFFF
        self._capture_out_of_order += 1
        self.counters.bump("frames_out_of_order")
        self.metrics.increment("ws_capture_out_of_order")
        if delta > 0 and delta < 0x8000:
            self.metrics.observe("ws_capture_gap_frames", float(delta))
            logger.warning(
                "ws device=%s capture gap: %d frame(s) missing (seq %d -> %d)",
                self.device_id,
                delta,
                expected - 1 if expected else 0,
                sequence,
            )
        else:
            self.metrics.increment("ws_capture_duplicate_or_reordered")
            logger.warning(
                "ws device=%s capture reordered/duplicated (seq %d -> %d)",
                self.device_id,
                previous,
                sequence,
            )

    async def _handle_binary(self, data: bytes) -> None:
        try:
            frame = decode_audio(data)
        except ProtocolError as exc:
            self._notify("protocol_error", str(exc))
            return
        if frame.kind != AUDIO_KIND_CAPTURE:
            self._notify("protocol_error", "فریم ورودی باید kind=capture داشته باشد.")
            return
        if frame.samples % CAPTURE_BLOCK_SAMPLES:
            # A 100 ms block would make VAD endpointing 5x too slow because the
            # detector counts frames at a fixed 20 ms.
            self.counters.bump("frames_bad_size")
            self.metrics.increment("ws_capture_bad_size")
            self._notify(
                "protocol_error",
                f"اندازهٔ فریم باید مضربی از {CAPTURE_BLOCK_SAMPLES} نمونه (۲۰ms) باشد؛ "
                f"{frame.samples} نمونه دریافت شد.",
            )
            return
        if not self._capture_rate_ok():
            self.counters.bump("frames_rate_limited")
            self.metrics.increment("ws_capture_rate_limited")
            return
        self.counters.bump("frames_in")
        self.metrics.increment("ws_frames_in")
        self._track_capture_sequence(frame.sequence)
        if self.pipeline is not None:
            # Re-chunk at the pipeline's block size; one pipeline block is
            # exactly 20 ms, and the VAD's timers assume that.
            for start in range(0, len(frame.pcm), CAPTURE_BLOCK_BYTES):
                self.pipeline.feed(frame.pcm[start : start + CAPTURE_BLOCK_BYTES])

    def _capture_rate_ok(self) -> bool:
        """Reject a device that streams far faster than 16 kHz / 320 samples.

        A correct device sends 50 frames per second.  ``PVAI_WS_FRAME_RATE``
        caps that so a buggy or hostile firmware cannot saturate the CPU with
        audio the pipeline will only discard, and so the backlog that makes
        endpointing late cannot build up.  The allowance is generous (4x the
        nominal rate by default) and bursty: a WiFi stall that delivers 50
        frames at once must not be throttled.
        """
        limit = self.settings.ws_frame_rate_per_s
        if limit <= 0:
            return True
        now = time.monotonic()
        window = 1.0
        # Sliding-window count: drop samples older than one second before appending
        # so a flood cannot grow _frame_stamps without bound.
        stamps = self._frame_stamps
        cutoff = now - window
        while stamps and stamps[0] < cutoff:
            stamps.popleft()
        if len(stamps) >= max(1.0, limit):
            return False
        stamps.append(now)
        return True

    async def _heartbeat(self) -> None:
        """Ping an otherwise silent connection; never let it die unnoticed.

        A device that vanishes (WiFi drop, battery) sends nothing, so the
        receive loop waits forever and only a TCP timeout — minutes — reclaims
        the connection *and its models*.  Closing on idle keeps the slot
        available and makes the failure visible in the log.
        """
        interval = self.settings.ws_heartbeat_s
        idle_limit = self.settings.ws_idle_timeout_s
        if interval <= 0 and idle_limit <= 0:
            return
        try:
            while True:
                await asyncio.sleep(max(0.05, interval or idle_limit))
                now = time.monotonic()
                if idle_limit > 0 and now - self._last_activity > idle_limit:
                    logger.warning(
                        "ws device=%s idle for %.1fs; closing",
                        self.device_id,
                        now - self._last_activity,
                    )
                    self.metrics.increment("ws_idle_timeouts")
                    self._closing_reason = "idle_timeout"
                    await self.websocket.close(code=CLOSE_GOING_AWAY)
                    # Closing is not enough: a peer that vanished cannot answer
                    # and will not send the disconnect that ``receive()`` is
                    # waiting for, so the loop would hang until TCP gives up
                    # anyway.  Cancel it explicitly.
                    task = self._serve_task
                    if task is not None:
                        task.cancel()
                    return
                if interval > 0:
                    self._queue_control(
                        ControlMessage(
                            MessageType.PING,
                            {"nonce": f"hb-{int(now * 1000) % 100000}"},
                        )
                    )
                    self.metrics.increment("ws_heartbeats")
        except asyncio.CancelledError:
            raise

    async def serve(self) -> None:
        await self.websocket.accept()
        self._serve_task = asyncio.current_task()
        heartbeat: asyncio.Task[None] | None = None
        try:
            try:
                accepted = await self._handshake()
            finally:
                if self.handshake_done is not None:
                    self.handshake_done()
            if not accepted:
                return
            if self.claim_capacity is not None:
                ok, reason = self.claim_capacity()
                if not ok:
                    self.counters.bump("rejected_capacity")
                    await self._reject(
                        reason,
                        "تعداد اتصال‌های هم‌زمان دستگاه‌ها به سقف رسیده است؛ بعداً تلاش کنید."
                        if reason == "too_many_connections" else
                        "نرخ اتصال بیش از حد مجاز است؛ کمی صبر کنید.",
                        CLOSE_TRY_AGAIN if reason == "connection_rate_limited" else CLOSE_UNAVAILABLE,
                    )
                    return
                self.capacity_owned = True
            if not await self._open_pipeline():
                return
            heartbeat = asyncio.create_task(self._heartbeat())
            while True:
                message = await self.websocket.receive()
                self._last_activity = time.monotonic()
                if message.get("type") == "websocket.disconnect":
                    break
                if message.get("bytes") is not None:
                    await self._handle_binary(message["bytes"])
                elif message.get("text") is not None:
                    await self._handle_control(message["text"])
        except asyncio.CancelledError:
            if self._closing_reason != "idle_timeout":
                raise
            # Our own heartbeat cancelled the loop because the device went
            # silent: that is a normal end of session, already logged.
            logger.info(
                "ws device=%s closed after idle timeout", self.device_id
            )
        except Exception:
            # Never swallow silently: a transport that loses every connection
            # with no log line is unfixable in the field.
            logger.exception(
                "ws device=%s session=%s failed", self.device_id, self.session_id
            )
            self.metrics.increment("ws_session_errors")
        finally:
            if heartbeat is not None:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
            # Also runs when the handshake or the bundle failed: a half-built
            # bundle (audio stream, HTTP client) must never outlive the session.
            await self.close()

    async def close(self) -> None:
        """Tear the session down; never leaves the session id locked out.

        Order matters, and it is the opposite of the obvious one:

        1. commit the reply that was in flight (synchronous, so the JSONL is
           final before anyone else may load it);
        2. release *both* claims — the OS lease on ``<id>.lock`` first, then the
           in-process registry — with no ``await`` in between;
        3. only then close the sink, the pipeline and the HTTP client, which
           can take seconds (Whisper/Piper teardown) or fail.

        Releasing the lease at the end of step 3 (the previous order) is what
        produced ``session_in_use`` on a device that reconnected while the old
        pipeline was still shutting down; doing it after a teardown that raised
        locked the id out for the life of the process.
        """
        finalize = getattr(self.pipeline, "finalize_pending_turn", None)
        if callable(finalize):
            try:
                # Synchronous on purpose: the JSONL must be final before the
                # lock is dropped, or a reconnect could load a file that is
                # still one record short.
                finalize()
            except Exception:  # a broken pipeline must not keep the lock
                logger.exception(
                    "ws device=%s final turn commit failed", self.device_id
                )
        self._release_capacity()
        self._release_claims()
        try:
            self.sink.close()
            self._frame_stamps.clear()
            self._overflow.clear()
        except Exception:  # cleanup must not stop the rest of cleanup
            logger.exception("ws device=%s sink teardown failed", self.device_id)
        try:
            if self._send_task is not None:
                self._send_task.cancel()
                await asyncio.gather(self._send_task, return_exceptions=True)
                self._send_task = None
            if self.pipeline is not None:
                await asyncio.gather(self.pipeline.close(), return_exceptions=True)
                self.pipeline = None
            if self.bundle is not None:
                await asyncio.gather(self.bundle.aclose(), return_exceptions=True)
                self.bundle = None
        finally:
            self._release_claims()
            self._release_capacity()
        if self.authenticated:
            self.counters.bump("active", -1)
        logger.info("ws device=%s session=%s closed", self.device_id, self.session_id)

    def _release_claims(self) -> None:
        """Release the OS lease *and* the registry claim, in that order.

        The registry is the gate a new connection checks (``_open_pipeline``
        claims it before the lease), so it may only say "free" once the lock is
        really gone.  This single entry point exists because the belt-and-braces
        release in :func:`build_router` used to free the registry on its own
        when a connection was cancelled hard — leaving the file locked but the
        session looking available, which is exactly the ``session_in_use`` a
        reconnecting device saw.
        """
        self._release_lease()
        self._release_session()

    def _release_capacity(self) -> None:
        """Return this connection's slot to the limiter, exactly once.

        Same reasoning as the session lock: the slot is not part of the audio
        pipeline.  Holding it until model teardown finished meant a device that
        reconnected quickly was answered ``too_many_connections`` even though
        ``PVAI_WS_MAX_CONNECTIONS`` was never actually exceeded.
        """
        if not self.capacity_owned:
            return
        self.capacity_owned = False
        release = self.release_capacity
        if release is not None:
            try:
                release()
            except Exception:  # pragma: no cover - defensive
                logger.exception("ws device=%s capacity release failed", self.device_id)

    def _release_lease(self) -> None:
        """Drop the OS lease on this session's JSONL, keeping the object.

        The conversation object stays attached to the pipeline (it is closed
        again, harmlessly, by ``Pipeline.close``) but the *lock* is gone, so a
        reconnect can resume the session immediately.
        """
        conversation = self._leased_conversation
        if conversation is None:
            return
        closer = getattr(conversation, "release_lease", None)
        try:
            if callable(closer):
                closer()
            else:  # a stub conversation in a test
                conversation.close()
        except Exception:  # pragma: no cover - defensive, close() is total
            logger.exception("ws device=%s lease release failed", self.device_id)

    def _release_session(self) -> None:
        if self._owned_session is not None and self.session_registry is not None:
            self.session_registry.release(self._owned_session, self._session_owner)
        self._owned_session = None


def build_router(
    settings: Settings,
    *,
    bundle_factory: Callable[[Settings, PlaybackSink, Metrics], VoiceBundle] = default_bundle,
    counters: WsCounters | None = None,
):
    """Create the FastAPI router for ``/ws/voice``."""
    router = APIRouter()
    shared = counters or WsCounters()

    @router.websocket("/ws/voice")
    async def voice_socket(websocket: WebSocket) -> None:
        active_settings = websocket.app.state.settings
        peer = websocket.client.host if websocket.client else "unknown"
        if not shared.handshake_enter(peer):
            await websocket.accept()
            await websocket.send_text(ControlMessage(MessageType.ERROR, {
                "code": "handshake_rate_limited", "detail": "درخواست‌های احراز هویت بیش از حد مجاز است."
            }).encode())
            await websocket.close(code=CLOSE_TRY_AGAIN)
            return
        held = True
        def leave_handshake() -> None:
            nonlocal held
            if held:
                shared.handshake_leave(peer)
                held = False

        limiter = shared.limiter(active_settings)
        session = VoiceWebSocket(
            websocket,
            active_settings,
            bundle_factory=bundle_factory,
            counters=shared,
            session_registry=shared.registry,
        )
        session.claim_capacity = limiter.acquire
        session.release_capacity = limiter.release
        session.handshake_done = leave_handshake
        try:
            await session.serve()
        finally:
            # Belt and braces: if the task is cancelled hard enough that
            # ``serve``'s finally never runs (TestClient teardown, server
            # shutdown), the session claim must still be released or the id is
            # locked out for the life of the process.
            session._release_claims()
            session._release_capacity()
            leave_handshake()
            if session.capacity_owned:  # belt and braces; normally already free
                limiter.release()
                session.capacity_owned = False

    router.counters = shared  # type: ignore[attr-defined]
    return router
