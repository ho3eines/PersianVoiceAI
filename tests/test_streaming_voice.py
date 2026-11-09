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


def test_llm_failure_releases_the_state_and_the_next_turn_recovers() -> None:
    """Generation errors must not wedge the conversation (audit §8 and §15-15)."""
    from server.streaming_voice import VoiceState

    async def scenario() -> None:
        state = {"calls": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            state["calls"] += 1
            if state["calls"] == 1:
                return httpx.Response(500, text="llama-server exploded")
            return httpx.Response(200, text=sse("بازیابی شد. "))

        events: list[dict] = []
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1", transport=httpx.MockTransport(handler)
        ) as client:
            pipeline, _ = make_pipeline(client=client, events=events)
            pipeline.start()
            try:
                for _ in range(20):
                    pipeline.feed(frame())
                for _ in range(32):
                    pipeline.feed(frame(0))
                await until(lambda: any(e["type"] == "error" for e in events))
                assert pipeline.pending_turn is None
                # The pipeline is listening again instead of claiming THINKING.
                assert pipeline.state in (VoiceState.IDLE, VoiceState.LISTENING)
                assert pipeline.states.violations == 0

                for _ in range(20):
                    pipeline.feed(frame())
                for _ in range(32):
                    pipeline.feed(frame(0))
                await until(
                    lambda: any(
                        e["type"] == "response_end" and e["status"] == "complete"
                        for e in events
                    )
                )
                assert state["calls"] == 2
                assistant = [m for m in pipeline.conversation.messages if m.role == "assistant"]
                assert [m.status for m in assistant] == ["complete"]
                assert "بازیابی" in assistant[0].content
            finally:
                await pipeline.close()

    asyncio.run(scenario())


# ------------------------------------------------------------------- resume
def test_resumed_pipeline_continues_ids_and_keeps_the_first_new_utterance(
    tmp_path,
) -> None:
    """A restart must not reuse utterance ids that the store already owns.

    Before the fix ``create_pipeline`` always built an empty conversation and
    the pipeline always started its utterance counter at 1; after a resume the
    first new final therefore looked like a replay of a stored utterance and was
    silently dropped by dedupe (the transcript event still fired, so nothing
    looked broken).
    """
    import asyncio

    import httpx

    from server.conversation import Conversation
    from server.conversation import ConversationStore
    from voicekit import FakeAsr, FakeSpeaker, feed_utterance, make_settings, sse, until

    store = ConversationStore(tmp_path / "conversations")
    previous = Conversation(store=store, session_id="resume-1")
    previous.add_user("سلامِ قبلی", utterance_id=1, turn_id=1)
    previous.finish_assistant("پاسخِ قبلی", turn_id=1, utterance_id=1)
    previous.add_user("پرسش دوم", utterance_id=2, turn_id=2)
    previous.finish_assistant("پاسخ دوم", turn_id=2, utterance_id=2)

    settings = make_settings(voice_asr_interval_s=10.0)
    conversation = Conversation.load(
        store,
        "resume-1",
        max_messages=settings.history_max_messages,
        token_budget=settings.history_token_budget,
        max_stored_messages=settings.history_max_stored_messages,
        include_interrupted=settings.history_include_interrupted,
    )
    client = httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=sse("پاسخ تازه. "))),
    )

    events: list[dict] = []

    async def scenario() -> None:
        pipeline = Pipeline(
            settings,
            FakeAsr(text="سلامِ جدید"),
            FakeSpeaker(),
            client,
            events.append,
            conversation=conversation,
        )
        # The counter starts after the stored utterances, not at 1.
        assert pipeline._next_utterance_id == 3
        pipeline.start()
        try:
            feed_utterance(pipeline)
            await until(
                lambda: any(m.content == "سلامِ جدید" for m in conversation.messages),
                timeout=5,
            )
            # The reply is committed by the play actor when its last sentence
            # finishes, so wait for that event rather than for a state.
            await until(
                lambda: any(event["type"] == "response_end" for event in events),
                timeout=5,
            )
            # The committed text keeps the sentence terminator the model
            # produced; the reply is only committed once playback finished.
            await until(
                lambda: any(m.content.startswith("پاسخ تازه") for m in conversation.messages),
                timeout=5,
            )
        finally:
            await pipeline.close()

    asyncio.run(scenario())
    texts = [message.content for message in conversation.messages]
    assert texts[:4] == ["سلامِ قبلی", "پاسخِ قبلی", "پرسش دوم", "پاسخ دوم"]
    assert "سلامِ جدید" in texts
    assert any(text.startswith("پاسخ تازه") for text in texts)
    # The resumed turn got a fresh turn id (the store already owns 1 and 2),
    # so the answer is not deduplicated away.
    assert conversation.messages[-1].turn_id == 3
    assert conversation.messages[-2].utterance_id == 3
    # And the context handed to the model still starts with the old exchange.
    assert conversation.context()[0]["content"] == "سلامِ قبلی"


