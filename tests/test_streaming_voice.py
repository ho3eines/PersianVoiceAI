from __future__ import annotations

import asyncio
import threading
from array import array
from types import SimpleNamespace

import httpx

from server.config import Settings
from server.streaming_voice import Pipeline, agreement


def frame(level=2000):
    return array('h', [level] * 320).tobytes()


async def until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(.005)


class Audio:
    def __init__(self):
        self.calls = []

    def transcribe_pcm(self, pcm):
        self.calls.append(len(pcm))
        return 'سلام دوست من'


class Speaker:
    def __init__(self):
        self.spoken = []

    def speak(self, text, token):
        if not token.is_set():
            self.spoken.append(text)


def test_local_agreement():
    assert agreement('سلام دوست عزیز', 'سلام دوست من') == 'سلام دوست'
    assert agreement('الف', 'ب') == ''


def test_partial_transcript_while_microphone_still_active():
    async def scenario():
        events = []
        pipeline = Pipeline(Settings(voice_asr_interval_s=.3), Audio(), Speaker(), None, events.append)
        pipeline.start()
        try:
            for _ in range(20):
                pipeline.feed(frame())
            await until(lambda: any(e['type'] == 'transcript_partial' for e in events))
            assert not any(e['type'] == 'speech_end' for e in events)
            assert pipeline.reply is None  # Never launch speculative commands from partial text.
            for _ in range(20):
                pipeline.feed(frame())
            await until(lambda: len(pipeline.audio.calls) == 2)
            partials = [e for e in events if e['type'] == 'transcript_partial']
            assert partials[-1]['agreed_prefix'] == 'سلام دوست من'
        finally:
            await pipeline.close()
    asyncio.run(scenario())


def test_async_sse_stream_cancelled_without_waiting_for_next_token():
    async def scenario():
        entered, exited = asyncio.Event(), asyncio.Event()

        class Response:
            def raise_for_status(self):
                pass

            async def aiter_lines(self):
                entered.set()
                await asyncio.Event().wait()
                yield ''

        class Context:
            async def __aenter__(self):
                return Response()

            async def __aexit__(self, *args):
                exited.set()

        pipeline = Pipeline(Settings(), Audio(), Speaker(), SimpleNamespace(stream=lambda *a, **k: Context()), lambda e: None)
        pipeline.reply = asyncio.create_task(pipeline.respond('سلام', 0, pipeline.cancel))
        await entered.wait()
        old_token = pipeline.cancel
        pipeline.interrupt()
        await asyncio.wait_for(exited.wait(), .3)
        assert old_token.is_set()
        await pipeline.close()
    asyncio.run(scenario())


def test_final_transcript_response_audio_and_history():
    async def scenario():
        body = ('data: {"choices":[{"delta":{"content":"درود! "}}]}\n\n'
                'data: {"choices":[{"delta":{"content":"خوبی؟"}}]}\n\n'
                'data: [DONE]\n\n')
        events = []
        speaker = Speaker()
        async with httpx.AsyncClient(base_url='http://127.0.0.1', transport=httpx.MockTransport(
                lambda request: httpx.Response(200, text=body))) as client:
            pipeline = Pipeline(Settings(), Audio(), speaker, client, events.append)
            pipeline.start()
            try:
                for _ in range(20):
                    pipeline.feed(frame())
                for _ in range(25):
                    pipeline.feed(frame(0))
                await until(lambda: any(e['type'] == 'response_end' for e in events))
                assert ''.join(speaker.spoken) == 'درود!خوبی؟'
                assert pipeline.history[-1]['content'] == 'درود! خوبی؟'
                assert any(e['type'] == 'transcript_final' for e in events)
            finally:
                await pipeline.close()
    asyncio.run(scenario())


def test_newest_snapshot_wins_and_overflow_clears_buffers():
    async def scenario():
        pipeline = Pipeline(Settings(), Audio(), Speaker(), None, lambda e: None)
        for _ in range(100):
            pipeline.offer(frame(), False)
        assert pipeline.snapshots.qsize() == 1
        pipeline.offer(frame(5), True)
        assert pipeline.snapshots.get_nowait().final
        old_token = pipeline.cancel
        pipeline.feed(frame())
        pipeline.overflow()
        assert old_token.is_set()
        assert pipeline.detector.frames == []
        assert pipeline.snapshots.empty()
    asyncio.run(scenario())


def test_stale_inflight_asr_result_is_discarded():
    async def scenario():
        started, release = threading.Event(), threading.Event()
        events = []

        def decode(pcm):
            started.set()
            release.wait(2)
            return 'متن قدیمی'

        pipeline = Pipeline(Settings(), SimpleNamespace(transcribe_pcm=decode), Speaker(), None, events.append)
        pipeline.start()
        try:
            pipeline.offer(frame(), False)
            await until(started.is_set)
            pipeline.interrupt()
            release.set()
            await asyncio.sleep(.05)
            assert not any(e['type'].startswith('transcript_') for e in events)
        finally:
            release.set()
            await pipeline.close()
    asyncio.run(scenario())


def test_incomplete_sse_does_not_commit_history():
    async def scenario():
        async with httpx.AsyncClient(base_url='http://127.0.0.1', transport=httpx.MockTransport(
                lambda request: httpx.Response(200, text='data: {"choices":[]}\n\n'))) as client:
            events = []
            pipeline = Pipeline(Settings(), Audio(), Speaker(), client, events.append)
            await pipeline.respond('سلام', 0, pipeline.cancel)
            assert pipeline.history == []
            assert pipeline.cancel.is_set()
            assert events[-1]['type'] == 'error'
    asyncio.run(scenario())


def test_incremental_playback_before_remaining_tokens():
    async def scenario():
        spoken = threading.Event()

        class Response:
            def raise_for_status(self):
                pass

            async def aiter_lines(self):
                yield 'data: {"choices":[{"delta":{"content":"سلام! "}}]}'
                await until(spoken.is_set)
                yield 'data: {"choices":[{"delta":{"content":"ادامه"}}]}'
                yield 'data: [DONE]'

        class Context:
            async def __aenter__(self):
                return Response()

            async def __aexit__(self, *args):
                pass

        events = []
        speaker = SimpleNamespace(speak=lambda text, token: spoken.set())
        pipeline = Pipeline(Settings(), Audio(), speaker,
                            SimpleNamespace(stream=lambda *a, **k: Context()), events.append)
        pipeline.start()
        try:
            await pipeline.respond('سلام', 0, pipeline.cancel)
            await until(lambda: any(e['type'] == 'response_end' for e in events))
            assert spoken.is_set()
        finally:
            await pipeline.close()
    asyncio.run(scenario())
