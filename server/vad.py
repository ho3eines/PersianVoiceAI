"""Local voice-activity detection for the PC microphone path.

The project deliberately ships a *measured* energy detector instead of a neural
VAD dependency: the target machine has two physical cores and every extra
model competes with Whisper, the LLM and Piper.  The energy detector here is
not the naive version that shipped before; it adds the four properties the
old endpoint lacked:

* **hysteresis** — onset uses a higher threshold than speech continuation, so
  a naturally quieter syllable does not end the utterance;
* **pre-roll** — a small ring buffer of audio *before* the onset is kept, so
  initial consonants ("س", "ش", "خ") are not clipped;
* **adaptive noise floor** — a slow exponential average of the quiet level
  raises the effective onset threshold in a noisy room (fan / air conditioner)
  instead of firing on every noise burst;
* **an explicit minimum speech duration and a bounded segment length** — short
  coughs are rejected, long monologues are split at a safe boundary without
  losing audio (the next segment starts with the pre-roll ring, which still
  contains the frames around the split).

The detector is a pure state machine over 20 ms PCM16 blocks: no threads, no
allocation-heavy work per block beyond the RMS loop, and every threshold is
configurable (see ``docs/CONFIGURATION.md``).

A transport-independent adapter for a future neural VAD is intentionally *not*
added; ``PVAI_VAD_BACKEND`` exists in the documentation as a design note only.
"""

from __future__ import annotations

import math
import sys
from array import array
from collections import deque
from dataclasses import dataclass

FRAME_SECONDS = 0.02
"""Audio block duration assumed by the detector (20 ms, 320 samples @ 16 kHz)."""


def block_rms(pcm: bytes) -> float:
    """Root-mean-square level of a PCM16 little-endian mono block in ``0..1``."""
    usable = len(pcm) - (len(pcm) % 2)
    if usable <= 0:
        return 0.0
    samples = array("h")
    samples.frombytes(pcm[:usable])
    if sys.byteorder == "big":  # PCM payloads are always little-endian
        samples.byteswap()
    total = 0
    for value in samples:
        total += value * value
    return math.sqrt(total / len(samples)) / 32768.0


@dataclass(frozen=True, slots=True)
class VadBoundary:
    """Diagnostic record for one detected speech boundary."""

    kind: str  # "onset" | "endpoint" | "max_segment"
    at: float
    rms: float
    noise_floor: float
    frames: int


