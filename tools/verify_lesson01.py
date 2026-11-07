"""End-to-end verification of the lesson-1 success criteria.

Checks, in order:
  1. local API is up and Ollama is reachable
  2. the configured model is available on the PC
  3. the model answers a Persian prompt in Persian
  4. streaming works and time-to-first-token is measurable
  5. the OpenAI-compatible model endpoint responds

Usage:
    python tools/verify_lesson01.py
Exit code 0 means all criteria passed.
"""

from __future__ import annotations

import json
import re
import sys
import time
from typing import Any

import httpx

BASE_URL = "http://127.0.0.1:8000"
PERSIAN_RE = re.compile(r"[\u0600-\u06FF]")


def _line(ok: bool, title: str, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {title}" + (f"  ({detail})" if detail else ""))


def _persian_ratio(text: str) -> float:
    letters = [char for char in text if char.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for char in letters if PERSIAN_RE.match(char)) / len(letters)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    results: list[bool] = []
    with httpx.Client(timeout=httpx.Timeout(300.0, connect=5.0)) as client:
        # 1. health
        try:
            health_response = client.get(f"{BASE_URL}/health", timeout=10.0)
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
            "مدل روی PC بالا آمده است",
            f"model={health.get('model')} ollama={health.get('ollama_version')}",
        )
        _line(
            bool(backend.get("reachable")),
            "Ollama در دسترس است",
            backend.get("error", "") if not backend.get("reachable") else "",
        )
        _line(
            bool(backend.get("model_available")),
            "مدل دانلود شده است",
            "" if backend.get("model_available") else "ollama pull <model>",
        )

        # 2. non-streaming Persian answer
        started = time.perf_counter()
        try:
            chat_response = client.post(
                "/v1/chat/completions",
                json={
                    "messages": [
                        {
                            "role": "user",
                            "content": "در دو جملهٔ کوتاه فارسی بگو هوش مصنوعی محلی چه فایده‌ای دارد.",
                        }
                    ],
                    "max_tokens": 120,
                },
            )
            chat_response.raise_for_status()
            body = chat_response.json()
            content = body["choices"][0]["message"]["content"]
            wall_ms = (time.perf_counter() - started) * 1000
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            _line(False, "پاسخ فارسی متنی دریافت شد", str(exc))
            return 1

        ratio = _persian_ratio(content)
        persian_ok = bool(PERSIAN_RE.search(content)) and ratio >= 0.5
        results.append(persian_ok)
        _line(
            persian_ok,
            "پاسخ فارسی متنی دریافت شد",
            f"{len(content)} کاراکتر، {ratio:.0%} حروف فارسی، {wall_ms / 1000:.2f}s",
        )

        timings = body.get("timings", {})
        timing_ok = bool(timings) and float(timings.get("wall_ms", 0)) > 0
        results.append(timing_ok)
        _line(
            timing_ok,
            "زمان پاسخ اندازه‌گیری شد",
            f"tokens={timings.get('completion_tokens')} tok/s={timings.get('tokens_per_second')}",
        )

        # 3. streaming + TTFT
        stream_ok = False
        ttft_ms = None
        streamed: list[str] = []
        try:
            started = time.perf_counter()
            with client.stream(
                "POST",
                "/v1/chat/completions",
                json={
                    "messages": [{"role": "user", "content": "سلام! فقط بنویس: آماده‌ام."}],
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
                        piece = (choices[0].get("delta") or {}).get("content")
                        if piece:
                            if ttft_ms is None:
                                ttft_ms = (time.perf_counter() - started) * 1000
                            streamed.append(piece)
            stream_ok = bool(streamed) and ttft_ms is not None
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            _line(False, "پاسخ streaming و TTFT", str(exc))

        results.append(stream_ok)
        _line(
            stream_ok,
            "پاسخ streaming و TTFT قابل اندازه‌گیری است",
            f"TTFT={ttft_ms / 1000:.2f}s" if ttft_ms else "",
        )

        # 4. models endpoint
        try:
            models = client.get("/v1/models", timeout=10.0).json().get("data", [])
            ids = [item.get("id") for item in models]
            models_ok = bool(ids)
        except (httpx.HTTPError, ValueError):
            ids = []
            models_ok = False
        results.append(models_ok)
        _line(models_ok, "API فهرست مدل‌ها را برمی‌گرداند", ", ".join(str(i) for i in ids))

        print("\nپاسخ نمونه:\n" + "-" * 60)
        print(content.strip())
        print("-" * 60)

    passed = all(results)
    print(f"\nنتیجه نهایی: {'همهٔ معیارها PASS شدند' if passed else 'برخی معیارها FAIL شدند'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
