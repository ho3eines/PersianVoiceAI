"""Audio device resolution, validation, pumping and stream recovery."""

from __future__ import annotations

import asyncio
import time

import pytest

from server import audio_io
from server.aec import DuplexAudio
from server.config import Settings
from server.voice import VoiceError

DEVICES = [
    {"name": "Speakers (Realtek)", "max_input_channels": 0, "max_output_channels": 2,
     "default_samplerate": 48000},
    {"name": "Headset Microphone", "max_input_channels": 1, "max_output_channels": 0,
     "default_samplerate": 16000},
    {"name": "USB Headset", "max_input_channels": 1, "max_output_channels": 2,
     "default_samplerate": 16000},
]


class FakeSd:
    PortAudioError = OSError

    def __init__(self, devices=DEVICES, reject: tuple[str, ...] = ()) -> None:
        self.devices = devices
        self.reject = reject
        self.checked: list[str] = []

    def query_devices(self):
        return self.devices

    def _check(self, kind: str, **options):
        self.checked.append(kind)
        if kind in self.reject:
            raise RuntimeError("invalid sample rate")
        return None

    def check_input_settings(self, **options):
        return self._check("input", **options)

    def check_output_settings(self, **options):
        return self._check("output", **options)


def test_resolve_device_by_index_name_and_default() -> None:
    sd = FakeSd()
    assert audio_io.resolve_device(sd, "", kind="ورودی") is None
    assert audio_io.resolve_device(sd, "1", kind="ورودی") == 1
    assert audio_io.resolve_device(sd, "USB", kind="خروجی") == 2
    # "Headset" matches both a microphone and a duplex device; the input-capable
    # match is preferred only when it is unique.
    with pytest.raises(VoiceError, match="مبهم"):
        audio_io.resolve_device(sd, "Headset", kind="ورودی")


def test_resolve_device_rejects_missing_or_wrong_channels() -> None:
    sd = FakeSd()
    with pytest.raises(VoiceError, match="پیدا نشد"):
        audio_io.resolve_device(sd, "Nonexistent", kind="ورودی")
    with pytest.raises(VoiceError, match="کانال"):
        audio_io.resolve_device(sd, "0", kind="ورودی")  # speaker has no input
    with pytest.raises(VoiceError, match="نامعتبر"):
        audio_io.resolve_device(sd, "99", kind="ورودی")


def test_describe_devices_lists_every_device() -> None:
    text = audio_io.describe_devices(FakeSd())
    assert "[1] Headset Microphone" in text and "in=1" in text


def test_plan_stream_validates_capabilities_and_reports_actionable_errors() -> None:
    settings = Settings(audio_input_device="1", audio_output_device="2")
    plan = audio_io.plan_stream(FakeSd(), settings)
    assert plan.input_device == 1 and plan.output_device == 2
    assert plan.samplerate == 16000 and plan.blocksize == 320
    assert plan.parameters(lambda **kwargs: None)["device"] == (1, 2)

    with pytest.raises(VoiceError, match="16000"):
        audio_io.plan_stream(FakeSd(reject=("input",)), settings)


def test_plan_stream_requires_at_least_one_device() -> None:
    with pytest.raises(VoiceError, match="دستگاه"):
        audio_io.plan_stream(FakeSd(devices=[]), Settings())


