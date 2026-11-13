"""Server-side LAN voice transport: auth, sessions, audio and cancellation.

These tests use an in-process WebSocket client (Starlette TestClient) and a fake
voice bundle, so they prove the *transport contract*: framing, authentication,
session isolation, control/event mapping and barge-in.  They say nothing about
real firmware, a real LAN or real echo; that needs `docs/HARDWARE_VALIDATION.md`.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from concurrent.futures import CancelledError
from contextlib import contextmanager
from array import array
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from server.config import Settings
from server.conversation import ConversationStore, create_conversation
from server.protocol import (
    AUDIO_KIND_CAPTURE,
    MessageType,
    ControlMessage,
    decode_audio,
    encode_audio,
    session_hello,
)
from server.ws_voice import PlaybackSink, VoiceBundle

LOUD = 2200
SILENCE = 0


# --------------------------------------------------------------------- helpers
def frame(level: int = LOUD, samples: int = 320) -> bytes:
    return array("h", [level] * samples).tobytes()


def capture_frame(level: int = LOUD, sequence: int = 0) -> bytes:
    return encode_audio(AUDIO_KIND_CAPTURE, sequence, sequence * 20, frame(level))


class NullLlm:
    """Minimal LLM stand-in so app startup/shutdown need no llama-server."""

    async def aclose(self) -> None:
        return None

    async def health(self) -> dict[str, Any]:
        return {"reachable": True, "model_available": True}

    async def list_models(self) -> list[str]:
        return ["test-model"]


class FakeAsr:
    def __init__(self, text: str = "سلام از دستگاه") -> None:
        self.text = text
        self.calls = 0

    def transcribe_pcm(self, pcm: bytes) -> str:
        self.calls += 1
        return self.text


class FakeSpeaker:
    """Writes a short 20 ms block to the sink; cancellable like the real one."""

    def __init__(self) -> None:
        self.spoken: list[str] = []

    def speak(self, text, token, *, on_first_chunk=None, on_first_frame=None, on_complete=None):
        if token.is_set():
            if on_complete is not None:
                on_complete(time.perf_counter(), 0.0)
            return
        self.spoken.append(text)
        if on_first_chunk is not None:
            on_first_chunk(time.perf_counter())
        if on_first_frame is not None:
            on_first_frame(time.perf_counter())
        total = 0.0
        if on_complete is not None:
            on_complete(time.perf_counter(), 60.0)
        return total


class BlockingSpeaker(FakeSpeaker):
    """Keeps the turn alive so a device interrupt can land mid-playback."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()

    def speak(self, text, token, *, on_first_chunk=None, on_first_frame=None, on_complete=None):
        self.spoken.append(text)
        if on_first_chunk is not None:
            on_first_chunk(time.perf_counter())
        if on_first_frame is not None:
            on_first_frame(time.perf_counter())
        self.entered.set()
        token.wait(5.0)
        if on_complete is not None:
            on_complete(time.perf_counter(), 0.0)


def bundle_factory(asr: Any = None, speaker: Any = None):
    def factory(settings: Settings, sink: PlaybackSink, metrics: Any) -> VoiceBundle:
        client = httpx.AsyncClient(
            base_url="http://127.0.0.1",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    text=(
                        'data: {"choices":[{"delta":{"content":"پاسخ دستگاه "}}]}\n\n'
                        'data: [DONE]\n\n'
                    ),
                )
            ),
        )
        return VoiceBundle(
            audio=asr or FakeAsr(),
            client=client,
            speaker_for=lambda voice: speaker or FakeSpeaker(),
        )

    return factory


def settings(tmp_path: Path, *, token: str = "secret-token", voice: str = "female") -> Settings:
    return Settings(
        ws_enabled=True,
        ws_token=token,
        data_dir=str(tmp_path),
        history_persist=False,
        tts_voice=voice,
        voice_asr_interval_s=10.0,
    )


def app_client(tmp_path: Path, *, asr: Any = None, speaker: Any = None, token: str = "secret-token"):
    from server.main import create_app

    app = create_app(
        settings=settings(tmp_path, token=token),
        llm=NullLlm(),
        voice_bundle=bundle_factory(asr, speaker),
    )
    return TestClient(app)


