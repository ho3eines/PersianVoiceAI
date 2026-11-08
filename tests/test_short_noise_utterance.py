"""Regression tests for the "silent reply" bug: a rejected short burst must
never swallow the answer an earlier, valid utterance still deserves.

``EnergyVAD`` (formerly ``_finish``) drops speech shorter than ``min_voiced_frames`` (160 ms by
default) and used to report only ``None``.  ``Pipeline.feed`` never learned that
the utterance it had just opened — and announced with ``speech_start`` — was
thrown away, so:

* ``_latest_utterance_id`` stayed on the rejected id and ``_on_final`` *skipped*
  the reply for the still-valid previous utterance (``response_skipped``) with
  no ``tts_start`` ever following it;
* the stale ``self.utterance`` kept the state machine in ``LISTENING``.

The three scenarios below are the ones the audit requires, each asserting
"exactly one ``response_end`` for utterance A" and "final state is IDLE".
"""

from __future__ import annotations

import asyncio

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


def user_messages(pipeline: Pipeline) -> list[str]:
    return [m.content for m in pipeline.conversation.messages if m.role == "user"]


# --------------------------------------------------------------------- scenario 1
def test_short_noise_during_asr_does_not_swallow_the_reply() -> None:
    """A cough while decode A is in flight must not cancel the reply for A."""

    async def scenario() -> None:
        asr = FakeAsr(texts=["جمله اول"])
        asr.block()
        speaker = FakeSpeaker()
        client = StreamClient(body=sse("باشه. "))
        pipeline, events = build(asr=asr, speaker=speaker, client=client)
        pipeline.start()
        try:
            feed_utterance(pipeline, speech=20, silence=32)  # utterance 1 (valid)
            await until(lambda: asr.entered.is_set())        # decode A is in flight
            # A cough / keyboard click: long enough for an onset (3 frames),
            # far too short to be a sentence (< min_voiced_frames = 8).
            feed_utterance(pipeline, speech=4, silence=32)
            assert pipeline.utterance is None, "the rejected burst must not stay open"
            assert any(e["type"] == "utterance_rejected" for e in events)
            asr.release()
            await until(lambda: len(kinds(events, "response_end")) == 1)
            assert pipeline.state.value == "idle"
            assert speaker.started, "the reply for utterance 1 must be spoken"
            assert not kinds(events, "response_skipped")
            assert [e["utterance_id"] for e in kinds(events, "response_end")] == [1]
            assert user_messages(pipeline) == ["جمله اول"]
            assert pipeline.states.violations == 0
        finally:
            asr.release()
            await pipeline.close()

    asyncio.run(scenario())


# --------------------------------------------------------------------- scenario 2
def test_short_noise_with_empty_transcript_still_answers_the_pending_turn() -> None:
    """Valid turn A is answered; the empty final B is not a reason to re-answer.

    B's final is empty and C is a rejected burst, so nothing answers B — and
    nothing may re-open A either.  The assertion is the contract: exactly one
    answer per valid user message, and no state stuck in LISTENING.
    """

    async def scenario() -> None:
        asr = FakeAsr(texts=["جمله اول", ""])
        speaker = FakeSpeaker()
        client = StreamClient(body=sse("باشه. "))
        pipeline, events = build(asr=asr, speaker=speaker, client=client)
        pipeline.start()
        try:
            feed_utterance(pipeline, speech=20, silence=32)
            await until(lambda: len(kinds(events, "response_end")) == 1)
            feed_utterance(pipeline, speech=20, silence=32)  # B: empty transcript
            await until(lambda: len(kinds(events, "transcript_final")) == 2)
            feed_utterance(pipeline, speech=4, silence=32)   # C: rejected burst
            await until(lambda: pipeline.state.value == "idle")
            assert pipeline.state.value == "idle"
            assert len(kinds(events, "response_end")) == 1
            assert len(kinds(events, "utterance_rejected")) == 1
            assert user_messages(pipeline) == ["جمله اول"]
            assistant = [m for m in pipeline.conversation.messages if m.role == "assistant"]
            assert len(assistant) == 1
            assert pipeline.states.violations == 0
        finally:
            await pipeline.close()

    asyncio.run(scenario())


# --------------------------------------------------------------------- scenario 3
def test_short_noise_after_a_final_is_queued_does_not_skip_its_reply() -> None:
    """The rejected burst must not raise ``_latest_utterance_id`` past the final."""

    async def scenario() -> None:
        asr = FakeAsr(texts=["جمله اول"])
        speaker = FakeSpeaker()
        client = StreamClient(body=sse("باشه. "))
        pipeline, events = build(asr=asr, speaker=speaker, client=client)
        pipeline.start()
        try:
            feed_utterance(pipeline, speech=20, silence=32)  # final for 1 queued
            feed_utterance(pipeline, speech=4, silence=32)   # rejected burst
            await until(lambda: len(kinds(events, "response_end")) == 1)
            assert pipeline.state.value == "idle"
            assert speaker.started
            assert not kinds(events, "response_skipped")
            assert pipeline.metrics.counter("utterances_rejected") == 1
        finally:
            await pipeline.close()

    asyncio.run(scenario())


