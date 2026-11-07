#!/usr/bin/env python3
"""End-to-end voice-pipeline benchmark for PersianVoiceAI (offline).

Why this exists
---------------
The audit requires measured numbers for ASR latency/RTF, LLM TTFT, TTS first
chunk, first audible audio, barge-in latency, CPU and RAM — reported separately
for idle, ASR-only, LLM-only, TTS-only, ASR+LLM, ASR+LLM+TTS and full duplex.
It also forbids inventing them.  This tool therefore labels *every* row:

``MEASURED``   produced on this machine, right now, with the real component.
``SIMULATED``  the real component was not available (no weights / no server);
               the row uses a documented synthetic latency and proves only that
               the plumbing runs.  These numbers must never be quoted as
               performance results.
``NOT MEASURED`` the scenario needs hardware or weights that are absent.

Scenarios
---------
``idle``      the live pipeline runs with silence: coordinator pacing jitter,
              CPU and RAM.
``asr``       local Whisper on a synthetic 3 s buffer: ``asr_ms``, ``asr_rtf``.
``llm``       llama-server streaming: TTFT and tokens/second.
``tts``       local Piper: first chunk, synthesis time, audio duration.
``combined``  one full conversational turn through the real ``Pipeline``
              (VAD → final ASR → streamed reply → sentence TTS → commit).
``bargein``   assistant speaking, user interrupts: teardown latency.
``all``       every scenario above (default).

Usage
-----
    python tools/benchmark_voice.py                    # everything available
    python tools/benchmark_voice.py --scenarios idle,combined --quick
    python tools/benchmark_voice.py --json-out benchmarks/results/voice.json

Nothing is uploaded; only ``127.0.0.1`` llama-server is contacted, exactly as
the live service does.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import sys
import threading
import time
from array import array
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from server.config import Settings  # noqa: E402
from server.metrics import Metrics  # noqa: E402
from server.resources import create_sampler, sample_process_usage  # noqa: E402
from server.streaming_voice import Pipeline  # noqa: E402

MEASURED = "MEASURED"
SIMULATED = "SIMULATED"
NOT_MEASURED = "NOT MEASURED"

BLOCK_MS = 20
FRAME_SAMPLES = 320  # 20 ms @ 16 kHz
LOUD = 2500


# --------------------------------------------------------------------- records
@dataclass
class Row:
    scenario: str
    metric: str
    value: float | None
    unit: str
    status: str
    note: str = ""


@dataclass
class Report:
    rows: list[Row] = field(default_factory=list)
    environment: dict[str, Any] = field(default_factory=dict)

    def add(
        self,
        scenario: str,
        metric: str,
        value: float | None,
        unit: str,
        status: str,
        note: str = "",
    ) -> None:
        self.rows.append(Row(scenario, metric, value, unit, status, note))

    def table(self) -> str:
        width = max([len(row.scenario) for row in self.rows] + [8])
        metric_width = max([len(row.metric) for row in self.rows] + [6])
        lines = [
            f"{'scenario':<{width}}  {'metric':<{metric_width}}  "
            f"{'value':>10}  {'unit':<8}  status"
        ]
        lines.append("-" * (width + metric_width + 40))
        for row in self.rows:
            value = "—" if row.value is None else f"{row.value:.2f}"
            lines.append(
                f"{row.scenario:<{width}}  {row.metric:<{metric_width}}  "
                f"{value:>10}  {row.unit:<8}  {row.status}"
                + (f"  ({row.note})" if row.note else "")
            )
        return "\n".join(lines)

    def as_dict(self) -> dict[str, Any]:
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "environment": self.environment,
            "rows": [
                {
                    "scenario": row.scenario,
                    "metric": row.metric,
                    "value": row.value,
                    "unit": row.unit,
                    "status": row.status,
                    "note": row.note,
                }
                for row in self.rows
            ],
        }


def frame(level: int = LOUD, samples: int = FRAME_SAMPLES) -> bytes:
    return array("h", [level] * samples).tobytes()


def silence(samples: int = FRAME_SAMPLES) -> bytes:
    return b"\x00\x00" * samples


def environment() -> dict[str, Any]:
    import os
    import platform

    usage = sample_process_usage()
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "processor": platform.processor() or "unknown",
        "cpu_count": os.cpu_count(),
        "ram_total_mb": _total_ram_mb(),
        "process_ram_mb": usage.ram_mb,
        "note": "single-run numbers; repeat and compare medians before quoting",
    }


def _total_ram_mb() -> float | None:
    try:
        import os

        if hasattr(os, "sysconf"):
            return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**20, 1)
    except (ValueError, OSError, AttributeError):  # pragma: no cover - platform specific
        return None
    return None


# ------------------------------------------------------------- fake components
class SimulatedSpeaker:
    """Speaker stand-in: ``synthesis_s`` per sentence, then plays ``audio_s``.

    It honours the same contract as :class:`server.live_voice.Speaker`
    (``cancel`` event, first-chunk/first-frame/complete callbacks), so the
    pipeline timing logic is exercised, but the audio is not real.
    """

    def __init__(self, synthesis_s: float = 0.05, audio_s: float = 0.4) -> None:
        self.synthesis_s = synthesis_s
        self.audio_s = audio_s
        self.spoken: list[str] = []

    def speak(self, text, token, *, on_first_chunk=None, on_first_frame=None, on_complete=None):
        started = time.perf_counter()
        if token.is_set():
            if on_complete is not None:
                on_complete(time.perf_counter(), 0.0)
            return
        time.sleep(self.synthesis_s)
        if on_first_chunk is not None:
            on_first_chunk(time.perf_counter())
        # Play in 20 ms blocks so cancellation is observable like real audio.
        deadline = time.perf_counter() + self.audio_s
        first = True
        while time.perf_counter() < deadline:
            if token.is_set():
                break
            if first:
                first = False
                if on_first_frame is not None:
                    on_first_frame(time.perf_counter())
            time.sleep(0.02)
        played = (time.perf_counter() - started) * 1000
        if not token.is_set():
            self.spoken.append(text)
        if on_complete is not None:
            on_complete(time.perf_counter(), played)


class SimulatedAsr:
    """ASR stand-in with a fixed decode delay (labelled SIMULATED)."""

    def __init__(self, delay_s: float = 0.2, text: str = "سلام، این یک آزمایش است.") -> None:
        self.delay_s = delay_s
        self.text = text

    def transcribe_pcm(self, pcm: bytes) -> str:
        time.sleep(self.delay_s)
        return self.text


class SimulatedStream:
    """httpx MockTransport shim: streams ``sentence`` word by word."""

    def __init__(self, tokens_per_second: float = 20.0, ttft_s: float = 0.3) -> None:
        self.tokens_per_second = tokens_per_second
        self.ttft_s = ttft_s

    async def aiter(self, payload: dict[str, Any]):
        await asyncio.sleep(self.ttft_s)
        words = "این یک پاسخ شبیه‌سازی‌شده برای سنجش خط لوله است.".split()
        for word in words:
            await asyncio.sleep(1.0 / self.tokens_per_second)
            yield word + " "


def simulated_client(stream: SimulatedStream) -> httpx.AsyncClient:
    """Fake SSE client built on httpx.MockTransport (no sockets)."""

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content or b"{}")
        words = "این یک پاسخ شبیه‌سازی‌شده برای سنجش خط لوله است.".split()
        body = "".join(
            'data: {"choices":[{"delta":{"content":"%s "}}]}\n\n' % word for word in words
        )
        body += "data: [DONE]\n\n"
        return httpx.Response(200, text=body)

    return httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(handler)
    )


# ------------------------------------------------------------------ scenarios
def scenario_idle_measured(report: Report, settings: Settings, seconds: float) -> None:
    """Silence through the live pipeline: pacing jitter, CPU, RAM."""
    metrics = Metrics()
    jitter: list[float] = []
    sampler = create_sampler()  # one sampler: CPU is a delta between samples

    async def run() -> None:
        pipeline = Pipeline(
            settings,
            SimulatedAsr(0.0),
            SimulatedSpeaker(),
            simulated_client(SimulatedStream()),
            lambda event: None,
            metrics=metrics,
        )
        pipeline.start()
        try:
            usage_before = sample_process_usage(sampler)
            started = time.perf_counter()
            previous = started
            while time.perf_counter() - started < seconds:
                await asyncio.sleep(BLOCK_MS / 1000)
                pipeline.feed(silence())
                now = time.perf_counter()
                jitter.append(abs((now - previous) * 1000 - BLOCK_MS))
                previous = now
            usage_after = sample_process_usage(sampler)
        finally:
            await pipeline.close()
            await pipeline.client.aclose()
        report.add(
            "idle", "audio_loop_jitter_p95_ms", _percentile(jitter, 95), "ms", MEASURED,
            "coordinator pacing vs the 20 ms block; audio-callback jitter needs hardware",
        )
        report.add(
            "idle", "audio_loop_jitter_max_ms", max(jitter) if jitter else None, "ms", MEASURED
        )
        cpu_status = MEASURED if usage_after.cpu_percent is not None else NOT_MEASURED
        ram_status = MEASURED if usage_after.ram_mb is not None else NOT_MEASURED
        report.add(
            "idle", "cpu_usage_percent", usage_after.cpu_percent, "%", cpu_status,
            f"process sample over {seconds:g}s ({usage_after.source})",
        )
        report.add("idle", "ram_usage_mb", usage_after.ram_mb, "MB", ram_status,
                   f"source {usage_after.source}")
        report.add("idle", "vad_false_positives", metrics.counter("utterances"), "count",
                   MEASURED, "must be 0 for pure silence")

    asyncio.run(run())


def scenario_asr(report: Report, settings: Settings, seconds: float) -> None:
    """Real local Whisper on a synthetic buffer (compute cost, not accuracy)."""
    from server.voice import LocalAudio, VoiceError

    audio = LocalAudio(settings)
    try:
        audio.validate()
    except VoiceError as exc:
        report.add("asr", "asr_ms", None, "ms", NOT_MEASURED, str(exc).split("؛")[0])
        return
    try:
        audio.warmup()
    except VoiceError as exc:
        report.add("asr", "asr_ms", None, "ms", NOT_MEASURED, f"warmup failed: {exc}")
        return
    pcm = frame(600)
    durations = [1.0, 3.0, 6.0]
    for duration in durations:
        samples = int(16000 * duration)
        payload = pcm * (samples // FRAME_SAMPLES)
        started = time.perf_counter()
        try:
            audio.transcribe_pcm(payload)
        except VoiceError as exc:
            report.add("asr", f"asr_ms@{duration:g}s", None, "ms", NOT_MEASURED, str(exc))
            return
        elapsed = (time.perf_counter() - started) * 1000
        report.add(
            "asr", f"asr_ms@{duration:g}s", elapsed, "ms", MEASURED,
            "synthetic tone; timing only, not transcription quality",
        )
        report.add(
            "asr", f"asr_rtf@{duration:g}s", elapsed / (duration * 1000), "x", MEASURED,
            "compute time / audio duration; <1 is faster than real time",
        )


def scenario_llm(report: Report, settings: Settings, seconds: float) -> None:
    """Real llama-server: TTFT and throughput on a short Persian prompt."""
    base = settings.llama_server_url
    payload = {
        "messages": [
            {"role": "system", "content": settings.system_prompt},
            {"role": "user", "content": "در دو جمله بگو چرا آسمان آبی است."},
        ],
        "stream": True,
        "temperature": 0.2,
        "max_tokens": 64,
    }
    started = time.perf_counter()
    first_token_at: float | None = None
    pieces = 0
    try:
        with httpx.Client(base_url=base, timeout=settings.request_timeout_s, trust_env=False) as client:
            with client.stream("POST", "/v1/chat/completions", json=payload) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        break
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    content = ""
                    for choice in data.get("choices", []):
                        content += choice.get("delta", {}).get("content") or ""
                    if content and first_token_at is None:
                        first_token_at = time.perf_counter()
                    if content:
                        pieces += 1
    except (httpx.HTTPError, OSError) as exc:
        report.add("llm", "llm_ttft_ms", None, "ms", NOT_MEASURED,
                   f"llama-server not reachable at {base}: {type(exc).__name__}")
        report.add("llm", "llm_tokens_per_second", None, "tok/s", NOT_MEASURED,
                   "start ./tools/start_server.ps1 and rerun")
        return
    total = time.perf_counter() - started
    if first_token_at is None:
        report.add("llm", "llm_ttft_ms", None, "ms", NOT_MEASURED, "no content token received")
        return
    generation = max(1e-6, time.perf_counter() - first_token_at)
    report.add("llm", "llm_ttft_ms", (first_token_at - started) * 1000, "ms", MEASURED)
    report.add("llm", "llm_tokens_per_second", pieces / generation, "tok/s", MEASURED,
               "inter-token rate after the first token")
    report.add("llm", "llm_stream_total_ms", total * 1000, "ms", MEASURED)


def scenario_tts(report: Report, settings: Settings, seconds: float) -> None:
    """Real Piper: first chunk, first frame, played duration (discarded sink)."""
    from server.live_voice import Speaker
    from server.voice import VoiceError

    class Sink:
        """Duplex sink that discards audio but honours the cancel contract."""

        def write(self, pcm: bytes, token: threading.Event) -> None:
            if token.is_set():
                return

    chunks: list[float] = []
    frames: list[float] = []
    completed: list[float] = []
    try:
        speaker = Speaker(settings, duplex=Sink())
    except VoiceError as exc:
        report.add("tts", "tts_first_chunk_ms", None, "ms", NOT_MEASURED, str(exc).split("؛")[0])
        return
    started = time.perf_counter()
    speaker.speak(
        "سلام، این یک جملهٔ آزمایشی فارسی است.",
        threading.Event(),
        on_first_chunk=chunks.append,
        on_first_frame=frames.append,
        on_complete=lambda at, played: completed.append(played),
    )
    if chunks:
        report.add("tts", "tts_first_chunk_ms", (chunks[0] - started) * 1000, "ms", MEASURED)
    else:
        report.add("tts", "tts_first_chunk_ms", None, "ms", NOT_MEASURED, "no audio chunk")
    if frames:
        report.add("tts", "tts_first_frame_ms", (frames[0] - started) * 1000, "ms", MEASURED,
                   "device hand-off proxy; DAC latency is not measurable here")
    if completed:
        report.add("tts", "tts_played_ms", completed[-1], "ms", MEASURED)
        report.add("tts", "tts_total_ms", (time.perf_counter() - started) * 1000, "ms", MEASURED)


async def _one_turn(
    settings: Settings,
    *,
    asr: Any,
    speaker: Any,
    client: Any,
    metrics: Metrics,
) -> tuple[list[dict[str, Any]], float]:
    events: list[dict[str, Any]] = []
    pipeline = Pipeline(settings, asr, speaker, client, events.append, metrics=metrics)
    pipeline.start()
    started = time.perf_counter()
    try:
        for _ in range(24):  # 480 ms of speech
            pipeline.feed(frame())
        for _ in range(32):  # 640 ms of silence → endpoint
            pipeline.feed(silence())
        deadline = time.perf_counter() + 60
        while time.perf_counter() < deadline:
            if any(event["type"] == "response_end" for event in events):
                break
            await asyncio.sleep(0.01)
    finally:
        elapsed = (time.perf_counter() - started) * 1000
        await pipeline.close()
        await pipeline.client.aclose()
    return events, elapsed


def scenario_combined(report: Report, settings: Settings, seconds: float) -> None:
    """One full turn through the real Pipeline with real or simulated parts."""
    asr_status, asr_note, asr = _maybe_real_asr(settings, report)
    tts_status, tts_note, speaker = _maybe_real_tts(settings, report)
    llm_status, llm_note, client_factory = _maybe_real_llm(settings, report)

    async def run() -> None:
        metrics = Metrics()
        client = client_factory()
        events, total_ms = await _one_turn(
            settings, asr=asr, speaker=speaker, client=client, metrics=metrics
        )
        snapshot = metrics.flat()
        for name in ("asr_ms", "asr_final_latency_ms", "llm_ttft_ms", "first_audible_ms",
                     "playback_duration_ms", "vad_start_ms", "vad_end_ms"):
            value = snapshot.get(f"{name}.last", snapshot.get(name))
            if name.startswith("asr"):
                status, note = asr_status, asr_note
            elif name.startswith("llm"):
                status, note = llm_status, llm_note
            elif name.startswith(("first_audible", "playback", "tts")):
                status, note = tts_status, tts_note
            else:  # VAD/endpointing always uses the real detector
                status, note = MEASURED, ""
            report.add(
                "combined",
                name,
                value,
                "ms",
                status,
                note,
            )
        report.add("combined", "turn_total_ms", total_ms, "ms", _worst(asr_status, tts_status, llm_status))
        report.add(
            "combined", "turn_completed", 1.0 if any(
                event["type"] == "response_end" and event.get("status") == "complete"
                for event in events
            ) else 0.0, "flag", MEASURED,
        )
        report.add("combined", "speech_queue_depth", metrics.gauge("speech_queue_depth"), "",
                   MEASURED, "must stay far below the configured bound")

    asyncio.run(run())


def scenario_bargein(report: Report, settings: Settings, seconds: float) -> None:
    """Assistant speaking → user interrupts → teardown latency."""
    asr_status, asr_note, asr = _maybe_real_asr(settings, report)
    tts_status, tts_note, speaker = _maybe_real_tts(settings, report)
    llm_status, llm_note, client_factory = _maybe_real_llm(settings, report)
    status = _worst(asr_status, tts_status, llm_status)
    note = asr_note or tts_note or llm_note

    async def run() -> None:
        metrics = Metrics()
        events: list[dict[str, Any]] = []
        pipeline = Pipeline(settings, asr, speaker, client_factory(), events.append, metrics=metrics)
        pipeline.start()
        try:
            for _ in range(24):
                pipeline.feed(frame())
            for _ in range(32):
                pipeline.feed(silence())
            deadline = time.perf_counter() + 30
            while time.perf_counter() < deadline and pipeline.state.value != "speaking":
                await asyncio.sleep(0.005)
            # Let playback start before interrupting, otherwise the measurement
            # would describe cancellation of a turn that never spoke.
            await asyncio.sleep(0.05)
            for _ in range(24):  # new speech while the assistant speaks
                pipeline.feed(frame())
            deadline = time.perf_counter() + 5
            while time.perf_counter() < deadline:
                if any(event["type"] == "interrupted" for event in events):
                    break
                await asyncio.sleep(0.005)
        finally:
            await pipeline.close()
            await pipeline.client.aclose()
        latency = metrics.summary("barge_in_latency_ms").last
        interrupted = any(event["type"] == "interrupted" for event in events)
        if latency == 0 and not interrupted:
            report.add("bargein", "barge_in_latency_ms", None, "ms", NOT_MEASURED,
                       "assistant was not speaking when the interruption arrived")
            return
        report.add("bargein", "barge_in_latency_ms", latency, "ms", status,
                   "logical teardown; device-level stop is hardware-validated" if not note else note)
        stale = [event for event in events if event["type"] == "response_end" and event.get("status") == "complete"]
        report.add("bargein", "stale_completions_after_interrupt", float(len(stale)), "count",
                   MEASURED, "must be 0")

    asyncio.run(run())


# ------------------------------------------------------------------- helpers
def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(percentile / 100 * len(ordered)) - 1))
    return ordered[index]


def _delta(before: float | None, after: float | None) -> float | None:
    if before is None or after is None:
        return None
    return max(0.0, after - before)


def _worst(*statuses: str) -> str:
    order = {MEASURED: 0, SIMULATED: 1, NOT_MEASURED: 2}
    return max(statuses, key=lambda status: order.get(status, 2))


def _maybe_real_asr(settings: Settings, report: Report) -> tuple[str, str, Any]:
    from server.voice import LocalAudio, VoiceError

    audio = LocalAudio(settings)
    try:
        audio.validate()
        audio.warmup()
    except VoiceError as exc:
        return SIMULATED, f"simulated ASR ({str(exc).split('؛')[0]})", SimulatedAsr()
    return MEASURED, "", audio


def _maybe_real_tts(settings: Settings, report: Report) -> tuple[str, str, Any]:
    from server.live_voice import Speaker
    from server.voice import VoiceError

    class Sink:
        def write(self, pcm: bytes, token: threading.Event) -> None:
            return None

    try:
        return MEASURED, "", Speaker(settings, duplex=Sink())
    except VoiceError as exc:
        return SIMULATED, f"simulated TTS ({str(exc).split('؛')[0]})", SimulatedSpeaker()


def _maybe_real_llm(settings: Settings, report: Report) -> tuple[str, str, Any]:
    try:
        with httpx.Client(
            base_url=settings.llama_server_url, timeout=5.0, trust_env=False
        ) as client:
            health = client.get("/health")
            health.raise_for_status()
    except (httpx.HTTPError, OSError):
        return (
            SIMULATED,
            f"simulated LLM (llama-server unreachable at {settings.llama_server_url})",
            lambda: simulated_client(SimulatedStream()),
        )
    return MEASURED, "", lambda: httpx.AsyncClient(
        base_url=settings.llama_server_url,
        timeout=settings.request_timeout_s,
        trust_env=False,
        follow_redirects=False,
    )


SCENARIOS = {
    "idle": scenario_idle_measured,
    "asr": scenario_asr,
    "llm": scenario_llm,
    "tts": scenario_tts,
    "combined": scenario_combined,
    "bargein": scenario_bargein,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PersianVoiceAI voice pipeline benchmark")
    parser.add_argument(
        "--scenarios",
        default="all",
        help="comma separated: " + ", ".join(SCENARIOS) + ", or all",
    )
    parser.add_argument("--seconds", type=float, default=3.0, help="idle scenario duration")
    parser.add_argument("--json-out", help="write the report as JSON")
    parser.add_argument("--quick", action="store_true", help="idle 1s, skip slow probes")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    report = Report(environment=environment())
    requested = (
        list(SCENARIOS) if args.scenarios.strip().lower() == "all"
        else [name.strip() for name in args.scenarios.split(",") if name.strip()]
    )
    unknown = [name for name in requested if name not in SCENARIOS]
    if unknown:
        parser.error(f"unknown scenarios: {', '.join(unknown)}")

    seconds = 1.0 if args.quick else args.seconds
    for name in requested:
        print(f"… running scenario {name}", file=sys.stderr, flush=True)
        try:
            SCENARIOS[name](report, settings, seconds)
        except Exception as exc:  # a broken optional component must not abort the run
            report.add(name, "scenario", None, "", NOT_MEASURED, f"{type(exc).__name__}: {exc}")

    print(report.table())
    print(
        "\nMEASURED = produced on this machine now; SIMULATED = real component missing, "
        "plumbing only; NOT MEASURED = unavailable."
    )
    if args.json_out:
        path = Path(args.json_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"report written to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