def hello(token: str = "secret-token", device: str = "esp32-test", **extra) -> str:
    message = session_hello(device, token)
    message.payload.update(extra)
    return message.encode()


@contextmanager
def device_session(client: TestClient, path: str = "/ws/voice"):
    """Open one device session and ignore the TestClient's teardown artefact.

    Starlette's TestClient cancels the portal task the moment the block exits,
    which surfaces as ``concurrent.futures.CancelledError`` even when the server
    shut the session down correctly.  Everything inside the block still fails the
    test normally; only the harness's own cancellation is tolerated.
    """
    session = client.websocket_connect(path)
    ws = session.__enter__()
    try:
        yield ws
    except BaseException:
        try:
            session.__exit__(None, None, None)
        except Exception:
            pass
        raise
    else:
        try:
            session.__exit__(None, None, None)
        except CancelledError:
            pass


def recv_json(ws) -> dict[str, Any]:
    return json.loads(ws.receive_text())


def wait_for(ws, kind: str, *, limit: int = 60) -> dict[str, Any]:
    """Receive messages until one has the wanted ``type``."""
    for _ in range(limit):
        message = json.loads(ws.receive_text())
        if message.get("type") == kind:
            return message
    raise AssertionError(f"{kind} not received")


# ------------------------------------------------------------------- handshake
def test_connection_is_refused_without_a_configured_token(tmp_path: Path) -> None:
    """No token configured => the endpoint refuses instead of becoming open."""
    with app_client(tmp_path, token="") as client:
        with pytest.raises(WebSocketDisconnect):
            with device_session(client) as ws:
                # The device does send a token; the *server* has none configured.
                ws.send_text(hello(token="device-token"))
                message = recv_json(ws)
                assert message["type"] == "error"
                assert message["code"] == "auth_not_configured"
                recv_json(ws)  # server closes with 1008 right after the error


def test_wrong_token_is_rejected_and_reports_a_reason(tmp_path: Path) -> None:
    with app_client(tmp_path) as client:
        with device_session(client) as ws:
            ws.send_text(hello(token="wrong"))
            message = recv_json(ws)
            assert message["type"] == "error" and message["code"] == "auth_failed"


def test_first_message_must_be_hello(tmp_path: Path) -> None:
    with app_client(tmp_path) as client:
        with device_session(client) as ws:
            ws.send_text(
                ControlMessage(
                    MessageType.PING, {"nonce": "1"}
                ).encode()
            )
            message = recv_json(ws)
            assert message["type"] == "error" and message["code"] == "handshake_required"


def test_the_hello_deadline_matches_the_documented_ten_seconds() -> None:
    """A TLS-capable ESP32 needs more than 3 s to send its first frame.

    ``docs/PROTOCOL.md`` §۶ promises ``handshake_timeout`` after ten seconds; the
    constant had drifted to 3.0, which closed correctly working devices. Half-open
    sockets are bounded by ``WsCounters.handshake_enter`` instead, so the deadline
    itself can stay generous. Whatever the value, the two must not drift again.
    """
    import server.ws_voice as ws_module

    documented = (Path(__file__).resolve().parents[1] / "docs" / "PROTOCOL.md").read_text(
        encoding="utf-8"
    )
    assert "ده ثانیه" in documented
    assert ws_module.HANDSHAKE_TIMEOUT_S == 10.0


def test_hello_ok_declares_session_and_audio_format(tmp_path: Path) -> None:
    with app_client(tmp_path) as client:
        with device_session(client) as ws:
            ws.send_text(hello())
            message = wait_for(ws, "hello_ok")
            assert message["session_id"]
            assert message["sample_rate"] == 16000
            assert message["channels"] == 1
            assert message["bits"] == 16
            assert message["protocol"] == 1


def test_ping_is_answered_with_the_same_nonce(tmp_path: Path) -> None:
    with app_client(tmp_path) as client:
        with device_session(client) as ws:
            ws.send_text(hello())
            wait_for(ws, "hello_ok")
            ws.send_text(ControlMessage(MessageType.PING, {"nonce": "abc"}).encode())
            pong = wait_for(ws, "pong")
            assert pong["nonce"] == "abc"


