"""Metrics registry: bounded summaries, thread safety, declared metric names."""

from __future__ import annotations

import threading

from server.metrics import METRIC_NAMES, Metrics, Summary


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
