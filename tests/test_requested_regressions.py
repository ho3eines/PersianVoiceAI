"""Reproductions of reported LAN, ASR and text regressions (no real models)."""
import asyncio
import threading
import time
from types import SimpleNamespace

import httpx
import pytest

from server.config import Settings
from server.main import create_app
from server.persian import prepare_for_tts
from server.voice import LocalAudio, AsrPreempted, local_client_headers
from server.streaming_voice import Pipeline
from voicekit import FakeAsr, FakeSpeaker, StreamClient, feed_utterance, make_settings, sse, until


def test_internal_chat_auth_asgi_transport():
    class Llm:
        async def aclose(self):
            pass
        async def chat(self, *args, **kwargs):
            from server.llm import ChatResult, Timings
            return ChatResult(content='سلام', model='fake', finish_reason='stop', timings=Timings(0, 0, 0, 0, 0, 0, 0))
    async def run():
        settings = Settings(api_token='top-secret')
        app = create_app(settings=settings, llm=Llm())
        async with app.router.lifespan_context(app):
          async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://local', headers=local_client_headers(settings)) as client:
              response = await client.post('/v1/chat/completions', json={'messages': [{'role': 'user', 'content': 'سلام'}], 'stream': False})
              assert response.status_code == 200
        assert local_client_headers(Settings()) == {}
    asyncio.run(run())


def test_auto_language_and_optional_metrics():
    audio = LocalAudio(Settings(asr_language='auto'))
    assert audio._transcribe_options()['language'] is None
    assert LocalAudio(Settings(asr_language=''))._transcribe_options()['language'] is None
    segment = SimpleNamespace(text='noise', no_speech_prob=.99, avg_logprob=-2, compression_ratio=1)
    assert audio._accepted_segments([segment]) == []


def test_whisper_filter_requires_both_no_speech_and_low_logprob():
    audio = LocalAudio(Settings(asr_max_no_speech_prob=.75, asr_log_prob_threshold=-1))
    def seg(p, lp):
        return SimpleNamespace(text='سلام', no_speech_prob=p, avg_logprob=lp, compression_ratio=1)
    assert audio._accepted_segments([seg(.1, -1.1), seg(.8, -.5)]) == [seg(.1, -1.1), seg(.8, -.5)]
    assert audio._accepted_segments([seg(.8, -1.1)]) == []


class _FakeSamples:
    def astype(self, dtype):
        return self

    def __truediv__(self, divisor):
        return self


_FAKE_NUMPY = SimpleNamespace(frombuffer=lambda *args, **kwargs: _FakeSamples(), float32=object())


def test_local_audio_cancels_lazy_generator(monkeypatch):
    audio = LocalAudio(Settings())
    cancel = threading.Event()
    class Model:
        def transcribe(self, source, **kwargs):
            def segments():
                yield SimpleNamespace(text='اول', no_speech_prob=0, avg_logprob=0, compression_ratio=1)
                cancel.set()
                yield SimpleNamespace(text='دوم', no_speech_prob=0, avg_logprob=0, compression_ratio=1)
            return segments(), None
    monkeypatch.setattr(audio, '_ensure_model', lambda: Model())
    monkeypatch.setattr('server.voice.dependency', lambda name: _FAKE_NUMPY)
    with pytest.raises(AsrPreempted):
        audio.transcribe_pcm(b'\0' * 640, cancel)


def test_empty_a_then_spoken_b_links_reply_to_b():
    async def run():
        pipeline = Pipeline(make_settings(), FakeAsr(texts=['', 'سلام']), FakeSpeaker(), StreamClient(body=sse('باشه. ')), lambda event: None)
        pipeline.start()
        try:
            feed_utterance(pipeline)
            await until(lambda: pipeline.metrics.counter('utterances_empty') == 1)
            feed_utterance(pipeline)
            await until(lambda: any(m.role == 'assistant' for m in pipeline.conversation.messages))
            user = next(m for m in pipeline.conversation.messages if m.role == 'user')
            assistant = next(m for m in pipeline.conversation.messages if m.role == 'assistant')
            assert assistant.utterance_id == user.utterance_id == 2
            assert pipeline._has_answer(2)
        finally:
            await pipeline.close()
    asyncio.run(run())