# ----------------------------------------------------------------- audio path
def test_capture_audio_reaches_the_pipeline_and_events_map_to_messages(tmp_path: Path) -> None:
    asr = FakeAsr(text="چراغ را روشن کن")
    with app_client(tmp_path, asr=asr) as client:
        with device_session(client) as ws:
            ws.send_text(hello())
            wait_for(ws, "hello_ok")
            for index in range(20):
                ws.send_bytes(capture_frame(LOUD, index))
            for index in range(32):
                ws.send_bytes(capture_frame(SILENCE, index))
            start = wait_for(ws, "speech_start")
            assert start["utterance_id"] >= 1
            final = wait_for(ws, "asr_final", limit=200)
            assert final["text"] == "چراغ را روشن کن"
            assert asr.calls >= 1
            play = wait_for(ws, "play_start", limit=200)
            assert play["turn_id"] >= 1 and play["sample_rate"] == 16000
            end = wait_for(ws, "play_end", limit=200)
            assert end["played_ms"] >= 0


def test_playback_frames_are_valid_protocol_frames(tmp_path: Path) -> None:
    """The device must receive decodable 20 ms playback frames."""
    delivered: list[bytes] = []

    def factory(settings_obj: Settings, sink: PlaybackSink, metrics: Any) -> VoiceBundle:
        def speaker_for(voice):
            speaker = FakeSpeaker()
            play = speaker.speak  # bind before patching: calling speaker.speak here would recurse

            def speak(text, token, *, on_first_chunk=None, on_first_frame=None,
                      on_complete=None):
                play(text, token, on_first_chunk=on_first_chunk,
                     on_first_frame=on_first_frame, on_complete=on_complete)
                for _ in range(3):
                    sink.write(frame(900), token)

            speaker.speak = speak  # type: ignore[method-assign]
            return speaker

        return VoiceBundle(
            audio=FakeAsr("سلام"),
            client=httpx.AsyncClient(
                base_url="http://127.0.0.1",
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200, text='data: {"choices":[{"delta":{"content":"بله. "}}]}\n\ndata: [DONE]\n\n'
                    )
                ),
            ),
            speaker_for=speaker_for,
        )

    from server.main import create_app

    app = create_app(settings=settings(tmp_path), llm=NullLlm(), voice_bundle=factory)
    with TestClient(app) as client:
        with device_session(client) as ws:
            ws.send_text(hello())
            wait_for(ws, "hello_ok")
            for index in range(20):
                ws.send_bytes(capture_frame(LOUD, index))
            for index in range(32):
                ws.send_bytes(capture_frame(SILENCE, index))
            # Read to the end of the turn: leaving mid-stream makes the test
            # client cancel the session task, which is a harness artefact and
            # not something the transport can influence.
            ended = False
            for _ in range(600):
                message = ws.receive()
                if message.get("bytes"):
                    delivered.append(message["bytes"])
                    continue
                payload = json.loads(message["text"])
                if payload["type"] == "play_end":
                    assert payload["played_ms"] >= 60.0, payload
                    ended = True
                    break
            assert ended, "play_end never arrived"
    assert len(delivered) >= 3, "play_end arrived before the playback frames"
    sequences = []
    for raw in delivered[:3]:
        parsed = decode_audio(raw)
        assert parsed.kind == 2 and parsed.samples == 320
        assert len(parsed.pcm) == 640
        sequences.append(parsed.sequence)
    assert sequences == [0, 1, 2]


def test_malformed_audio_frame_is_reported_not_crashing(tmp_path: Path) -> None:
    with app_client(tmp_path) as client:
        with device_session(client) as ws:
            ws.send_text(hello())
            wait_for(ws, "hello_ok")
            ws.send_bytes(b"\x00\x01short")
            message = recv_json(ws)
            assert message["type"] == "error" and message["code"] == "protocol_error"
            # Connection stays usable: a ping still answers.
            ws.send_text(ControlMessage(MessageType.PING, {"nonce": "still-alive"}).encode())
            assert wait_for(ws, "pong")["nonce"] == "still-alive"


