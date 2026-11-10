"""Stage-1 regression tests for the P0-01 utterance / generation lifecycle.

Each test encodes one mandatory scenario from the audit:

1. a final ASR decode in flight survives a new user speech onset;
2. multiple consecutive utterances are all preserved, in order;
3. replacing a partial never loses the final;
4. speech longer than the 15 s segment limit keeps every segment;
5. new user speech during pending ASR does not invalidate an earlier final;
6. interrupting the assistant does not corrupt the conversation history.
"""

from __future__ import annotations

import asyncio
import threading

from server.streaming_voice import Pipeline
from voicekit import FakeAsr, FakeSpeaker, StreamClient, feed_utterance, make_settings, sse, until


def build(asr=None, speaker=None, client=None, **settings_overrides):
    events: list[dict] = []
    pipeline = Pipeline(
        make_settings(**settings_overrides),
        asr or FakeAsr(),
        speaker or FakeSpeaker(),
        client,
        events.append,
    )
    return pipeline, events


def finals(events: list[dict]) -> list[dict]:
    return [event for event in events if event["type"] == "transcript_final" and not event.get("empty")]


def user_messages(pipeline: Pipeline) -> list[str]:
    return [m.content for m in pipeline.conversation.messages if m.role == "user"]


# --------------------------------------------------------------------- 1 and 5
def test_final_asr_in_flight_survives_new_speech_onset() -> None:
    async def scenario() -> None:
        asr = FakeAsr(texts=["جمله اول"])
        asr.block()
        speaker = FakeSpeaker()
        pipeline, events = build(asr=asr, speaker=speaker)
        pipeline.start()
        try:
            feed_utterance(pipeline)                     # utterance 1 → final queued
            await until(lambda: asr.entered.is_set())    # decode A is in flight
            for _ in range(10):                          # user starts utterance 2
                pipeline.feed(bytearray(b"\x00\x01" * 320))
            for _ in range(10):
                pipeline.feed(__import__("voicekit").frame())
            assert pipeline.utterance is not None
            assert pipeline.utterance.id == 2
            asr.release()                                # decode A completes late
            await until(lambda: pipeline.conversation.messages != [])
            assert user_messages(pipeline) == ["جمله اول"]
            assert finals(events)[0]["utterance_id"] == 1
            # A newer utterance started, so no reply is generated for the stale one.
            assert any(e["type"] == "response_skipped" for e in events)
            assert speaker.spoken == []
        finally:
            asr.release()
            await pipeline.close()

    asyncio.run(scenario())


def test_new_speech_during_pending_asr_preserves_all_finals_in_order() -> None:
    async def scenario() -> None:
        asr = FakeAsr(texts=["اول", "دوم"])
        asr.block()
        pipeline, events = build(asr=asr)
        pipeline.start()
        try:
            feed_utterance(pipeline)
            await until(lambda: asr.entered.is_set())
            feed_utterance(pipeline)  # second utterance while the first is decoding
            asr.release()
            await until(lambda: len(finals(events)) == 2)
            await until(lambda: len(pipeline.conversation.messages) >= 2)
            assert user_messages(pipeline) == ["اول", "دوم"]
            assert [event["utterance_id"] for event in finals(events)] == [1, 2]
        finally:
            asr.release()
            await pipeline.close()

    asyncio.run(scenario())


# --------------------------------------------------------------------------- 2
def test_multiple_consecutive_utterances_are_all_preserved() -> None:
    async def scenario() -> None:
        asr = FakeAsr(texts=["سلام", "حالت چطوره", "خوبی"])
        speaker = FakeSpeaker()
        client = StreamClient(body=sse("باشه. "))
        pipeline, events = build(asr=asr, speaker=speaker, client=client)
        pipeline.start()
        try:
            for expected in range(1, 4):
                feed_utterance(pipeline)
                await until(lambda: len([
                    m for m in pipeline.conversation.messages if m.role == "assistant"
                ]) >= expected)
            assert user_messages(pipeline) == ["سلام", "حالت چطوره", "خوبی"]
            assistant = [m for m in pipeline.conversation.messages if m.role == "assistant"]
            assert len(assistant) == 3
            assert all(m.status == "complete" for m in assistant)
            assert len(pipeline.scheduler.finals) == 0  # nothing silently dropped
        finally:
            await pipeline.close()

    asyncio.run(scenario())


# --------------------------------------------------------------------------- 3
def test_partial_replacement_never_loses_the_final() -> None:
    async def scenario() -> None:
        asr = FakeAsr(text="سلام دوست من")
        asr.block()
        pipeline, events = build(asr=asr, voice_asr_interval_s=0.2)
        pipeline.start()
        try:
            for _ in range(80):
                pipeline.feed(__import__("voicekit").frame())
            assert pipeline.scheduler.partial is not None  # latest-only mailbox holds one
            assert pipeline.scheduler.dropped_partial >= 1
            for _ in range(32):
                pipeline.feed(__import__("voicekit").frame(0))
            # Submitting the final drops the now-obsolete partial, not the final.
            assert pipeline.scheduler.partial is None
            assert pipeline.scheduler.pending_finals == 1
            asr.release()
            await until(lambda: len(finals(events)) == 1)
            assert user_messages(pipeline) == ["سلام دوست من"]
            assert pipeline.metrics.counter("asr_final_job_count") == 1
        finally:
            asr.release()
            await pipeline.close()

    asyncio.run(scenario())


