"""Core conversation-engine behaviour: partials, final ASR, SSE, playback, history."""

from __future__ import annotations

import asyncio
import threading
from array import array
from types import SimpleNamespace

import httpx

from server.config import Settings
from server.streaming_voice import AssistantTurn, Pipeline, agreement

LOUD = 2000


def frame(level: int = LOUD) -> bytes:
    return array("h", [level] * 320).tobytes()


async def until(predicate, timeout: float = 3.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


def settings(**overrides) -> Settings:
    base = {"history_persist": False, "voice_asr_interval_s": 0.3}
    base.update(overrides)
    return Settings(**base)


class FakeAudio:
    def __init__(self, text: str = "سلام دوست من", delay: float = 0.0) -> None:
        self.text = text
        self.delay = delay
        self.calls: list[int] = []
        self.gate: threading.Event | None = None

    def transcribe_pcm(self, pcm: bytes) -> str:
        self.calls.append(len(pcm))
        if self.gate is not None:
            self.gate.wait(3)
        if self.delay:
            import time

            time.sleep(self.delay)
        return self.text


class FakeSpeaker:
    def __init__(self, on_speak=None) -> None:
        self.spoken: list[str] = []
        self.cancelled: list[bool] = []
        self.on_speak = on_speak

    def speak(self, text, token, *, on_first_chunk=None, on_first_frame=None, on_complete=None):
        self.cancelled.append(token.is_set())
        if not token.is_set():
            self.spoken.append(text)
            if on_first_chunk is not None:
                on_first_chunk(1.0)
            if on_first_frame is not None:
                on_first_frame(1.05)
            if self.on_speak is not None:
                self.on_speak(text)
        if on_complete is not None:
            on_complete(1.1, 120.0)


def sse(*pieces: str, done: bool = True) -> str:
    body = "".join(
        'data: {"choices":[{"delta":{"content":"%s"}}]}\n\n' % piece for piece in pieces
    )
    return body + ("data: [DONE]\n\n" if done else "")


def make_pipeline(audio=None, speaker=None, client=None, cfg=None, events=None):
    events = events if events is not None else []
    pipeline = Pipeline(
        cfg or settings(),
        audio or FakeAudio(),
        speaker or FakeSpeaker(),
        client,
        events.append,
    )
    return pipeline, events


def test_local_agreement() -> None:
    assert agreement("سلام دوست عزیز", "سلام دوست من") == "سلام دوست"
    assert agreement("الف", "ب") == ""


def test_partial_transcript_while_microphone_still_active() -> None:
    async def scenario() -> None:
        pipeline, events = make_pipeline()
        pipeline.start()
        try:
            for _ in range(20):
                pipeline.feed(frame())
            await until(lambda: any(e["type"] == "transcript_partial" for e in events))
            assert not any(e["type"] == "speech_end" for e in events)
            assert pipeline.reply is None  # never act on partial text
            assert pipeline.state.value == "listening"
            for _ in range(20):
                pipeline.feed(frame())
            await until(lambda: len(pipeline.audio.calls) >= 2)
            partials = [e for e in events if e["type"] == "transcript_partial"]
            assert partials[-1]["agreed_prefix"] == "سلام دوست من"
            assert partials[-1]["windowed"] is False
        finally:
            await pipeline.close()

    asyncio.run(scenario())


def test_final_transcript_response_audio_and_history() -> None:
    async def scenario() -> None:
        events: list[dict] = []
        speaker = FakeSpeaker()
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, text=sse("درود! ", "خوبی؟"))
            ),
        ) as client:
            pipeline, _ = make_pipeline(speaker=speaker, client=client, events=events)
            pipeline.start()
            try:
                for _ in range(20):
                    pipeline.feed(frame())
                for _ in range(32):
                    pipeline.feed(frame(0))
                await until(lambda: any(e["type"] == "response_end" for e in events))
                assert speaker.spoken == ["درود!", "خوبی؟"]
                assert pipeline.history[0] == {"role": "user", "content": "سلام دوست من"}
                assert pipeline.history[-1]["role"] == "assistant"
                assert "درود!" in pipeline.history[-1]["content"]
                assert any(e["type"] == "transcript_final" for e in events)
                final = next(e for e in events if e["type"] == "transcript_final")
                assert final["utterance_id"] == 1
                # 20 loud frames + the required 100 ms tail; endpoint silence is trimmed.
                assert final["audio_ms"] == (20 + 5) * 20
            finally:
                await pipeline.close()

    asyncio.run(scenario())


def test_incomplete_sse_does_not_commit_history() -> None:
    async def scenario() -> None:
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, text='data: {"choices":[]}\n\n')
            ),
        ) as client:
            events: list[dict] = []
            pipeline, _ = make_pipeline(client=client, events=events)
            turn = AssistantTurn(
                turn_id=1,
                utterance_id=1,
                generation=pipeline.generation,
                endpoint_at=pipeline.clock(),
            )
            await pipeline.respond(turn, pipeline.cancel)
            assert pipeline.history == []
            assert turn.status == "failed"
            ends = [e for e in events if e["type"] == "response_end"]
            assert len(ends) == 1
            assert ends[0]["status"] == "failed"
            assert any(e["type"] == "error" for e in events)

    asyncio.run(scenario())