# ------------------------------------------------------------ barge-in / state
def test_device_interrupt_cancels_playback_and_reports_play_stop(tmp_path: Path) -> None:
    speaker = BlockingSpeaker()
    with app_client(tmp_path, speaker=speaker) as client:
        with device_session(client) as ws:
            ws.send_text(hello())
            wait_for(ws, "hello_ok")
            for index in range(20):
                ws.send_bytes(capture_frame(LOUD, index))
            for index in range(32):
                ws.send_bytes(capture_frame(SILENCE, index))
            wait_for(ws, "play_start", limit=200)
            assert speaker.entered.wait(2.0)
            ws.send_text(
                ControlMessage(MessageType.INTERRUPT, {"reason": "manual"}).encode()
            )
            stop = wait_for(ws, "play_stop", limit=200)
            assert stop["reason"] in {"barge_in", "new_turn", "shutdown", "error"}
            assert stop["turn_id"] >= 1


def test_session_isolation_between_two_devices(tmp_path: Path) -> None:
    with app_client(tmp_path) as client:
        with device_session(client) as first:
            first.send_text(hello(device="dev-a"))
            session_a = wait_for(first, "hello_ok")["session_id"]
        with device_session(client) as second:
            second.send_text(hello(device="dev-b"))
            session_b = wait_for(second, "hello_ok")["session_id"]
    assert session_a and session_b and session_a != session_b


def test_invalid_session_id_is_rejected(tmp_path: Path) -> None:
    with app_client(tmp_path) as client:
        with device_session(client) as ws:
            ws.send_text(hello(session_id="../etc/passwd"))
            message = recv_json(ws)
            assert message["type"] == "error" and message["code"] == "invalid_session"


def test_unsupported_device_message_is_reported(tmp_path: Path) -> None:
    with app_client(tmp_path) as client:
        with device_session(client) as ws:
            ws.send_text(hello())
            wait_for(ws, "hello_ok")
            ws.send_text(
                ControlMessage(
                    MessageType.PLAY_END, {"turn_id": 1, "played_ms": 20.0}
                ).encode()
            )
            message = recv_json(ws)
            assert message["type"] == "error" and message["code"] == "unsupported_message"


# ------------------------------------------------------------------ transport
def test_cancelled_playback_frames_are_dropped_before_play_stop(tmp_path: Path) -> None:
    """A barge-in must empty the playback queue *before* play_stop is announced.

    Deterministic unit-level check: no real socket, no timing race.  The device
    contract says no frame from the cancelled turn may arrive after play_stop.
    """
    from server.ws_voice import VoiceWebSocket

    sink = PlaybackSink()
    token = threading.Event()
    for _ in range(5):
        sink.write(frame(900), token)
    assert sink.frames.qsize() == 5

    session = VoiceWebSocket(
        websocket=None,  # _on_pipeline_event only queues; it never touches the socket
        settings=settings(tmp_path),
        bundle_factory=bundle_factory(),
    )
    session.sink = sink
    session._on_pipeline_event({"type": "interrupted", "reason": "manual", "turn_id": 7})

    assert sink.frames.empty(), "cancelled frames were left in the playback queue"
    assert sink.dropped == 5
    stop = json.loads(session.outgoing.get_nowait())
    assert stop["type"] == "play_stop"
    assert stop["turn_id"] == 7
    assert stop["reason"] == "barge_in"


