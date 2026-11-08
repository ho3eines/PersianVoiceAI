"""In-process metrics for the voice pipeline — bounded memory, no dependencies.

The previous pipeline logged a handful of ad-hoc events but could not answer
the questions that matter for a two-core laptop: how long ASR took relative to
audio duration, how deep the queues got, how many partials were dropped, how
long barge-in took, or how much CPU/RAM the process used.

This module keeps that cheap and bounded:

* counters (monotonic integers),
* gauges (last value),
* observations summarised as ``count / last / min / max / mean`` — the raw
  samples are **not** retained, so a long-running conversation cannot leak.

Every metric name used by the pipeline is declared in ``METRIC_NAMES`` so tests
and the benchmark report can check that the documented set is actually emitted.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

METRIC_NAMES: tuple[str, ...] = (
    # VAD / endpointing
    "vad_start_ms",
    "vad_end_ms",
    "vad_onset_events",
    "vad_noise_floor",
    # ASR
    "asr_ms",
    "asr_rtf",
    "asr_final_latency_ms",
    "asr_final_dispatch_wait_ms",
    "asr_final_wait_behind_partial_ms",
    "asr_final_decode_ms",
    "asr_partial_latency_ms",
    "asr_partial_decode_ms",
    "asr_partial_wasted_ms",
    "asr_queue_depth",
    "asr_dropped_partial_count",
    "asr_final_job_count",
    "asr_errors",
    # LLM
    "llm_ttft_ms",
    "llm_tokens_per_second",
    "llm_requests",
    "llm_failures",
    # TTS / playback
    "tts_first_chunk_ms",
    "first_audible_ms",
    "playback_duration_ms",
    "tts_failures",
    # interaction
    "barge_in_latency_ms",
    "barge_in_count",
    "utterances",
    "utterances_empty",
    "responses_skipped",
    "responses_recovered",
    "utterances_rejected",
    "vad_rejected_utterances",
    "asr_partial_preempted",
    "asr_partial_cancelled",
    "asr_final_waited_behind_partial",
    # audio transport / system
    "audio_callback_jitter_ms",
    "audio_callback_count",
    "audio_overflow_count",
    "queue_depth",
    "speech_queue_depth",
    "cpu_usage_percent",
    "ram_usage_mb",
    "aec_process_ms",
    # WebSocket voice transport (LAN terminals)
    "ws_connections",
    "ws_frames_in",
    "ws_frames_out",
    "ws_auth_failures",
)


@dataclass(slots=True)
class Summary:
    count: int = 0
    total: float = 0.0
    last: float = 0.0
    minimum: float = 0.0
    maximum: float = 0.0

    def observe(self, value: float) -> None:
        value = float(value)
        if self.count == 0:
            self.minimum = self.maximum = value
        else:
            self.minimum = min(self.minimum, value)
            self.maximum = max(self.maximum, value)
        self.count += 1
        self.total += value
        self.last = value

    @property
    def mean(self) -> float:
        return round(self.total / self.count, 3) if self.count else 0.0

    def as_dict(self) -> dict[str, float | int]:
        return {
            "count": self.count,
            "last": round(self.last, 3),
            "min": round(self.minimum, 3),
            "max": round(self.maximum, 3),
            "mean": self.mean,
        }


@dataclass(slots=True)
class Metrics:
    """Thread-safe metric registry (audio callbacks and asyncio both write)."""

    enabled: bool = True
    counters: dict[str, int] = field(default_factory=dict)
    gauges: dict[str, float] = field(default_factory=dict)
    observations: dict[str, Summary] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def increment(self, name: str, amount: int = 1) -> None:
        if not self.enabled:
            return
        with self._lock:
            self.counters[name] = self.counters.get(name, 0) + amount

    def set(self, name: str, value: float) -> None:  # noqa: A003 - metric API
        if not self.enabled:
            return
        with self._lock:
            self.gauges[name] = float(value)

    def observe(self, name: str, value: float) -> None:
        if not self.enabled:
            return
        with self._lock:
            summary = self.observations.get(name)
            if summary is None:
                summary = self.observations[name] = Summary()
            summary.observe(value)

    def counter(self, name: str) -> int:
        with self._lock:
            return self.counters.get(name, 0)

    def gauge(self, name: str) -> float:
        with self._lock:
            return self.gauges.get(name, 0.0)

    def summary(self, name: str) -> Summary:
        with self._lock:
            return self.observations.get(name, Summary())

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "counters": dict(self.counters),
                "gauges": {name: round(value, 3) for name, value in self.gauges.items()},
                "observations": {
                    name: summary.as_dict() for name, summary in self.observations.items()
                },
            }

    def flat(self) -> dict[str, float]:
        """Flat ``name.mean`` view, handy for a benchmark table."""
        snapshot = self.snapshot()
        flat: dict[str, float | int] = {}
        flat.update(snapshot["counters"])
        flat.update(snapshot["gauges"])
        for name, values in snapshot["observations"].items():
            flat[name] = values["mean"]
            flat[f"{name}.last"] = values["last"]
            flat[f"{name}.max"] = values["max"]
        return flat

    def reset(self) -> None:
        with self._lock:
            self.counters.clear()
            self.gauges.clear()
            self.observations.clear()

    def missing(self, names: Iterable[str] = METRIC_NAMES) -> list[str]:
        """Names that were never recorded — used by tests and the benchmark."""
        snapshot = self.snapshot()
        recorded = (
            set(snapshot["counters"]) | set(snapshot["gauges"]) | set(snapshot["observations"])
        )
        return sorted(set(names) - recorded)
