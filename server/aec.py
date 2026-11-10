"""SpeexDSP acoustic echo cancellation and synchronized duplex PCM transport.

Native SpeexDSP is local and explicitly required when AEC is enabled. No silent bypass.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import queue
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from .voice import VoiceError

FRAME_SAMPLES = 320
FRAME_BYTES = 640
RATE = 16000
REQUIRED_SYMBOLS = (
    "speex_echo_state_init",
    "speex_echo_state_destroy",
    "speex_echo_state_reset",
    "speex_echo_ctl",
    "speex_echo_cancellation",
)


def find_library(library: str = "") -> str | None:
    """Resolve the configured SpeexDSP shared library, or ``None``."""
    if library:
        path = Path(library)
        return str(path.resolve()) if path.is_file() else None
    return ctypes.util.find_library("speexdsp")


def probe_aec(library: str = "") -> dict[str, Any]:
    """Check availability and exported symbols **without** allocating a filter.

    Used by ``/ready`` and the startup report so a missing DLL is a clear,
    actionable status instead of an unexplained crash when the live path starts.
    """
    location = find_library(library)
    if not location:
        return {
            "enabled": None,
            "available": False,
            "library": library or "",
            "detail": "کتابخانهٔ SpeexDSP پیدا نشد؛ PVAI_AEC_LIBRARY را تنظیم کنید یا AEC را خاموش کنید.",
        }
    try:
        handle = ctypes.CDLL(location)
    except OSError as exc:
        return {
            "enabled": None,
            "available": False,
            "library": location,
            "detail": f"بارگذاری کتابخانهٔ SpeexDSP ناموفق بود: {exc}",
        }
    missing = [name for name in REQUIRED_SYMBOLS if not hasattr(handle, name)]
    if missing:
        return {
            "enabled": None,
            "available": False,
            "library": location,
            "detail": "نمادهای لازم در کتابخانهٔ SpeexDSP نیست: " + ", ".join(missing),
        }
    return {"enabled": None, "available": True, "library": location, "detail": "OK"}


def aec_status(settings: Any) -> dict[str, Any]:
    """Readiness view of AEC for the configured settings (never raises)."""
    if not getattr(settings, "aec_enabled", False):
        return {
            "enabled": False,
            "available": None,
            "library": "",
            "detail": "AEC خاموش است؛ برای تست بدون هدفون از آن استفاده نکنید.",
        }
    probe = probe_aec(getattr(settings, "aec_library", ""))
    enabled = True
    if getattr(settings, "audio_sample_rate", RATE) != RATE or getattr(
        settings, "audio_block_ms", 20
    ) != 20:
        return {
            "enabled": enabled,
            "available": False,
            "library": probe.get("library", ""),
            "detail": "AEC فقط با 16kHz و فریم 20ms کار می‌کند؛ تنظیمات صوتی را اصلاح کنید.",
        }
    return {"enabled": enabled, **{k: v for k, v in probe.items() if k != "enabled"}}


class SpeexAEC:
    def __init__(self, library: str = "", *, filter_ms: int = 250, delay_ms: int = 40) -> None:
        location = find_library(library)
        if not location:
            raise VoiceError("کتابخانهٔ SpeexDSP پیدا نشد؛ PVAI_AEC_LIBRARY را تنظیم کنید یا AEC را صریحاً خاموش کنید.")
        try:
            self.lib = ctypes.CDLL(location)
            pointer = ctypes.POINTER(ctypes.c_int16)
            self.lib.speex_echo_state_init.argtypes = [ctypes.c_int, ctypes.c_int]
            self.lib.speex_echo_state_init.restype = ctypes.c_void_p
            self.lib.speex_echo_state_destroy.argtypes = [ctypes.c_void_p]
            self.lib.speex_echo_state_destroy.restype = None
            self.lib.speex_echo_state_reset.argtypes = [ctypes.c_void_p]
            self.lib.speex_echo_state_reset.restype = None
            self.lib.speex_echo_ctl.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
            self.lib.speex_echo_ctl.restype = ctypes.c_int
            self.lib.speex_echo_cancellation.argtypes = [ctypes.c_void_p, pointer, pointer, pointer]
            self.lib.speex_echo_cancellation.restype = None
            self.state = self.lib.speex_echo_state_init(FRAME_SAMPLES, RATE * filter_ms // 1000)
            if not self.state:
                raise RuntimeError("AEC allocation failed")
            rate = ctypes.c_int(RATE)
            if self.lib.speex_echo_ctl(self.state, 24, ctypes.byref(rate)) != 0:
                self.close()
                raise RuntimeError("AEC sample rate rejected")
        except (OSError, AttributeError, RuntimeError) as exc:
            raise VoiceError("راه‌اندازی SpeexDSP ناموفق بود؛ فایل کتابخانه و معماری ۶۴بیتی را بررسی کنید.") from exc
        self.delay_frames = delay_ms // 20
        self.reset_delay()

    def reset_delay(self) -> None:
        self.references: deque[bytes] = deque([bytes(FRAME_BYTES)] * self.delay_frames)

    def reset(self) -> None:
        self.lib.speex_echo_state_reset(self.state)
        self.reset_delay()

    def process(self, microphone: bytes, reference: bytes) -> bytes:
        if len(microphone) != FRAME_BYTES or len(reference) != FRAME_BYTES:
            raise ValueError("AEC requires 20ms PCM16 mono frames")
        self.references.append(reference)
        delayed = self.references.popleft()
        samples = ctypes.c_int16 * FRAME_SAMPLES
        output = samples()
        self.lib.speex_echo_cancellation(self.state, samples.from_buffer_copy(microphone),
                                        samples.from_buffer_copy(delayed), output)
        return bytes(output)

    def close(self) -> None:
        if getattr(self, "state", None):
            self.lib.speex_echo_state_destroy(self.state)
            self.state = None


class DuplexAudio:
    """Callback only copies bounded PCM. Native AEC runs outside the callback."""

    def __init__(self) -> None:
        self.capture: queue.Queue[tuple[bytes, bytes]] = queue.Queue(maxsize=100)
        self.render: queue.Queue[tuple[bytes, threading.Event, threading.Event]] = queue.Queue(maxsize=50)
        self.overflow = threading.Event()
        self.closed = threading.Event()
        # Diagnostics written from the audio callback (plain scalars only).
        self.last_frame_at: float | None = None
        self.callbacks = 0
        self.jitter_ms = 0.0
        self.max_jitter_ms = 0.0
        self.frame_mismatch = 0
        self.last_frames = FRAME_SAMPLES
        self._last_callback_at: float | None = None

    def _measure(self, frames: int) -> None:
        now = time.perf_counter()
        self.last_frame_at = now
        if self._last_callback_at is not None:
            expected = frames / RATE * 1000.0
            deviation = abs((now - self._last_callback_at) * 1000.0 - expected)
            self.jitter_ms = deviation
            if deviation > self.max_jitter_ms:
                self.max_jitter_ms = deviation
        self._last_callback_at = now
        self.callbacks += 1

    def stats(self) -> dict[str, float | int | None]:
        return {
            "callbacks": self.callbacks,
            "jitter_ms": round(self.jitter_ms, 3),
            "max_jitter_ms": round(self.max_jitter_ms, 3),
            "frame_mismatch": self.frame_mismatch,
            "last_frames": self.last_frames,
        }

    def callback(self, indata: Any, outdata: Any, frames: int, timing: Any, status: Any) -> None:
        self._measure(frames)
        self.last_frames = int(frames)
        if frames != FRAME_SAMPLES:
            outdata[:] = bytes(len(outdata))
            self.frame_mismatch += 1
            self.overflow.set()
            return
        reference = bytes(FRAME_BYTES)
        # Bounded scan removes stale generation frames, never emits cancelled speech.
        for _ in range(50):
            try:
                pcm, token, submitted = self.render.get_nowait()
            except queue.Empty:
                break
            submitted.set()
            if not token.is_set():
                reference = pcm
                break
        outdata[:] = reference
        if status:
            self.overflow.set()
        try:
            self.capture.put_nowait((bytes(indata), reference))
        except queue.Full:
            self.overflow.set()

    def write(self, pcm: bytes, cancel: threading.Event) -> None:
        last: threading.Event | None = None
        for start in range(0, len(pcm), FRAME_BYTES):
            if cancel.is_set() or self.closed.is_set():
                return
            frame = pcm[start:start + FRAME_BYTES].ljust(FRAME_BYTES, b"\0")
            last = threading.Event()
            while not cancel.is_set() and not self.closed.is_set():
                try:
                    self.render.put((frame, cancel, last), timeout=.05)
                    break
                except queue.Full:
                    continue
        # Playback submission is tracked, not merely queue insertion. DAC latency remains.
        while last is not None and not last.wait(.05):
            if cancel.is_set() or self.closed.is_set():
                return

    def close(self) -> None:
        self.closed.set()
