"""Compare local GGUF models through a llama-server process."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import platform
import socket
import statistics
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.config import DEFAULT_SYSTEM_PROMPT
from server.llm import _ThinkingFilter

DEFAULT_PROMPTS = ROOT / "benchmarks" / "prompts_fa.json"
RESULTS_DIR = ROOT / "benchmarks" / "results"
LOGS_DIR = ROOT / "logs"


class ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


def _windows_process_memory(pid: int) -> tuple[int, int] | None:
    if platform.system() != "Windows":
        return None

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_process.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    get_memory = psapi.GetProcessMemoryInfo
    get_memory.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ProcessMemoryCounters),
        wintypes.DWORD,
    ]
    get_memory.restype = wintypes.BOOL

    process_query_information = 0x0400
    process_vm_read = 0x0010
    handle = open_process(process_query_information | process_vm_read, False, pid)
    if not handle:
        return None

    try:
        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        if not get_memory(handle, ctypes.byref(counters), counters.cb):
            return None
        return counters.WorkingSetSize, counters.PrivateUsage
    finally:
        close_handle(handle)


def _gpu_memory_mib() -> int | None:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            check=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    values: list[int] = []
    for line in result.stdout.splitlines():
        try:
            values.append(int(line.strip()))
        except ValueError:
            continue
    return sum(values) if values else None


class ResourceMonitor:
    def __init__(self, pid: int, interval_s: float = 1.0) -> None:
        self.pid = pid
        self.interval_s = interval_s
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.working_set_peak = 0
        self.private_bytes_peak = 0
        self.gpu_used_peak: int | None = None
        self.samples = 0

    def _sample(self) -> None:
        while not self.stop_event.is_set():
            memory = _windows_process_memory(self.pid)
            if memory:
                working_set, private_bytes = memory
                self.working_set_peak = max(self.working_set_peak, working_set)
                self.private_bytes_peak = max(self.private_bytes_peak, private_bytes)
            gpu_used = _gpu_memory_mib()
            if gpu_used is not None:
                self.gpu_used_peak = max(self.gpu_used_peak or 0, gpu_used)
            self.samples += 1
            self.stop_event.wait(self.interval_s)

    def start(self) -> None:
        self.thread = threading.Thread(target=self._sample, daemon=True)
        self.thread.start()

    def stop(self) -> dict[str, int | None]:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=self.interval_s + 2)
        return {
            "working_set_peak_mib": round(self.working_set_peak / (1024 * 1024), 1),
            "private_bytes_peak_mib": round(self.private_bytes_peak / (1024 * 1024), 1),
            "gpu_used_peak_mib_system_total": self.gpu_used_peak,
            "samples": self.samples,
        }


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as model_file:
        for block in iter(lambda: model_file.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _command_output(command: list[str]) -> str | None:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=15, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return (result.stdout or result.stderr).strip() or None


def _wait_for_server(process: subprocess.Popen[bytes], base_url: str, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    with httpx.Client(timeout=httpx.Timeout(3.0, connect=1.0)) as client:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("llama-server exited before becoming ready")
            try:
                response = client.get(f"{base_url}/health")
                if response.is_success:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(1)
    raise TimeoutError(f"llama-server did not become ready within {timeout_s:.0f}s")


def _stream_completion(
    client: httpx.Client,
    base_url: str,
    prompt: str,
    *,
    model_id: str,
    max_tokens: int,
    temperature: float,
) -> dict[str, Any]:
    payload = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": DEFAULT_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "stream": True,
        "stream_options": {"include_usage": True},
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    started = time.perf_counter()
    first_token_ms: float | None = None
    content: list[str] = []
    final_event: dict[str, Any] = {}
    content_filter = _ThinkingFilter()

    with client.stream("POST", f"{base_url}/v1/chat/completions", json=payload) as response:
        if response.status_code != 200:
            detail = response.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"llama-server returned HTTP {response.status_code}: {detail[:400]}")
        for line in response.iter_lines():
            if not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if raw == "[DONE]":
                break
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if event.get("error"):
                raise RuntimeError(str(event["error"]))

            choices = event.get("choices") or []
            if "usage" in event or "timings" in event:
                final_event.update(event)
            if choices:
                delta = choices[0].get("delta") or {}
                text_piece = content_filter.feed(str(delta.get("content") or ""))
                if text_piece:
                    if first_token_ms is None:
                        first_token_ms = round((time.perf_counter() - started) * 1000, 2)
                    content.append(text_piece)
                if choices[0].get("finish_reason") is not None:
                    final_event.update(event)

    tail = content_filter.finish()
    if tail:
        if first_token_ms is None:
            first_token_ms = round((time.perf_counter() - started) * 1000, 2)
        content.append(tail)

    wall_ms = round((time.perf_counter() - started) * 1000, 2)
    return {
        "answer": "".join(content),
        "ttft_ms": first_token_ms,
        "wall_ms": wall_ms,
        "usage": final_event.get("usage", {}),
        "timings": final_event.get("timings", {}),
        "finish_reason": (final_event.get("choices") or [{}])[0].get("finish_reason"),
    }


def _run_model(
    server_exe: Path,
    model_path: Path,
    prompts: list[dict[str, Any]],
    *,
    context_size: int,
    threads: int,
    gpu_layers: int | None,
    device: str,
    max_tokens: int,
    temperature: float,
    timeout_s: float,
) -> dict[str, Any]:
    port = _available_port()
    base_url = f"http://127.0.0.1:{port}"
    model_path = model_path.resolve()
    backend_tag = "cpu" if gpu_layers == 0 else "gpu-auto" if gpu_layers is None else f"gpu-{gpu_layers}"
    log_path = LOGS_DIR / f"model-bench-{model_path.stem}-{backend_tag}.log"
    command = [
        str(server_exe.resolve()),
        "-m",
        str(model_path),
        "-c",
        str(context_size),
        "-t",
        str(threads),
        "--parallel",
        "1",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--reasoning-format",
        "deepseek",
        "--reasoning",
        "off",
    ]
    if gpu_layers == 0:
        command.extend(["-ngl", "0", "--device", "none"])
    elif gpu_layers is None:
        command.extend(["--device", device, "--fit", "on"])
    else:
        command.extend(["-ngl", str(gpu_layers), "--device", device, "--fit", "off"])
    gpu_baseline = _gpu_memory_mib()
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(timezone.utc).isoformat()

    with log_path.open("w", encoding="utf-8") as log_file:
        backend_name = "CPU" if gpu_layers == 0 else "GPU auto-fit" if gpu_layers is None else f"GPU {gpu_layers} layers"
        print(f"\n[{model_path.name}] starting llama-server ({backend_name})", flush=True)
        process = subprocess.Popen(command, stdout=log_file, stderr=subprocess.STDOUT)
        monitor = ResourceMonitor(process.pid)
        monitor.start()
        try:
            _wait_for_server(process, base_url, timeout_s)
            with httpx.Client(
                timeout=httpx.Timeout(timeout_s, connect=5.0),
                headers={"Accept": "text/event-stream"},
            ) as client:
                warmup = _stream_completion(
                    client,
                    base_url,
                    "فقط بنویس: آماده‌ام.",
                    model_id=model_path.name,
                    max_tokens=32,
                    temperature=0.0,
                )
                results: list[dict[str, Any]] = []
                print(f"[{model_path.name}] warmup complete; {len(prompts)} prompts", flush=True)
                for index, prompt in enumerate(prompts, start=1):
                    print(f"[{model_path.name}] [{index}/{len(prompts)}] {prompt['id']}", flush=True)
                    outcome = _stream_completion(
                        client,
                        base_url,
                        prompt["prompt"],
                        model_id=model_path.name,
                        max_tokens=max_tokens,
                        temperature=temperature,
                    )
                    results.append(
                        {
                            "id": prompt["id"],
                            "category": prompt.get("category", ""),
                            "prompt": prompt["prompt"],
                            "checks": prompt.get("checks", ""),
                            "token_limit_reached": (
                                int(outcome["usage"].get("completion_tokens", 0)) >= max_tokens
                            ),
                            **outcome,
                        }
                    )
        except Exception as exc:
            raise RuntimeError(f"Model run failed; see llama-server log: {log_path}: {exc}") from exc
        finally:
            resources = monitor.stop()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

    if process.returncode not in (0, -15, 1):
        raise RuntimeError(f"llama-server exited with code {process.returncode}; see {log_path}")

    return {
        "model": model_path.stem,
        "model_path": str(model_path),
        "model_file_bytes": model_path.stat().st_size,
        "model_sha256": _file_sha256(model_path),
        "llama_server": str(server_exe.resolve()),
        "profile": {
            "context_size": context_size,
            "threads": threads,
            "parallel_slots": 1,
            "gpu_layers": "auto-fit" if gpu_layers is None else gpu_layers,
            "device": "none" if gpu_layers == 0 else device,
            "reasoning_format": "deepseek",
            "reasoning": "off",
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
        "started_at": started_at,
        "warmup": warmup,
        "resources": {
            "gpu_used_before_mib_system_total": gpu_baseline,
            "gpu_delta_peak_mib_system_total": (
                max(0, int(resources["gpu_used_peak_mib_system_total"]) - gpu_baseline)
                if gpu_baseline is not None and resources["gpu_used_peak_mib_system_total"] is not None
                else None
            ),
            **resources,
        },
        "server_log": str(log_path),
        "results": results,
        "summary": {
            "ttft_ms_mean": round(
                statistics.fmean(item["ttft_ms"] for item in results if item["ttft_ms"] is not None), 2
            ) if any(item["ttft_ms"] is not None for item in results) else None,
            "wall_ms_mean": round(statistics.fmean(item["wall_ms"] for item in results), 2),
            "tokens_per_second_mean": round(
                statistics.fmean(
                    float(
                        item["timings"].get("predicted_per_second")
                        or item["timings"].get("tokens_per_second")
                        or 0
                    )
                    for item in results
                ),
                2,
            ),
            "empty_answer_count": sum(not item["answer"].strip() for item in results),
            "token_limit_reached_count": sum(item["token_limit_reached"] for item in results),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare local GGUF models on the same Persian prompts")
    parser.add_argument("--server", type=Path, required=True, help="path to llama-server.exe")
    parser.add_argument("--models", type=Path, nargs="+", required=True)
    parser.add_argument("--prompts", type=Path, default=DEFAULT_PROMPTS)
    parser.add_argument("--ctx-size", type=int, default=4096)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument(
        "--gpu-layers",
        type=int,
        default=None,
        help="0 for CPU; omit to let llama.cpp auto-fit Vulkan layers; positive values are fixed",
    )
    parser.add_argument("--device", default="Vulkan0")
    parser.add_argument("--max-tokens", type=int, default=192)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    server_exe = args.server.resolve()
    if not server_exe.is_file():
        parser.error(f"llama-server executable not found: {server_exe}")
    model_paths = [path.resolve() for path in args.models]
    for model_path in model_paths:
        if not model_path.is_file():
            parser.error(f"GGUF model file not found: {model_path}")
    prompts = json.loads(args.prompts.read_text(encoding="utf-8"))

    runs = [
        _run_model(
            server_exe,
            model_path,
            prompts,
            context_size=args.ctx_size,
            threads=args.threads,
            gpu_layers=args.gpu_layers,
            device=args.device,
            max_tokens=args.max_tokens,
            temperature=args.temperature,
            timeout_s=args.timeout,
        )
        for model_path in model_paths
    ]
    report = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "platform": platform.platform(),
            "processor": platform.processor(),
            "logical_cpu_count": os.cpu_count(),
            "llama_server_version": _command_output([str(server_exe), "--version"]),
            "nvidia_gpu": _command_output(
                [
                    "nvidia-smi",
                    "--query-gpu=name,memory.total,driver_version,compute_cap",
                    "--format=csv,noheader",
                ]
            ),
        },
        "prompt_file": str(args.prompts.resolve()),
        "prompt_count": len(prompts),
        "runs": runs,
        "quality_scoring": (
            "Only user-visible content is saved; score each response against its listed checks. "
            "Reasoning fields are excluded."
        ),
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    output = args.out or RESULTS_DIR / f"pc1-model-compare-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved comparison report: {output}")
    for run in runs:
        print(
            f"{run['model']}: mean TTFT {run['summary']['ttft_ms_mean'] / 1000:.2f}s, "
            f"mean wall {run['summary']['wall_ms_mean'] / 1000:.2f}s, "
            f"mean generation {run['summary']['tokens_per_second_mean']:.2f} tok/s, "
            f"peak process RAM {run['resources']['working_set_peak_mib']} MiB, "
            f"peak system VRAM delta {run['resources']['gpu_delta_peak_mib_system_total']} MiB"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
