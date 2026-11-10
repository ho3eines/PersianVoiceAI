"""Bug 4 — a final must not wait for a partial that is already decoding.

Whisper runs in a single actor on two cores, so a partial decode of up to
``PVAI_ASR_PARTIAL_WINDOW_S`` seconds of audio blocks the final transcript that
actually unblocks the conversation.  Two things were missing:

1. **a measurement** — ``asr_final_latency_ms`` bundled the queue wait, the
   wait behind the in-flight partial, and the decode into one number, so the
   delay could not be attributed;
2. **a remedy** — the partial could not be stopped once it had started.

The tests below drive the real pipeline with a fake Whisper that honours the
cancellation token, and assert both the behaviour and the recorded metrics.
"""

from __future__ import annotations

import asyncio
import threading
import time

from server.streaming_voice import AsrJob, AsrScheduler, Pipeline
from voicekit import FakeSpeaker, StreamClient, frame, make_settings, sse, until


class CancellableAsr:
    """Whisper-like decoder: long (cancellable) partial window, short final.

    ``transcribe_pcm(pcm, token, job)`` is the optional three-argument contract
    the pipeline probes for; the partial returns after 150 ms *unless* cancelled,
    which is what the preemption is supposed to achieve.
    """

    def __init__(self, texts: list[str], partial_delay: float = 0.15) -> None:
        self.texts = list(texts)
        self.partial_delay = partial_delay
        self.calls: list[tuple[bool, float]] = []
        self.partial_started = threading.Event()
        self.partial_cancelled = 0
        self._lock = threading.Lock()

    def transcribe_pcm(self, pcm: bytes, token: threading.Event | None = None, job=None) -> str:
        final = True if job is None else bool(job.final)
        with self._lock:
            self.calls.append((final, time.perf_counter()))
            text = self.texts.pop(0) if len(self.texts) > 1 else self.texts[0]
        if not final and token is not None:
            self.partial_started.set()
            if token.wait(self.partial_delay):
                self.partial_cancelled += 1
                raise RuntimeError("CancelledError")  # mirrors a real aborted decode
            time.sleep(self.partial_delay)
            return text
        time.sleep(0.01)
        return text


def build(asr, **settings_overrides):
    events: list[dict] = []
    pipeline = Pipeline(
        make_settings(**settings_overrides),
        asr,
        FakeSpeaker(),
        StreamClient(body=sse("باشه. ")),
        events.append,
    )
    return pipeline, events


def kinds(events: list[dict], kind: str) -> list[dict]:
    return [event for event in events if event["type"] == kind]


# ------------------------------------------------------------------ measurement
def test_final_wait_behind_a_partial_is_measured_and_not_hidden() -> None:
    """With preemption disabled the metric must expose the delay it caused."""

    async def scenario() -> None:
        asr = CancellableAsr(["جمله اول"], partial_delay=0.25)
        pipeline, events = build(asr, voice_asr_interval_s=0.05)
        pipeline.preempt_partials = False  # document the cost of not preempting
        pipeline.start()
        try:
            for _ in range(30):  # long enough to submit several partials
                pipeline.feed(frame())
            await until(lambda: asr.partial_started.is_set())
            for _ in range(32):  # endpoint: the final is queued now
                pipeline.feed(frame(0))
            await until(lambda: len(kinds(events, "response_end")) == 1, timeout=5)
            behind = pipeline.metrics.summary("asr_final_wait_behind_partial_ms")
            assert behind.count == 1
            assert behind.last >= 100, behind.last  # it really waited
            dispatch = pipeline.metrics.summary("asr_final_dispatch_wait_ms")
            assert dispatch.count == 1
            assert any(e["type"] == "asr_final_waited_behind_partial" for e in events)
            assert asr.partial_cancelled == 0
        finally:
            await pipeline.close()

    asyncio.run(scenario())


