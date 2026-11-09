"""Security and resource hardening of the LAN transport and REST surface.

Each test corresponds to one reported defect:

1. REST was unauthenticated while ``PVAI_HOST=0.0.0.0`` was needed for devices.
2. Model loading (Whisper/Piper) ran inside the event loop.
3. Every connection loaded its own models, with no connection cap.
4. ``set_voice`` accepted any string and cached one Piper model per name.
5. Capture frames of any size were accepted; sequence numbers were ignored.
6. Playback was sent unpaced, so barge-in left seconds of stale audio.
7. Critical control messages were dropped by a full queue; one counter had
   two meanings.
8. ``serve`` swallowed every exception without a log line and never noticed a
   dead connection.

These are contract tests with fakes: they say nothing about real hardware.
"""

from __future__ import annotations

import asyncio
import json
import time
from array import array
from contextlib import contextmanager
from concurrent.futures import CancelledError
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from server.config import Settings
from server.protocol import (
    AUDIO_KIND_CAPTURE,
    ControlMessage,
    MessageType,
    encode_audio,
    session_hello,
)
from server.security import ConnectionLimiter, RateLimiter, SessionRegistry
from server.ws_voice import (
    MODEL_POOL,
    PlaybackSink,
    VoiceBundle,
    VoiceWebSocket,
    reset_model_pool,
)

LOUD = 2200
SILENCE = 0


# --------------------------------------------------------------------- helpers
def frame(level: int = LOUD, samples: int = 320) -> bytes:
    return array("h", [level] * samples).tobytes()


def capture(sequence: int = 0, samples: int = 320, level: int = LOUD) -> bytes:
    return encode_audio(AUDIO_KIND_CAPTURE, sequence, sequence * 20, frame(level, samples))


class NullLlm:
    async def aclose(self) -> None:
        return None

    async def health(self) -> dict[str, Any]:
        return {"reachable": True, "model_available": True}

    async def list_models(self) -> list[str]:
        return ["test-model"]

    async def chat(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("not used")


class FakeAsr:
    def __init__(self, text: str = "سلام") -> None:
        self.text = text
        self.calls = 0

    def transcribe_pcm(self, pcm: bytes) -> str:
        self.calls += 1
        return self.text


class FakeSpeaker:
    def speak(self, text, token, *, on_first_chunk=None, on_first_frame=None, on_complete=None):
        if on_first_chunk is not None:
            on_first_chunk(time.perf_counter())
        if on_first_frame is not None:
            on_first_frame(time.perf_counter())
        if on_complete is not None:
            on_complete(time.perf_counter(), 0.0)


def bundle_factory(asr: Any = None, speaker: Any = None):
    def factory(settings: Settings, sink: PlaybackSink, metrics: Any) -> VoiceBundle:
        return VoiceBundle(
            audio=asr or FakeAsr(),
            client=httpx.AsyncClient(
                base_url="http://127.0.0.1",
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200,
                        text='data: {"choices":[{"delta":{"content":"پاسخ "}}]}\n\ndata: [DONE]\n\n',
                    )
                ),
            ),
            speaker_for=lambda voice: speaker or FakeSpeaker(),
        )

    return factory


def settings(tmp_path: Path, **overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "ws_enabled": True,
        "ws_token": "",
        "ws_tokens": "esp32-test:secret-token",
        "data_dir": str(tmp_path),
        "history_persist": False,
        "tts_voice": "female",
        "voice_asr_interval_s": 10.0,
        "ws_heartbeat_s": 0.0,
        "ws_idle_timeout_s": 0.0,
    }
    base.update(overrides)
    return Settings(**base)


def hello(token: str = "secret-token", device: str = "esp32-test", **extra: Any) -> str:
    message = session_hello(device, token)
    message.payload.update(extra)
    return message.encode()


@contextmanager
def device_session(client: TestClient, path: str = "/ws/voice"):
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


def wait_for(ws, kind: str, *, limit: int = 200) -> dict[str, Any]:
    for _ in range(limit):
        message = json.loads(ws.receive_text())
        if message.get("type") == kind:
            return message
    raise AssertionError(f"{kind} not received")


def app_client(tmp_path: Path, *, asr: Any = None, speaker: Any = None, **overrides: Any):
    from server.main import create_app

    configured = settings(tmp_path, **overrides)
    app = create_app(
        settings=configured,
        llm=NullLlm(),
        voice_bundle=bundle_factory(asr, speaker),
    )
    return TestClient(app)


@pytest.fixture(autouse=True)
def _fresh_models():
    reset_model_pool()
    yield
    reset_model_pool()


