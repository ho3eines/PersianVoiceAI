"""Endpointing, phrase splitting and PCM output helpers for the async PC runtime.

``Endpoint`` is kept as a thin, API-compatible alias of the improved
:class:`server.vad.EnergyVAD` so older call sites and tests keep working; the
live pipeline builds its detector from ``Settings`` through ``vad.build_vad``.

``Speaker`` owns Piper synthesis and playback.  It reports three distinct
moments that used to be conflated:

* ``on_first_chunk`` — the first PCM chunk came out of the model;
* ``on_first_frame`` — the first 20 ms block was handed to the output device
  (this is the closest local proxy for "first audible sound"; DAC latency is
  not measurable from Python);
* ``on_complete`` — the last block was accepted by the device, with the played
  duration.  "Playback finished" is therefore not confused with "synthesis
  finished" or "queued into an unbounded buffer".
"""

from __future__ import annotations

import math
import re
import threading
import time
from pathlib import Path
from typing import Any

from .config import Settings
from .vad import EnergyVAD
from .voice import VoiceError, dependency

SENTENCE_END = re.compile(r"[.!؟?\n؛](?:\s|$)")
MAX_CHUNK_CHARS = 180
OUTPUT_BLOCK_SECONDS = 0.02


class Endpoint(EnergyVAD):
    """Energy endpoint with the historical constructor signature."""

    def __init__(self, threshold: float = 0.015, silence_frames: int = 25,
                 max_frames: int = 750) -> None:
        super().__init__(threshold, silence_frames=silence_frames, max_frames=max_frames)


def sentences(buffer: str, final: bool = False) -> tuple[list[str], str]:
    """Split ready sentences from a streaming buffer, preserving the tail."""
    ready: list[str] = []
    while True:
        match = SENTENCE_END.search(buffer)
        if match:
            end = match.end()
        elif len(buffer) > MAX_CHUNK_CHARS:
            end = buffer.rfind(" ", 0, MAX_CHUNK_CHARS)
        else:
            end = -1
        if end <= 0:
            break
        piece = buffer[:end].strip()
        if piece:
            ready.append(piece)
        buffer = buffer[end:]
        buffer = buffer.lstrip()
    if final and buffer.strip():
        ready.append(buffer.strip())
        buffer = ""
    return ready, buffer


def _validate_tts_model(path: str) -> None:
    model = Path(path)
    if not model.is_file():
        raise VoiceError(
            "مدل Piper موجود نیست؛ PVAI_TTS_MODEL_PATH (یا PVAI_TTS_MODEL_MALE/"
            "PVAI_TTS_MODEL_FEMALE) را برای صدای انتخاب‌شده تنظیم کنید."
        )
    config = Path(str(model) + ".json")
    if not config.is_file():
        raise VoiceError(
            "فایل تنظیمات مدل Piper (.onnx.json) موجود نیست؛ بستهٔ وزن فارسی کامل نیست."
        )


def load_piper_voice(settings: Settings, voice: str) -> Any:
    """Load (or validate) one Piper voice without building a ``Speaker``.

    Kept separate from ``Speaker.__init__`` so a process-wide pool can own the
    model while each device connection owns only its playback sink.
    """
    path = settings.tts_model_for(voice)
    _validate_tts_model(path)
    try:
        return dependency("piper.voice").PiperVoice.load(path)
    except VoiceError:
        raise
    except Exception as exc:
        raise VoiceError(
            "بارگذاری مدل Piper ناموفق بود؛ سازگاری وزن فارسی و وابستگی onnxruntime را بررسی کنید."
        ) from exc


