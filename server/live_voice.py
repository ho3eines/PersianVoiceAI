"""Endpointing, phrase splitting and PCM output helpers for the async PC runtime."""
from __future__ import annotations

import math
import re
import threading
from array import array

from .config import Settings
from .voice import dependency


class Endpoint:
    """Bounded PCM16/16k mono endpoint detector (energy VAD, not echo cancellation)."""
    def __init__(self, threshold: float, silence_frames: int = 25, max_frames: int = 750) -> None:
        self.threshold = threshold
        self.silence_frames = silence_frames
        self.max_frames = max_frames
        self.frames: list[bytes] = []
        self.quiet = 0
        self.voiced = 0
        self.started = False

    def feed(self, pcm: bytes) -> tuple[bool, bytes | None]:
        samples = array("h", pcm)
        rms = math.sqrt(sum(x * x for x in samples) / max(1, len(samples))) / 32768
        loud = rms >= self.threshold
        onset = False
        if loud or self.frames:
            self.frames.append(pcm)
            self.quiet = 0 if loud else self.quiet + 1
            self.voiced += int(loud)
            if self.voiced >= 3 and not self.started:
                self.started = True
                onset = True
            if self.quiet >= self.silence_frames or len(self.frames) >= self.max_frames:
                result = b"".join(self.frames) if self.voiced >= 8 else None
                self.frames, self.quiet, self.voiced, self.started = [], 0, 0, False
                return onset, result
        return onset, None


def sentences(buffer: str, final: bool = False) -> tuple[list[str], str]:
    ready = []
    while True:
        match = re.search(r"[.!؟?\n؛](?:\s|$)", buffer)
        end = match.end() if match else (buffer.rfind(" ", 0, 180) if len(buffer) > 180 else -1)
        if end <= 0:
            break
        ready.append(buffer[:end].strip())
        buffer = buffer[end:]
    if final and buffer.strip():
        ready.append(buffer.strip())
        buffer = ""
    return [text for text in ready if text], buffer


class Speaker:
    def __init__(self, settings: Settings, duplex=None) -> None:
        self.duplex = duplex
        self.model = dependency("piper.voice").PiperVoice.load(settings.tts_model_path)
        self.sd = dependency("sounddevice")

    def speak(self, text: str, cancel: threading.Event) -> None:
        if cancel.is_set():
            return
        # Piper yields PCM chunks; write in 20ms blocks so cancellation is bounded
        # by the next synthesis chunk plus one audio block, not the whole sentence.
        for chunk in self.model.synthesize(text):
            if cancel.is_set():
                return
            if getattr(self, "duplex", None) is not None:
                from math import gcd
                np = dependency("numpy")
                resample = dependency("scipy.signal").resample_poly
                samples = np.frombuffer(chunk.audio_int16_bytes, dtype="<i2").astype(np.float32)
                samples = samples.reshape(-1, chunk.sample_channels).mean(axis=1)
                divisor = gcd(chunk.sample_rate, 16000)
                if chunk.sample_rate != 16000:
                    samples = resample(samples, 16000 // divisor, chunk.sample_rate // divisor)
                pcm = np.clip(np.rint(samples), -32768, 32767).astype("<i2").tobytes()
                self.duplex.write(pcm, cancel)
                continue
            with self.sd.RawOutputStream(samplerate=chunk.sample_rate, channels=chunk.sample_channels,
                                         dtype="int16") as output:
                pcm = chunk.audio_int16_bytes
                size = int(chunk.sample_rate * .02) * chunk.sample_channels * 2
                for start in range(0, len(pcm), size):
                    if cancel.is_set():
                        output.abort()
                        return
                    output.write(pcm[start:start + size])


def run(settings: Settings) -> None:
    """Default runtime is the asynchronous incremental pipeline."""
    import asyncio
    from .streaming_voice import run_live
    asyncio.run(run_live(settings))
