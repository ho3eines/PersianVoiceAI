"""Audio device resolution, validation, pumping and stream recovery."""

from __future__ import annotations

import asyncio
import threading

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
