"""ESP32 wire-protocol definitions: framing, validation and authentication rules."""

from __future__ import annotations

import json

import pytest

from server.protocol import (
    AUDIO_HEADER,
    AUDIO_KIND_CAPTURE,
    AUDIO_KIND_PLAYBACK,
    MAX_CONTROL_BYTES,
    ControlMessage,
    MessageType,
    ProtocolError,
    check_token,
    decode_audio,
    describe,
    encode_audio,
    hello_ok,
    missing_types,
    require_authenticated,
    server_message_allowed,
    session_hello,
)


def test_audio_frame_round_trip() -> None:
    pcm = b"\x01\x00" * 320
    frame = encode_audio(AUDIO_KIND_CAPTURE, 7, 1234, pcm)
    assert len(frame) == AUDIO_HEADER.size + len(pcm)
    decoded = decode_audio(frame)
    assert decoded.kind == AUDIO_KIND_CAPTURE
    assert decoded.sequence == 7
    assert decoded.samples == 320
    assert decoded.timestamp_ms == 1234
    assert decoded.pcm == pcm
    assert decoded.duration_ms == pytest.approx(20.0)
    assert decoded.direction == "capture"
    assert decoded.as_dict()["sequence"] == 7


@pytest.mark.parametrize(
    "arguments",
    [
        {"kind": 9, "sequence": 0, "timestamp_ms": 0, "pcm": b"\x00\x00"},
        {"kind": AUDIO_KIND_CAPTURE, "sequence": 70000, "timestamp_ms": 0, "pcm": b"\x00\x00"},
        {"kind": AUDIO_KIND_CAPTURE, "sequence": 0, "timestamp_ms": -1, "pcm": b"\x00\x00"},
        {"kind": AUDIO_KIND_CAPTURE, "sequence": 0, "timestamp_ms": 0, "pcm": b"\x00"},
        {"kind": AUDIO_KIND_CAPTURE, "sequence": 0, "timestamp_ms": 0, "pcm": b""},
    ],
)
def test_encode_audio_rejects_invalid_fields(arguments) -> None:
    with pytest.raises(ProtocolError):
        encode_audio(**arguments)


def test_decode_audio_rejects_truncated_and_mismatched_frames() -> None:
    with pytest.raises(ProtocolError, match="header"):
        decode_audio(b"\x01\x01")
    frame = encode_audio(AUDIO_KIND_PLAYBACK, 1, 0, b"\x00\x00" * 320)
    with pytest.raises(ProtocolError, match="payload"):
        decode_audio(frame[:-2])
    corrupted = bytearray(frame)
    corrupted[0] = 2  # unsupported version
    with pytest.raises(ProtocolError, match="version"):
        decode_audio(bytes(corrupted))


def test_control_message_round_trip() -> None:
    message = ControlMessage(
        MessageType.SPEECH_START, {"utterance_id": 3}, seq=11
    )
    encoded = message.encode()
    assert json.loads(encoded)["type"] == "speech_start"
    decoded = ControlMessage.decode(encoded)
    assert decoded.type is MessageType.SPEECH_START
    assert decoded.get("utterance_id") == 3
    assert decoded.seq == 11


def test_missing_required_fields_are_rejected() -> None:
    with pytest.raises(ProtocolError, match="utterance_id"):
        ControlMessage.decode('{"type": "speech_end", "reason": "endpoint"}')


def test_unknown_type_invalid_json_and_oversize_are_rejected() -> None:
    with pytest.raises(ProtocolError, match="unknown control message type"):
        ControlMessage.decode('{"type": "reboot"}')
    with pytest.raises(ProtocolError, match="JSON"):
        ControlMessage.decode("{not json")
    with pytest.raises(ProtocolError, match="too large"):
        ControlMessage.decode("{" + "x" * (MAX_CONTROL_BYTES + 1) + "}")
    with pytest.raises(ProtocolError, match="string 'type'"):
        ControlMessage.decode('{"type": 5}')
    with pytest.raises(ProtocolError, match="JSON object"):
        ControlMessage.decode("[]")


def test_value_validation_per_message_type() -> None:
    with pytest.raises(ProtocolError, match="speech_end reason"):
        ControlMessage.decode('{"type":"speech_end","utterance_id":1,"reason":"nope"}')
    with pytest.raises(ProtocolError, match="interrupt reason"):
        ControlMessage.decode('{"type":"interrupt","reason":"nope"}')
    with pytest.raises(ProtocolError, match="male"):
        ControlMessage.decode('{"type":"set_voice","voice":"robot"}')
    long_text = json.dumps({"type": "asr_final", "utterance_id": 1, "text": "x" * 4001})
    with pytest.raises(ProtocolError, match="text"):
        ControlMessage.decode(long_text)


def test_every_message_type_has_a_required_field_rule() -> None:
    assert missing_types(MessageType) == []


def test_handshake_and_session_gating() -> None:
    hello = session_hello("esp32-01", "secret", firmware="0.1.0")
    assert hello.type is MessageType.HELLO
    require_authenticated(hello, authenticated=False)  # hello is allowed pre-auth
    with pytest.raises(ProtocolError, match="handshake"):
        require_authenticated(
            ControlMessage(MessageType.SPEECH_START, {"utterance_id": 1}), authenticated=False
        )
    require_authenticated(ControlMessage(MessageType.PING, {"nonce": 1}), authenticated=True)
    with pytest.raises(ProtocolError):
        server_message_allowed(ControlMessage(MessageType.PLAY_START,
                                             {"turn_id": 1, "sample_rate": 16000}),
                               authenticated=False)
    server_message_allowed(ControlMessage(MessageType.ERROR, {"code": "x", "detail": "y"}),
                           authenticated=False)


def test_hello_ok_declares_audio_format() -> None:
    message = hello_ok("s-1", server_time_ms=10)
    assert message.get("sample_rate") == 16000
    assert message.get("channels") == 1
    assert message.get("protocol") == 1


def test_token_comparison() -> None:
    assert check_token("abc", "abc") is True
    assert check_token("abc", "abd") is False
    assert check_token(None, "abc") is False
    assert check_token("anything", "") is True  # authentication explicitly disabled


def test_protocol_description_is_honest_about_implementation_state() -> None:
    description = describe()
    assert description["implemented"] is False
    assert description["audio"]["sample_rate"] == 16000
    assert "hello" in description["control_types"]
    assert any("No WebSocket transport" in note for note in description["notes"])
