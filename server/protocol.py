"""ESP32-S3 ↔ PC audio protocol: wire format definitions and validation.

**This is a design artifact, not a server.**  The PC voice engine must pass its
acceptance tests first; the transport (WebSocket endpoint, device registry,
reconnect loop) is deliberately not implemented yet.  What *is* implemented here
is the part that must be exact before firmware work starts: the binary audio
framing, the control-message schema, sequence numbers, and authentication rules.
These are pure functions with unit tests, so the firmware and the future server
cannot drift apart.

Transport (target): private LAN WebSocket, binary audio frames, JSON text
control messages, optional TLS, bearer device token.

Binary audio frame (10-byte header, big endian)::

    0      version        uint8   = 1
    1      kind           uint8   1 = capture (device→PC), 2 = playback (PC→device)
    2..3   sequence       uint16  per-stream, wraps at 65536
    4..5   samples        uint16  number of int16 samples that follow
    6..9   timestamp_ms   uint32  device monotonic clock, milliseconds
    10..   payload        int16 little-endian mono PCM @ 16 kHz

Control messages are JSON objects with a mandatory ``type`` field and an
optional ``seq`` echo.  See :class:`MessageType` for the allowed types and
``docs/ESP32_PROTOCOL.md`` for the full description and state machine.
"""

from __future__ import annotations

import hmac
import json
import struct
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable

PROTOCOL_VERSION = 1
MAX_CONTROL_BYTES = 8192
MAX_AUDIO_SAMPLES = 16_000  # one second at 16 kHz: a hard upper bound per frame
AUDIO_SAMPLE_RATE = 16_000
AUDIO_CHANNELS = 1
AUDIO_BITS = 16

AUDIO_KIND_CAPTURE = 1
AUDIO_KIND_PLAYBACK = 2
AUDIO_KINDS = {AUDIO_KIND_CAPTURE: "capture", AUDIO_KIND_PLAYBACK: "playback"}

AUDIO_HEADER = struct.Struct(">BBHHI")


class ProtocolError(ValueError):
    """Malformed frame or control message; safe to report to the device."""


class MessageType(str, Enum):
    HELLO = "hello"
    HELLO_OK = "hello_ok"
    SESSION_START = "session_start"
    SESSION_ACK = "session_ack"
    SPEECH_START = "speech_start"
    SPEECH_END = "speech_end"
    INTERRUPT = "interrupt"
    PLAY_START = "play_start"
    PLAY_END = "play_end"
    PLAY_STOP = "play_stop"
    SET_VOICE = "set_voice"
    ASR_PARTIAL = "asr_partial"
    ASR_FINAL = "asr_final"
    PING = "ping"
    PONG = "pong"
    ERROR = "error"


REQUIRED_FIELDS: dict[MessageType, tuple[str, ...]] = {
    MessageType.HELLO: ("device_id", "token"),
    MessageType.HELLO_OK: ("session_id",),
    MessageType.SESSION_START: ("session_id",),
    MessageType.SESSION_ACK: ("session_id",),
    MessageType.SPEECH_START: ("utterance_id",),
    MessageType.SPEECH_END: ("utterance_id", "reason"),
    MessageType.INTERRUPT: ("reason",),
    MessageType.PLAY_START: ("turn_id", "sample_rate"),
    MessageType.PLAY_END: ("turn_id", "played_ms"),
    MessageType.PLAY_STOP: ("turn_id", "reason"),
    MessageType.SET_VOICE: ("voice",),
    MessageType.ASR_PARTIAL: ("utterance_id", "text"),
    MessageType.ASR_FINAL: ("utterance_id", "text"),
    MessageType.PING: ("nonce",),
    MessageType.PONG: ("nonce",),
    MessageType.ERROR: ("code", "detail"),
}

#: Messages a device may send before a successful ``hello`` handshake.
PRE_AUTH_ALLOWED = frozenset({MessageType.HELLO})
#: Messages the *server* may send before the handshake (error/reject only).
SERVER_PRE_AUTH_ALLOWED = frozenset({MessageType.ERROR})

SPEECH_END_REASONS = frozenset({"endpoint", "max_segment", "forced", "overflow", "device"})
INTERRUPT_REASONS = frozenset({"user_speech", "manual", "timeout", "shutdown", "superseded"})
PLAY_STOP_REASONS = frozenset({"barge_in", "new_turn", "shutdown", "error"})
VALID_VOICES = frozenset({"male", "female"})