# ------------------------------------------------- 1. REST authentication
def test_rest_requires_the_bearer_token(tmp_path: Path) -> None:
    """The device transport needed 0.0.0.0; that must not expose REST."""
    with app_client(tmp_path, api_token="s3cret") as client:
        for path in ("/api/sessions", "/ready", "/v1/models", "/api/protocol"):
            assert client.get(path).status_code == 401, path
        assert client.post("/api/chat", json={"message": "سلام"}).status_code == 401
        # A wrong token is refused too.
        assert client.get(
            "/api/sessions", headers={"Authorization": "Bearer wrong"}
        ).status_code == 401
        # The documented scheme is accepted.
        ok = client.get("/api/sessions", headers={"Authorization": "Bearer s3cret"})
        assert ok.status_code == 200


def test_session_transcripts_are_not_readable_without_the_token(tmp_path: Path) -> None:
    """The specific leak: GET /api/sessions/{id} returned the whole transcript."""
    from server.conversation import create_conversation

    configured = settings(tmp_path, history_persist=True, api_token="s3cret")
    conversation = create_conversation(configured, session_id="private")
    conversation.add_user("متن خصوصی", utterance_id=1)
    conversation.finish_assistant("پاسخ خصوصی", turn_id=1)

    from server.main import create_app

    with TestClient(create_app(settings=configured, llm=NullLlm())) as client:
        unauthorised = client.get("/api/sessions/private")
        assert unauthorised.status_code == 401
        assert "متن خصوصی" not in unauthorised.text
        assert client.delete("/api/sessions/private").status_code == 401
        assert client.get(
            "/api/sessions/private", headers={"Authorization": "Bearer s3cret"}
        ).status_code == 200


def test_health_is_the_only_public_route_and_leaks_nothing(tmp_path: Path) -> None:
    with app_client(tmp_path, api_token="s3cret") as client:
        response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    for secret in ("model", "backend", "directory", "path"):
        assert secret not in body


def test_session_listing_does_not_disclose_the_data_directory(tmp_path: Path) -> None:
    with app_client(tmp_path, api_token="token", history_persist=True) as client:
        body = client.get(
            "/api/sessions", headers={"Authorization": "Bearer token"}
        ).json()
    assert "directory" not in body
    assert str(tmp_path) not in json.dumps(body)


def test_a_non_loopback_bind_without_a_token_refuses_to_start() -> None:
    errors = Settings(api_host="0.0.0.0").config_errors()
    assert any("PVAI_API_TOKEN" in error for error in errors)
    # Loopback without a token stays allowed (the documented local default).
    assert not [e for e in Settings(api_host="127.0.0.1").config_errors() if "PVAI_API_TOKEN" in e]
    # And so does a LAN bind *with* a token.
    assert not [
        e
        for e in Settings(api_host="0.0.0.0", api_token="x").config_errors()
        if "PVAI_API_TOKEN" in e
    ]


# ------------------------------------------------- 2 + 3. models, limits
def test_model_loading_does_not_run_in_the_event_loop(tmp_path: Path) -> None:
    """A synchronous bundle froze every connection *and* the REST API.

    This drives the real ``_open_pipeline`` with a factory that sleeps, while a
    ticker runs concurrently: a blocking load shows up as a gap in the ticks.
    """
    interval = 0.01

    def slow_factory(settings_obj, sink, metrics):
        time.sleep(0.3)  # stands in for loading Whisper + Piper
        return VoiceBundle(
            audio=FakeAsr(),
            client=httpx.AsyncClient(
                base_url="http://127.0.0.1",
                transport=httpx.MockTransport(lambda r: httpx.Response(200, text="")),
            ),
            speaker_for=lambda voice: FakeSpeaker(),
        )

    session = VoiceWebSocket(
        websocket=None,
        settings=settings(tmp_path),
        bundle_factory=slow_factory,
    )

    async def scenario() -> float:
        ticks: list[float] = []
        stop = asyncio.Event()

        async def ticker() -> None:
            while not stop.is_set():
                ticks.append(time.monotonic())
                await asyncio.sleep(interval)

        task = asyncio.create_task(ticker())
        await asyncio.sleep(0)  # let the ticker take its first sample
        started = time.monotonic()
        await session._open_pipeline()
        elapsed = time.monotonic() - started
        stop.set()
        await task
        assert elapsed >= 0.25, "the fake did not actually sleep"
        assert len(ticks) >= 10, len(ticks)
        # The largest gap between ticks is how long the loop was blocked.
        return max(b - a for a, b in zip(ticks, ticks[1:]))

    gap = asyncio.run(scenario())
    assert gap < 0.15, f"event loop blocked for {gap * 1000:.0f} ms during model load"


