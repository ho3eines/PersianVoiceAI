"""Metrics registry: bounded summaries, thread safety, declared metric names."""

from __future__ import annotations

import threading
from pathlib import Path

from server.metrics import METRIC_NAMES, Metrics, Summary

ROOT = Path(__file__).resolve().parents[1]


def test_counter_and_gauge() -> None:
    metrics = Metrics()
    metrics.increment("barge_in_count")
    metrics.increment("barge_in_count", 2)
    metrics.set("asr_queue_depth", 3)
    assert metrics.counter("barge_in_count") == 3
    assert metrics.gauge("asr_queue_depth") == 3
    snapshot = metrics.snapshot()
    assert snapshot["counters"]["barge_in_count"] == 3
    assert snapshot["gauges"]["asr_queue_depth"] == 3


def test_summary_is_bounded_and_reports_statistics() -> None:
    summary = Summary()
    for value in (10, 20, 30):
        summary.observe(value)
    assert summary.count == 3
    assert summary.last == 30
    assert summary.minimum == 10 and summary.maximum == 30
    assert summary.mean == 20.0
    assert summary.as_dict() == {"count": 3, "last": 30, "min": 10, "max": 30, "mean": 20.0}


def test_observations_do_not_retain_samples() -> None:
    metrics = Metrics()
    for index in range(10_000):
        metrics.observe("asr_ms", index)
    summary = metrics.summary("asr_ms")
    assert summary.count == 10_000  # bounded memory: one Summary per metric
    assert "asr_ms" in metrics.snapshot()["observations"]


def test_disabled_metrics_record_nothing() -> None:
    metrics = Metrics(enabled=False)
    metrics.increment("utterances")
    metrics.observe("asr_ms", 5)
    metrics.set("queue_depth", 2)
    assert metrics.snapshot() == {"counters": {}, "gauges": {}, "observations": {}}


def test_flat_view_and_missing_names() -> None:
    metrics = Metrics()
    metrics.observe("asr_ms", 100)
    metrics.increment("utterances")
    flat = metrics.flat()
    assert flat["asr_ms"] == 100
    assert flat["utterances"] == 1
    missing = metrics.missing(("asr_ms", "utterances", "llm_ttft_ms"))
    assert missing == ["llm_ttft_ms"]


def test_documented_metric_names_cover_the_pipeline() -> None:
    # Required by the audit: every documented metric must be declared once.
    required = {
        "vad_start_ms", "vad_end_ms", "asr_ms", "asr_rtf", "llm_ttft_ms",
        "tts_first_chunk_ms", "first_audible_ms", "playback_duration_ms",
        "barge_in_latency_ms", "audio_callback_jitter_ms", "cpu_usage_percent",
        "ram_usage_mb", "queue_depth", "asr_queue_depth", "asr_final_latency_ms",
        "asr_partial_latency_ms", "asr_dropped_partial_count", "asr_final_job_count",
        "llm_tokens_per_second",
    }
    assert required <= set(METRIC_NAMES)


def test_every_declared_metric_is_actually_emitted_somewhere() -> None:
    """A declared name nothing records is a lie in the docs and the benchmark.

    ``METRIC_NAMES`` is what the docs promise the pipeline measures.  ``vad_onset_events``
    lived in that tuple while no module ever recorded it: a reader (or the
    benchmark's ``missing()`` call) was told a number existed that could never
    appear.  Mirroring the declaration list against the source keeps that from
    coming back.
    """
    source = "".join(
        path.read_text(encoding="utf-8-sig")
        for path in (ROOT / "server").glob("*.py")
        if path.name != "metrics.py"
    )
    unemitted = [name for name in METRIC_NAMES if f'"{name}"' not in source]
    assert not unemitted, f"declared but never recorded: {unemitted}"


def test_every_recorded_metric_is_declared() -> None:
    """The other direction: the registry must not under-report what it measures.

    ``/api/protocol`` and the benchmark derive the metric set from
    ``METRIC_NAMES``; a name recorded by the pipeline but absent from that tuple
    is measured and then invisible to every consumer of the registry.  Twenty
    such names existed (``ws_capture_out_of_order``, ``asr_model_load_ms``,
    ``api_rate_limited`` …) while the module docstring claimed completeness.
    """
    import re

    pattern = re.compile(
        r'\.(?:increment|set|observe|counter|gauge|summary)\(\s*"([a-z0-9_]+)"'
    )
    declared = set(METRIC_NAMES)
    undeclared: dict[str, str] = {}
    for path in (ROOT / "server").glob("*.py"):
        if path.name == "metrics.py":
            continue
        for match in pattern.finditer(path.read_text(encoding="utf-8-sig")):
            if match.group(1) not in declared:
                undeclared.setdefault(match.group(1), path.name)
    assert not undeclared, f"recorded but never declared: {sorted(undeclared.items())}"


def test_concurrent_updates_are_thread_safe() -> None:
    metrics = Metrics()

    def worker() -> None:
        for _ in range(500):
            metrics.increment("audio_callback_count")
            metrics.observe("audio_callback_jitter_ms", 1.5)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert metrics.counter("audio_callback_count") == 2000
    assert metrics.summary("audio_callback_jitter_ms").count == 2000


def test_reset_clears_everything() -> None:
    metrics = Metrics()
    metrics.increment("utterances")
    metrics.observe("asr_ms", 1)
    metrics.reset()
    assert metrics.snapshot() == {"counters": {}, "gauges": {}, "observations": {}}