@pytest.mark.parametrize('raw,expected', [
    ('-5 درجه', 'منفی پنج درجه.'),
    ('2026/11/09', 'نه نوامبر دو هزار و بیست و شش.'),
    ('0912-345-6789', 'صفر نه یک دو سه چهار پنج شش هفت هشت نه.'),
    ('GPT-4o', 'GPT-4o.'), ('Qwen3', 'Qwen3.'),
    ('v2.3.1', 'نسخه دو نقطه سه نقطه یک.'),
    ('1/2', 'یک دوم.'),
])
def test_reported_tts_cases(raw, expected):
    assert prepare_for_tts(raw) == expected


def test_internal_auth_diagnostic_is_actionable():
    from server.voice import assert_local_api_authorized, VoiceError
    with pytest.raises(VoiceError, match='PVAI_API_TOKEN برای کلاینت داخلی تنظیم نشده'):
        assert_local_api_authorized(httpx.Response(401, request=httpx.Request('GET', 'http://local/ready')))
    assert_local_api_authorized(httpx.Response(503, request=httpx.Request('GET', 'http://local/ready')))


def test_partial_window_default_bounded():
    assert Settings().asr_partial_window_s <= 3


def test_false_reject_metric_only_counts_real_speech_with_empty_transcripts():
    from benchmarks.persian_eval import score_speech_rejections
    data = {'sentences': [
        {'id': 's1', 'kind': 'speech', 'text': 'سلام'},
        {'id': 's2', 'kind': 'speech', 'text': 'درود'},
        {'id': 'n1', 'kind': 'no_speech', 'text': ''},
    ]}
    assert score_speech_rejections(data, {'s1': '', 's2': 'درود', 'n1': ''}) == {
        'clips': 2, 'rejected': 1, 'false_reject_rate': .5, 'missing_ids': []
    }


# Forty additional concrete spoken-number contexts, not a roundtrip through
# the implementation under test. All are model-free golden outputs.
_UNIT_WORDS = ['صفر', 'یک', 'دو', 'سه', 'چهار', 'پنج', 'شش', 'هفت', 'هشت', 'نه']
_TEENS = ['ده', 'یازده', 'دوازده', 'سیزده', 'چهارده', 'پانزده', 'شانزده', 'هفده', 'هجده', 'نوزده']
_TENS = ['بیست', 'سی']
_GOLDEN_0_TO_39 = (_UNIT_WORDS + _TEENS + [
    tens if unit == 0 else f'{tens} و {word}'
    for tens in _TENS for unit, word in enumerate(_UNIT_WORDS)
])


@pytest.mark.parametrize('number,words', list(enumerate(_GOLDEN_0_TO_39)))
def test_forty_spoken_quantity_contexts(number, words):
    assert prepare_for_tts(f'{number} کتاب داریم') == f'{words} کتاب داریم.'


def test_retention_defaults_to_thirty_days():
    assert Settings().history_retention_days == 30


def test_llama_launch_uses_jinja_template():
    assert '--jinja' in Settings().llama_server_command()


def test_ssl_settings_passed_to_uvicorn(monkeypatch, tmp_path):
    from server.__main__ import main
    from server.config import Settings as Config
    cert = tmp_path / 'cert.pem'; key = tmp_path / 'key.pem'
    cert.write_text('cert'); key.write_text('key')
    configured = Config(ssl_certfile=str(cert), ssl_keyfile=str(key))
    monkeypatch.setattr(Config, 'from_env', lambda: configured)
    calls = {}
    monkeypatch.setattr('uvicorn.run', lambda *args, **kwargs: calls.update(kwargs))
    main()
    assert calls['ssl_certfile'] == str(cert)
    assert calls['ssl_keyfile'] == str(key)