def test_one_play_end_per_turn_not_per_sentence(tmp_path: Path) -> None:
    """``play_end`` is a turn boundary: per-sentence mapping told devices a
    multi-sentence reply had finished while audio was still queued."""
    from server.ws_voice import VoiceWebSocket

    session = VoiceWebSocket(websocket=None, settings=settings(tmp_path), bundle_factory=bundle_factory())
    session._on_pipeline_event({"type": "tts_end", "turn_id": 4, "text": "یک."})
    session._on_pipeline_event({"type": "tts_end", "turn_id": 4, "text": "دو."})
    assert session.outgoing.empty(), "tts_end must not close the turn"

    # ``played_ms`` reports audio *sent to the device*, not audio merely queued
    # for synthesis, and with pacing the frames of the turn are still going out
    # when ``response_end`` arrives.  The sender therefore fills the count in at
    # send time; here the turn boundary itself is what is being asserted.
    session.frames_sent = 7  # seven 20 ms frames already reached this device
    session._on_pipeline_event({"type": "response_end", "turn_id": 4, "status": "complete"})
    assert session.outgoing.qsize() == 1
    end = json.loads(session.outgoing.get_nowait())
    assert end["type"] == "play_end" and end["turn_id"] == 4
    assert "played_ms" in end

    # The sender resolves it against frames delivered since the last boundary.
    session._frames_sent_at_turn_start = 0
    session.frames_sent = 7
    finalized = json.loads(session._finalize_play_end(ControlMessage(
        MessageType.PLAY_END, {"turn_id": 4, "played_ms": None, "_final": True}
    ).encode()))
    assert finalized["played_ms"] == 140.0
    assert "_final" not in finalized
    # The boundary has moved: the next turn counts from here.
    session.frames_sent = 9
    again = json.loads(session._finalize_play_end(ControlMessage(
        MessageType.PLAY_END, {"turn_id": 5, "played_ms": None, "_final": True}
    ).encode()))
    assert again["played_ms"] == 40.0

    # An interrupted turn never reports play_end; play_stop already said so.
    session._on_pipeline_event({"type": "response_end", "turn_id": 5, "status": "interrupted"})
    assert session.outgoing.empty()


# ------------------------------------------------------------------- resume
def persisted_app_client(tmp_path: Path, *, asr: Any = None, speaker: Any = None):
    """A device client with local persistence on, so sessions can be resumed."""
    from server.main import create_app

    configured = Settings(
        ws_enabled=True,
        ws_token="secret-token",
        data_dir=str(tmp_path),
        history_persist=True,
        tts_voice="female",
        voice_asr_interval_s=10.0,
    )
    app = create_app(
        settings=configured,
        llm=NullLlm(),
        voice_bundle=bundle_factory(asr, speaker),
    )
    return TestClient(app)


def exchange(ws, *, record_file: Path | None = None, silence: int = 32) -> None:
    """Feed one loud utterance and wait until its reply was recorded.

    A device session is torn down by closing the connection, and the assistant
    turn is committed on that shutdown; when ``record_file`` is given the test
    polls the JSONL for an assistant record instead of racing the socket.
    """
    baseline = _assistant_records(record_file) if record_file is not None else 0
    for index in range(20):
        ws.send_bytes(capture_frame(LOUD, index))
    for index in range(silence):
        ws.send_bytes(capture_frame(SILENCE, index))
    if record_file is None:
        return
    # Wait for *this* exchange, not for any assistant record: a resumed session
    # already has one from the earlier connection, so polling for "an assistant
    # record exists" returned immediately and the caller read the file before
    # the new reply was written -- an intermittent failure that looked like a
    # resume bug.
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if _assistant_records(record_file) > baseline:
            return
        time.sleep(0.01)
    raise AssertionError(
        f"the reply was not recorded in time (assistant records: {baseline})"
    )


def _assistant_records(record_file: Path) -> int:
    """How many assistant replies the store holds right now."""
    try:
        lines = record_file.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return 0
    count = 0
    for line in lines:
        if not line.strip():
            continue
        try:
            if json.loads(line).get("role") == "assistant":
                count += 1
        except ValueError:  # a partially written last line
            continue
    return count