def test_whisper_model_is_shared_across_connections(tmp_path: Path) -> None:
    """Two devices must not load two copies of the same Whisper model."""
    configured = settings(tmp_path)

    class FakeLocalAudio:
        def __init__(self, settings_obj, metrics=None):
            self.settings = settings_obj

    import server.voice as voice_module

    original = voice_module.LocalAudio
    voice_module.LocalAudio = FakeLocalAudio  # type: ignore[assignment]
    try:
        first = MODEL_POOL.audio(configured, None)
        second = MODEL_POOL.audio(configured, None)
    finally:
        voice_module.LocalAudio = original  # type: ignore[assignment]

    assert first is second
    assert MODEL_POOL.telemetry["asr_loads"] == 1
    assert MODEL_POOL.telemetry["hits"] >= 1


def test_connection_limit_refuses_the_extra_device() -> None:
    limiter = ConnectionLimiter(max_connections=2)
    assert limiter.acquire()[0] is True
    assert limiter.acquire()[0] is True
    ok, reason = limiter.acquire()
    assert ok is False and reason == "too_many_connections"
    limiter.release()
    assert limiter.acquire()[0] is True


def test_connection_limit_is_enforced_by_the_endpoint(tmp_path: Path) -> None:
    with app_client(tmp_path, ws_max_connections=1) as client:
        with device_session(client) as first:
            first.send_text(hello())
            wait_for(first, "hello_ok")
            with device_session(client) as second:
                second.send_text(hello())
                message = json.loads(second.receive_text())
                assert message["type"] == "error"
                assert message["code"] == "too_many_connections"


def test_two_devices_cannot_own_one_session(tmp_path: Path) -> None:
    """One JSONL, two writers: interleaved ids and turns."""
    with app_client(tmp_path, ws_max_connections=4) as client:
        with device_session(client) as first:
            first.send_text(hello())
            session_id = wait_for(first, "hello_ok")["session_id"]
            with device_session(client) as second:
                second.send_text(hello(session_id=session_id))
                message = json.loads(second.receive_text())
                assert message["type"] == "error"
                assert message["code"] == "session_in_use"


def test_session_claim_is_released_on_close() -> None:
    registry = SessionRegistry()
    assert registry.claim("s-1", "device-a") is True
    assert registry.claim("s-1", "device-b") is False
    registry.release("s-1", "device-a")
    assert registry.claim("s-1", "device-b") is True


# --------------------------------------------------------- 4. set_voice
def test_set_voice_accepts_only_the_documented_voices(tmp_path: Path) -> None:
    loaded: list[str] = []

    def factory(settings_obj, sink, metrics):
        def speaker_for(voice):
            loaded.append(voice)
            return FakeSpeaker()

        return VoiceBundle(
            audio=FakeAsr(),
            client=httpx.AsyncClient(
                base_url="http://127.0.0.1",
                transport=httpx.MockTransport(lambda r: httpx.Response(200, text="")),
            ),
            speaker_for=speaker_for,
        )

    from server.main import create_app

    app = create_app(settings=settings(tmp_path), llm=NullLlm(), voice_bundle=factory)

    with TestClient(app) as client:
        with device_session(client) as ws:
            ws.send_text(hello())
            wait_for(ws, "hello_ok")
            before = len(loaded)
            # (An empty string never reaches here: payload validation refuses it
            # as a protocol error, which is also correct.)
            # Raw JSON, not ControlMessage: the constructor does not validate,
            # so this is exactly what a hostile device can put on the wire.
            for name in ("evil-1", "x" * 40, "anything", "male2", "../etc/passwd"):
                ws.send_text(json.dumps({"type": "set_voice", "voice": name}))
                message = json.loads(ws.receive_text())
                assert message["type"] == "error", message
                # Two independent layers refuse it: the wire parser, and the
                # handler (which also covers a caller that skips parsing).
                assert message["code"] in {"protocol_error", "invalid_voice"}, message
            # No model was loaded for any of them.
            assert len(loaded) == before, loaded


# ------------------------------------------------- 5. frame size + sequence
def test_capture_frame_must_be_a_multiple_of_the_pipeline_block(
    tmp_path: Path,
) -> None:
    session = VoiceWebSocket(websocket=None, settings=settings(tmp_path))
    fed: list[int] = []

    class SinkPipeline:
        def feed(self, pcm):
            fed.append(len(pcm))

    session.pipeline = SinkPipeline()
    asyncio.run(session._handle_binary(capture(0, samples=320)))  # 20 ms: fine
    asyncio.run(session._handle_binary(capture(1, samples=1600)))  # 100 ms: re-chunked
    asyncio.run(session._handle_binary(capture(2, samples=100)))  # not a multiple: refused
    # Every block handed to the pipeline is exactly one 20 ms frame.
    assert fed == [640, 640, 640, 640, 640, 640]
    assert session.counters.frames_bad_size == 1
    queued = list(session.outgoing._queue)
    assert any("۲۰ms" in message for message in queued)