# --------------------------------------------------------------------------- 4
def test_long_speech_beyond_segment_limit_keeps_every_segment() -> None:
    async def scenario() -> None:
        asr = FakeAsr(texts=["بخش اول", "بخش دوم"])
        pipeline, events = build(
            asr=asr,
            voice_vad_max_segment_s=2.0,  # 100 frames at 20 ms
        )
        pipeline.start()
        try:
            for _ in range(120):  # 2.4 s without any pause
                pipeline.feed(__import__("voicekit").frame())
            await until(lambda: len(finals(events)) == 1)
            ends = [e for e in events if e["type"] == "speech_end"]
            assert ends[0]["boundary"] == "max_segment"
            assert ends[0]["audio_ms"] == 100 * 20
            for _ in range(32):  # end the second segment with real silence
                pipeline.feed(__import__("voicekit").frame(0))
            await until(lambda: len(finals(events)) == 2)
            assert user_messages(pipeline) == ["بخش اول", "بخش دوم"]
            # No audio is lost at the split: the next segment starts with the pre-roll.
            assert pipeline.metrics.counter("asr_final_job_count") == 2
        finally:
            await pipeline.close()

    asyncio.run(scenario())


# --------------------------------------------------------------------------- 6
def test_assistant_interruption_does_not_corrupt_history() -> None:
    async def scenario() -> None:
        first_sentence_spoken = threading.Event()

        async def lines(payload: dict):
            yield 'data: {"choices":[{"delta":{"content":"درود! "}}]}'
            yield 'data: {"choices":[{"delta":{"content":"این پاسخ طولانی"}}]}'
            await asyncio.Event().wait()  # never completes: the user interrupts

        speaker = FakeSpeaker(on_speak=lambda text: first_sentence_spoken.set())
        client = StreamClient(lines=lines)
        pipeline, events = build(speaker=speaker, client=client)
        pipeline.start()
        try:
            feed_utterance(pipeline)
            await until(first_sentence_spoken.is_set)
            assert pipeline.state.value == "speaking"
            # A new utterance while the assistant is talking → confirmed barge-in.
            for _ in range(12):
                pipeline.feed(__import__("voicekit").frame())
            assert pipeline.state.value == "listening"
            await until(lambda: any(e["type"] == "interrupted" for e in events))
            interrupted = next(
                m for m in pipeline.conversation.messages if m.role == "assistant"
            )
            assert interrupted.status == "interrupted"
            assert interrupted.content == "درود!"
            # Interrupted text is kept for inspection but excluded from the model context.
            assert [m["content"] for m in pipeline.conversation.context()] == ["سلام دوست من"]
            assert any(e["type"] == "response_end" and e["status"] == "interrupted" for e in events)
            assert pipeline.generation == 1
            assert pipeline.pending_turn is None
        finally:
            await pipeline.close()

    asyncio.run(scenario())


def test_system_prompt_is_sent_to_the_model() -> None:
    async def scenario() -> None:
        client = StreamClient(body=sse("باشه."))
        pipeline, _ = build(client=client)
        pipeline.start()
        try:
            feed_utterance(pipeline)
            await until(lambda: any(
                m.role == "assistant" for m in pipeline.conversation.messages
            ))
            payload = client.payloads[0]
            assert payload["messages"][0]["role"] == "system"
            assert payload["messages"][0]["content"] == make_settings().system_prompt
            assert payload["messages"][-1] == {
                "role": "user",
                "content": "سلام دوست من",
            }
        finally:
            await pipeline.close()

    asyncio.run(scenario())


def test_duplicate_final_result_is_not_committed_twice() -> None:
    async def scenario() -> None:
        pipeline, events = build()
        pipeline.conversation.add_user("سلام", utterance_id=7)
        from server.streaming_voice import AsrJob

        job = AsrJob(utterance_id=7, pcm=b"", final=True, submitted_at=0.0, total_frames=1)
        await pipeline._on_final(job, "سلام")
        await pipeline._on_final(job, "سلام")
        assert len([m for m in pipeline.conversation.messages if m.role == "user"]) == 1
        assert pipeline.metrics.counter("asr_duplicate_final") == 1
        await pipeline.close()

    asyncio.run(scenario())


def test_empty_transcript_never_reaches_the_model() -> None:
    async def scenario() -> None:
        asr = FakeAsr(["", "سلام"])
        client = StreamClient(body=sse("باشه."))
        pipeline, events = build(asr=asr, client=client)
        pipeline.start()
        try:
            feed_utterance(pipeline)
            await until(lambda: client.opened == 0 and pipeline.state.value == "idle")
            assert client.opened == 0
            assert any(e["type"] == "transcript_final" and e.get("empty") for e in events)
            assert pipeline.metrics.counter("utterances_empty") == 1
        finally:
            await pipeline.close()

    asyncio.run(scenario())


def test_shutdown_marks_pending_reply_interrupted() -> None:
    async def scenario() -> None:
        async def lines(payload: dict):
            yield 'data: {"choices":[{"delta":{"content":"شروع پاسخ"}}]}'
            await asyncio.Event().wait()

        pipeline, events = build(client=StreamClient(lines=lines))
        pipeline.start()
        feed_utterance(pipeline)
        await until(lambda: pipeline.pending_turn is not None)
        # Wait for the first *delta*, not merely for the user message: closing
        # before any text arrived left nothing to mark as interrupted, which made
        # this assertion racy on a loaded machine.
        await until(
            lambda: pipeline.pending_turn is not None and bool(pipeline.pending_turn.text)
        )
        await pipeline.close()
        assistant = [m for m in pipeline.conversation.messages if m.role == "assistant"]
        assert assistant and assistant[0].status == "interrupted"

    asyncio.run(scenario())