class Speaker:
    """Piper synthesis + 20 ms-block PCM playback with literal cancellation."""

    def __init__(
        self,
        settings: Settings,
        duplex=None,
        *,
        voice: str | None = None,
        model: Any = None,
        model_lock: Any = None,
    ) -> None:
        self.settings = settings
        self.duplex = duplex
        # ``synthesize`` returns a lazy generator that advances model state as
        # it is consumed, so a model shared by several connections needs its
        # steps serialised.  Per-chunk, not per-utterance: playback of one
        # device must not have to finish before another can start speaking.
        self._model_lock = model_lock
        self.voice = (voice or settings.tts_voice or "female").strip().lower()
        self.model_path = settings.tts_model_for(self.voice)
        _validate_tts_model(self.model_path)
        if model is not None:
            # Shared, already-loaded Piper model (see ws_voice.ModelPool): one
            # ONNX session per voice instead of one per device.
            self.model = model
        else:
            try:
                self.model = dependency("piper.voice").PiperVoice.load(self.model_path)
            except VoiceError:
                raise
            except Exception as exc:
                raise VoiceError(
                    "بارگذاری مدل Piper ناموفق بود؛ سازگاری وزن فارسی و وابستگی onnxruntime را بررسی کنید."
                ) from exc
        self.sd = dependency("sounddevice")

    # ------------------------------------------------------------------ helpers
    def describe(self) -> dict[str, str]:
        return {"voice": self.voice, "model": self.model_path}

    def _mono_16k(self, chunk) -> bytes:  # noqa: ANN001 - Piper AudioChunk
        return self._to_mono_rate(chunk, 16000)

    def _to_mono_rate(self, chunk, target_rate: int) -> bytes:  # noqa: ANN001
        """Convert any Piper chunk to PCM16 mono at ``target_rate``.

        Piper Persian voices are commonly 22 050 Hz while the duplex transport
        (and the ASR contract) is 16 kHz.  Writing 22 050 Hz samples into a
        16 kHz stream plays the reply ~27 % too fast and at the wrong pitch —
        a real defect the previous implementation had on the speaker-only path.
        """
        np = dependency("numpy")
        samples = np.frombuffer(chunk.audio_int16_bytes, dtype="<i2").astype(np.float32)
        channels = int(getattr(chunk, "sample_channels", 1) or 1)
        if channels > 1:
            samples = samples.reshape(-1, channels).mean(axis=1)
        rate = int(getattr(chunk, "sample_rate", 16000))
        target = int(target_rate)
        if rate != target:
            resample_poly = dependency("scipy.signal").resample_poly
            divisor = math.gcd(rate, target)
            samples = resample_poly(samples, target // divisor, rate // divisor)
        return np.clip(np.rint(samples), -32768, 32767).astype("<i2").tobytes()

    # ---------------------------------------------------------------- synthesis
    def speak(
        self,
        text: str,
        cancel: threading.Event,
        *,
        on_first_chunk=None,
        on_first_frame=None,
        on_complete=None,
    ) -> None:
        """Synthesize and play ``text``; stop within one block of ``cancel``."""
        if cancel.is_set() or not (text or "").strip():
            if on_complete is not None:
                on_complete(time.perf_counter(), 0.0)
            return
        played_blocks = 0
        first_frame: float | None = None
        self._output = None
        try:
            chunks = self._synthesize(text)
            if getattr(self, "duplex", None) is None:
                self._output = self._open_output()
            for chunk in chunks:
                if on_first_chunk is not None:
                    on_first_chunk(time.perf_counter())
                    on_first_chunk = None
                if cancel.is_set():
                    break
                pcm, rate, channels = self._chunk_pcm(chunk)
                sample_width = 2 * max(1, channels)
                block = max(1, int(rate * OUTPUT_BLOCK_SECONDS)) * sample_width
                stopped = False
                for start in range(0, len(pcm), block):
                    if cancel.is_set():
                        stopped = True
                        break
                    piece = pcm[start:start + block]
                    self._write(piece, cancel)
                    if first_frame is None:
                        first_frame = time.perf_counter()
                        if on_first_frame is not None:
                            on_first_frame(first_frame)
                    played_blocks += len(piece) // sample_width
                if stopped:
                    break
        finally:
            output = self._output
            if output is not None:
                try:
                    if cancel.is_set():
                        output.abort()
                    output.__exit__(None, None, None)
                except Exception:  # pragma: no cover - device already gone
                    pass
                self._output = None
            if on_complete is not None:
                on_complete(time.perf_counter(), played_blocks * OUTPUT_BLOCK_SECONDS * 1000)

    def _synthesize(self, text: str) -> Any:
        """Yield chunks from the model, serialising each model step.

        Without a shared model (``model_lock`` is ``None``) this is a plain
        pass-through; with one, only ``next()`` is held under the lock, so two
        devices speak concurrently while the model itself is stepped one
        caller at a time.
        """
        chunks = self.model.synthesize(text)
        lock = getattr(self, "_model_lock", None)  # absent on partially built Speakers
        if lock is None:
            yield from chunks
            return
        iterator = iter(chunks)
        while True:
            with lock:
                try:
                    chunk = next(iterator)
                except StopIteration:
                    return
            yield chunk

    # --------------------------------------------------------------- audio I/O
    def _output_rate(self) -> int:
        settings = getattr(self, "settings", None)
        return int(getattr(settings, "audio_sample_rate", 16000) or 16000)

    def _open_output(self):  # noqa: ANN202
        # One stream, one format: the device is opened at the configured rate
        # and every Piper chunk is normalised to it before writing.
        output = self.sd.RawOutputStream(
            samplerate=self._output_rate(), channels=1, dtype="int16"
        )
        return output.__enter__()

    def _chunk_pcm(self, chunk) -> tuple[bytes, int, int]:  # noqa: ANN001
        missing = [
            name
            for name in ("audio_int16_bytes", "sample_rate", "sample_channels")
            if not hasattr(chunk, name)
        ]
        if missing:
            raise VoiceError(
                "خروجی مدل Piper با قرارداد AudioChunk سازگار نیست (فیلدهای "
                + ", ".join(missing)
                + "). نسخهٔ piper-tts و وزن .onnx را بررسی کنید."
            )
        target = self._output_rate()
        native = (
            int(chunk.sample_rate) == target and int(chunk.sample_channels) == 1
        )
        if native:  # already the stream format: no numpy/scipy needed
            return chunk.audio_int16_bytes, target, 1
        return self._to_mono_rate(chunk, target), target, 1

    def _write(self, pcm: bytes, cancel: threading.Event) -> None:
        duplex = getattr(self, "duplex", None)
        if duplex is not None:
            duplex.write(pcm, cancel)
            return
        self._output.write(pcm)  # type: ignore[union-attr]  # opened by ``speak``


def run(settings: Settings) -> None:
    """Default runtime is the asynchronous incremental pipeline."""
    import asyncio

    from .streaming_voice import run_live

    asyncio.run(run_live(settings))