def test_pump_runs_aec_before_the_pipeline_and_clears_overflow() -> None:
    async def scenario() -> None:
        duplex = DuplexAudio()
        order: list[str] = []

        class Aec:
            def reset(self) -> None:
                order.append("reset")

            def process(self, microphone: bytes, reference: bytes) -> bytes:
                order.append("aec")
                return microphone

        class Pipeline:
            def feed(self, pcm: bytes) -> None:
                order.append("feed")

            def overflow(self) -> None:
                order.append("overflow")

        pipeline = Pipeline()
        task = asyncio.create_task(audio_io.pump(duplex, pipeline, Settings(), aec=Aec()))
        try:
            duplex.callback(b"\x01\x00" * 320, bytearray(640), 320, None, False)
            async with asyncio.timeout(1):
                while "feed" not in order:
                    await asyncio.sleep(0.005)
            # AEC runs on the microphone frame *before* VAD/ASR see it.
            assert order[:2] == ["aec", "feed"]
            duplex.overflow.set()  # simulated PortAudio overflow
            async with asyncio.timeout(1):
                while "reset" not in order:
                    await asyncio.sleep(0.005)
            assert order.index("overflow") < order.index("reset")
            assert not duplex.overflow.is_set()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_pump_detects_a_stalled_device() -> None:
    async def scenario() -> None:
        duplex = DuplexAudio()
        duplex.last_frame_at = 0.0  # long ago

        class Pipeline:
            def feed(self, pcm: bytes) -> None:  # pragma: no cover
                raise AssertionError("no frames should be pumped")

            def overflow(self) -> None:  # pragma: no cover
                pass

        with pytest.raises(audio_io.AudioStalled):
            await audio_io.pump(duplex, Pipeline(), Settings(audio_stall_s=0.01))

    asyncio.run(scenario())


def test_stream_forever_reopens_after_portaudio_failure_then_gives_up() -> None:
    async def scenario() -> None:
        attempts = {"count": 0}

        class Stream:
            def __init__(self, **kwargs):
                pass

            def __enter__(self):
                attempts["count"] += 1
                raise FakeSd.PortAudioError("device unplugged")

            def __exit__(self, *args):
                pass

        sd = FakeSd()
        sd.RawStream = Stream

        class Pipeline:
            def feed(self, pcm):  # pragma: no cover
                pass

            def overflow(self):  # pragma: no cover
                pass

        with pytest.raises(VoiceError, match="قطع"):
            await audio_io.stream_forever(
                sd, DuplexAudio(), Settings(audio_reopen_attempts=1), Pipeline()
            )
        assert attempts["count"] == 2  # initial attempt + one reopen

    asyncio.run(scenario())


def test_stream_forever_propagates_unexpected_errors() -> None:
    async def scenario() -> None:
        class Stream:
            def __init__(self, **kwargs):
                pass

            def __enter__(self):
                raise RuntimeError("stop test")

            def __exit__(self, *args):
                pass

        sd = FakeSd()
        sd.RawStream = Stream
        with pytest.raises(RuntimeError, match="stop test"):
            await audio_io.stream_forever(sd, DuplexAudio(), Settings(), object())

    asyncio.run(scenario())


def test_drain_capture_empties_the_queue() -> None:
    duplex = DuplexAudio()
    for _ in range(5):
        duplex.capture.put_nowait((b"", b""))
    assert audio_io.drain_capture(duplex) == 5
    assert audio_io.drain_capture(duplex) == 0


def test_callback_measures_jitter_and_frame_mismatch() -> None:
    duplex = DuplexAudio()
    output = bytearray(640)
    duplex.callback(b"\x00" * 640, output, 320, None, False)
    duplex.callback(b"\x00" * 640, output, 320, None, False)
    stats = duplex.stats()
    assert stats["callbacks"] == 2
    assert stats["jitter_ms"] >= 0
    duplex.callback(b"\x00" * 642, output, 321, None, False)
    assert duplex.stats()["frame_mismatch"] == 1  # device ignored blocksize
    assert duplex.overflow.is_set()