def test_capture_sequence_gaps_are_counted_not_ignored(tmp_path: Path) -> None:
    session = VoiceWebSocket(websocket=None, settings=settings(tmp_path))
    session.pipeline = type("P", (), {"feed": lambda self, pcm: None})()

    async def feed_everything() -> None:
        for sequence in (0, 1, 2, 5, 6, 6, 7):
            await session._handle_binary(capture(sequence))

    asyncio.run(feed_everything())
    # 2 -> 5 is a gap of two frames; 6 -> 6 is a duplicate.
    assert session.counters.frames_out_of_order == 2
    # The size of the gap is recorded, not just the fact that one happened.
    gap = session.metrics.summary("ws_capture_gap_frames")
    assert gap.count == 1 and gap.last == 2.0
    assert session.metrics.counter("ws_capture_duplicate_or_reordered") == 1


# --------------------------------------------------------- 6. pacing / 7. drops
def test_playback_is_paced_instead_of_burst(tmp_path: Path) -> None:
    """Frames must leave at real-time rate, not as fast as Piper synthesises."""
    paced = VoiceWebSocket(
        websocket=None, settings=settings(tmp_path, ws_playback_pacing=True)
    )
    assert paced._playback_ready_at() is None  # first frame goes immediately
    paced._next_frame_at = time.monotonic() + 0.05
    ready = paced._playback_ready_at()
    assert ready is not None and ready > time.monotonic()

    unpaced = VoiceWebSocket(
        websocket=None, settings=settings(tmp_path, ws_playback_pacing=False)
    )
    unpaced._next_frame_at = time.monotonic() + 5.0
    assert unpaced._playback_ready_at() is None  # pacing disabled


def test_paced_sender_does_not_get_more_than_the_credit_ahead(tmp_path: Path) -> None:
    """The device must never be handed more audio than it can play in time."""
    sent: list[float] = []

    class Socket:
        async def send_bytes(self, payload):
            sent.append(time.monotonic())

        async def send_text(self, payload):
            return None

    session = VoiceWebSocket(
        websocket=Socket(),
        settings=settings(tmp_path, ws_playback_credit_ms=100.0),
    )
    for _ in range(12):  # 240 ms of audio queued by the synthesiser
        session.sink.frames.put_nowait(frame())

    async def drain() -> float:
        started = time.monotonic()
        for _ in range(400):  # ask repeatedly, as the sender loop does
            await session._send_playback_frame()
            if session.sink.frames.empty():
                break
        return time.monotonic() - started

    elapsed = asyncio.run(drain())
    # 240 ms of audio minus the 100 ms credit = ~140 ms of sending time.
    assert len(sent) == 12
    assert elapsed >= 0.10, f"12 frames went out in {elapsed * 1000:.0f} ms (unpaced)"
    assert elapsed < 1.0, f"pacing too slow: {elapsed * 1000:.0f} ms"


def test_critical_messages_are_never_dropped(tmp_path: Path) -> None:
    session = VoiceWebSocket(websocket=None, settings=settings(tmp_path))
    # Fill the queue with partials, the only drop-able message.
    for index in range(70):
        session._queue_control(
            ControlMessage(MessageType.ASR_PARTIAL, {"utterance_id": 1, "text": f"p{index}"})
        )
    assert session.counters.control_dropped >= 6  # partials were dropped
    assert session.counters.rejected == 0  # ...but not counted as rejections

    session._queue_control(
        ControlMessage(MessageType.PLAY_STOP, {"turn_id": 1, "reason": "barge_in"})
    )
    # A full queue cannot make room, so the critical message waits in the
    # overflow: delayed until the sender drains, never dropped.
    assert session.counters.critical_reordered >= 1
    assert [m.type.value for m in session._overflow] == ["play_stop"]
    assert session.counters.rejected == 0


def test_play_stop_survives_a_completely_full_queue_of_criticals(tmp_path: Path) -> None:
    session = VoiceWebSocket(websocket=None, settings=settings(tmp_path))
    for index in range(70):
        session._queue_control(
            ControlMessage(MessageType.ASR_FINAL, {"utterance_id": index, "text": "x"})
        )
    session._queue_control(
        ControlMessage(MessageType.PLAY_STOP, {"turn_id": 1, "reason": "barge_in"})
    )
    assert [m.type.value for m in session._overflow][-1] == "play_stop", (
        "nothing but a critical message may end up in the overflow"
    )
    assert all(
        m.type.value in {"play_stop", "play_end", "asr_final", "error", "hello_ok",
                         "session_ack", "ping", "pong"}
        for m in session._overflow
    )
    # The sender drains the overflow before anything else.
    import inspect

    assert "_overflow" in inspect.getsource(__import__(
        "server.ws_voice", fromlist=["VoiceWebSocket"]
    ).VoiceWebSocket._sender)