def test_async_sse_stream_cancelled_without_waiting_for_next_token() -> None:
    async def scenario() -> None:
        entered, exited = asyncio.Event(), asyncio.Event()

        class Response:
            def raise_for_status(self) -> None:
                pass

            async def aiter_lines(self):
                entered.set()
                await asyncio.Event().wait()
                yield ""  # pragma: no cover - never reached

        class Context:
            async def __aenter__(self):
                return Response()

            async def __aexit__(self, *args):
                exited.set()

        pipeline, events = make_pipeline(
            client=SimpleNamespace(stream=lambda *a, **k: Context())
        )
        pipeline.conversation.add_user("سلام", utterance_id=1)
        turn = AssistantTurn(
            turn_id=1,
            utterance_id=1,
            generation=pipeline.generation,
            endpoint_at=pipeline.clock(),
        )
        pipeline.pending_turn = turn
        pipeline.reply = asyncio.create_task(pipeline.respond(turn, pipeline.cancel))
        await entered.wait()
        old_token = pipeline.cancel
        assert pipeline.barge_in(reason="user_speech") is True
        await asyncio.wait_for(exited.wait(), 0.5)
        assert old_token.is_set()
        assert turn.token.is_set()
        assert turn.status == "interrupted"
        assert any(e["type"] == "interrupted" for e in events)
        await pipeline.close()

    asyncio.run(scenario())


def test_incremental_playback_before_remaining_tokens() -> None:
    async def scenario() -> None:
        spoken = threading.Event()

        class Response:
            def raise_for_status(self) -> None:
                pass

            async def aiter_lines(self):
                yield 'data: {"choices":[{"delta":{"content":"سلام! "}}]}'
                await until(spoken.is_set)
                yield 'data: {"choices":[{"delta":{"content":"ادامه"}}]}'
                yield "data: [DONE]"

        class Context:
            async def __aenter__(self):
                return Response()

            async def __aexit__(self, *args):
                pass

        events: list[dict] = []
        speaker = FakeSpeaker(on_speak=lambda text: spoken.set())
        pipeline, _ = make_pipeline(
            speaker=speaker,
            client=SimpleNamespace(stream=lambda *a, **k: Context()),
            events=events,
        )
        pipeline.conversation.add_user("سلام", utterance_id=1)
        pipeline.start()
        try:
            turn = AssistantTurn(
                turn_id=1,
                utterance_id=1,
                generation=pipeline.generation,
                endpoint_at=pipeline.clock(),
            )
            pipeline.pending_turn = turn
            await pipeline.respond(turn, pipeline.cancel)
            assert spoken.is_set()
            await until(lambda: any(e["type"] == "response_end" for e in events))
            assert any(e["type"] == "first_token" for e in events)
            assert any(e["type"] == "first_audible" for e in events)
        finally:
            await pipeline.close()

    asyncio.run(scenario())


def test_overflow_resets_detector_but_keeps_pending_final_jobs() -> None:
    async def scenario() -> None:
        audio = FakeAudio()
        pipeline, events = make_pipeline(audio=audio)
        pipeline.start()
        try:
            for _ in range(20):
                pipeline.feed(frame())
            for _ in range(32):
                pipeline.feed(frame(0))
            pending_before = pipeline.scheduler.pending_finals
            pipeline.overflow()
            assert pipeline.detector.frames == []
            assert pipeline.scheduler.pending_finals == pending_before
            assert pipeline.metrics.counter("audio_overflow_count") == 1
            assert any(e["type"] == "error" for e in events)
        finally:
            await pipeline.close()

    asyncio.run(scenario())


def test_metrics_snapshot_contains_pipeline_stage_timings() -> None:
    async def scenario() -> None:
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, text=sse("سلام! ", "خوبی؟"))
            ),
        ) as client:
            events: list[dict] = []
            pipeline, _ = make_pipeline(client=client, events=events)
            pipeline.start()
            try:
                for _ in range(20):
                    pipeline.feed(frame())
                for _ in range(32):
                    pipeline.feed(frame(0))
                await until(lambda: any(e["type"] == "response_end" for e in events))
                snapshot = pipeline.metrics_snapshot()
                assert snapshot["observations"]["asr_ms"]["count"] >= 1
                assert snapshot["observations"]["llm_ttft_ms"]["count"] == 1
                assert snapshot["observations"]["first_audible_ms"]["count"] >= 1
                assert snapshot["counters"]["utterances"] == 1
                assert snapshot["counters"]["asr_final_job_count"] == 1
            finally:
                await pipeline.close()

    asyncio.run(scenario())


def test_state_machine_reports_violations_without_crashing() -> None:
    from server.streaming_voice import StateMachine, VoiceState

    seen: list[dict] = []
    machine = StateMachine(seen.append)
    machine.transition(VoiceState.LISTENING, reason="speech_onset")
    assert machine.state is VoiceState.LISTENING
    machine.transition(VoiceState.SPEAKING, reason="illegal")  # reported, not raised
    assert machine.violations == 1
    violation = next(e for e in seen if e["type"] == "state_violation")
    assert violation["target"] == "speaking"
    assert violation["state"] == "listening"
