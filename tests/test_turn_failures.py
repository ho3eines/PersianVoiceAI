"""Failed turns must release the state machine (bugs 2 and 3).

* **Bug 2 — TTS exception.**  ``Pipeline.play`` committed the turn as ``failed``
  but returned to the top of the loop with the machine still in ``SPEAKING`` and
  ``pending_turn`` already cleared.  The next thing the user said was therefore
  counted as a barge-in against a reply that no longer existed (the P1-11 shape,
  TTS branch), and the state reported to the user/device was wrong.
* **Bug 3 — ``respond`` with nothing to send.**  ``if not messages: return``
  left ``pending_turn`` set and the machine in ``THINKING`` forever: no
  ``response_end`` for the announced turn, no reply, and the next utterance was
  mis-read as a barge-in.
"""

from __future__ import annotations

import asyncio

from server.conversation import Conversation
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


def kinds(events: list[dict], kind: str) -> list[dict]:
    return [event for event in events if event["type"] == kind]


class ExplodingSpeaker(FakeSpeaker):
    """Raises inside ``speak`` exactly like a missing Piper model does."""

    def speak(self, text, token, *, on_first_chunk=None, on_first_frame=None, on_complete=None):
        self.started.append(text)
        raise RuntimeError("piper model missing")


# --------------------------------------------------------------------------- bug 2
def test_tts_failure_leaves_speaking_and_is_not_a_barge_in() -> None:
    async def scenario() -> None:
        speaker = ExplodingSpeaker()
        client = StreamClient(body=sse("باشه. "))
        pipeline, events = build(speaker=speaker, client=client)
        pipeline.start()
        try:
            feed_utterance(pipeline)
            await until(lambda: bool(kinds(events, "response_end")))
            failed = kinds(events, "response_end")[0]
            assert failed["status"] == "failed"
            assert failed["reason"] == "tts_error"
            # The machine left SPEAKING and no turn is left behind.
            assert pipeline.state.value == "idle", pipeline.state.value
            assert pipeline.pending_turn is None
            assert any(e["type"] == "error" for e in events)
            assert pipeline.metrics.counter("tts_failures") == 1
            assert pipeline.states.violations == 0

            before = len(kinds(events, "interrupted"))
            for _ in range(12):  # next speech is a fresh turn, not a barge-in
                pipeline.feed(__import__("voicekit").frame())
            assert len(kinds(events, "interrupted")) == before
            assert pipeline.state.value == "listening"
            assert pipeline.generation == 0
        finally:
            await pipeline.close()

    asyncio.run(scenario())


def test_conversation_recovers_after_a_tts_failure() -> None:
    async def scenario() -> None:
        speaker = ExplodingSpeaker()
        client = StreamClient(body=sse("باشه. "))
        pipeline, events = build(asr=FakeAsr(texts=["اول", "دوم"]), speaker=speaker, client=client)
        pipeline.start()
        try:
            feed_utterance(pipeline)
            await until(lambda: bool(kinds(events, "response_end")))
            # The same fake speaker keeps failing; what matters is that the
            # pipeline still runs the second turn end to end instead of
            # treating it as an interruption.
            feed_utterance(pipeline)
            await until(lambda: len(kinds(events, "response_end")) == 2)
            assert all(e["status"] == "failed" for e in kinds(events, "response_end"))
            assert not kinds(events, "interrupted")
            assert pipeline.metrics.counter("llm_requests") == 2
            assert pipeline.state.value == "idle"
        finally:
            await pipeline.close()

    asyncio.run(scenario())


# --------------------------------------------------------------------------- bug 3
def test_empty_context_fails_the_turn_instead_of_wedging_thinking() -> None:
    async def scenario() -> None:
        client = StreamClient(body=sse("باشه. "))
        pipeline, events = build(client=client, system_prompt="")
        pipeline.conversation = Conversation(token_budget=1, max_messages=1)
        pipeline.start()
        try:
            # No system prompt and no user message: there is nothing to send.
            turn = pipeline.start_reply(1)
            await asyncio.wait_for(asyncio.shield(pipeline.reply), timeout=3)
            await until(lambda: bool(kinds(events, "response_end")))
            ended = kinds(events, "response_end")[0]
            assert ended["status"] == "failed"
            assert ended["reason"] == "empty_context"
            assert ended["turn_id"] == turn.turn_id
            assert pipeline.pending_turn is None
            assert pipeline.state.value == "idle"
            assert client.opened == 0  # nothing was sent to the model
            assert any(e["type"] == "error" for e in events)
        finally:
            await pipeline.close()

    asyncio.run(scenario())


def test_history_failure_is_a_failed_turn_not_a_stuck_state() -> None:
    """A raising conversation backend must not kill the actor or the state."""

    class BrokenConversation(Conversation):
        def context_with_system(self, system_prompt):  # noqa: ANN001, D401
            raise RuntimeError("history store unavailable")

    async def scenario() -> None:
        client = StreamClient(body=sse("باشه. "))
        pipeline, events = build(client=client)
        pipeline.conversation = BrokenConversation(max_messages=4)
        pipeline.start()
        try:
            feed_utterance(pipeline)
            await until(lambda: bool(kinds(events, "response_end")))
            ended = kinds(events, "response_end")[0]
            assert ended["status"] == "failed"
            assert ended["reason"] == "history_error"
            assert pipeline.state.value == "idle"
            assert pipeline.pending_turn is None
            assert client.opened == 0
            assert pipeline.reply is not None and pipeline.reply.done()
            assert pipeline.reply.exception() is None  # reported, not leaked
        finally:
            await pipeline.close()

    asyncio.run(scenario())


def test_empty_context_turn_does_not_block_the_next_utterance() -> None:
    async def scenario() -> None:
        client = StreamClient(body=sse("باشه. "))
        pipeline, events = build(client=client, system_prompt="")
        pipeline.conversation = Conversation(token_budget=1, max_messages=1)
        pipeline.start()
        try:
            pipeline.start_reply(1)
            await until(lambda: bool(kinds(events, "response_end")))
            before = len(kinds(events, "interrupted"))
            for _ in range(12):
                pipeline.feed(__import__("voicekit").frame())
            assert len(kinds(events, "interrupted")) == before
            assert pipeline.state.value == "listening"
        finally:
            await pipeline.close()

    asyncio.run(scenario())
