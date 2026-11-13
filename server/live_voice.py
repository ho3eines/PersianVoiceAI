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

import json
import logging
import math
import re
import threading
import time
from pathlib import Path
from typing import Any

from .config import Settings
from .vad import EnergyVAD
from .voice import AudioValidationError, ModelLoadError, VoiceError, dependency

logger = logging.getLogger("persian_voice_ai.tts")

SENTENCE_END = re.compile(r"[.!؟?\n؛](?:\s|$)")
MAX_CHUNK_CHARS = 180

#: Digits of every script a Persian model writes: ASCII, Arabic-Indic (٠-٩) and
#: Persian (۰-۹).  Matching only ASCII was a real defect — ``۱. اول`` (the form a
#: Persian model produces) was not recognised as a list marker, so every item
#: number became its own utterance and Piper read it as "یک.".
_DIGITS = "0-9\u0660-\u0669\u06f0-\u06f9"

#: A boundary immediately preceded by one of these is not a sentence end:
#: ``1.`` / ``2)`` is a list marker and ``3.5`` a decimal.  The original
#: ``SENTENCE_END`` split ``"**مهم:** سه مورد:\n1. اول\n2. دوم"`` into
#: ``['…\n1.', 'اول\n2.', 'دوم']``, so Piper pronounced every list number as
#: its own sentence with an audible full stop.
_NOT_A_BOUNDARY = re.compile(
    rf"(?:[{_DIGITS}]{{1,3}}[.)]|[{_DIGITS}]\.[{_DIGITS}]|[A-Za-z]\.[A-Za-z])$"
)
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
        # A dot that ends a list number or a decimal is not a sentence end.
        # Scan past it rather than re-searching from its own end, or the same
        # match is found again and ``"2."`` becomes its own utterance.
        while match is not None and _NOT_A_BOUNDARY.search(
            buffer[: match.end() - 1].rstrip()
        ):
            match = SENTENCE_END.search(buffer, match.end())
        if match:
            end = match.end()
        elif len(buffer) > MAX_CHUNK_CHARS:
            end = buffer.rfind(" ", 0, MAX_CHUNK_CHARS)
            if end <= 0:
                # No space at all in the window (an unpunctuated blob, or a
                # language that does not separate words).  Refusing to split
                # held the whole buffer back until the end of the turn, so the
                # utterance latency grew without bound; cut at the limit
                # instead, which is what the limit exists for.
                end = MAX_CHUNK_CHARS
        else:
            end = -1
        if end <= 0:
            break
        piece = buffer[:end].strip()
        if piece:
            ready.append(piece)
        buffer = buffer[end:]
        buffer = buffer.lstrip()
    if not final and ready:
        # Streaming: a comma or a list/dictionary marker only becomes a
        # sentence end once the *next* character decides.  Holding the last
        # piece back means "2. دوم" is split as one utterance instead of
        # "2." followed by "دوم" -- the number is a marker, not a full stop.
        last = ready[-1]
        if last.endswith((",", "،")) or re.search(rf"[{_DIGITS}]{{1,3}}[.)]$", last):
            ready = ready[:-1]
            buffer = (last + " " + buffer).strip()
    if final and buffer.strip():
        ready.append(buffer.strip())
        buffer = ""
    return ready, buffer


def _validate_tts_model(path: str) -> None:
    if not isinstance(path, str) or not path.strip():
        raise ModelLoadError(
            "مسیر مدل Piper تنظیم نشده است؛ PVAI_TTS_MODEL_PATH یا صدای انتخاب‌شده را تنظیم کنید."
        )
    model = Path(path)
    if not model.is_file():
        raise ModelLoadError(
            "مدل Piper موجود نیست؛ PVAI_TTS_MODEL_PATH (یا PVAI_TTS_MODEL_MALE/"
            "PVAI_TTS_MODEL_FEMALE) را برای صدای انتخاب‌شده تنظیم کنید."
        )
    config = Path(str(model) + ".json")
    if not config.is_file():
        raise ModelLoadError(
            "فایل تنظیمات مدل Piper (.onnx.json) موجود نیست؛ بستهٔ وزن فارسی کامل نیست."
        )
    try:
        config_data = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ModelLoadError(
            f"فایل تنظیمات مدل Piper (.onnx.json) نامعتبر است: {config}"
        ) from exc
    if not isinstance(config_data, dict):
        raise ModelLoadError(
            f"ساختار فایل تنظیمات مدل Piper (.onnx.json) نامعتبر است: {config}"
        )


