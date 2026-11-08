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
  ``session_start``) when ``PVAI_HISTORY_PERSIST`` is on.
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
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import APIRouter, WebSocket

from .config import Settings
from .conversation import create_conversation
from .metrics import Metrics
from .protocol import (
    AUDIO_KIND_CAPTURE,
    AUDIO_KIND_PLAYBACK,
    MAX_AUDIO_SAMPLES,
    MessageType,
    ProtocolError,
    ControlMessage,
    check_token,
    decode_audio,
    encode_audio,
    hello_ok,
    require_authenticated,
)
from .streaming_voice import Pipeline

logger = logging.getLogger("persian_voice_ai.ws")

CLOSE_UNAUTHORIZED = 1008
CLOSE_UNAVAILABLE = 1011
HANDSHAKE_TIMEOUT_S = 10.0
PLAYBACK_FRAME_BYTES = 640  # 20 ms of PCM16 mono at 16 kHz
SINK_QUEUE_FRAMES = 250  # ~5 s of playback backpressure before synthesis blocks

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


def default_bundle(settings: Settings, sink: PlaybackSink, metrics: Metrics) -> VoiceBundle:
    """Build the real (local-model) pipeline dependencies for one connection."""
    import httpx

    from .live_voice import Speaker
    from .voice import LocalAudio, local_api_url

    audio = LocalAudio(settings, metrics=metrics)
    speakers: dict[str, Any] = {}

    def speaker_for(voice: str | None) -> Any:
        key = (voice or settings.tts_voice or "female").strip().lower()
        if key not in speakers:
            speakers[key] = Speaker(settings, duplex=sink, voice=key)
        return speakers[key]

    client = httpx.AsyncClient(
        base_url=local_api_url(settings),
        timeout=settings.request_timeout_s,
        trust_env=False,
        follow_redirects=False,
    )
    return VoiceBundle(audio=audio, client=client, speaker_for=speaker_for)


