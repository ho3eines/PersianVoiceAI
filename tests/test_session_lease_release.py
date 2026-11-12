"""A disconnected device must never leave its ``session_id`` locked out.

The reported failure: after a device drops, ``hello`` with the *same*
``session_id`` is answered with ``session_in_use``.  Two separate claims hold a
session, and each one used to be released at a different moment:

* ``SessionRegistry`` (in-process ownership) was released first, in pure
  bookkeeping;
* the OS lease (:class:`server.conversation.SessionLease`, an ``flock`` on
  ``<id>.lock``) was released only at the *end* of teardown — after the pipeline,
  the ASR scheduler and the HTTP client had been closed.

So a reconnect that arrived during teardown passed the registry check and then
failed inside ``create_conversation`` with ``SessionInUse``: exactly the
``session_in_use`` reject the device saw.  A teardown that raised (a broken
sink, a failing pipeline) skipped the release entirely and locked the session
out for the life of the process.

Everything here is deterministic: no sleeps, no polling — a reconnect is
attempted while teardown is *provably* still running, because teardown is
blocked on an ``asyncio.Event`` the test controls.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from server.config import Settings
from server.conversation import (
    ConversationStore,
    SessionInUse,
    SessionLease,
    create_conversation,
)
from server.security import SessionRegistry
from server.ws_voice import VoiceWebSocket


def lease_settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=str(tmp_path),
        history_persist=True,
        ws_token="secret-token",
        tts_voice="female",
    )


class StubWebSocket:
    """Enough of a Starlette WebSocket for teardown (no socket is opened)."""

    client = None

    async def accept(self) -> None:  # pragma: no cover - not used by close()
        return None

    async def send_text(self, text: str) -> None:  # pragma: no cover
        return None

    async def close(self, code: int = 1000) -> None:  # pragma: no cover
        return None


class SlowPipeline:
    """A pipeline whose ``close`` blocks until the test lets it finish."""

    def __init__(self) -> None:
        self.closing = asyncio.Event()
        self.finished = asyncio.Event()
        self.closed = False

    async def close(self) -> None:
        self.closing.set()
        await self.finished.wait()
        self.closed = True


class RaisingPipeline:
    """A pipeline whose ``close`` fails, as a broken backend would."""

    async def close(self) -> None:
        raise RuntimeError("pipeline teardown exploded")


class _NullBundle:
    async def aclose(self) -> None:
        return None


def connected_session(
    settings: Settings,
    *,
    registry: SessionRegistry | None = None,
) -> tuple[VoiceWebSocket, str]:
    """A session that already owns the registry claim *and* the OS lease."""
    registry = registry or SessionRegistry()
    session = VoiceWebSocket(
        StubWebSocket(), settings, session_registry=registry
    )
    conversation = create_conversation(
        settings, session_id="s-leased", resume=False, exclusive=True
    )
    session._leased_conversation = conversation
    session.session_id = conversation.session_id
    owner = "device#1"
    assert registry.claim(conversation.session_id, owner)
    session._owned_session = conversation.session_id
    session._session_owner = owner
    return session, conversation.session_id


def reconnect(settings: Settings, session_id: str):
    """What a rebooted device does: claim the same session again, right now."""
    return create_conversation(
        settings, session_id=session_id, resume=False, exclusive=True
    )


def test_a_reconnect_succeeds_while_the_old_session_is_still_tearing_down(
    tmp_path: Path,
) -> None:
    """The lease is released *before* the slow part of teardown, not after.

    ``pipeline.close()`` is blocked on an event, so the only way this test can
    pass is if the OS lock was already gone when ``close()`` reached that await.
    """
    settings = lease_settings(tmp_path)
    session, session_id = connected_session(settings)
    pipeline = SlowPipeline()
    session.pipeline = pipeline  # type: ignore[assignment]
    session.bundle = _NullBundle()  # type: ignore[assignment]

    async def scenario() -> None:
        closing = asyncio.create_task(session.close())
        await asyncio.wait_for(pipeline.closing.wait(), timeout=5.0)
        # Teardown is provably unfinished here.
        assert not closing.done()
        resumed = reconnect(settings, session_id)
        assert resumed.session_id == session_id
        resumed.close()
        pipeline.finished.set()
        await asyncio.wait_for(closing, timeout=5.0)

    asyncio.run(scenario())
    assert pipeline.closed


def test_the_registry_claim_is_also_free_while_teardown_is_running(
    tmp_path: Path,
) -> None:
    """Both claims — registry and OS lease — must be released together."""
    settings = lease_settings(tmp_path)
    registry = SessionRegistry()
    session, session_id = connected_session(settings, registry=registry)
    pipeline = SlowPipeline()
    session.pipeline = pipeline  # type: ignore[assignment]

    async def scenario() -> None:
        closing = asyncio.create_task(session.close())
        await asyncio.wait_for(pipeline.closing.wait(), timeout=5.0)
        assert registry.owner(session_id) is None
        assert registry.claim(session_id, "another-device")
        registry.release(session_id, "another-device")
        pipeline.finished.set()
        await asyncio.wait_for(closing, timeout=5.0)

    asyncio.run(scenario())


def test_a_failing_sink_during_teardown_still_releases_the_lease(
    tmp_path: Path,
) -> None:
    """``sink.close()`` used to run outside the ``try`` that released the lock."""
    settings = lease_settings(tmp_path)
    session, session_id = connected_session(settings)

    def boom() -> None:
        raise RuntimeError("sink teardown failed")

    session.sink.close = boom  # type: ignore[method-assign]

    async def scenario() -> None:
        await session.close()  # teardown must not propagate a cleanup error

    asyncio.run(scenario())
    resumed = reconnect(settings, session_id)
    resumed.close()


def test_a_failing_pipeline_during_teardown_still_releases_the_lease(
    tmp_path: Path,
) -> None:
    settings = lease_settings(tmp_path)
    session, session_id = connected_session(settings)
    session.pipeline = RaisingPipeline()  # type: ignore[assignment]

    async def scenario() -> None:
        await session.close()

    asyncio.run(scenario())
    resumed = reconnect(settings, session_id)
    resumed.close()


def test_a_cancelled_teardown_still_releases_the_lease(tmp_path: Path) -> None:
    """A server shutdown cancels the session task mid-teardown."""
    settings = lease_settings(tmp_path)
    session, session_id = connected_session(settings)
    pipeline = SlowPipeline()
    session.pipeline = pipeline  # type: ignore[assignment]

    async def scenario() -> None:
        closing = asyncio.create_task(session.close())
        await asyncio.wait_for(pipeline.closing.wait(), timeout=5.0)
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing

    asyncio.run(scenario())
    resumed = reconnect(settings, session_id)
    resumed.close()


def test_lease_close_releases_the_file_lock_when_unlock_itself_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Closing the descriptor frees the lock even if the explicit unlock fails.

    Windows (``msvcrt.locking``) and POSIX (``fcntl.flock``) both drop a lock
    when the *descriptor* is closed.  Raising out of ``close()`` before that
    left the descriptor open, so the lock outlived the session.
    """
    store = ConversationStore(tmp_path)
    lease = store.claim("stubborn")

    def failing_unlock(*args: Any, **kwargs: Any) -> None:
        raise OSError("unlock refused")

    monkeypatch.setattr(
        "msvcrt.locking" if os.name == "nt" else "fcntl.flock", failing_unlock
    )
    lease.close()
    monkeypatch.undo()

    again = store.claim("stubborn")
    again.close()