class EnergyVAD:
    """Bounded PCM16/16 kHz mono endpoint detector with hysteresis and pre-roll.

    ``feed`` returns ``(onset, segment_pcm)``:

    * ``onset`` is ``True`` exactly once per utterance, on the block where the
      onset requirement is met;
    * ``segment_pcm`` is the complete utterance audio when the utterance ends
      (endpoint silence or maximum segment length), otherwise ``None``.

    A ``segment_pcm`` of ``None`` with a non-``None`` second element never
    happens: an utterance shorter than ``min_voiced_frames`` produces ``None``.
    """

    def __init__(
        self,
        threshold: float = 0.015,
        *,
        silence_frames: int = 30,
        max_frames: int = 750,
        onset_frames: int = 3,
        offset_threshold: float | None = None,
        min_voiced_frames: int = 8,
        pre_roll_frames: int = 0,
        noise_adapt: bool = False,
        noise_multiplier: float = 2.5,
        noise_alpha: float = 0.05,
    ) -> None:
        if threshold <= 0:
            raise ValueError("VAD threshold must be positive")
        if offset_threshold is not None and not 0 < offset_threshold <= threshold:
            raise ValueError("VAD offset threshold must be in (0, threshold]")
        self.threshold = float(threshold)
        self.offset_threshold = float(offset_threshold if offset_threshold is not None else threshold)
        self.silence_frames = max(1, int(silence_frames))
        self.max_frames = max(2, int(max_frames))
        self.onset_frames = max(1, int(onset_frames))
        self.min_voiced_frames = max(1, int(min_voiced_frames))
        self.pre_roll_frames = max(0, int(pre_roll_frames))
        self.noise_adapt = bool(noise_adapt)
        self.noise_multiplier = float(noise_multiplier)
        self.noise_alpha = float(noise_alpha)

        self._ring: deque[bytes] = deque(maxlen=max(self.onset_frames + self.pre_roll_frames, 1))
        self.frames: list[bytes] = []
        self.quiet = 0
        self.voiced = 0
        self.started = False
        self.noise_floor = 0.0
        self.last_rms = 0.0
        self.last_boundary: VadBoundary | None = None
        self.last_trailing_quiet = 0
        self.last_voiced_frames = 0
        self._onset_at: float | None = None

    # ------------------------------------------------------------------ config
    @property
    def onset_level(self) -> float:
        """Effective onset threshold (base threshold raised by measured noise)."""
        if not self.noise_adapt:
            return self.threshold
        return max(self.threshold, self.noise_floor * self.noise_multiplier)

    @property
    def continuation_level(self) -> float:
        """Threshold used while the utterance is open (hysteresis, lower).

        It scales with the adaptive onset level so a noisy room does not keep
        an utterance open on residual fan noise alone.
        """
        ratio = self.offset_threshold / self.threshold
        return max(1e-6, self.onset_level * ratio)

    def reset(self) -> None:
        self._ring.clear()
        self.frames = []
        self.quiet = 0
        self.voiced = 0
        self.started = False
        self._onset_at = None

    # ------------------------------------------------------------------- feed
    def feed(self, pcm: bytes, *, at: float | None = None) -> tuple[bool, bytes | None]:
        """Consume one audio block and return ``(onset, complete_segment)``."""
        level = block_rms(pcm)
        self.last_rms = level
        if not self.started:
            return self._feed_idle(pcm, level, at)
        return self._feed_active(pcm, level, at)

    def _feed_idle(self, pcm: bytes, level: float, at: float | None) -> tuple[bool, bytes | None]:
        self._ring.append(pcm)
        if level >= self.onset_level:
            self.voiced += 1
            if self.voiced >= self.onset_frames:
                self.started = True
                self.frames = list(self._ring)
                self._ring.clear()
                self.quiet = 0
                self._onset_at = at
                self.last_boundary = VadBoundary(
                    "onset", at or 0.0, level, self.noise_floor, len(self.frames)
                )
                return True, None
            return False, None
        # Not voiced: count the block towards the noise estimate and restart the run.
        self.voiced = 0
        if self.noise_adapt:
            alpha = self.noise_alpha
            self.noise_floor = (1 - alpha) * self.noise_floor + alpha * level
        return False, None

    def _feed_active(self, pcm: bytes, level: float, at: float | None) -> tuple[bool, bytes | None]:
        self.frames.append(pcm)
        if level >= self.continuation_level:
            self.quiet = 0
            self.voiced += 1
        else:
            self.quiet += 1
            if self.noise_adapt:
                alpha = self.noise_alpha
                self.noise_floor = (1 - alpha) * self.noise_floor + alpha * level
        if self.quiet >= self.silence_frames:
            self.last_boundary = VadBoundary(
                "endpoint", at or 0.0, level, self.noise_floor, len(self.frames)
            )
            return False, self._finish()
        if len(self.frames) >= self.max_frames:
            self.last_boundary = VadBoundary(
                "max_segment", at or 0.0, level, self.noise_floor, len(self.frames)
            )
            return False, self._finish()
        return False, None

    def _finish(self) -> bytes | None:
        frames = self.frames
        accepted = self.voiced >= self.min_voiced_frames
        # Reported so the caller can trim the endpoint silence before ASR.
        self.last_trailing_quiet = self.quiet
        self.last_voiced_frames = self.voiced
        # The pre-roll ring must not replay audio that was already emitted.
        self.frames = []
        self.quiet = 0
        self.voiced = 0
        self.started = False
        self._onset_at = None
        self._ring.clear()
        return b"".join(frames) if accepted and frames else None

    # ------------------------------------------------------------- diagnostics
    def onset_delay_ms(self) -> float:
        """Detection delay of the last onset in milliseconds (block quantised)."""
        return self.onset_frames * FRAME_SECONDS * 1000

    def endpoint_delay_ms(self) -> float:
        """Detection delay of the endpoint in milliseconds (block quantised)."""
        return self.silence_frames * FRAME_SECONDS * 1000

    def describe(self) -> dict[str, float | int | bool]:
        return {
            "threshold": round(self.threshold, 5),
            "onset_level": round(self.onset_level, 5),
            "offset_level": round(self.continuation_level, 5),
            "noise_floor": round(self.noise_floor, 5),
            "noise_adapt": self.noise_adapt,
            "onset_frames": self.onset_frames,
            "silence_frames": self.silence_frames,
            "max_frames": self.max_frames,
            "pre_roll_frames": self.pre_roll_frames,
            "min_voiced_frames": self.min_voiced_frames,
        }


def build_vad(settings) -> EnergyVAD:  # noqa: ANN001 - avoids a config import cycle
    """Create the configured detector for the live pipeline."""
    block_ms = settings.audio_block_ms
    return EnergyVAD(
        settings.voice_vad_threshold,
        silence_frames=max(1, round(settings.voice_vad_silence_ms / block_ms)),
        max_frames=max(2, round(settings.voice_vad_max_segment_s * 1000 / block_ms)),
        onset_frames=max(1, round(settings.voice_vad_onset_ms / block_ms)),
        offset_threshold=settings.voice_vad_threshold * settings.voice_vad_offset_ratio,
        min_voiced_frames=max(1, round(settings.voice_vad_min_speech_ms / block_ms)),
        pre_roll_frames=max(0, round(settings.voice_vad_pre_roll_ms / block_ms)),
        noise_adapt=settings.voice_vad_noise_adapt,
        noise_multiplier=settings.voice_vad_noise_multiplier,
    )