# ------------------------------------------------- 8. logging, heartbeat
def test_serve_logs_failures_instead_of_swallowing_them(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    class BrokenSocket:
        async def accept(self):
            return None

        async def receive(self):
            return {"type": "websocket.receive", "bytes": b"junk"}

        async def send_text(self, payload):
            return None

        async def close(self, code=1000):
            return None

    session = VoiceWebSocket(websocket=BrokenSocket(), settings=settings(tmp_path))

    async def explode(*args, **kwargs):
        raise RuntimeError("boom")

    session._handle_binary = explode  # type: ignore[assignment]
    session._handshake = lambda: _true()  # type: ignore[assignment]
    session._open_pipeline = lambda: _true()  # type: ignore[assignment]

    with caplog.at_level("ERROR"):
        asyncio.run(session.serve())
    assert any("boom" in record.getMessage() or "failed" in record.getMessage()
               for record in caplog.records), caplog.text


async def _true() -> bool:
    return True


def test_idle_connection_is_closed_and_logged(tmp_path: Path) -> None:
    closed: list[int] = []

    class SilentSocket:
        async def send_text(self, payload):
            return None

        async def close(self, code=1000):
            closed.append(code)

    session = VoiceWebSocket(
        websocket=SilentSocket(),
        settings=settings(tmp_path, ws_heartbeat_s=0.05, ws_idle_timeout_s=0.15),
    )

    async def scenario() -> None:
        task = asyncio.create_task(session._heartbeat())
        await asyncio.sleep(0.4)
        assert task.done()

    asyncio.run(scenario())
    assert closed, "a silent device must not hold the connection forever"


def test_heartbeat_pings_a_live_connection(tmp_path: Path) -> None:
    session = VoiceWebSocket(
        websocket=None,
        settings=settings(tmp_path, ws_heartbeat_s=0.05, ws_idle_timeout_s=10.0),
    )

    async def scenario() -> None:
        task = asyncio.create_task(session._heartbeat())
        await asyncio.sleep(0.18)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
    types = [json.loads(raw)["type"] for raw in session.outgoing._queue]
    assert types.count("ping") >= 2


# ----------------------------------------------------------- rate limiting
def test_rate_limiter_refuses_a_flood_and_recovers() -> None:
    clock = {"now": 1000.0}
    limiter = RateLimiter(rate=2.0, burst=2, now=lambda: clock["now"])
    assert limiter.check("a")[0] is True
    assert limiter.check("a")[0] is True
    allowed, retry_after = limiter.check("a")
    assert allowed is False and retry_after > 0
    # A different client has its own bucket.
    assert limiter.check("b")[0] is True
    clock["now"] += 1.0  # one token refilled at 2/s
    assert limiter.check("a")[0] is True


def test_rest_requests_are_rate_limited(tmp_path: Path) -> None:
    with app_client(
        tmp_path, api_token="token", api_rate_limit_per_s=1.0, api_rate_burst=2
    ) as client:
        headers = {"Authorization": "Bearer token"}
        codes = [
            client.get("/api/sessions", headers=headers).status_code for _ in range(5)
        ]
    assert 429 in codes, codes
    # /health is exempt: a monitor must not be locked out.
    assert codes[0] == 200


# ------------------------------------------------- per-device device tokens
def test_each_device_has_its_own_token(tmp_path: Path) -> None:
    with app_client(
        tmp_path, ws_tokens="esp32-a:token-a,esp32-b:token-b"
    ) as client:
        with device_session(client) as ws:
            ws.send_text(hello(token="token-b", device="esp32-b"))
            assert wait_for(ws, "hello_ok")["session_id"]
        # A token paired with the wrong device id is refused.
        with device_session(client) as ws:
            ws.send_text(hello(token="token-b", device="esp32-a"))
            message = json.loads(ws.receive_text())
            assert message["code"] == "device_mismatch"
    # The revoked device cannot connect while the other still can.
    with app_client(tmp_path, ws_tokens="esp32-b:token-b") as client:
        with device_session(client) as ws:
            ws.send_text(hello(token="token-a", device="esp32-a"))
            assert json.loads(ws.receive_text())["code"] == "auth_failed"


def test_malformed_device_token_list_is_reported_not_ignored() -> None:
    settings_obj = Settings(ws_tokens="no-colon-here, ok:token, ok2:token")
    errors = settings_obj.config_errors()
    assert any("PVAI_WS_TOKENS" in error for error in errors)


def test_set_voice_handler_also_rejects_unknown_names(tmp_path: Path) -> None:
    """Defence in depth: the handler must not rely on the parser.

    ``ControlMessage(...)`` does not validate its payload, so any in-process
    caller (or a future parsing change) can bypass ``parse`` and reach the
    handler with an arbitrary string.  ``speaker_for`` would then load a Piper
    model per distinct string -- an unbounded cache.
    """
    loaded: list[str] = []

    class Bundle:
        def speaker_for(self, voice):
            loaded.append(voice)
            return FakeSpeaker()

    session = VoiceWebSocket(websocket=None, settings=settings(tmp_path))
    session.bundle = Bundle()
    session.pipeline = type("P", (), {"speaker": None})()
    session.authenticated = True

    async def drive(names: list[str]) -> list[list[dict]]:
        results = []
        for name in names:
            await session._set_voice(name)
            messages = []
            while not session.outgoing.empty():
                messages.append(json.loads(session.outgoing.get_nowait()))
            results.append(messages)
        return results
        messages = []
        while not session.outgoing.empty():
            messages.append(json.loads(session.outgoing.get_nowait()))
        return messages

    results = asyncio.run(
        drive(["evil", "female2", "x" * 200, "MALE", "female"])
    )
    for messages in results[:3]:
        assert messages and messages[0]["code"] == "invalid_voice", messages
    assert loaded == ["male", "female"], loaded  # no load for a rejected name
    assert session.counters.voice_rejected == 3


# ------------------------------------- 7b. one play_start per turn
def test_play_start_is_emitted_once_per_turn_not_per_sentence(tmp_path: Path) -> None:
    """``tts_start`` fires per sentence; ``play_start`` is a turn boundary."""
    session = VoiceWebSocket(websocket=None, settings=settings(tmp_path))
    for turn, sentences in ((1, 3), (2, 2)):
        for _ in range(sentences):
            session._on_pipeline_event(
                {"type": "tts_start", "turn_id": turn, "text": "یک جمله."}
            )
    starts = [
        json.loads(raw) for raw in session.outgoing._queue
    ]
    assert [m["turn_id"] for m in starts] == [1, 2]
    assert all(m["type"] == "play_start" for m in starts)

    # A barge-in resets the boundary: the retried turn may announce itself again.
    session._reset_playback_pacing()
    session._on_pipeline_event({"type": "tts_start", "turn_id": 3, "text": "دوباره."})
    assert json.loads(list(session.outgoing._queue)[-1])["turn_id"] == 3


# ------------------------------------- 5b. capture frame rate
def test_runaway_capture_rate_is_throttled(tmp_path: Path) -> None:
    """A device sending far faster than real time must not saturate the CPU."""
    fed: list[bytes] = []

    session = VoiceWebSocket(
        websocket=None, settings=settings(tmp_path, ws_frame_rate_per_s=10.0)
    )
    session.pipeline = type("P", (), {"feed": lambda self, pcm: fed.append(pcm)})()

    async def flood() -> None:
        # 40 frames in one go: a correct device sends 50/s, so this is a flood.
        for sequence in range(40):
            await session._handle_binary(capture(sequence))

    asyncio.run(flood())
    assert len(fed) == 10, len(fed)  # only the allowance reached the pipeline
    assert session.counters.frames_rate_limited == 30

    # A normal burst (a WiFi stall delivering half a second at once) is fine.
    session2 = VoiceWebSocket(
        websocket=None, settings=settings(tmp_path, ws_frame_rate_per_s=200.0)
    )
    session2.pipeline = type("P", (), {"feed": lambda self, pcm: None})()
    asyncio.run(_feed_all(session2, 50))
    assert session2.counters.frames_rate_limited == 0


async def _feed_all(session: Any, count: int) -> None:
    for sequence in range(count):
        await session._handle_binary(capture(sequence))


def test_an_anonymous_flood_cannot_lock_out_the_legitimate_client(tmp_path: Path) -> None:
    """Rate limiting must throttle the attacker, not the operator.

    Every request arrives from one address (and behind a TLS reverse proxy —
    the documented setup — *all* clients do), so an address-keyed bucket lets
    anyone lock the API out by flooding invalid tokens.
    """
    with app_client(
        tmp_path, api_token="token", api_rate_limit_per_s=1.0, api_rate_burst=2
    ) as client:
        for _ in range(6):  # anonymous flood; each refused (401, then 429)
            assert client.get("/api/sessions").status_code in {401, 429}
        # The real client is unaffected and its own bucket is still full.
        ok = client.get("/api/sessions", headers={"Authorization": "Bearer token"})
        assert ok.status_code == 200, ok.text

        # The attacker still cannot guess: their attempts stay rate limited.
        codes = [
            client.get(
                "/api/sessions", headers={"Authorization": f"Bearer guess-{i}"}
            ).status_code
            for i in range(6)
        ]
        assert 429 in codes, codes


def test_serve_returns_when_the_device_goes_silent(tmp_path: Path) -> None:
    """A dead peer must end the session without waiting for a TCP timeout.

    ``close()`` on the socket is not enough: a device that vanished sends no
    disconnect, so the receive loop would stay parked in ``await receive()``
    and hold its connection slot and models.
    """
    closed: list[int] = []

    class DeadSocket:
        async def accept(self):
            return None

        async def receive(self):
            await asyncio.sleep(3600)  # a peer that never speaks again
            return {"type": "websocket.disconnect"}

        async def send_text(self, payload):
            return None

        async def close(self, code=1000):
            closed.append(code)

    session = VoiceWebSocket(
        websocket=DeadSocket(),
        settings=settings(tmp_path, ws_heartbeat_s=0.05, ws_idle_timeout_s=0.15),
    )

    async def noop() -> bool:
        return True

    session._handshake = noop  # type: ignore[assignment]
    session._open_pipeline = noop  # type: ignore[assignment]

    async def scenario() -> None:
        await asyncio.wait_for(session.serve(), timeout=3.0)

    asyncio.run(scenario())  # would time out if the loop never exited
    assert closed, "the socket must be closed when the peer is gone"
    assert session.metrics.counter("ws_idle_timeouts") >= 1


def test_tls_advice_is_a_warning_not_a_startup_failure() -> None:
    """A LAN deployment must not be impossible because TLS is missing.

    ``config_errors`` is a hard gate (``run_live`` raises, ``/ready`` reports
    the install as broken), so advisory risks live in ``config_warnings``.
    """
    lan = Settings(api_host="192.168.1.5", api_token="t", ws_enabled=True, ws_tokens="a:tok")
    assert not lan.config_errors(), lan.config_errors()
    assert any("TLS" in w or "wss" in w for w in lan.config_warnings()), lan.config_warnings()

    # Loopback with no token stays a warning, not an error.
    local = Settings(api_host="127.0.0.1")
    assert not local.config_errors()
    assert local.config_warnings()


def test_ready_reports_warnings_without_failing(tmp_path: Path) -> None:
    with app_client(tmp_path, api_token="token") as client:
        body = client.get(
            "/ready", headers={"Authorization": "Bearer token"}
        ).json()
    configuration = body["checks"]["configuration"]
    assert "warnings" in configuration
    # The warnings never decide readiness.
    assert configuration["ok"] is (not configuration["errors"])


def test_shared_whisper_model_serialises_decoding() -> None:
    """One model, many connections: CTranslate2 decoding is not thread-safe.

    ``MODEL_POOL`` hands the same ``LocalAudio`` to every connection, so two
    concurrent ``transcribe`` calls would drive one model object at the same
    time.  The decode must be serialised -- and the serialisation must not
    deadlock or leak between calls.
    """
    from server.voice import LocalAudio

    class FakeModel:
        def __init__(self) -> None:
            self.inside = 0
            self.overlaps = 0

        def transcribe(self, source, **kwargs):
            self.inside += 1
            if self.inside > 1:
                self.overlaps += 1
            time.sleep(0.02)  # a decode takes real time

            def segments():
                yield type("S", (), {"text": "سلام"})()

            self.inside -= 1
            return segments(), None

    audio = LocalAudio.__new__(LocalAudio)
    audio.settings = Settings()
    audio.metrics = None
    audio.model = FakeModel()
    audio._lock = __import__("threading").Lock()
    audio._decode_lock = __import__("threading").Lock()

    results: list[str] = []

    def worker() -> None:
        # Call the decode path directly: ``transcribe_pcm`` needs numpy, which
        # is a real dependency but not what this test is about.
        results.append(audio._transcribe(None))

    import threading

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results == ["سلام"] * 4
    assert audio.model.overlaps == 0, "two decodes ran on one model at once"


def test_shared_piper_model_steps_one_caller_at_a_time() -> None:
    """``synthesize`` is a lazy generator that advances the ONNX session.

    One shared voice and two devices synthesising at once would interleave two
    generators on one model, so the step is serialised while playback stays
    concurrent.
    """
    from server.live_voice import Speaker

    class Model:
        def __init__(self) -> None:
            self.inside = 0
            self.overlaps = 0

        def synthesize(self, text):
            for _ in range(5):
                self.inside += 1
                if self.inside > 1:
                    self.overlaps += 1
                time.sleep(0.005)
                self.inside -= 1
                yield b"chunk"

    speaker = Speaker.__new__(Speaker)
    speaker.model = Model()
    speaker._model_lock = __import__("threading").Lock()

    def drain() -> int:
        return sum(1 for _ in speaker._synthesize("سلام"))

    import threading

    counts: list[int] = []
    threads = [threading.Thread(target=lambda: counts.append(drain())) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert counts == [5, 5, 5]
    assert speaker.model.overlaps == 0, "two synthesise generators advanced one model"


def test_piper_lock_is_skipped_for_an_unshared_model(tmp_path: Path) -> None:
    """Without a pool lock the speaker must behave exactly as before."""
    from server.live_voice import Speaker

    class Model:
        def synthesize(self, text):
            yield b"a"
            yield b"b"

    speaker = Speaker.__new__(Speaker)
    speaker.model = Model()
    assert list(speaker._synthesize("سلام")) == [b"a", b"b"]


def test_default_bundle_wires_the_shared_piper_lock(tmp_path: Path) -> None:
    """Sharing the model without the lock would leave production unprotected.

    The synthetic ``Speaker`` tests above pass even if the real bundle forgets
    to hand a lock over, so this one goes through ``default_bundle`` itself.
    """
    import server.ws_voice as ws_module

    loaded: dict[str, Any] = {}

    def fake_loader(settings_obj, voice):
        loaded["voice"] = voice
        # Cache like the real pool does, so "shared" is observable.
        return loaded.setdefault(voice, object())

    import server.live_voice as live_module

    original = ws_module.MODEL_POOL.piper_voice
    original_validate = live_module._validate_tts_model
    original_dependency = live_module.dependency
    ws_module.MODEL_POOL.piper_voice = fake_loader  # type: ignore[assignment]
    live_module._validate_tts_model = lambda path: None  # type: ignore[assignment]
    # sounddevice is deliberately not installed in CI; Speaker only needs it at
    # playback time and this test never plays.
    live_module.dependency = lambda name: object()  # type: ignore[assignment]
    try:
        bundle = ws_module.default_bundle(settings(tmp_path), PlaybackSink(), None)
        first = bundle.speaker_for("female")
        second = bundle.speaker_for("female")
    finally:
        ws_module.MODEL_POOL.piper_voice = original  # type: ignore[assignment]
        live_module._validate_tts_model = original_validate  # type: ignore[assignment]
        live_module.dependency = original_dependency  # type: ignore[assignment]

    assert first.model is second.model, "the Piper model must be shared"
    assert first._model_lock is not None, "a shared model needs its step lock"
    assert first._model_lock is second._model_lock, "one lock per shared model"
    # Distinct voices do not block each other.
    assert first._model_lock is not ws_module.MODEL_POOL.piper_lock("male")


def test_off_loop_events_are_marshalled_onto_the_loop(tmp_path: Path) -> None:
    """Pipeline events can be raised from a worker thread.

    ``Speaker`` runs in ``asyncio.to_thread`` and calls back on the first
    audible frame, which emits a pipeline event.  ``asyncio.Queue`` is not
    thread-safe, so such a call must be handed to the loop.

    The mechanism is asserted directly rather than through a spy on the loop:
    ``asyncio.to_thread`` schedules its own callbacks with
    ``call_soon_threadsafe`` too, so counting those calls cannot tell the guard
    apart from the machinery that wraps it.
    """
    session = VoiceWebSocket(websocket=None, settings=settings(tmp_path))

    async def scenario() -> None:
        # On the loop: straight into the queue, and the loop is captured.
        assert session._on_loop_thread() is True
        session._queue_control(
            ControlMessage(MessageType.PLAY_STOP, {"turn_id": 1, "reason": "barge_in"})
        )
        assert session._loop is asyncio.get_running_loop()

        # From a worker thread it must be recognised as off-loop...
        assert await asyncio.to_thread(session._on_loop_thread) is False
        # ... and the message still arrives, in order.
        await asyncio.to_thread(
            session._queue_control,
            ControlMessage(MessageType.PLAY_END, {"turn_id": 1, "played_ms": 20.0}),
        )
        await asyncio.sleep(0.05)  # let the scheduled callback run
        types = [json.loads(raw)["type"] for raw in session.outgoing._queue]
        assert types == ["play_stop", "play_end"], types

    asyncio.run(scenario())


def test_a_guard_that_never_marshals_would_lose_the_thread_check(tmp_path: Path) -> None:
    """Pin the failure mode: recognising only a *running* loop is not enough.

    A worker thread has no running loop at all, so a guard that treats "no
    running loop" as "same thread" silently never marshals anything.  This is
    the bug the implementation was written against.
    """
    session = VoiceWebSocket(websocket=None, settings=settings(tmp_path))

    def from_worker() -> bool:
        return session._on_loop_thread()

    import threading

    results: list[bool] = []
    thread = threading.Thread(target=lambda: results.append(from_worker()))
    thread.start()
    thread.join()
    assert results == [True], "with no loop captured at all, a sync caller is the same thread"

    async def capture() -> bool:
        session._on_loop_thread()  # captures the loop
        return await asyncio.to_thread(from_worker)

    assert asyncio.run(capture()) is False
