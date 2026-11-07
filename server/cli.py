"""Interactive Persian chat client for the local API.

Run the API first:
    python -m uvicorn server.main:app --host 127.0.0.1 --port 8000

Then:
    python -m server.cli

The client keeps the current session's turns in memory only. Persistent
memory is intentionally out of scope for lesson 1.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

import httpx

MAX_SESSION_TURNS = 6  # 6 user + 6 assistant messages sent as context

HELP_TEXT = """\
دستورهای موجود:
  /راهنما  یا /help    نمایش این راهنما
  /پاک     یا /clear   پاک کردن تاریخچهٔ همین نشست
  /آمار    یا /stats   نمایش زمان‌سنجی آخرین پاسخ
  /خروج    یا /exit    خروج

نکته: تاریخچه فقط تا پایان همین نشست در حافظهٔ کلاینت می‌ماند.
"""


def _enable_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    try:
        sys.stdin.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def _format_timings(timings: dict[str, Any], ttft_ms: float | None = None) -> str:
    parts: list[str] = []
    if ttft_ms is not None:
        parts.append(f"اولین توکن {ttft_ms / 1000:.2f}s")
    if timings.get("wall_ms") is not None:
        parts.append(f"کل {float(timings['wall_ms']) / 1000:.2f}s")
    if timings.get("tokens_per_second"):
        parts.append(f"{float(timings['tokens_per_second']):.1f} توکن/ثانیه")
    if timings.get("completion_tokens") is not None:
        parts.append(f"{timings['completion_tokens']} توکن")
    return " | ".join(parts) if parts else "زمان‌سنجی در دسترس نیست"


def _extract_error(response: httpx.Response) -> str:
    try:
        data = response.json()
    except json.JSONDecodeError:
        return response.text.strip() or f"HTTP {response.status_code}"
    detail = data.get("detail") or data.get("error") or data
    if isinstance(detail, dict):
        detail = detail.get("message") or json.dumps(detail, ensure_ascii=False)
    return str(detail)


def _trim_history(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """Keep the optional system message and the most recent turns."""
    system = [m for m in messages if m["role"] == "system"][:1]
    turns = [m for m in messages if m["role"] != "system"]
    while len(turns) > MAX_SESSION_TURNS * 2:
        turns = turns[2:]
    return system + turns


def _payload(
    args: argparse.Namespace,
    messages: list[dict[str, str]],
    *,
    stream: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"messages": messages, "stream": stream}
    if args.model:
        payload["model"] = args.model
    if args.temperature is not None:
        payload["temperature"] = args.temperature
    if args.max_tokens is not None:
        payload["max_tokens"] = args.max_tokens
    return payload


def _chat_once(
    client: httpx.Client,
    args: argparse.Namespace,
    messages: list[dict[str, str]],
) -> tuple[str, dict[str, Any]]:
    response = client.post(
        f"{args.base_url}/v1/chat/completions",
        json=_payload(args, messages, stream=False),
    )
    if response.status_code != 200:
        raise RuntimeError(_extract_error(response))
    data = response.json()
    content = data["choices"][0]["message"]["content"]
    print(content)
    print(f"[زمان] {_format_timings(data.get('timings', {}))}", file=sys.stderr)
    return content, data.get("timings", {})


def _chat_stream(
    client: httpx.Client,
    args: argparse.Namespace,
    messages: list[dict[str, str]],
) -> tuple[str, dict[str, Any], float | None]:
    started = time.perf_counter()
    first_token_ms: float | None = None
    chunks: list[str] = []
    timings: dict[str, Any] = {}

    with client.stream(
        "POST",
        f"{args.base_url}/v1/chat/completions",
        json=_payload(args, messages, stream=True),
    ) as response:
        if response.status_code != 200:
            response.read()
            raise RuntimeError(_extract_error(response))
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
                raise RuntimeError(message or "خطای نامشخص از سرور")
            choices = event.get("choices") or []
            if choices:
                piece = (choices[0].get("delta") or {}).get("content")
                if piece:
                    if first_token_ms is None:
                        first_token_ms = round((time.perf_counter() - started) * 1000, 2)
                    chunks.append(piece)
                    print(piece, end="", flush=True)
            if event.get("timings"):
                timings = event["timings"]
        print()

    return "".join(chunks), timings, first_token_ms


def _print_health(client: httpx.Client, args: argparse.Namespace) -> bool:
    try:
        response = client.get(f"{args.base_url}/health", timeout=5.0)
        response.raise_for_status()
        health = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        print(f"سرور محلی در {args.base_url} در دسترس نیست: {exc}", file=sys.stderr)
        print(
            "ابتدا سرور را اجرا کنید:\n"
            "  .\\.venv\\Scripts\\python.exe -m uvicorn server.main:app --host 127.0.0.1 --port 8000",
            file=sys.stderr,
        )
        return False

    backend = health.get("backend", {})
    if health.get("status") != "ok":
        print(f"هشدار: سرویس در وضعیت «{health.get('status')}» است.", file=sys.stderr)
        if not backend.get("reachable"):
            print(f"  Ollama در دسترس نیست: {backend.get('error', '')}", file=sys.stderr)
            print("  اجرا کنید: ollama serve", file=sys.stderr)
        elif not backend.get("model_available"):
            print(
                f"  مدل «{health.get('model')}» دانلود نشده است. اجرا کنید: "
                f"ollama pull {health.get('model')}",
                file=sys.stderr,
            )
        return False

    print(f"Persian Voice AI | مدل: {health.get('model')} | API: {args.base_url}")
    print("برای دیدن دستورها /راهنما را وارد کنید. برای خروج /خروج.\n")
    return True


def _run_once(client: httpx.Client, args: argparse.Namespace) -> int:
    messages: list[dict[str, str]] = []
    if args.system:
        messages.append({"role": "system", "content": args.system})
    messages.append({"role": "user", "content": args.once})
    try:
        if args.no_stream:
            _chat_once(client, args, messages)
        else:
            _chat_stream(client, args, messages)
        return 0
    except (RuntimeError, httpx.HTTPError) as exc:
        print(f"خطا: {exc}", file=sys.stderr)
        return 1


def _run_interactive(client: httpx.Client, args: argparse.Namespace) -> int:
    if not _print_health(client, args):
        return 2

    messages: list[dict[str, str]] = []
    if args.system:
        messages.append({"role": "system", "content": args.system})
    last_timings: dict[str, Any] = {}
    last_ttft: float | None = None

    while True:
        try:
            user_text = input("شما: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nخدانگهدار.")
            return 0

        if not user_text:
            continue
        command = user_text.replace("‌", "").lower()
        if command in {"/exit", "/quit", "/خروج"}:
            print("خدانگهدار.")
            return 0
        if command in {"/help", "/?", "/راهنما"}:
            print(HELP_TEXT)
            continue
        if command in {"/clear", "/پاک"}:
            messages = [m for m in messages if m["role"] == "system"]
            print("تاریخچهٔ نشست پاک شد.\n")
            continue
        if command in {"/stats", "/آمار"}:
            if last_timings:
                print(f"[زمان] {_format_timings(last_timings, last_ttft)}\n")
            else:
                print("هنوز پاسخی ثبت نشده است.\n")
            continue
        if command == "/model":
            print(f"مدل: {args.model or 'پیش‌فرض سرور'}\n")
            continue

        messages.append({"role": "user", "content": user_text})
        try:
            if args.no_stream:
                content, last_timings = _chat_once(client, args, messages)
            else:
                content, last_timings, last_ttft = _chat_stream(client, args, messages)
            messages.append({"role": "assistant", "content": content})
            messages = _trim_history(messages)
            if not args.no_stream:
                print(f"[زمان] {_format_timings(last_timings, last_ttft)}\n")
            else:
                print()
        except KeyboardInterrupt:
            messages.pop()
            print("\n[لغو شد]\n")
        except (RuntimeError, httpx.HTTPError) as exc:
            messages.pop()
            print(f"\nخطا: {exc}\n", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Persian Voice AI - local Persian chat")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--model", default=None, help="override the server model")
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--max-tokens", type=int, default=None)
    parser.add_argument("--system", default=None, help="optional custom system prompt")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--no-stream", action="store_true", help="disable token streaming")
    parser.add_argument("--once", default=None, metavar="TEXT", help="send one message and exit")
    return parser


def main(argv: list[str] | None = None) -> int:
    _enable_utf8()
    args = build_parser().parse_args(argv)
    args.base_url = args.base_url.rstrip("/")

    with httpx.Client(
        timeout=httpx.Timeout(args.timeout, connect=5.0),
        headers={"Accept": "text/event-stream"},
    ) as client:
        if args.once:
            return _run_once(client, args)
        return _run_interactive(client, args)


if __name__ == "__main__":
    raise SystemExit(main())
