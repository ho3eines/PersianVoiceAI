"""ASR scheduling: lossless finals, replaceable partials, no head-of-line blocking."""

from __future__ import annotations

import asyncio

from server.streaming_voice import AsrJob, AsrScheduler


def job(utterance_id: int, *, final: bool, frames: int = 10, window_start: int = 0) -> AsrJob:
    return AsrJob(
        utterance_id=utterance_id,
        pcm=b"\x00" * (frames * 640),
        final=final,
        submitted_at=0.0,
        total_frames=frames,
        window_start_frame=window_start,
    )


def test_finals_are_fifo_and_lossless() -> None:
    async def scenario() -> None:
        scheduler = AsrScheduler()
        for utterance in (1, 2, 3):
            scheduler.submit_final(job(utterance, final=True))
        assert scheduler.pending_finals == 3
        received = [await scheduler.next() for _ in range(3)]
        assert [item.utterance_id for item in received] == [1, 2, 3]
        assert all(item.final for item in received)
        assert scheduler.received_final == 3 and scheduler.started_final == 3
        assert scheduler.depth == 0

    asyncio.run(scenario())


def test_partial_is_replaced_not_queued() -> None:
    async def scenario() -> None:
        scheduler = AsrScheduler()
        assert scheduler.submit_partial(job(1, final=False, frames=20)) is True
        assert scheduler.submit_partial(job(1, final=False, frames=40)) is False
        assert scheduler.dropped_partial == 1
        assert scheduler.depth == 1
        taken = await scheduler.next()
        assert taken.total_frames == 40  # the newest window, not a backlog
        assert scheduler.depth == 0

    asyncio.run(scenario())


def test_final_always_beats_a_waiting_partial() -> None:
    async def scenario() -> None:
        scheduler = AsrScheduler()
        scheduler.submit_partial(job(1, final=False, frames=80))
        scheduler.submit_final(job(1, final=True, frames=90))
        taken = await scheduler.next()
        assert taken.final is True  # partial was obsolete and dropped

    asyncio.run(scenario())


def test_clearing_partials_never_touches_finals() -> None:
    scheduler = AsrScheduler()
    scheduler.submit_final(job(7, final=True))
    scheduler.submit_partial(job(8, final=False))
    assert scheduler.drop_partial() is True
    assert scheduler.pending_finals == 1
    assert scheduler.partial is None
    assert scheduler.drop_partial() is False


def test_partial_from_a_newer_utterance_survives_drop_for_older() -> None:
    scheduler = AsrScheduler()
    scheduler.submit_partial(job(2, final=False))
    assert scheduler.drop_partial(1) is False  # nothing older to drop
    assert scheduler.partial is not None


def test_waiting_actor_is_woken_by_a_final() -> None:
    async def scenario() -> None:
        scheduler = AsrScheduler()
        waiter = asyncio.create_task(scheduler.next())
        await asyncio.sleep(0)
        assert not waiter.done()
        scheduler.submit_final(job(1, final=True))
        taken = await asyncio.wait_for(waiter, 1)
        assert taken.utterance_id == 1
        assert scheduler.started_final == 1

    asyncio.run(scenario())


def test_backlog_warning_does_not_drop_finals() -> None:
    scheduler = AsrScheduler(max_finals=2)
    for utterance in range(1, 6):
        scheduler.submit_final(job(utterance, final=True))
    assert scheduler.pending_finals == 5
    assert scheduler.backlog_warnings == 3


def test_submit_landing_between_check_and_wait_is_not_lost() -> None:
    """Regression: clearing the wake-up flag after the queue check lost a job.

    The scheduler used to do ``check queues → clear() → await wait()``.  A final
    submitted in that window set the flag, the actor immediately cleared it, and
    the job waited for a *later* submit — which may never come.  The final
    transcript then sat in the queue forever: the conversation looked deaf.
    """

    async def scenario() -> None:
        scheduler = AsrScheduler()
        real_clear = scheduler._wakeup.clear
        injected = {"done": False}

        def racing_clear() -> None:
            # Simulate the submit that lands exactly in the vulnerable window.
            if not injected["done"]:
                injected["done"] = True
                scheduler.submit_final(job(7, final=True))
            real_clear()

        scheduler._wakeup.clear = racing_clear  # type: ignore[method-assign]
        received = await asyncio.wait_for(scheduler.next(), timeout=1.0)
        assert received.utterance_id == 7 and received.final
        assert scheduler.pending_finals == 0

    asyncio.run(scenario())


def test_next_waits_then_returns_a_later_final() -> None:
    """The actor must block when idle and wake on the next submit."""

    async def scenario() -> None:
        scheduler = AsrScheduler()
        task = asyncio.create_task(scheduler.next())
        await asyncio.sleep(0.02)
        assert not task.done()
        scheduler.submit_final(job(3, final=True))
        received = await asyncio.wait_for(task, timeout=1.0)
        assert received.utterance_id == 3

    asyncio.run(scenario())
