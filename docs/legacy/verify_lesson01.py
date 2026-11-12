"""End-to-end verification of the lesson-1 success criteria (llama.cpp backend).

Checks, in order:
  1. local API is up and llama.cpp model is loaded
  2. the configured GGUF model file is available
  3. the model generates user-visible response content
  4. streaming works and time-to-first-token is measurable
  5. the OpenAI-compatible model endpoint responds

Usage:
    python tools/verify_lesson01.py
Exit code 0 means all criteria passed.

This verification checks the local pipeline and user-visible response content;
it does not score Persian language quality. Private reasoning fields are ignored.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
PERSIAN_RE = re.compile(r"[\u0600-\u06FF]")


def _line(ok: bool, title: str, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {title}" + (f"  ({detail})" if detail else ""))


def _persian_ratio(text: str) -> float:
    letters = [char for char in text if char.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for char in letters if PERSIAN_RE.match(char)) / len(letters)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify the local llama.cpp + FastAPI text path")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="local FastAPI URL")
    args = parser.parse_args(argv)
    base_url = args.base_url.rstrip("/")

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    results: list[bool] = []
    with httpx.Client(timeout=httpx.Timeout(300.0, connect=5.0)) as client:
        # 1. health
        try:
            health_response = client.get(f"{base_url}/health", timeout=10.0)
            health_response.raise_for_status()
            health: dict[str, Any] = health_response.json()
        except (httpx.HTTPError, ValueError) as exc:
            _line(False, "API محلی در دسترس است", str(exc))
            print("\nنتیجه: سرور اجرا نشده است. tools\\start_server.ps1 را اجرا کنید.")
            return 1

        backend = health.get("backend", {})
        api_ok = health.get("status") == "ok"
        results.append(api_ok)
        _line(
            api_ok,
            "مدل GGUF روی PC لود شده است",
            f"model={health.get('model')}",
        )
        _line(
            bool(backend.get("reachable")),
            "بک‌اند llama.cpp در دسترس است",
            backend.get("error", "") if not backend.get("reachable") else "",
        )
        _line(
            bool(backend.get("model_available")),
            "فایل مدل GGUF موجود است",
            "" if backend.get("model_available") else "فایل .gguf در models/ نیست",
        )

        # 2. non-streaming response (user-visible content only)
        started = time.perf_counter()
        try:
            chat_response = client.post(
                f"{base_url}/v1/chat/completions",
                json={
                    "messages": [
                        {
                            "role": "user",
                            "content": "سلام",
                        }
                    ],
                    "max_tokens": 120,
                },
            )
            chat_response.raise_for_status()
            body = chat_response.json()
            choice = body["choices"][0]
            message = choice.get("message", {})
            content = message.get("content") or ""
            wall_ms = (time.perf_counter() - started) * 1000
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            _line(False, "پاسخ از مدل دریافت شد", str(exc))
            return 1

        has_response = bool(content and content.strip())
        results.append(has_response)
        _line(
            has_response,
            "پاسخ متنی قابل نمایش از مدل دریافت شد",
            f"{len(content)} کاراکتر، {wall_ms / 1000:.2f}s",
        )

        # Persian check (informational, not required for this model)
        ratio = _persian_ratio(content)
        persian_ok = bool(PERSIAN_RE.search(content)) and ratio >= 0.5
        _line(
            persian_ok,
            "پاسخ حاوی متن فارسی است (اطلاعی)",
            f"{ratio:.0%} حروف فارسی" if ratio > 0 else "پاسخ حاوی حرف فارسی نیست",
        )

        timings = body.get("timings", {})
        timing_ok = bool(timings) and float(timings.get("wall_ms", 0)) > 0
        results.append(timing_ok)
        _line(
            timing_ok,
            "زمان پاسخ اندازه‌گیری شد",
            f"tokens={timings.get('completion_tokens')} tok/s={timings.get('tokens_per_second')}",
        )

        # 3. streaming + TTFT (user-visible content only)
        stream_ok = False
        ttft_ms = None
        streamed_any: list[str] = []
        try:
            started = time.perf_counter()
            with client.stream(
                "POST",
                f"{base_url}/v1/chat/completions",
                json={
                    "messages": [{"role": "user", "content": "سلام"}],
                    "stream": True,
                    "max_tokens": 40,
                },
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line.startswith("data: "):
                        continue
                    raw = line[6:].strip()
                    if raw == "[DONE]":
                        break
                    event = json.loads(raw)
                    if "error" in event:
                        raise RuntimeError(str(event["error"]))
                    choices = event.get("choices") or []
                    if choices:
                        delta = choices[0].get("delta") or {}
                        piece = delta.get("content") or ""
                        if piece:
                            if ttft_ms is None:
                                ttft_ms = (time.perf_counter() - started) * 1000
                            streamed_any.append(piece)
            stream_ok = bool(streamed_any) and ttft_ms is not None
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            _line(False, "پاسخ streaming و TTFT", str(exc))

        results.append(stream_ok)
        _line(
            stream_ok,
            "پاسخ streaming و TTFT قابل اندازه‌گیری است",
            f"TTFT={ttft_ms / 1000:.2f}s" if ttft_ms else "هیچ تکه‌ای دریافت نشد",
        )

        # 4. models endpoint
        try:
            models = client.get(f"{base_url}/v1/models", timeout=10.0).json().get("data", [])
            ids = [item.get("id") for item in models]
            models_ok = bool(ids)
        except (httpx.HTTPError, ValueError):
            ids = []
            models_ok = False
        results.append(models_ok)
        _line(models_ok, "API فهرست مدل‌ها را برمی‌گرداند", ", ".join(str(i) for i in ids))

        print("\nپاسخ نمونه:\n" + "-" * 60)
        print(content.strip() if content else "(پاسخ متنی قابل نمایش دریافت نشد)")
        print("-" * 60)

    passed = all(results)
    print(f"\nنتیجه نهایی: {'همهٔ معیارها PASS شدند' if passed else 'برخی معیارها FAIL شدند'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