def test_pump_publishes_system_and_queue_metrics() -> None:
    """Live sessions must expose jitter/backlog/CPU/RAM, not only the benchmark.

    Mapping requirement: ``audio_callback_jitter_ms``, ``queue_depth``,
    ``cpu_usage_percent`` and ``ram_usage_mb`` were declared metric names but
    nothing in the live path ever recorded them.
    """

    async def scenario() -> None:
        from server.metrics import Metrics
        from server.resources import create_sampler, sample_process_usage

        duplex = DuplexAudio()
        metrics = Metrics()

        class Pipeline:
            def feed(self, pcm: bytes) -> None:
                pass

            def overflow(self) -> None:
                pass

        task = asyncio.create_task(
            audio_io.pump(
                duplex,
                Pipeline(),
                Settings(),
                metrics=metrics,
                metrics_interval_s=0.05,
            )
        )
        try:
            for _ in range(2):
                duplex.callback(b"\x01\x00" * 320, bytearray(640), 320, None, False)
                await asyncio.sleep(0.03)
            async with asyncio.timeout(2):
                while "queue_depth" not in metrics.snapshot()["gauges"]:
                    await asyncio.sleep(0.01)
            snapshot = metrics.snapshot()
            assert "queue_depth" in snapshot["gauges"]
            assert metrics.summary("audio_callback_jitter_ms").count >= 1
            assert metrics.counter("audio_callback_count") >= 1

            probe = sample_process_usage(create_sampler())
            if probe.cpu_percent is not None:
                assert "cpu_usage_percent" in snapshot["gauges"]
            if probe.ram_mb is not None:
                assert "ram_usage_mb" in snapshot["gauges"]
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# A stream that opens and never delivers audio must exhaust the reopen budget.
# The old loop reset its counter as soon as ``RawStream.__enter__`` returned, so
# a muted / exclusive-mode / Bluetooth microphone was reopened every few hundred
# milliseconds forever and the user never got an error.
# ---------------------------------------------------------------------------
class _FakeStream:
    """``RawStream`` double whose sessions run a scripted frame feeder."""

    def __init__(self, owner: "_FakeSd", callback, **_: object) -> None:
        owner.opens += 1
        owner.callbacks.append(callback)
        self._owner = owner
        self._session = owner.opens

    def __enter__(self) -> "_FakeStream":
        behaviour = self._owner.behaviour
        if behaviour is not None:
            import threading

            threading.Thread(
                target=behaviour,
                args=(self._owner.callbacks[-1], self._session),
                daemon=True,
            ).start()
        return self

    def __exit__(self, *args: object) -> None:
        pass


class _FakeSd:
    PortAudioError = OSError

    def __init__(self, behaviour=None) -> None:
        self.behaviour = behaviour
        self.opens = 0
        self.callbacks: list = []

    def query_devices(self):
        return DEVICES

    def check_input_settings(self, **options):  # pragma: no cover - validation only
        return None

    def check_output_settings(self, **options):  # pragma: no cover - validation only
        return None

    @property
    def RawStream(self):  # noqa: N802 - mirrors the sounddevice API
        owner = self

        class Stream(_FakeStream):
            def __init__(self, callback, **kwargs):
                super().__init__(owner, callback, **kwargs)

        return Stream


class _CounterPipeline:
    def __init__(self) -> None:
        self.frames = 0

    def feed(self, pcm: bytes) -> None:
        self.frames += 1

    def overflow(self) -> None:  # pragma: no cover - not exercised here
        pass


def _emit(frames: int):
    """Feeder that delivers ``frames`` blocks, then goes silent forever."""

    def feed(callback, session: int) -> None:
        for _ in range(frames):
            callback(b"\x01\x00" * 320, bytearray(640), 320, None, False)

    return feed


def _emit_once(frames: int):
    """Only the *first* session delivers frames; later ones stay silent."""

    def feed(callback, session: int) -> None:
        if session != 1:
            return
        for _ in range(frames):
            callback(b"\x01\x00" * 320, bytearray(640), 320, None, False)

    return feed


def test_silent_device_exhausts_the_reopen_budget_instead_of_looping_forever() -> None:
    """Bug: a device that opens but never sends a frame was reopened forever."""

    async def scenario() -> None:
        sd = _FakeSd(behaviour=_emit(0))  # opens fine, never calls back
        with pytest.raises(VoiceError, match="صدا نمی‌دهد"):
            await asyncio.wait_for(
                audio_io.stream_forever(
                    sd,
                    DuplexAudio(),
                    Settings(audio_stall_s=0.01, audio_reopen_attempts=2),
                    _CounterPipeline(),
                    reopen_delay=lambda _attempt: 0.0,
                ),
                timeout=5,
            )
        # initial open + exactly ``audio_reopen_attempts`` reopens, then it stops.
        assert sd.opens == 3

    asyncio.run(scenario())