def test_retention_purges_while_server_is_running(monkeypatch):
    from server import main as api
    calls = []
    monkeypatch.setattr(api, 'purge_expired', lambda settings, store: calls.append(1))
    async def run():
        task = asyncio.create_task(api.periodic_retention(Settings(), object(), interval_s=.01))
        try:
            async with asyncio.timeout(.5):
                while len(calls) < 2:
                    await asyncio.sleep(.005)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(run())


def test_ready_thinking_probe_checks_real_response_fields():
    from server.llm import LlamaServerClient
    async def run():
        llm = LlamaServerClient(Settings())
        async def handle(req):
            assert req.method == 'POST'
            assert __import__('json').loads(req.content)['chat_template_kwargs']['enable_thinking'] is False
            return httpx.Response(200, json={'choices': [{'message': {'content': 'پاسخ', 'reasoning_content': 'private'}}]})
        await llm._client.aclose()
        llm._client = httpx.AsyncClient(base_url='http://local', transport=httpx.MockTransport(handle))
        try:
            report = await llm.probe_no_thinking()
            assert not report['ok'] and 'reasoning_content' in report['detail']
        finally:
            await llm.aclose()
    asyncio.run(run())


def test_pipeline_reaches_authenticated_chat_api_with_asgi_transport():
    from test_api import FakeLLM
    async def run():
        settings = make_settings(api_token='secret')
        app = create_app(settings=settings, llm=FakeLLM())
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(base_url='http://local', transport=transport, headers=local_client_headers(settings)) as client:
                pipeline = Pipeline(settings, FakeAsr(text='سلام'), FakeSpeaker(), client, lambda event: None)
                pipeline.start()
                try:
                    feed_utterance(pipeline)
                    await until(lambda: any(m.role == 'assistant' for m in pipeline.conversation.messages))
                    assert pipeline.conversation.messages[-1].utterance_id == 1
                finally:
                    await pipeline.close()
    asyncio.run(run())


def test_valid_tls_removes_plaintext_websocket_warning(tmp_path):
    cert = tmp_path/'c.pem'; key = tmp_path/'k.pem'
    cert.write_text('c'); key.write_text('k')
    settings = Settings(api_host='0.0.0.0', api_token='api', ws_enabled=True,
                        ws_tokens='dev:token', ssl_certfile=str(cert), ssl_keyfile=str(key))
    assert not any('plaintext' in warning for warning in settings.config_warnings())


def test_missing_silence_clip_not_counted_as_false_accept_negative():
    from benchmarks.persian_eval import score_no_speech
    data = {'sentences': [
        {'id': 'n1', 'kind': 'no_speech', 'text': ''},
        {'id': 'n2', 'kind': 'no_speech', 'text': ''},
    ]}
    assert score_no_speech(data, {'n1': 'توهم'})['clips'] == 1


def test_pipeline_unauthorized_error_names_internal_token():
    from test_api import FakeLLM
    async def run():
        settings = make_settings(api_token='correct')
        app = create_app(settings=settings, llm=FakeLLM())
        events = []
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://local') as client:
                pipeline = Pipeline(settings, FakeAsr(text='سلام'), FakeSpeaker(), client, events.append)
                pipeline.start()
                try:
                    feed_utterance(pipeline)
                    await until(lambda: any(e['type'] == 'error' for e in events))
                    assert 'PVAI_API_TOKEN برای کلاینت داخلی تنظیم نشده' in events[-2]['detail'] or any(
                        'PVAI_API_TOKEN برای کلاینت داخلی تنظیم نشده' in e.get('detail', '') for e in events)
                finally:
                    await pipeline.close()
    asyncio.run(run())