def test_device_resumes_a_session_with_its_earlier_context(tmp_path: Path) -> None:
    """``hello`` with a known ``session_id`` must continue that conversation.

    This is the transport-level regression: ``_open_pipeline`` builds the
    conversation, and before the fix it never loaded the stored JSONL — the
    device got an empty history *and* appended duplicate ids to the file.
    """
    asr = FakeAsr(text="سلام از دستگاه")
    with persisted_app_client(tmp_path, asr=asr) as client:
        with device_session(client) as first:
            first.send_text(hello())
            session_id = wait_for(first, "hello_ok")["session_id"]
            record_file = tmp_path / "conversations" / f"{session_id}.jsonl"
            exchange(first, record_file=record_file)
        # A second connection asks for the same session, as a rebooted device would.
        with device_session(client) as second:
            second.send_text(hello(session_id=session_id))
            assert wait_for(second, "hello_ok")["session_id"] == session_id
            exchange(second, record_file=record_file)

    records = [
        json.loads(line)
        for line in (tmp_path / "conversations" / f"{session_id}.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    ids = [record["id"] for record in records]
    turns = [record["turn_id"] for record in records if record["turn_id"] is not None]
    assert len(ids) == len(set(ids)), ids  # duplicate ids mean an unloaded resume
    assert len(turns) == len(set(turns)), turns
    # Two connections, two exchanges, four messages in one continuous log.
    assert [record["role"] for record in records] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert asr.calls >= 2


def test_resumed_device_session_context_reaches_the_model(tmp_path: Path) -> None:
    """The loaded history is what the LLM is asked with, not just what is stored."""
    seen: list[list[dict[str, Any]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content)["messages"])
        return httpx.Response(
            200,
            text='data: {"choices":[{"delta":{"content":"پاسخ "}}]}\n\ndata: [DONE]\n\n',
        )

    def factory(settings: Settings, sink: PlaybackSink, metrics: Any) -> VoiceBundle:
        return VoiceBundle(
            audio=FakeAsr(text="پیام دوم"),  # the resumed device's new turn
            client=httpx.AsyncClient(
                base_url="http://127.0.0.1", transport=httpx.MockTransport(handler)
            ),
            speaker_for=lambda voice: FakeSpeaker(),
        )

    from server.main import create_app

    configured = Settings(
        ws_enabled=True,
        ws_token="secret-token",
        data_dir=str(tmp_path),
        history_persist=True,
        voice_asr_interval_s=10.0,
    )
    app = create_app(settings=configured, llm=NullLlm(), voice_bundle=factory)
    with TestClient(app) as client:
        # The first connection is a *previous* session: seed it through the
        # store the API uses, then prove a device that asks for it by id gets
        # that history in its prompt.
        seed = create_conversation(configured, session_id="device-1")
        seed.add_user("سلام از دستگاه", utterance_id=1, turn_id=1)
        seed.finish_assistant("پاسخ دستگاه", turn_id=1)
        with device_session(client) as ws:
            ws.send_text(hello(session_id="device-1"))
            assert wait_for(ws, "hello_ok")["session_id"] == "device-1"
            exchange(ws)

    assert len(seen) >= 1
    resumed_prompt = [item["content"] for item in seen[-1]]
    # The seeded exchange is in the prompt the resumed device caused.
    assert "سلام از دستگاه" in resumed_prompt, resumed_prompt
    assert "پاسخ دستگاه" in resumed_prompt, resumed_prompt


def test_unknown_session_id_starts_an_empty_conversation(tmp_path: Path) -> None:
    with persisted_app_client(tmp_path) as client:
        with device_session(client) as ws:
            ws.send_text(hello(session_id="brand-new"))
            assert wait_for(ws, "hello_ok")["session_id"] == "brand-new"


def test_device_without_a_session_id_never_joins_the_last_local_session(
    tmp_path: Path,
) -> None:
    """Resume must not leak one session into an unrelated device connection.

    ``PVAI_HISTORY_RESUME`` defaults to resuming the newest session for the
    *local* live loop.  A LAN device that sends no ``session_id`` is a different
    caller: it must get a fresh session instead of another device's history.
    """
    configured = Settings(
        ws_enabled=True,
        ws_token="secret-token",
        data_dir=str(tmp_path),
        history_persist=True,
        voice_asr_interval_s=10.0,
    )
    seeded = create_conversation(configured, session_id="local-voice")
    seeded.add_user("صحبتِ محلی", utterance_id=1)

    with persisted_app_client(tmp_path) as client:
        with device_session(client) as ws:
            ws.send_text(hello())
            session_id = wait_for(ws, "hello_ok")["session_id"]
    assert session_id != "local-voice"
    # The local session is untouched: nothing was appended to it.
    assert [record["content"] for record in ConversationStore(
        tmp_path / "conversations"
    ).load("local-voice")] == ["صحبتِ محلی"]