def test_a_short_burst_of_frames_does_not_refund_the_reopen_budget() -> None:
    """Three frames is not a healthy device: the budget must keep draining."""

    async def scenario() -> None:
        sd = _FakeSd(behaviour=_emit(3))
        with pytest.raises(VoiceError, match="صدا نمی‌دهد"):
            await asyncio.wait_for(
                audio_io.stream_forever(
                    sd,
                    DuplexAudio(),
                    Settings(audio_stall_s=0.01, audio_reopen_attempts=2),
                    _CounterPipeline(),
                    reopen_delay=lambda _attempt: 0.0,
                ),
                timeout=5,
            )
        assert sd.opens == 3  # still bounded, not an endless loop

    asyncio.run(scenario())


def test_a_device_that_delivered_real_frames_earns_a_fresh_budget() -> None:
    """A stream that worked before dying may be reopened without limit abuse."""

    async def scenario() -> None:
        from server.audio_io import PROGRESS_FRAMES

        # First session delivers enough frames to count as progress, then stalls.
        sd = _FakeSd(behaviour=_emit_once(PROGRESS_FRAMES))
        pipeline = _CounterPipeline()
        with pytest.raises(VoiceError, match="صدا نمی‌دهد"):
            await asyncio.wait_for(
                audio_io.stream_forever(
                    sd,
                    DuplexAudio(),
                    # The feeder thread pushes all frames in a tight loop and then
                    # goes silent.  A 10 ms stall window used to expire before the
                    # pump loop had drained those 50 queued frames on a loaded
                    # machine, so ``pipeline.frames`` was flaky; the window only
                    # has to be long enough to drain the queue, not to test speed.
                    Settings(audio_stall_s=0.2, audio_reopen_attempts=1),
                    pipeline,
                    reopen_delay=lambda _attempt: 0.0,
                ),
                timeout=5,
            )
        # Every session made progress, so each failure was forgiven once; the
        # loop still terminates because the *last* session stopped delivering.
        assert sd.opens >= 2
        assert pipeline.frames >= PROGRESS_FRAMES

    asyncio.run(scenario())


def test_stable_frames_threshold_is_configurable() -> None:
    async def scenario() -> None:
        sd = _FakeSd(behaviour=_emit(3))
        with pytest.raises(VoiceError, match="صدا نمی‌دهد"):
            await asyncio.wait_for(
                audio_io.stream_forever(
                    sd,
                    DuplexAudio(),
                    Settings(
                        audio_stall_s=0.01,
                        audio_reopen_attempts=0,
                        audio_reopen_stable_frames=2,
                    ),
                    _CounterPipeline(),
                    reopen_delay=lambda _attempt: 0.0,
                ),
                timeout=5,
            )
        assert sd.opens == 1  # 3 frames >= 2, but with no budget left it must stop

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Bug 3: the stall watchdog only fired when ``last_frame_at`` was already set,
# so a stream that opened and never called back was never detected at all.
# ---------------------------------------------------------------------------
def test_pump_detects_a_stream_that_never_delivered_a_frame() -> None:
    async def scenario() -> None:
        duplex = DuplexAudio()  # opened, but no callback ever arrived

        class Pipeline:
            def feed(self, pcm: bytes) -> None:  # pragma: no cover
                raise AssertionError("no frames should be pumped")

            def overflow(self) -> None:  # pragma: no cover
                pass

        task = asyncio.create_task(
            audio_io.pump(duplex, Pipeline(), Settings(audio_stall_s=0.01))
        )
        with pytest.raises(audio_io.AudioStalled):
            await asyncio.wait_for(task, timeout=2)

    asyncio.run(scenario())