@dataclass(slots=True)
class AudioFrame:
    """One binary audio frame in either direction."""

    kind: int
    sequence: int
    samples: int
    timestamp_ms: int
    pcm: bytes = field(repr=False)

    @property
    def duration_ms(self) -> float:
        return self.samples * 1000.0 / AUDIO_SAMPLE_RATE

    @property
    def direction(self) -> str:
        return AUDIO_KINDS.get(self.kind, "unknown")

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "direction": self.direction,
            "sequence": self.sequence,
            "samples": self.samples,
            "timestamp_ms": self.timestamp_ms,
            "duration_ms": round(self.duration_ms, 2),
        }


def encode_audio(
    kind: int,
    sequence: int,
    timestamp_ms: int,
    pcm: bytes,
    *,
    samples: int | None = None,
) -> bytes:
    """Build a binary audio frame; validates every field."""
    if kind not in AUDIO_KINDS:
        raise ProtocolError(f"unknown audio kind: {kind}")
    if not 0 <= sequence <= 0xFFFF:
        raise ProtocolError("audio sequence must fit in uint16")
    if not 0 <= timestamp_ms <= 0xFFFFFFFF:
        raise ProtocolError("audio timestamp must fit in uint32")
    if len(pcm) % 2:
        raise ProtocolError("PCM payload must contain whole int16 samples")
    count = samples if samples is not None else len(pcm) // 2
    if count != len(pcm) // 2:
        raise ProtocolError("declared sample count does not match payload length")
    if not 0 < count <= MAX_AUDIO_SAMPLES:
        raise ProtocolError(f"audio frame must contain 1..{MAX_AUDIO_SAMPLES} samples")
    header = AUDIO_HEADER.pack(PROTOCOL_VERSION, kind, sequence, count, timestamp_ms)
    return header + pcm


def decode_audio(data: bytes) -> AudioFrame:
    """Parse and validate a binary audio frame."""
    if len(data) < AUDIO_HEADER.size:
        raise ProtocolError("audio frame shorter than the 10-byte header")
    version, kind, sequence, count, timestamp_ms = AUDIO_HEADER.unpack_from(data)
    if version != PROTOCOL_VERSION:
        raise ProtocolError(f"unsupported audio frame version: {version}")
    if kind not in AUDIO_KINDS:
        raise ProtocolError(f"unknown audio kind: {kind}")
    if not 0 < count <= MAX_AUDIO_SAMPLES:
        raise ProtocolError("audio frame sample count out of range")
    pcm = data[AUDIO_HEADER.size :]
    if len(pcm) != count * 2:
        raise ProtocolError("audio frame payload length does not match the header")
    return AudioFrame(kind, sequence, count, timestamp_ms, pcm)


@dataclass(slots=True)
class ControlMessage:
    """A validated JSON control message."""

    type: MessageType
    payload: dict[str, Any] = field(default_factory=dict)
    seq: int | None = None

    def get(self, name: str, default: Any = None) -> Any:
        return self.payload.get(name, default)

    def require(self, *names: str) -> None:
        missing = [name for name in names if name not in self.payload]
        if missing:
            raise ProtocolError(f"{self.type.value}: missing field(s) {', '.join(missing)}")

    def encode(self) -> str:
        body: dict[str, Any] = {"type": self.type.value, **self.payload}
        if self.seq is not None:
            body["seq"] = self.seq
        return json.dumps(body, ensure_ascii=False, separators=(",", ":"))

    def as_dict(self) -> dict[str, Any]:
        return {"type": self.type.value, "seq": self.seq, **self.payload}

    @classmethod
    def decode(cls, text: str | bytes) -> "ControlMessage":
        if isinstance(text, bytes):
            raw = text
        else:
            raw = text.encode("utf-8")
        if len(raw) > MAX_CONTROL_BYTES:
            raise ProtocolError("control message too large")
        try:
            body = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ProtocolError("control message is not valid JSON") from exc
        if not isinstance(body, dict):
            raise ProtocolError("control message must be a JSON object")
        kind = body.get("type")
        if not isinstance(kind, str):
            raise ProtocolError("control message requires a string 'type'")
        try:
            message_type = MessageType(kind)
        except ValueError as exc:
            raise ProtocolError(f"unknown control message type: {kind}") from exc
        seq = body.pop("seq", None)
        if seq is not None and not isinstance(seq, int):
            raise ProtocolError("'seq' must be an integer")
        body.pop("type", None)
        message = cls(message_type, body, seq)
        message.require(*REQUIRED_FIELDS[message_type])
        message.validate()
        return message

    def validate(self) -> None:
        """Type/value validation specific to a message."""
        if self.type in (MessageType.ASR_PARTIAL, MessageType.ASR_FINAL):
            text = self.payload.get("text")
            if not isinstance(text, str) or len(text) > 4000:
                raise ProtocolError("transcript text must be a string under 4000 characters")
        if self.type is MessageType.SPEECH_END:
            if self.payload.get("reason") not in SPEECH_END_REASONS:
                raise ProtocolError("invalid speech_end reason")
        if self.type is MessageType.INTERRUPT:
            if self.payload.get("reason") not in INTERRUPT_REASONS:
                raise ProtocolError("invalid interrupt reason")
        if self.type is MessageType.PLAY_STOP:
            if self.payload.get("reason") not in PLAY_STOP_REASONS:
                raise ProtocolError("invalid play_stop reason")
        if self.type is MessageType.SET_VOICE:
            if self.payload.get("voice") not in VALID_VOICES:
                raise ProtocolError("voice must be 'male' or 'female'")
        if self.type is MessageType.HELLO:
            token = self.payload.get("token")
            device = self.payload.get("device_id")
            if not isinstance(token, str) or not token:
                raise ProtocolError("hello requires a device token")
            if not isinstance(device, str) or not 1 <= len(device) <= 64:
                raise ProtocolError("hello requires a device_id of 1..64 characters")
        if self.type is MessageType.SPEECH_START:
            if not isinstance(self.payload.get("utterance_id"), int):
                raise ProtocolError("utterance_id must be an integer")