class WsCounters:
    """Process-wide counters for the transport (cheap, bounded, observable)."""

    def __init__(self) -> None:
        self.active = 0
        self.total = 0
        self.rejected = 0
        self.frames_in = 0
        self.frames_out = 0
        self.lock = threading.Lock()

    def bump(self, name: str, amount: int = 1) -> None:
        with self.lock:
            setattr(self, name, getattr(self, name) + amount)

    def snapshot(self) -> dict[str, int]:
        with self.lock:
            return {
                "active": self.active,
                "total": self.total,
                "rejected": self.rejected,
                "frames_in": self.frames_in,
                "frames_out": self.frames_out,
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
    ) -> None:
        self.websocket = websocket
        self.settings = settings
        self.bundle_factory = bundle_factory
        self.counters = counters or WsCounters()
        self.metrics = metrics or Metrics(enabled=settings.metrics_enabled)
        self.sink = PlaybackSink()
        self.pipeline: Pipeline | None = None
        self.bundle: VoiceBundle | None = None
        self.session_id: str | None = None
        self.device_id: str = ""
        self.voice: str = settings.tts_voice
        self.authenticated = False
        self.playback_sequence = 0
        # Playback accounting for ``play_end``: frames actually handed to this
        # device, measured per turn (a turn starts where the previous one ended).
        self.frames_sent = 0
        self._written_at_turn_start = 0
        self.outgoing: asyncio.Queue[str] = asyncio.Queue(maxsize=64)
        self._send_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------ output
    def _queue_control(self, message: ControlMessage) -> None:
        try:
            self.outgoing.put_nowait(message.encode())
        except asyncio.QueueFull:  # a slow device loses control chatter, not audio
            self.counters.bump("rejected")

    def _notify(self, code: str, detail: str) -> None:
        self._queue_control(
            ControlMessage(MessageType.ERROR, {"code": code, "detail": detail})
        )

    def _take_played_ms(self) -> float:
        """Milliseconds of audio queued for this turn since the last boundary.

        Counted from the sink (everything synthesis produced), not from the
        sender: the pipeline thread answers ``response_end`` before the sender
        has necessarily pushed the last blocks onto the wire, and the sender
        guarantees the frames are sent before the ``play_end`` that reports them.
        """
        written = self.sink.written
        frames = written - self._written_at_turn_start
        self._written_at_turn_start = written
        return max(0, frames) * 20.0  # one sink block == 20 ms of audio

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
            self._queue_control(
                ControlMessage(
                    MessageType.PLAY_START,
                    {
                        "turn_id": int(event.get("turn_id") or 0),
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
                played_ms = self._take_played_ms()
                self._queue_control(
                    ControlMessage(
                        MessageType.PLAY_END,
                        {"turn_id": turn_id, "played_ms": round(played_ms, 1)},
                    )
                )
        elif kind == "interrupted":
            self.sink.drain()  # nothing from the cancelled turn may be sent later
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

    async def _send_playback_frame(self) -> bool:
        """Send one queued 20 ms block; ``False`` when the queue is empty."""
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
                if pending is None:
                    try:
                        pending = self.outgoing.get_nowait()
                    except asyncio.QueueEmpty:
                        pending = None
                if pending is not None:
                    if _message_type(pending) == MessageType.PLAY_END.value and not self.sink.frames.empty():
                        if await self._send_playback_frame():
                            continue
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
    async def _handshake(self) -> bool:
        expected = getattr(self.settings, "ws_token", "") or ""
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
        if not expected:
            self.counters.bump("rejected")
            self.metrics.increment("ws_auth_failures")
            await self._reject(
                "auth_not_configured",
                "احراز هویت دستگاه تنظیم نشده است؛ PVAI_WS_TOKEN را در .env قرار دهید. "
                "این API بدون توکن عمداً بسته است.",
            )
            return False
        if not check_token(message.payload.get("token"), expected):
            self.counters.bump("rejected")
            self.metrics.increment("ws_auth_failures")
            await self._reject("auth_failed", "توکن دستگاه نامعتبر است.")
            return False

        self.device_id = str(message.payload.get("device_id") or "")
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

    @staticmethod
    def _validate_session_id(session_id: str) -> None:
        from .conversation import ConversationStore

        ConversationStore.sanitize(session_id)

    async def _open_pipeline(self) -> bool:
        conversation = create_conversation(self.settings, session_id=self.session_id)
        self.session_id = conversation.session_id
        try:
            self.bundle = self.bundle_factory(self.settings, self.sink, self.metrics)
            speaker = self.bundle.speaker_for(self.voice)
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
                voice = str(message.payload.get("voice"))
                try:
                    self.pipeline.speaker = self.bundle.speaker_for(voice)
                except Exception as exc:  # missing weights for that voice
                    logger.warning("ws set_voice failed for %s: %s", voice, exc)
                    self._notify(
                        "voice_unavailable",
                        f"مدل صدای «{voice}» آماده نیست؛ PVAI_TTS_MODEL_MALE/FEMALE را بررسی کنید.",
                    )
                else:
                    self.voice = voice  # applies from the next sentence onwards
                    logger.info("ws device=%s voice=%s", self.device_id, voice)
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

    async def _handle_binary(self, data: bytes) -> None:
        try:
            frame = decode_audio(data)
        except ProtocolError as exc:
            self._notify("protocol_error", str(exc))
            return
        if frame.kind != AUDIO_KIND_CAPTURE:
            self._notify("protocol_error", "فریم ورودی باید kind=capture داشته باشد.")
            return
        if frame.samples > MAX_AUDIO_SAMPLES:
            self._notify("protocol_error", "فریم صوتی بزرگ‌تر از حد مجاز است.")
            return
        self.counters.bump("frames_in")
        self.metrics.increment("ws_frames_in")
        if self.pipeline is not None:
            self.pipeline.feed(frame.pcm)

    async def serve(self) -> None:
        await self.websocket.accept()
        try:
            if not await self._handshake():
                return
            if not await self._open_pipeline():
                return
            while True:
                message = await self.websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                if message.get("bytes") is not None:
                    await self._handle_binary(message["bytes"])
                elif message.get("text") is not None:
                    await self._handle_control(message["text"])
        except Exception:  # client vanished mid-frame
            pass
        finally:
            # Also runs when the handshake or the bundle failed: a half-built
            # bundle (audio stream, HTTP client) must never outlive the session.
            await self.close()

    async def close(self) -> None:
        self.sink.close()
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
        if self.authenticated:
            self.counters.bump("active", -1)
        logger.info("ws device=%s session=%s closed", self.device_id, self.session_id)


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
        await VoiceWebSocket(
            websocket,
            websocket.app.state.settings,
            bundle_factory=bundle_factory,
            counters=shared,
        ).serve()

    router.counters = shared  # type: ignore[attr-defined]
    return router