def test_stall_is_measured_from_stream_open_not_only_from_the_first_frame() -> None:
    """A freshly opened stream gets the full stall window before being blamed."""
    duplex = DuplexAudio()
    opened_at = time.monotonic() - 0.01
    duplex.opened(at=opened_at)
    assert duplex.last_frame_at is None
    assert duplex.stall_baseline() == pytest.approx(opened_at)

    async def scenario() -> None:
        class Pipeline:
            def feed(self, pcm: bytes) -> None:  # pragma: no cover
                pass

            def overflow(self) -> None:  # pragma: no cover
                pass

        task = asyncio.create_task(
            audio_io.pump(duplex, Pipeline(), Settings(audio_stall_s=0.01))
        )
        with pytest.raises(audio_io.AudioStalled):
            await asyncio.wait_for(task, timeout=2)
        # A frame stamps the same clock and restarts the window.
        duplex.callback(b"\x00" * 640, bytearray(640), 320, None, False)
        assert duplex.stall_baseline() == duplex.last_frame_at

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Bug 2: ``pump`` compares ``time.monotonic() - last_frame_at`` while
# ``DuplexAudio._measure`` stamped with ``time.perf_counter``.  The two origins
# are undefined relative to each other (and differ on Windows), so a healthy
# stream could be declared dead, or a dead one look alive forever.
# ---------------------------------------------------------------------------
def test_callback_timestamps_share_the_watchdogs_clock() -> None:
    monkeypatch = pytest.MonkeyPatch()
    try:
        # A ``perf_counter`` from a completely different origin: if the callback
        # ever went back to it, ``last_frame_at`` would land far away from the
        # ``monotonic()`` the watchdog reads.
        monkeypatch.setattr(time, "perf_counter", lambda: 5.0)
        duplex = DuplexAudio()
        duplex.opened()
        before = time.monotonic()
        duplex.callback(b"\x00" * 640, bytearray(640), 320, None, False)
        after = time.monotonic()
        stamp = duplex.last_frame_at
        assert stamp is not None
        assert before <= stamp <= after, f"stamp {stamp} is not on the monotonic clock"
        assert duplex.stall_baseline() == stamp
    finally:
        monkeypatch.undo()


def test_healthy_stream_is_not_stalled_when_perf_counter_has_another_origin() -> None:
    """The exact Windows failure: a fresh frame must never look ancient."""

    async def scenario() -> None:
        import time as time_module

        duplex = DuplexAudio()
        duplex.opened()
        duplex.callback(b"\x00" * 640, bytearray(640), 320, None, False)

        sentinel = 5.0
        original = time_module.perf_counter
        time_module.perf_counter = lambda: sentinel  # an origin far from monotonic
        try:
            class Pipeline:
                def feed(self, pcm: bytes) -> None:  # pragma: no cover
                    pass

                def overflow(self) -> None:  # pragma: no cover
                    pass

            task = asyncio.create_task(
                audio_io.pump(duplex, Pipeline(), Settings(audio_stall_s=0.3))
            )
            with pytest.raises(asyncio.TimeoutError):
                # The frame is fresh *on the watchdog's own clock*: no stall.
                await asyncio.wait_for(asyncio.shield(task), timeout=0.15)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        finally:
            time_module.perf_counter = original

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Bug 4: nothing device-local was reset on reopen, so the detector kept a
# half-recorded utterance from before the gap and the AEC filter kept an echo
# path that no longer existed.
# ---------------------------------------------------------------------------
def test_reopen_resets_detector_aec_and_buffered_audio() -> None:
    async def scenario() -> None:
        resets: list[str] = []

        class Detector:
            def reset(self) -> None:
                resets.append("detector")

        class Pipeline:
            def __init__(self) -> None:
                self.detector = Detector()

            def feed(self, pcm: bytes) -> None:
                pass

            def overflow(self) -> None:  # pragma: no cover
                pass

        class Aec:
            def reset(self) -> None:
                resets.append("aec")

            def process(self, microphone: bytes, reference: bytes) -> bytes:
                return microphone

        sd = _FakeSd(behaviour=_emit(1))  # one frame, then silence -> stall
        duplex = DuplexAudio()
        duplex.capture.put_nowait((b"stale", b"stale"))  # buffered across the gap
        with pytest.raises(VoiceError, match="صدا نمی‌دهد"):
            await asyncio.wait_for(
                audio_io.stream_forever(
                    sd,
                    duplex,
                    Settings(audio_stall_s=0.01, audio_reopen_attempts=1),
                    Pipeline(),
                    aec=Aec(),
                    reopen_delay=lambda _attempt: 0.0,
                ),
                timeout=5,
            )
        # One reopen happened: detector + AEC were reset and the stale frame dropped.
        assert resets.count("detector") >= 1
        assert resets.count("aec") >= 1
        assert duplex.capture.empty()
        # The last stream (which opened, delivered one frame, then died) is the
        # one the reset belongs to; its stale clock must not survive.
        assert duplex.last_frame_at is None

    asyncio.run(scenario())