def require_authenticated(message: ControlMessage, *, authenticated: bool) -> None:
    """Reject session messages before a successful handshake."""
    if authenticated:
        return
    if message.type not in PRE_AUTH_ALLOWED:
        raise ProtocolError("handshake required before session messages")


def server_message_allowed(message: ControlMessage, *, authenticated: bool) -> None:
    if authenticated:
        return
    if message.type not in SERVER_PRE_AUTH_ALLOWED:
        raise ProtocolError("server must not start a session before the handshake")


def check_token(provided: str | None, expected: str | None) -> bool:
    """Constant-time device-token comparison.

    If ``expected`` is empty, authentication is **disabled** — which is only
    acceptable on a trusted loopback interface.  The live API must never be
    exposed to the public internet, and ``docs/ESP32_PROTOCOL.md`` says so.
    """
    if not expected:
        return True
    if not provided:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def session_hello(
    device_id: str,
    token: str,
    *,
    firmware: str = "",
    seq: int | None = None,
) -> ControlMessage:
    payload: dict[str, Any] = {
        "device_id": device_id,
        "token": token,
        "protocol": PROTOCOL_VERSION,
    }
    if firmware:
        payload["firmware"] = firmware
    return ControlMessage(MessageType.HELLO, payload, seq)


def hello_ok(session_id: str, *, server_time_ms: int, seq: int | None = None) -> ControlMessage:
    return ControlMessage(
        MessageType.HELLO_OK,
        {
            "session_id": session_id,
            "protocol": PROTOCOL_VERSION,
            "sample_rate": AUDIO_SAMPLE_RATE,
            "channels": AUDIO_CHANNELS,
            "bits": AUDIO_BITS,
            "server_time_ms": server_time_ms,
        },
        seq,
    )


def describe() -> dict[str, Any]:
    """Machine-readable protocol summary (also served by ``/api/protocol``)."""
    return {
        "protocol_version": PROTOCOL_VERSION,
        "transport": "WebSocket over private LAN (binary audio + JSON control)",
        "audio": {
            "sample_rate": AUDIO_SAMPLE_RATE,
            "channels": AUDIO_CHANNELS,
            "bits": AUDIO_BITS,
            "frame_header_bytes": AUDIO_HEADER.size,
            "max_samples_per_frame": MAX_AUDIO_SAMPLES,
        },
        "control_types": [kind.value for kind in MessageType],
        "implemented": False,
        "notes": [
            "This module defines and validates the wire format only.",
            "No WebSocket transport, device registry or firmware exists yet.",
        ],
    }


def missing_types(types: Iterable[MessageType]) -> list[str]:
    """Helper for tests: control types without a declared required-field rule."""
    return sorted(kind.value for kind in types if kind not in REQUIRED_FIELDS)
