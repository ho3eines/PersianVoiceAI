"""Explicit conversational state machine (P1-02) — transition-table tests.

The live pipeline used to call ``interrupt()`` on *every* speech onset, whatever
the assistant was doing, which is how a final ASR result from the previous
sentence could be cancelled by the next one.  The fix is an explicit state
machine; this file tests the table itself plus the states the pipeline actually
visits during one complete exchange.

These are unit tests with fakes.  They say nothing about real microphone
behaviour; that still requires the hardware checklist in
``docs/HARDWARE_VALIDATION.md``.
"""

from __future__ import annotations

import asyncio
import threading

import httpx
import pytest

from server.streaming_voice import (
    ALLOWED_TRANSITIONS,
    Pipeline,
    StateMachine,
    VoiceState,
)
from voicekit import FakeAsr, FakeSpeaker, feed_utterance, frame, make_settings, sse, until


class BlockingSpeaker:
    """Speaks until cancelled; keeps the pipeline in SPEAKING for the test."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.spoken: list[str] = []

    def speak(self, text, token, *, on_first_chunk=None, on_first_frame=None, on_complete=None):
        self.spoken.append(text)
        if on_first_chunk is not None:
            on_first_chunk(1.0)
        if on_first_frame is not None:
            on_first_frame(1.01)
        self.entered.set()
        token.wait(3.0)
        if on_complete is not None:
            on_complete(1.02, 100.0)


def client_for(body: str):
    """Async httpx client with a scripted SSE response."""
    return httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=body)),
    )

STATES = list(VoiceState)


def test_idle_is_the_initial_state_and_self_transitions_are_no_ops() -> None:
    machine = StateMachine()
    assert machine.state is VoiceState.IDLE
    for state in STATES:
        machine.state = state
        assert machine.transition(state) is False
        assert machine.state is state
    assert machine.violations == 0


@pytest.mark.parametrize("source", STATES)
@pytest.mark.parametrize("target", STATES)
def test_only_documented_transitions_are_allowed(source: VoiceState, target: VoiceState) -> None:
    """Every (source, target) pair is either allowed or reported, never silent."""
    events: list[dict] = []
    machine = StateMachine(events.append)
    machine.state = source

    changed = machine.transition(target, reason="table")

    if source is target:
        assert changed is False
        return
    assert changed is True
    allowed = target in ALLOWED_TRANSITIONS[source]
    violation = [event for event in events if event["type"] == "state_violation"]
    assert bool(violation) is (not allowed)
    assert machine.violations == (0 if allowed else 1)
    transition = [event for event in events if event["type"] == "state"][0]
    assert transition["previous"] == source.value
    assert transition["state"] == target.value
    assert transition["forced"] is (not allowed)


def test_forced_transition_is_allowed_but_reported() -> None:
    events: list[dict] = []
    machine = StateMachine(events.append)
    # IDLE -> SPEAKING is not a normal transition; force must still mark it.
    assert machine.transition(VoiceState.SPEAKING, reason="test", force=True) is True
    assert machine.state is VoiceState.SPEAKING
    assert machine.violations == 0  # forced transitions are not violations
    assert events[-1]["forced"] is True


def test_every_state_can_return_to_idle_or_listening() -> None:
    """The machine must never be able to trap the pipeline in a terminal state."""
    for state in STATES:
        assert VoiceState.IDLE in ALLOWED_TRANSITIONS[state] or state is VoiceState.IDLE
        assert (
            VoiceState.LISTENING in ALLOWED_TRANSITIONS[state]
            or state is VoiceState.IDLE
            or state is VoiceState.LISTENING
        )


def test_pipeline_walks_the_documented_states_for_one_exchange() -> None:
    async def scenario() -> None:
        events: list[dict] = []
        async with client_for(sse("سلام!")) as client:
            pipeline = Pipeline(
                make_settings(voice_asr_interval_s=10.0),
                FakeAsr(text="سلام"),
                FakeSpeaker(),
                client,
                events.append,
            )
            pipeline.start()
            try:
                feed_utterance(pipeline)
                await until(
                    lambda: any(event["type"] == "response_end" for event in events),
                    timeout=5,
                )
            finally:
                await pipeline.close()

        states = [event["state"] for event in events if event["type"] == "state"]
        assert states[0] == VoiceState.LISTENING.value
        assert VoiceState.TRANSCRIBING.value in states
        assert VoiceState.THINKING.value in states
        assert VoiceState.SPEAKING.value in states
        assert pipeline.state is VoiceState.IDLE
        assert pipeline.states.violations == 0

    asyncio.run(scenario())


def test_barge_in_from_speaking_returns_to_listening_without_violation() -> None:
    async def scenario() -> None:
        events: list[dict] = []
        speaker = BlockingSpeaker()
        async with client_for(sse("یک پاسخ بسیار طولانی برای قطع کردن!")) as client:
            pipeline = Pipeline(
                make_settings(voice_asr_interval_s=10.0),
                FakeAsr(text="سلام"),
                speaker,
                client,
                events.append,
            )
            pipeline.start()
            try:
                feed_utterance(pipeline)
                await until(lambda: pipeline.state is VoiceState.SPEAKING, timeout=5)
                # New user speech while the assistant speaks → barge-in.
                for _ in range(20):
                    pipeline.feed(frame())
                assert pipeline.state is VoiceState.LISTENING
                assert pipeline.states.violations == 0
                assert any(event["type"] == "interrupted" for event in events)
            finally:
                await pipeline.close()

    asyncio.run(scenario())