# ------------------------------------------------------------------ extra coverage
def test_next_valid_turn_after_a_rejected_burst_is_answered_normally() -> None:
    """A rejected burst must not leave a pending "newer utterance" behind."""

    async def scenario() -> None:
        asr = FakeAsr(texts=["اول", "دوم"])
        speaker = FakeSpeaker()
        client = StreamClient(body=sse("باشه. "))
        pipeline, events = build(asr=asr, speaker=speaker, client=client)
        pipeline.start()
        try:
            feed_utterance(pipeline, speech=20, silence=32)
            await until(lambda: len(kinds(events, "response_end")) == 1)
            feed_utterance(pipeline, speech=4, silence=32)    # rejected burst
            await until(lambda: pipeline.state.value == "idle")
            feed_utterance(pipeline, speech=20, silence=32)   # real second turn
            await until(lambda: len(kinds(events, "response_end")) == 2)
            assert user_messages(pipeline) == ["اول", "دوم"]
            assert len(speaker.started) == 2
        finally:
            await pipeline.close()

    asyncio.run(scenario())


def test_failed_retry_does_not_delete_the_answer_of_the_pending_turn() -> None:
    """A newer utterance whose decode *fails* must not eat the pending answer.

    This is the second half of bug 1: "the next utterance produced nothing" is
    not the same as "the next utterance answered".  Turn A is committed with no
    reply (its decode fails), turn B arrives and decodes fine — the user is
    still waiting for A, so A must be the turn that gets answered.
    """

    class FlakyAsr(FakeAsr):
        """Fails the first decode, then behaves normally."""

        def __init__(self, texts):
            super().__init__(texts=texts)
            self.failed = False

        def transcribe_pcm(self, pcm: bytes) -> str:
            if not self.failed:
                self.failed = True
                self.calls.append(len(pcm))
                raise RuntimeError("whisper unavailable")
            return super().transcribe_pcm(pcm)

    async def scenario() -> None:
        speaker = FakeSpeaker()
        client = StreamClient(body=sse("پاسخ به نوبت اول. "))
        pipeline, events = build(asr=FlakyAsr(["اول", "دوم"]), speaker=speaker, client=client)
        pipeline.start()
        try:
            feed_utterance(pipeline, speech=20, silence=32)   # A: decode fails
            await until(lambda: any(e["type"] == "error" for e in events))
            assert pipeline.state.value == "idle"
            assert not kinds(events, "response_end")
            feed_utterance(pipeline, speech=20, silence=32)   # B: decodes as "دوم"
            await until(lambda: len(kinds(events, "response_end")) == 1)
            answered = kinds(events, "response_end")[0]["utterance_id"]
            # A was committed with no answer, so A is the turn still owed one.
            assert answered == 1
            assert pipeline.state.value == "idle"
        finally:
            await pipeline.close()

    asyncio.run(scenario())


def test_a_newer_turn_that_was_answered_still_supersedes_an_older_one() -> None:
    """The documented barge-in skip must survive: answer the newest, not both.

    The FIFO usually delivers finals in order, so this needs a final that lands
    after a newer turn was already answered (input overflow, a late re-queue).
    When it happens, A's late transcript must not talk over the answer the user
    just heard: it is reported as ``response_skipped`` with an explicit reason
    instead of producing a second, out-of-order reply.
    """
    from server.streaming_voice import STATUS_COMPLETE, AsrJob

    async def scenario() -> None:
        pipeline, events = build(client=StreamClient(body=sse("باشه. ")))
        pipeline.start()
        try:
            pipeline.conversation.add_user("دوم", utterance_id=2)
            turn = pipeline.start_reply(2)
            turn.text = "باشه."  # what the assistant actually said for turn 2
            pipeline._commit_turn(turn, STATUS_COMPLETE, reason="test")
            assert pipeline._newest_answered() == 2
            job = AsrJob(utterance_id=1, pcm=b"", final=True, submitted_at=0.0, total_frames=1)
            await pipeline._on_final(job, "اول")
            skipped = kinds(events, "response_skipped")
            assert [e["utterance_id"] for e in skipped] == [1]
            assert skipped[0]["reason"] == "newer_final_answered"
            assert skipped[0]["answered_utterance_id"] == 2
            assert pipeline.metrics.counter("responses_skipped") == 1
            assert pipeline.metrics.counter("responses_recovered") == 0
            assert [m.content for m in pipeline.conversation.messages] == ["دوم", "باشه.", "اول"]
            assert not [e for e in kinds(events, "response_end") if e["utterance_id"] == 1]
        finally:
            await pipeline.close()

    asyncio.run(scenario())
