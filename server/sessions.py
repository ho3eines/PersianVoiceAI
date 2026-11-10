"""Inspect and delete locally persisted conversations.

Privacy requirement: the conversation store must be inspectable and removable by
the user.  This CLI is the offline counterpart of ``/api/sessions`` and never
sends anything anywhere.

Usage::

    python -m server.sessions list
    python -m server.sessions show s-1a2b3c
    python -m server.sessions delete s-1a2b3c
    python -m server.sessions delete-all
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Sequence

from .config import Settings
from .conversation import ConversationStore, NullStore, open_store
from .voice import VoiceError


def _store(settings: Settings | None = None) -> ConversationStore | NullStore:
    resolved = settings or Settings.from_env()
    store = open_store(resolved)
    if not isinstance(store, ConversationStore):
        raise VoiceError("ذخیره‌سازی محلی خاموش است (PVAI_HISTORY_PERSIST=false).")
    return store


def list_sessions(settings: Settings | None = None) -> list[dict[str, Any]]:
    return _store(settings).sessions()


def show_session(session_id: str, settings: Settings | None = None) -> dict[str, Any]:
    store = _store(settings)
    records = [record for record in store.load(session_id) if record.get("kind") == "message"]
    if not records:
        raise VoiceError(f"نشستی با شناسهٔ «{session_id}» پیدا نشد.")
    return {"session_id": store.sanitize(session_id), "messages": records}


def delete_session(session_id: str, settings: Settings | None = None) -> bool:
    return _store(settings).delete(session_id)


def delete_all(settings: Settings | None = None) -> int:
    return _store(settings).delete_all()


def _format(message: dict[str, Any]) -> str:
    role = {"user": "شما", "assistant": "دستیار"}.get(str(message.get("role")), "?")
    status = message.get("status", "complete")
    suffix = "" if status == "complete" else f" [{status}]"
    return f"{role}{suffix}: {message.get('content', '')}"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="مدیریت مکالمات ذخیره‌شدهٔ محلی")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="فهرست نشست‌ها")
    show = sub.add_parser("show", help="نمایش پیام‌های یک نشست")
    show.add_argument("session_id")
    delete = sub.add_parser("delete", help="حذف یک نشست")
    delete.add_argument("session_id")
    sub.add_parser("delete-all", help="حذف همهٔ نشست‌ها")
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        if args.command == "list":
            sessions = list_sessions()
            if not sessions:
                print("هیچ نشستی ذخیره نشده است.")
                return 0
            for entry in sessions:
                print(
                    f"{entry['session_id']}  messages={entry['messages']}  "
                    f"bytes={entry['bytes']}"
                )
            return 0
        if args.command == "show":
            data = show_session(args.session_id)
            print(json.dumps({"session_id": data["session_id"]}, ensure_ascii=False))
            for message in data["messages"]:
                print(_format(message))
            return 0
        if args.command == "delete":
            deleted = delete_session(args.session_id)
            print("حذف شد." if deleted else "نشست پیدا نشد.")
            return 0 if deleted else 1
        removed = delete_all()
        print(f"{removed} نشست حذف شد.")
        return 0
    except VoiceError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
