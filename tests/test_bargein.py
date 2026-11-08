"""Barge-in semantics beyond the state machine.

Two mandatory checks from the audit that were not covered by a dedicated test:

7. *no stale audio plays after interruption* — sentences queued behind the one
   being spoken must never reach synthesis/playback once the user interrupts;
6. *playback must not continuously retrigger VAD* — the assistant's own output
   (or residual echo) must not look like a new user turn, while genuine louder
   speech still interrupts.

Both use fakes; real echo suppression remains a hardware question.
"""

from __future__ import annotations

import asyncio
import threading
import time

import httpx

from server.streaming_voice import Pipeline, VoiceState
from server.vad import block_rms
from voicekit import FakeAsr, feed_utterance, frame, make_settings, sse, until


class BlockingSpeaker:
    """Blocks inside ``speak`` until cancelled; counts how often it was called."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.entered = threading.Event()

    def speak(self, text, token, *, on_first_chunk=None, on_first_frame=None, on_complete=None):
        self.calls.append(text)
        if on_first_chunk is not None:
            on_first_chunk(time.perf_counter())
        if on_first_frame is not None:
            on_first_frame(time.perf_counter())
        self.entered.set()
        while not token.is_set():
            time.sleep(0.002)
        if on_complete is not None:
            on_complete(time.perf_counter(), 0.0)


def pipeline_for(reply: str, speaker) -> tuple[Pipeline, list[dict]]:
    events: list[dict] = []
    client = httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=reply)),
    )
    pipeline = Pipeline(
        make_settings(voice_asr_interval_s=10.0), FakeAsr(text="سلام"), speaker, client, events.append
    )
    return pipeline, events


def test_sentences_queued_behind_the_spoken_one_never_play_after_barge_in() -> None:
    async def scenario() -> None:
        speaker = BlockingSpeaker()
        pipeline, events = pipeline_for(
            sse("جملهٔ یک. ", "جملهٔ دو. ", "جملهٔ سه. "), speaker
        )
        pipeline.start()
        try:
            feed_utterance(pipeline)
            await until(speaker.entered.is_set, timeout=5)
            assert len(speaker.calls) == 1  # only the first sentence started
            # User interrupts while the first sentence is playing.
            for _ in range(12):
                pipeline.feed(frame())
            await until(lambda: any(e["type"] == "interrupted" for e in events), timeout=5)
            # Give the play actor several scheduling slices: stale items must be skipped.
            for _ in range(50):
                await asyncio.sleep(0.005)

            assert speaker.calls == ["جملهٔ یک."], speaker.calls  # nothing stale started
            assert sum(1 for e in events if e["type"] == "tts_start") == 1
            turns = [m for m in pipeline.conversation.messages if m.role == "assistant"]
            assert len(turns) == 1
            assert turns[0].status == "interrupted"
            assert turns[0].content == "جملهٔ یک."
            assert pipeline.pending_turn is None
        finally:
            await pipeline.close()
            await pipeline.client.aclose()

    asyncio.run(scenario())


def test_moderate_playback_echo_does_not_retrigger_vad_but_real_speech_does() -> None:
    """Playback-level audio must not fabricate a user turn; louder speech must."""
    settings = make_settings()
    margin = settings.voice_barge_in_margin
    # Above the base threshold but below the raised (speaking) threshold.
    echo_level = int(settings.voice_vad_threshold * 1.2 * 32768)
    speech_level = int(0.20 * 32768)

    async def scenario() -> None:
        speaker = BlockingSpeaker()
        pipeline, events = pipeline_for(sse("پاسخ طولانی برای قطع کردن. "), speaker)
        pipeline.start()
        try:
            feed_utterance(pipeline)
            await until(lambda: pipeline.state is VoiceState.SPEAKING, timeout=5)
            # While speaking, the detector threshold is raised by the margin.
            assert pipeline.detector.threshold == settings.voice_vad_threshold * margin

            for _ in range(30):  # ~600 ms of "echo" at a moderate level
                pipeline.feed(frame(echo_level))
            assert pipeline.utterance is None  # VAD did not open a turn
            assert not any(e["type"] == "interrupted" for e in events)
            assert pipeline.state is VoiceState.SPEAKING

            for _ in range(15):  # genuine, louder speech
                pipeline.feed(frame(speech_level))
            await until(lambda: any(e["type"] == "interrupted" for e in events), timeout=5)
            assert pipeline.state is VoiceState.LISTENING
            # Margin is restored so quiet speech is detected again after barge-in.
            assert pipeline.detector.threshold == settings.voice_vad_threshold
        finally:
            await pipeline.close()
            await pipeline.client.aclose()

    asyncio.run(scenario())


def test_margin_only_raises_the_detector_threshold() -> None:
    pipeline, _ = pipeline_for(sse("باشه."), BlockingSpeaker())
    base = pipeline.detector.threshold
    pipeline._set_vad_margin(3.0)
    assert pipeline.detector.threshold == base * 3.0
    pipeline._set_vad_margin(1.0)
    assert pipeline.detector.threshold == base


def test_echo_level_is_measurable_by_the_detector_contract() -> None:
    """Guard the levels used above against a future RMS/threshold change."""
    settings = make_settings()
    base = settings.voice_vad_threshold
    raised = base * settings.voice_barge_in_margin
    assert base < block_rms(frame(int(base * 1.2 * 32768))) < raised
    assert block_rms(frame(int(0.20 * 32768))) > raised