def test_a_final_cancels_the_partial_that_is_already_decoding() -> None:
    async def scenario() -> None:
        asr = CancellableAsr(["جمله اول"], partial_delay=2.0)  # a 6 s window, worst case
        pipeline, events = build(asr, voice_asr_interval_s=0.05)
        pipeline.start()
        try:
            for _ in range(30):
                pipeline.feed(frame())
            await until(lambda: asr.partial_started.is_set())
            queued_at = time.perf_counter()
            for _ in range(32):
                pipeline.feed(frame(0))
            await until(lambda: len(kinds(events, "response_end")) == 1, timeout=3)
            elapsed = time.perf_counter() - queued_at
            # The partial alone would have needed 2 s; the answer must not.
            assert elapsed < 1.0, f"final waited {elapsed:.2f}s behind the partial"
            assert asr.partial_cancelled == 1
            assert pipeline.metrics.counter("asr_partial_preempted") == 1
            assert pipeline.metrics.counter("asr_partial_cancelled") == 1
            assert any(e["type"] == "asr_partial_preempted" for e in events)
            assert any(e["type"] == "asr_partial_cancelled" for e in events)
            # The cancelled partial is reported as dropped, never as an ASR error.
            assert pipeline.metrics.counter("asr_errors") == 0
            assert pipeline.scheduler.preempted_partial == 1
            # A cancelled partial is display-only: the final transcript still wins.
            assert [e["text"] for e in kinds(events, "transcript_final")] == ["جمله اول"]
        finally:
            await pipeline.close()

    asyncio.run(scenario())


def test_no_partial_is_submitted_while_a_final_is_waiting() -> None:
    """Obsolete partials are dropped at submission, not queued then thrown away."""
    scheduler = AsrScheduler()
    scheduler.submit_final(
        AsrJob(utterance_id=5, pcm=b"\x00" * 640, final=True, submitted_at=0.0, total_frames=5)
    )
    older = AsrJob(utterance_id=5, pcm=b"\x00" * 640, final=False, submitted_at=0.0, total_frames=5)
    newer = AsrJob(utterance_id=6, pcm=b"\x00" * 640, final=False, submitted_at=0.0, total_frames=5)
    assert scheduler.submit_partial(older) is False
    assert scheduler.partial is None
    assert scheduler.submit_partial(newer) is True  # a newer utterance is still useful


def test_preempt_only_targets_a_started_partial() -> None:
    async def scenario() -> None:
        scheduler = AsrScheduler()
        final = AsrJob(utterance_id=1, pcm=b"", final=True, submitted_at=0.0, total_frames=1)
        partial = AsrJob(utterance_id=2, pcm=b"", final=False, submitted_at=0.0, total_frames=1)
        scheduler.begin(partial)
        assert scheduler.preempt() is True
        assert scheduler.end(partial) is True  # consumed as preempted
        assert scheduler.preempted_partial == 1
        scheduler.begin(final)
        assert scheduler.preempt() is False  # a final is never preempted
        assert scheduler.end(final) is False

    asyncio.run(scenario())


class LegacyAsr:
    """Historical one-argument backend: the token cannot stop its thread.

    The conversation must still be correct — the partial's result is discarded
    at dispatch (``AsrScheduler.end``) — and the extra latency it caused must
    still be reported instead of hiding inside ``asr_final_latency_ms``.
    """

    def __init__(self, text: str, partial_delay: float = 0.3) -> None:
        self.text = text
        self.partial_delay = partial_delay
        self.partial_started = threading.Event()
        self.finals = 0

    def transcribe_pcm(self, pcm: bytes) -> str:  # no token parameter
        if not self.partial_started.is_set():
            self.partial_started.set()
            time.sleep(self.partial_delay)
        else:
            self.finals += 1
        return self.text


def test_backend_without_a_cancellation_token_still_works_and_still_measures() -> None:
    async def scenario() -> None:
        asr = LegacyAsr("سلام")
        pipeline, events = build(asr, voice_asr_interval_s=0.05)
        pipeline.start()
        try:
            for _ in range(30):
                pipeline.feed(frame())
            await until(lambda: asr.partial_started.is_set())
            for _ in range(32):
                pipeline.feed(frame(0))
            await until(lambda: len(kinds(events, "response_end")) == 1, timeout=5)
            # The partial could not be stopped, so the final paid for it and the
            # metric says so instead of silently inflating the final latency.
            behind = pipeline.metrics.summary("asr_final_wait_behind_partial_ms")
            assert behind.count == 1
            assert behind.last >= 100, behind.last
            assert pipeline.metrics.counter("asr_partial_cancelled") == 1
            assert pipeline.metrics.counter("asr_errors") == 0
            # The partial's text must never be shown as a final transcript.
            assert [e["text"] for e in kinds(events, "transcript_final")] == ["سلام"]
        finally:
            await pipeline.close()

    asyncio.run(scenario())