def test_lease_close_is_idempotent(tmp_path: Path) -> None:
    lease = SessionLease(tmp_path / "twice.lock")
    lease.close()
    lease.close()
    with SessionLease(tmp_path / "twice.lock"):
        pass


def test_conversation_close_is_idempotent_and_frees_the_lock(tmp_path: Path) -> None:
    settings = lease_settings(tmp_path)
    conversation = create_conversation(
        settings, session_id="s-close-twice", resume=False, exclusive=True
    )
    conversation.close()
    conversation.close()
    again = create_conversation(
        settings, session_id="s-close-twice", resume=False, exclusive=True
    )
    again.close()


def first_verdict(ws, limit: int = 60) -> dict[str, Any]:
    """First frame that decides the handshake, with its reason if it failed."""
    for _ in range(limit):
        try:
            message = json.loads(ws.receive_text())
        except Exception as exc:  # the server closed before answering
            raise AssertionError(f"closed without a verdict: {exc!r}") from exc
        if message.get("type") in {"hello_ok", "error"}:
            return message
    raise AssertionError("no hello_ok/error frame arrived")


def wait_until_the_server_dropped_the_session(client, session_id: str, timeout: float = 5.0) -> float:
    """Wait for the *server* to finish the session; return how long that took.

    ``WebSocketTestSession.__exit__`` only closes the client side: the server
    task is cancelled asynchronously, so a reconnect issued on the next line can
    legitimately arrive while the old connection is still open.  That case must
    still be refused (two live writers on one JSONL is the corruption this whole
    module prevents), so the test waits for the server's own record of the
    disconnect — the session registry on ``app.state`` — and *then* reconnects
    without any delay of its own.
    """
    registry = client.app.state.ws_counters.registry
    started = time.monotonic()
    while registry.owner(session_id) is not None:
        if time.monotonic() - started > timeout:
            raise AssertionError(
                f"the server never released session {session_id!r} after the disconnect"
            )
        time.sleep(0.005)
    return time.monotonic() - started