def test_fresh_pipeline_still_starts_its_utterance_ids_at_one() -> None:
    from voicekit import make_settings

    pipeline = Pipeline(
        make_settings(), None, None, None, lambda event: None
    )
    assert pipeline._next_utterance_id == 1


# ------------------------------------------------------- shutdown hygiene
def test_close_drains_transient_audio_but_keeps_the_transcript() -> None:
    """Shutdown releases buffers *and* the closing session's history.

    ``Pipeline.close`` used to keep the queued playback sentences and the
    half-finished utterance alive for as long as anything referenced the
    pipeline object (the LAN transport keeps one per connection).  Draining
    them is what makes the object collectable; the recorded messages must
    survive, because the last turn is exactly what a restart resumes.
    """

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
            for _ in range(20):
                pipeline.feed(frame())
            for _ in range(32):
                pipeline.feed(frame(0))
            await until(lambda: any(e["type"] == "response_end" for e in events))
            recorded = list(pipeline.conversation.messages)
            pipeline.speech.put_nowait("یک جملهٔ پخش‌نشده")
            await pipeline.close()
        assert pipeline.speech.empty()
        assert len(pipeline.scheduler.finals) == 0
        assert pipeline.scheduler.partial is None
        assert pipeline.utterance is None
        assert pipeline.conversation.messages == recorded
        assert any(message.role == "assistant" for message in recorded)

    asyncio.run(scenario())


def test_recognize_releases_each_job_before_decoding_the_next() -> None:
    """The ASR worker must not pin the previous job's PCM while it waits.

    A final job carries the whole utterance as bytes.  Keeping the last one in a
    loop variable meant the single ASR actor held up to a full utterance of
    audio while idle on every connection — unexplained RSS growth on a long run.
    """

    async def scenario() -> None:
        import gc
        import weakref

        from server.streaming_voice import AsrJob

        class Pcm(bytearray):
            """Markable payload: it must be collectable once decoded."""

        async with httpx.AsyncClient(
            base_url="http://127.0.0.1",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, text=sse("درود!"))
            ),
        ) as client:
            pipeline, events = make_pipeline(client=client)
            payload = Pcm(640)
            marker = weakref.ref(payload)
            pipeline.start()
            try:
                pipeline.scheduler.submit_final(
                    AsrJob(utterance_id=1, pcm=payload, final=True,
                           submitted_at=0.0, total_frames=1)
                )
                del payload
                await until(lambda: any(e["type"] == "response_end" for e in events))
                await until(lambda: pipeline.state.value in {"idle", "listening"})
                gc.collect()
                assert marker() is None, "the decoded utterance's PCM is still referenced"
            finally:
                await pipeline.close()

    asyncio.run(scenario())


def test_respond_does_not_copy_every_token_into_a_list() -> None:
    """A long reply must be assembled as one string, not as a piece list.

    ``respond`` kept every SSE delta in a list that was never read; a 400-token
    answer therefore retained hundreds of tiny strings per turn for no reason.
    """
    import inspect

    from server.streaming_voice import Pipeline as PipelineClass

    source = inspect.getsource(PipelineClass.respond)
    assert "pieces = []" not in source
    assert "pieces.append" not in source