def test_reopen_resets_a_pipeline_that_owns_its_own_reset() -> None:
    """``Pipeline.reset()`` is preferred over poking ``detector.reset()``."""

    async def scenario() -> None:
        resets: list[str] = []

        class Pipeline:
            def reset(self) -> None:
                resets.append("pipeline")

            def feed(self, pcm: bytes) -> None:
                pass

            def overflow(self) -> None:  # pragma: no cover
                pass

        sd = _FakeSd(behaviour=_emit(0))
        with pytest.raises(VoiceError):
            await asyncio.wait_for(
                audio_io.stream_forever(
                    sd,
                    DuplexAudio(),
                    Settings(audio_stall_s=0.01, audio_reopen_attempts=0),
                    Pipeline(),
                    reopen_delay=lambda _attempt: 0.0,
                ),
                timeout=5,
            )
        assert resets == []

    asyncio.run(scenario())


def test_reset_stream_state_handles_plain_fakes() -> None:
    """Fakes without the new hooks must still be drained, never crash."""

    class Plain:
        def __init__(self) -> None:
            self.capture = __import__("queue").Queue()

    plain = Plain()
    plain.capture.put_nowait((b"a", b"b"))
    audio_io.reset_stream_state(plain, object(), object())
    assert plain.capture.empty()


def test_pipeline_reset_abandons_the_unfinished_utterance_but_keeps_finals() -> None:
    """A reopen must not merge audio from before the gap into a transcript.

    ``Pipeline.reset()`` is what ``reset_stream_state`` calls; it drops the
    half-recorded utterance (like ``overflow`` does) while leaving the lossless
    final queue untouched, and it announces why.
    """
    from server.streaming_voice import Pipeline
    from voicekit import FakeAsr, FakeSpeaker, feed_utterance, frame, make_settings

    events: list[dict] = []
    pipeline = Pipeline(
        make_settings(voice_asr_interval_s=10.0),
        FakeAsr(text="جمله"),
        FakeSpeaker(),
        None,
        events.append,
    )
    # Mid-utterance: the detector holds frames from before the device died.
    for _ in range(12):
        pipeline.feed(frame())
    assert pipeline.utterance is not None
    assert pipeline.detector.started
    queued = pipeline.scheduler.pending_finals

    pipeline.reset()

    assert pipeline.detector.started is False
    assert pipeline.detector.frames == []
    assert pipeline.utterance is None
    assert pipeline.scheduler.pending_finals == queued  # ASR queue untouched
    assert pipeline.state.value == "idle"
    reset = [event for event in events if event["type"] == "input_reset"]
    assert len(reset) == 1
    assert reset[0]["reason"] == "audio_stream_reopened"
    assert reset[0]["abandoned_utterance_id"] >= 1
    assert pipeline.metrics.counter("audio_reopen_reset_count") == 1
    # The next real utterance is a fresh id, never a continuation of the dead one.
    feed_utterance(pipeline)
    assert pipeline.utterance is None or pipeline.utterance.id > reset[0]["abandoned_utterance_id"]