def test_local_audio_stub_final_wait_is_bounded_by_lazy_segment(tmp_path, monkeypatch):
    """Synthetic slow Whisper generator, not a measurement of model hardware."""
    from voicekit import frame
    settings = make_settings(voice_asr_interval_s=.05, asr_partial_window_s=3)
    audio = LocalAudio(settings)
    partial_started = threading.Event()
    class Model:
        def __init__(self):
            self.calls = 0
        def transcribe(self, source, **kwargs):
            self.calls += 1
            current = self.calls
            def lazy():
                if current == 1:
                    partial_started.set()
                    for _ in range(50):
                        time.sleep(.02)
                        yield SimpleNamespace(text='آزمایش', no_speech_prob=0, avg_logprob=0, compression_ratio=1)
                else:
                    yield SimpleNamespace(text='سلام', no_speech_prob=0, avg_logprob=0, compression_ratio=1)
            return lazy(), None
    model = Model()
    monkeypatch.setattr(audio, '_ensure_model', lambda: model)
    monkeypatch.setattr('server.voice.dependency', lambda name: _FAKE_NUMPY)
    async def run():
        pipeline = Pipeline(settings, audio, FakeSpeaker(), StreamClient(body=sse('باشه. ')), lambda e: None)
        pipeline.start()
        try:
            for _ in range(30):
                pipeline.feed(frame())
            await until(partial_started.is_set)
            for _ in range(32):
                pipeline.feed(frame(0))
            await until(lambda: pipeline.metrics.summary('asr_final_wait_behind_partial_ms').count == 1)
            assert pipeline.metrics.summary('asr_final_wait_behind_partial_ms').last < 200
        finally:
            await pipeline.close()
    asyncio.run(run())


def test_auto_language_is_none_in_actual_model_call(monkeypatch):
    settings = Settings(asr_language='auto')
    audio = LocalAudio(settings)
    seen = []
    class Stub:
        def transcribe(self, source, **kwargs):
            seen.append(kwargs)
            return iter(()), None
    monkeypatch.setattr(audio, '_ensure_model', lambda: Stub())
    assert audio._transcribe(None) == ''
    assert seen[0]['language'] is None


def test_two_concurrent_api_writers_do_not_duplicate_message_ids(tmp_path):
    from server.llm import ChatResult, Timings
    class SlowLlm:
        def __init__(self):
            self.entered = asyncio.Event()
            self.release = asyncio.Event()
        async def chat(self, *args, **kwargs):
            self.entered.set()
            await self.release.wait()
            return ChatResult('پاسخ', 'fake', 'stop', Timings(0, 0, 0, 0, 0, 0, 0))
        async def aclose(self):
            pass
    async def run():
        llm = SlowLlm()
        settings = make_settings(data_dir=str(tmp_path), history_persist=True)
        app = create_app(settings=settings, llm=llm)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://local') as client:
                payload = {'message': 'سلام', 'session_id': 'shared'}
                first = asyncio.create_task(client.post('/api/chat', json=payload))
                await llm.entered.wait()
                try:
                    second = await asyncio.wait_for(client.post('/api/chat', json=payload), timeout=.5)
                finally:
                    llm.release.set()
                assert second.status_code == 409
                assert (await first).status_code == 200
    asyncio.run(run())


def test_internal_header_only_for_configured_local_address_not_remote():
    settings = Settings(api_token='secret', api_host='0.0.0.0', api_port=9000)
    assert local_client_headers(settings, 'http://localhost:9000') == {'Authorization': 'Bearer secret'}
    assert local_client_headers(settings, 'https://example.com:9000') == {}
    assert local_client_headers(settings, 'http://127.0.0.1:8000') == {}


def test_internal_url_uses_https_when_direct_ssl_is_configured():
    from server.voice import local_api_url
    assert local_api_url(Settings(ssl_certfile='cert.pem', ssl_keyfile='key.pem')).startswith('https://')