def load_piper_voice(settings: Settings, voice: str) -> Any:
    """Load (or validate) one Piper voice without building a ``Speaker``.

    Kept separate from ``Speaker.__init__`` so a process-wide pool can own the
    model while each device connection owns only its playback sink.
    """
    path = settings.tts_model_for(voice)
    _validate_tts_model(path)
    started = time.perf_counter()
    logger.info("Loading Piper voice=%s from %s", voice, path)
    try:
        loaded = dependency("piper.voice").PiperVoice.load(path)
        logger.info("Piper voice=%s ready in %.1f ms", voice, (time.perf_counter() - started) * 1000)
        return loaded
    except VoiceError:
        raise
    except Exception as exc:
        raise ModelLoadError(
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
            self.model = load_piper_voice(settings, self.voice)
        self.sd = dependency("sounddevice")

    # ------------------------------------------------------------------ helpers
    def describe(self) -> dict[str, str]:
        return {"voice": self.voice, "model": self.model_path}

    def close(self) -> None:
        """Drop references to the TTS model and duplex device."""
        self.model = None
        self.duplex = None

    def _mono_16k(self, chunk) -> bytes:  # noqa: ANN001 - Piper AudioChunk
        return self._to_mono_rate(chunk, 16000)

    def _to_mono_rate(self, chunk, target_rate: int) -> bytes:  # noqa: ANN001
        """Convert any Piper chunk to PCM16 mono at ``target_rate``.

        Piper Persian voices are commonly 22 050 Hz while the duplex transport
        (and the ASR contract) is 16 kHz.  Writing 22 050 Hz samples into a
        16 kHz stream plays the reply ~27 % too fast and at the wrong pitch —
        a real defect the previous implementation had on the speaker-only path.
        """
        raw = chunk.audio_int16_bytes
        if not isinstance(raw, (bytes, bytearray, memoryview)):
            raise AudioValidationError("بافر خروجی Piper از نوع بایتی معتبر نیست.")
        try:
            channels = int(getattr(chunk, "sample_channels", 1))
            rate = int(getattr(chunk, "sample_rate", 16000))
            target = int(target_rate)
        except (TypeError, ValueError) as exc:
            raise AudioValidationError("مشخصات نرخ نمونه‌برداری یا کانال خروجی Piper نامعتبر است.") from exc
        if channels <= 0 or rate <= 0 or target <= 0:
            raise AudioValidationError(
                f"نرخ نمونه‌برداری یا تعداد کانال خروجی Piper نامعتبر است (rate={rate}, channels={channels}, target={target})."
            )
        if not raw:
            return b""
        if len(raw) % (2 * channels):
            raise AudioValidationError("طول بافر PCM خروجی Piper با تعداد کانال‌ها هم‌تراز نیست.")
        np = dependency("numpy")
        samples = None
        try:
            samples = np.frombuffer(raw, dtype="<i2").astype(np.float32)
            if channels > 1:
                samples = samples.reshape(-1, channels).mean(axis=1, dtype=np.float32)
            if rate != target:
                resample_poly = dependency("scipy.signal").resample_poly
                divisor = math.gcd(rate, target)
                samples = resample_poly(samples, target // divisor, rate // divisor)
            if not np.isfinite(samples).all():
                raise AudioValidationError("تبدیل خروجی Piper مقدار نامعتبر NaN/Inf تولید کرد.")
            return np.clip(np.rint(samples), -32768, 32767).astype("<i2").tobytes()
        except AudioValidationError:
            raise
        except Exception as exc:
            raise AudioValidationError(f"تبدیل نرخ نمونه‌برداری یا کانال خروجی Piper ناموفق بود: {exc}") from exc
        finally:
            del samples

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
        chunks = None
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
                if not pcm:
                    continue
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
            closer = getattr(chunks, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:
                    pass
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

    def synthesize_pcm(self, text: str) -> tuple[bytes, int]:
        """Synthesize ``text`` to mono PCM16 without opening a speaker device.

        The browser UI plays the reply itself, so the server must return audio
        bytes instead of writing to a local sound device.  The returned rate is
        the same one ``speak`` uses (``audio_sample_rate``, 16 kHz by contract).
        """
        if not (text or "").strip():
            return b"", self._output_rate()
        pieces: list[bytes] = []
        chunks = self._synthesize(text)
        try:
            for chunk in chunks:
                pcm, _rate, _channels = self._chunk_pcm(chunk)
                if pcm:
                    pieces.append(pcm)
        finally:
            closer = getattr(chunks, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:  # noqa: BLE001 - closing a lazy generator
                    pass
        return b"".join(pieces), self._output_rate()

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
            try:
                yield from chunks
            finally:
                closer = getattr(chunks, "close", None)
                if callable(closer):
                    closer()
            return
        iterator = iter(chunks)
        try:
            while True:
                with lock:
                    try:
                        chunk = next(iterator)
                    except StopIteration:
                        return
                yield chunk
        finally:
            closer = getattr(chunks, "close", None)
            if callable(closer):
                with lock:
                    closer()

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
        try:
            rate = int(chunk.sample_rate)
            channels = int(chunk.sample_channels)
        except (TypeError, ValueError) as exc:
            raise AudioValidationError("مشخصات نرخ نمونه‌برداری یا کانال خروجی Piper نامعتبر است.") from exc
        if rate <= 0 or channels <= 0:
            raise AudioValidationError(
                f"نرخ نمونه‌برداری یا تعداد کانال خروجی Piper نامعتبر است (rate={rate}, channels={channels})."
            )
        pcm = chunk.audio_int16_bytes
        if not isinstance(pcm, (bytes, bytearray, memoryview)):
            raise AudioValidationError("بافر خروجی Piper از نوع بایتی معتبر نیست.")
        if len(pcm) % (2 * channels):
            raise AudioValidationError("طول بافر PCM خروجی Piper با تعداد کانال‌ها هم‌تراز نیست.")
        target = self._output_rate()
        if rate == target and channels == 1:
            return bytes(pcm), target, 1
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
