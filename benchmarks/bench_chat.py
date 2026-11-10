"""Lesson 1 benchmark: Persian chat latency and throughput.

Measures, for every prompt:
  * TTFT  - time to first streamed token (wall clock)
  * wall  - total request time (wall clock)
  * tok/s - generation speed reported by the local project API
  * token counts

Results are written to benchmarks/results/ as JSON so the lesson report is
reproducible.

Usage:
    python benchmarks/bench_chat.py --runs 1 --max-tokens 200
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
# Direct script execution puts benchmarks/ (not the repository root) on sys.path.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.config import Settings  # noqa: E402
from server.voice import local_client_headers, local_api_url  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROMPTS = ROOT / "benchmarks" / "prompts_fa.json"


def results_dir() -> Path:
    """Where JSON reports go; ``PVAI_BENCHMARK_OUTPUT_DIR`` overrides the default.

    The default used to be a repository path, which made a CI run write into the
    checkout.  An environment variable (or ``--out``) keeps the report next to
    the corpus/CI artefacts instead, and works from any working directory.
    """
    override = os.getenv("PVAI_BENCHMARK_OUTPUT_DIR", "").strip()
    if override:
        return Path(os.path.expandvars(override)).expanduser()
    return ROOT / "benchmarks" / "results"


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _summarize(values: list[float]) -> dict[str, float]:
    if not values:
        return {"mean": 0.0, "median": 0.0, "p95": 0.0, "min": 0.0, "max": 0.0}
    return {
        "mean": round(statistics.fmean(values), 2),
        "median": round(statistics.median(values), 2),
        "p95": round(_percentile(values, 0.95), 2),
        "min": round(min(values), 2),
        "max": round(max(values), 2),
    }


def _error_detail(response: httpx.Response) -> str:
    try:
        data = response.json()
    except Exception:
        return response.text.strip() or f"HTTP {response.status_code}"
    detail = data.get("detail") or data.get("error") or data
    return json.dumps(detail, ensure_ascii=False) if isinstance(detail, dict) else str(detail)


def stream_completion(
    client: httpx.Client,
    base_url: str,
    prompt: str,
    *,
    max_tokens: int,
    temperature: float,
) -> dict[str, Any]:
    payload = {
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    started = time.perf_counter()
    first_token_ms: float | None = None
    chunks: list[str] = []
    timings: dict[str, Any] = {}

    with client.stream("POST", f"{base_url}/v1/chat/completions", json=payload) as response:
        if response.status_code != 200:
            response.read()
            raise RuntimeError(_error_detail(response))
        for line in response.iter_lines():
            if not line.startswith("data: "):
                continue
            raw = line[6:].strip()
            if raw == "[DONE]":
                break
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if "error" in event:
                error = event["error"]
                message = error.get("message") if isinstance(error, dict) else str(error)
                raise RuntimeError(message or "unknown streaming error")
            choices = event.get("choices") or []
            if choices:
                piece = (choices[0].get("delta") or {}).get("content")
                if piece:
                    if first_token_ms is None:
                        first_token_ms = round((time.perf_counter() - started) * 1000, 2)
                    chunks.append(piece)
            if event.get("timings"):
                timings = event["timings"]

    wall_ms = round((time.perf_counter() - started) * 1000, 2)
    return {
        "content": "".join(chunks),
        "ttft_ms": first_token_ms if first_token_ms is not None else wall_ms,
        "wall_ms": wall_ms,
        "timings": timings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Persian Voice AI lesson-1 benchmark")
    parser.add_argument("--base-url", default=local_api_url(Settings.from_env()))
    parser.add_argument("--prompts", type=Path, default=DEFAULT_PROMPTS,
                        help="prompt set JSON (default: benchmarks/prompts_fa.json)")
    parser.add_argument("--runs", type=int, default=1, help="repetitions per prompt")
    parser.add_argument("--max-tokens", type=int, default=200)
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    prompts = json.loads(args.prompts.read_text(encoding="utf-8"))
    base_url = args.base_url.rstrip("/")

    with httpx.Client(
        timeout=httpx.Timeout(args.timeout, connect=5.0),
        headers={"Accept": "text/event-stream", **local_client_headers(Settings.from_env(), base_url)},
    ) as client:
        health_response = client.get(f"{base_url}/health", timeout=10.0)
        health_response.raise_for_status()
        health = health_response.json()
        if health.get("status") != "ok":
            print("سرویس آماده نیست:", json.dumps(health, ensure_ascii=False, indent=2), file=sys.stderr)
            return 2

        print(f"مدل: {health.get('model')} | API: {base_url}")
        print("در حال گرم‌کردن مدل (خارج از سنجش)...", flush=True)
        warmup = stream_completion(
            client,
            base_url,
            "سلام، فقط بنویس: آماده‌ام.",
            max_tokens=32,
            temperature=0.0,
        )
        print(
            f"گرم‌کردن: {warmup['wall_ms'] / 1000:.2f}s "
            f"(بارگذاری مدل {float(warmup['timings'].get('load_ms', 0)) / 1000:.2f}s)\n"
        )

        results: list[dict[str, Any]] = []
        for prompt in prompts:
            for run_index in range(1, args.runs + 1):
                print(
                    f"[{prompt['id']}] اجرای {run_index}/{args.runs} ...",
                    end=" ",
                    flush=True,
                )
                try:
                    outcome = stream_completion(
                        client,
                        base_url,
                        prompt["prompt"],
                        max_tokens=args.max_tokens,
                        temperature=args.temperature,
                    )
                except (RuntimeError, httpx.HTTPError) as exc:
                    print(f"خطا: {exc}")
                    return 1
                timings = outcome["timings"]
                print(
                    f"TTFT {outcome['ttft_ms'] / 1000:.2f}s | "
                    f"کل {outcome['wall_ms'] / 1000:.2f}s | "
                    f"{float(timings.get('tokens_per_second', 0)):.1f} tok/s"
                )
                results.append(
                    {
                        "id": prompt["id"],
                        "category": prompt.get("category", ""),
                        "prompt": prompt["prompt"],
                        "checks": prompt.get("checks", ""),
                        "run": run_index,
                        "content": outcome["content"],
                        "ttft_ms": outcome["ttft_ms"],
                        "wall_ms": outcome["wall_ms"],
                        "timings": timings,
                    }
                )

    ttft_values = [float(item["ttft_ms"]) for item in results]
    wall_values = [float(item["wall_ms"]) for item in results]
    tps_values = [
        float(item["timings"].get("tokens_per_second", 0)) for item in results
    ]
    token_values = [
        int(item["timings"].get("completion_tokens", 0)) for item in results
    ]

    summary = {
        "prompts": len(results),
        "ttft_ms": _summarize(ttft_values),
        "wall_ms": _summarize(wall_values),
        "tokens_per_second": _summarize(tps_values),
        "completion_tokens": _summarize([float(value) for value in token_values]),
    }

    report = {
        "schema_version": 1,
        "lesson": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "health": health,
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "processor": platform.processor(),
            "cpu_count": os.cpu_count(),
        },
        "settings": {
            "runs": args.runs,
            "max_tokens": args.max_tokens,
            "temperature": args.temperature,
        },
        "warmup": warmup,
        "results": results,
        "summary": summary,
    }

    directory = results_dir()
    directory.mkdir(parents=True, exist_ok=True)
    output = args.out or directory / f"lesson01-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "-" * 72)
    print(
        f"میانگین TTFT: {summary['ttft_ms']['mean'] / 1000:.2f}s | "
        f"میانه: {summary['ttft_ms']['median'] / 1000:.2f}s | "
        f"P95: {summary['ttft_ms']['p95'] / 1000:.2f}s"
    )
    print(
        f"میانگین زمان کل: {summary['wall_ms']['mean'] / 1000:.2f}s | "
        f"میانگین سرعت: {summary['tokens_per_second']['mean']:.1f} tok/s"
    )
    print(f"گزارش ذخیره شد: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