def test_cli_default_url_matches_configured_tls_and_port(monkeypatch):
    from server import cli
    configured = Settings(api_port=9443, ssl_certfile='cert.pem', ssl_keyfile='key.pem')
    monkeypatch.setattr(cli.Settings, 'from_env', lambda: configured)
    assert cli.build_parser().parse_args([]).base_url == 'https://127.0.0.1:9443'


def test_ready_includes_real_thinking_probe_outcome():
    from fastapi.testclient import TestClient
    from test_api import FakeLLM
    class ProbeLlm(FakeLLM):
        async def probe_no_thinking(self):
            return {'ok': False, 'detail': '<think> detected in test output'}
        async def health(self):
            return {'reachable': True, 'model_available': True}
    with TestClient(create_app(settings=Settings(), llm=ProbeLlm())) as client:
        body = client.get('/ready').json()
    assert body['checks']['thinking']['ok'] is False
    assert body['ready'] is False


def test_tls_config_rejects_one_sided_or_missing_key(tmp_path):
    cert = tmp_path/'c.pem'; cert.write_text('cert')
    assert any('PVAI_SSL_KEYFILE' in problem for problem in Settings(ssl_certfile=str(cert)).config_errors())
    assert any('PVAI_SSL_KEYFILE' in problem for problem in Settings(ssl_certfile=str(cert), ssl_keyfile=str(tmp_path/'absent.pem')).config_errors())


def test_active_session_cannot_be_deleted_and_returns_conflict(tmp_path):
    from fastapi.testclient import TestClient
    from server.conversation import open_store
    from test_api import FakeLLM
    settings = Settings(data_dir=str(tmp_path), history_persist=True)
    store = open_store(settings)
    assert store.append('live', {'kind': 'message', 'id': 1, 'role': 'user', 'content': 'سلام'})
    with TestClient(create_app(settings=settings, llm=FakeLLM())) as client:
        with store.claim('live'):
            assert client.delete('/api/sessions/live').status_code == 409
        assert client.delete('/api/sessions/live').status_code == 200


def test_local_audio_stub_preemption_reduces_final_wait_against_same_backend(monkeypatch):
    from voicekit import frame
    settings = make_settings(voice_asr_interval_s=.05)
    monkeypatch.setattr('server.voice.dependency', lambda name: _FAKE_NUMPY)

    async def wait_behind_partial(preempt: bool) -> float:
        audio = LocalAudio(settings)
        started = threading.Event()
        class SlowWhisper:
            def __init__(self):
                self.calls = 0
            def transcribe(self, source, **kwargs):
                self.calls += 1
                call = self.calls
                def segments():
                    if call == 1:
                        started.set()
                        for _ in range(35):
                            time.sleep(.02)
                            yield SimpleNamespace(text='آزمایش', no_speech_prob=0,
                                                  avg_logprob=0, compression_ratio=1)
                    else:
                        yield SimpleNamespace(text='سلام', no_speech_prob=0,
                                              avg_logprob=0, compression_ratio=1)
                return segments(), None
        model = SlowWhisper()
        monkeypatch.setattr(audio, '_ensure_model', lambda: model)
        pipeline = Pipeline(settings, audio, FakeSpeaker(), StreamClient(body=sse('باشه. ')), lambda e: None)
        pipeline.preempt_partials = preempt
        pipeline.start()
        try:
            for _ in range(30):
                pipeline.feed(frame())
            await until(started.is_set)
            for _ in range(32):
                pipeline.feed(frame(0))
            await until(lambda: pipeline.metrics.summary('asr_final_wait_behind_partial_ms').count == 1)
            return pipeline.metrics.summary('asr_final_wait_behind_partial_ms').last
        finally:
            await pipeline.close()

    async def compare():
        baseline = await wait_behind_partial(False)
        stopped = await wait_behind_partial(True)
        assert baseline > 300  # synthetic segments cost about 700 ms
        assert stopped < baseline / 4, (baseline, stopped)
    asyncio.run(compare())
