"""Long-running stability: bounded queues, bounded RAM, no duplicate commits.

Requirement 20 of the audit checklist: a long conversation must not create
unbounded queues or memory growth.  A live assistant runs for hours, and the
previous design had three structures that grew without limit (conversation
history in RAM, the dedupe set of utterance ids, and no bound check at all on
scheduler backlog).

These tests are deliberately synthetic — a fake microphone level, fake ASR and
fake TTS — so they prove *resource accounting*, not acoustic quality.
"""

from __future__ import annotations

import asyncio

import httpx

from server.streaming_voice import Pipeline
from voicekit import FakeAsr, FakeSpeaker, feed_utterance, make_settings, sse, until


def build(*, max_stored: int = 40) -> tuple[Pipeline, list[dict], FakeSpeaker, FakeAsr]:
    events: list[dict] = []
    asr = FakeAsr(text="جملهٔ آزمایشی")
    speaker = FakeSpeaker()
    client = httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text=sse("پاسخ کوتاه. "))
        ),
    )
    pipeline = Pipeline(
        make_settings(
            voice_asr_interval_s=10.0,
            history_max_stored_messages=max_stored,
            voice_speech_queue=8,
        ),
        asr,
        speaker,
        client,
        events.append,
    )
    return pipeline, events, speaker, asr


def test_many_turns_keep_every_queue_and_ram_window_bounded() -> None:
    async def scenario() -> None:
        turns = 120
        pipeline, events, speaker, asr = build(max_stored=40)
        pipeline.start()
        try:
            completed = 0
            for _ in range(turns):
                start = len([e for e in events if e["type"] == "response_end"])
                feed_utterance(pipeline)
                await until(
                    lambda start=start: len(
                        [e for e in events if e["type"] == "response_end"]
                    )
                    > start,
                    timeout=10,
                )
                completed += 1
                # Backlog invariants after every turn.
                assert pipeline.scheduler.depth <= 2
                assert pipeline.scheduler.pending_finals == 0
                assert pipeline.speech.qsize() <= pipeline.speech.maxsize
                assert len(pipeline._finalized_ids) <= pipeline._finalized.maxlen
                assert len(pipeline.conversation.messages) <= 40
                assert len(pipeline.conversation._utterance_ids) <= 4096
            assert completed == turns
            # Every user turn reached the conversation exactly once (the RAM
            # window keeps the newest 40, so 20 exchanges = 40 messages).
            users = [m for m in pipeline.conversation.messages if m.role == "user"]
            assert len(users) == 20
            ids = [m.id for m in pipeline.conversation.messages]
            assert ids == sorted(ids) and len(ids) == len(set(ids))
            # One assistant reply per user turn, none duplicated.
            assert len(speaker.spoken) == turns
            assert len(events) < 60 * turns  # no event storm per turn
            # Metric registry is bounded: a fixed set of names, summarised.
            snapshot = pipeline.metrics.snapshot()
            assert len(snapshot["observations"]) <= 40
            assert all(value["count"] <= turns for value in snapshot["observations"].values())
        finally:
            await pipeline.close()
            await pipeline.client.aclose()

    asyncio.run(scenario())


def test_repeated_turns_never_duplicate_a_committed_message() -> None:
    async def scenario() -> None:
        pipeline, events, _, asr = build()
        pipeline.start()
        try:
            for _ in range(5):
                start = len([e for e in events if e["type"] == "response_end"])
                feed_utterance(pipeline)
                await until(
                    lambda start=start: len(
                        [e for e in events if e["type"] == "response_end"]
                    )
                    > start,
                )
            # Replay the last final job by hand: the dedupe must reject it.
            assert pipeline.conversation.add_user("تکراری", utterance_id=1) is None
            user_ids = [m.utterance_id for m in pipeline.conversation.messages if m.role == "user"]
            assert len(user_ids) == len(set(user_ids)) == 5
            turn_ids = [m.turn_id for m in pipeline.conversation.messages if m.role == "assistant"]
            assert len(turn_ids) == len(set(turn_ids)) == 5
        finally:
            await pipeline.close()
            await pipeline.client.aclose()

    asyncio.run(scenario())