def test_a_device_reconnect_never_sees_session_in_use(tmp_path: Path) -> None:
    """Drop the socket, let the server notice, resume immediately: no delay.

    Repeated because a single round trip can accidentally look clean.  Nothing
    here sleeps between "the server dropped it" and the next ``hello`` — only
    the teardown of the *previous* connection is awaited, which is what a real
    device observes as its socket closing.
    """
    from test_ws_voice import device_session, hello, persisted_app_client, wait_for

    with persisted_app_client(tmp_path) as client:
        with device_session(client) as ws:
            ws.send_text(hello())
            session_id = wait_for(ws, "hello_ok")["session_id"]
        for attempt in range(3):
            released_after = wait_until_the_server_dropped_the_session(client, session_id)
            with device_session(client) as ws:
                ws.send_text(hello(session_id=session_id))
                message = first_verdict(ws)
                assert message["type"] == "hello_ok", message
                assert message["session_id"] == session_id, attempt
            # The release is bookkeeping, not model teardown: it must be fast.
            assert released_after < 2.0, released_after


def test_the_lock_is_free_even_if_the_owner_thread_raised(tmp_path: Path) -> None:
    """A connection that dies by exception must not keep the session."""
    settings = lease_settings(tmp_path)
    store = ConversationStore(tmp_path / "conversations")
    logged: list[str] = []

    def owner() -> None:
        # The shape of a device handler: the lease is taken with a context
        # manager, the body blows up, the transport logs it and moves on.
        try:
            with store.claim("s-threaded"):
                raise RuntimeError("device handler crashed")
        except RuntimeError as exc:
            logged.append(str(exc))

    thread = threading.Thread(target=owner)
    thread.start()
    thread.join(timeout=5.0)
    assert not thread.is_alive()
    free = store.claim("s-threaded")  # the raise must not have kept the lock
    free.close()
    resumed = create_conversation(
        settings, session_id="s-threaded", resume=False, exclusive=True
    )
    resumed.close()


def test_a_pending_reply_is_recorded_before_the_lock_is_dropped(tmp_path: Path) -> None:
    """Hand-over ordering: the JSONL is final before a reconnect may load it.

    The transport asks the pipeline to commit the in-flight turn *synchronously*
    and only then releases the lease.  Here the pipeline does exactly that and
    tries to take the session for itself at the same moment: while it is writing
    the lock must still be ours, and the record must be readable afterwards.
    """
    import json

    settings = lease_settings(tmp_path)
    session, session_id = connected_session(settings, registry=SessionRegistry())
    conversation = session._leased_conversation
    observed: dict[str, bool] = {}

    class RecordingPipeline:
        async def close(self) -> None:
            return None

        def finalize_pending_turn(self) -> None:
            conversation.finish_assistant(
                "پاسخ نیمه‌کاره", status="interrupted", turn_id=1, utterance_id=1
            )
            try:
                reconnect(settings, session_id).close()
            except SessionInUse:
                observed["locked_while_committing"] = True
            else:
                observed["locked_while_committing"] = False

    session.pipeline = RecordingPipeline()  # type: ignore[assignment]
    asyncio.run(session.close())

    assert observed["locked_while_committing"] is True
    records = [
        json.loads(line)
        for line in (tmp_path / "conversations" / f"{session_id}.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [record["content"] for record in records] == ["پاسخ نیمه‌کاره"]


def test_a_failed_pipeline_construction_releases_the_local_session_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`run_live` failing halfway must not need a process restart to recover."""
    from server import streaming_voice

    settings = lease_settings(tmp_path)

    class ExplodingPipeline:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("no audio device")

    monkeypatch.setattr(streaming_voice, "Pipeline", ExplodingPipeline)
    with pytest.raises(RuntimeError, match="no audio device"):
        streaming_voice.create_pipeline(
            settings,
            audio=None,
            speaker=None,
            client=None,
            emit=lambda event: None,
            session_id="s-local",
        )
    store = ConversationStore(tmp_path / "conversations")
    lease = store.claim("pc-s-local")  # local sessions are prefixed
    lease.close()


def test_a_second_live_device_is_still_refused_for_the_same_session(
    tmp_path: Path,
) -> None:
    """The fix must not weaken the guarantee: two live writers stay impossible.

    Releasing the lock promptly on disconnect is only safe because a session
    that is *still alive* keeps its claims.  The second device is refused, and
    the first one is not disturbed (it still answers a ping).
    """
    from test_ws_voice import (
        device_session,
        hello,
        persisted_app_client,
        wait_for,
    )
    from server.protocol import ControlMessage, MessageType

    with persisted_app_client(tmp_path) as client:
        with device_session(client) as first:
            first.send_text(hello())
            session_id = wait_for(first, "hello_ok")["session_id"]
            with device_session(client) as second:
                second.send_text(hello(session_id=session_id))
                message = first_verdict(second)
                assert message["type"] == "error"
                assert message["code"] == "session_in_use"
            first.send_text(
                ControlMessage(MessageType.PING, {"nonce": "still-here"}).encode()
            )
            assert wait_for(first, "pong")["nonce"] == "still-here"
        # And once the owner is gone, the same id is free again.
        wait_until_the_server_dropped_the_session(client, session_id)
        with device_session(client) as third:
            third.send_text(hello(session_id=session_id))
            assert first_verdict(third)["type"] == "hello_ok"
