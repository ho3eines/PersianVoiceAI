"""Local conversation memory: sessions, message lifecycle, token-aware context.

Previously the live pipeline kept ``self.history`` in RAM only, truncated it by
*message count* with two inconsistent slices (``[-10:]`` when building the
request, ``[-12:]`` when committing), never recorded the user message when the
assistant reply failed, and silently dropped interrupted replies without any
lifecycle status.  Restarting the assistant erased the conversation.

This module replaces that with an explicit model:

* ``Message`` — a user or assistant message with a lifecycle ``status``
  (``complete`` / ``interrupted`` / ``failed``) and the ``utterance_id`` /
  ``turn_id`` that produced it.  User messages are deduplicated by
  ``utterance_id``, so a retried or replayed final ASR result can never create a
  second copy.
* ``Conversation`` — append-only message log plus a token-aware context builder.
  Truncation is by *estimated tokens* and only then by message count; orphaned
  leading assistant messages are dropped so the chat template always starts
  with a user turn.  Interrupted assistant text is kept for inspection but is
  excluded from the model context by default (``PVAI_HISTORY_INCLUDE_INTERRUPTED``).
* ``ConversationStore`` — optional append-only JSONL persistence under
  ``PVAI_DATA_DIR/conversations`` (local only, no cloud, no summarisation, no
  long-term personal memory).  Sessions can be listed, inspected and deleted.
  Persistence is configurable and every write failure degrades to RAM-only.

Token counting is an explicit heuristic: the project must not depend on a model
tokenizer to *estimate* a budget.  Persian text is counted at ~2 characters per
token and ASCII at ~4, plus a small per-message overhead.  The heuristic is
conservative on purpose (it over-estimates a little) because exceeding
``n_ctx`` would make llama-server truncate the prompt silently.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Literal

ROLE_USER: Literal["user"] = "user"
ROLE_ASSISTANT: Literal["assistant"] = "assistant"

STATUS_COMPLETE = "complete"
STATUS_INTERRUPTED = "interrupted"
STATUS_FAILED = "failed"
STATUSES = frozenset({STATUS_COMPLETE, STATUS_INTERRUPTED, STATUS_FAILED})

SESSION_ID_ALPHABET = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
)
"""Characters allowed in a session identifier.

Deliberately ASCII-only.  ``str.isalnum()`` accepts Persian/Arabic-Indic digits
and accented letters, which are then written into a filename: that is a
portability trap on Windows (case folding and Unicode normalisation mean two
different ids can address one file) and on any zip/backup tool the user carries
the folder through.  A session id is an opaque key, not user-facing text, so
restricting it to ``[A-Za-z0-9_-]`` costs nothing and removes the whole class of
problems.
"""

WINDOWS_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul", "clock$"}
    | {f"com{index}" for index in range(1, 10)}
    | {f"lpt{index}" for index in range(1, 10)}
)
"""Device names Windows refuses as ``<name>.jsonl`` regardless of the extension."""

MESSAGE_OVERHEAD_TOKENS = 4
"""Chat-template per-message overhead (role markers, separators)."""

MAX_TRACKED_UTTERANCE_IDS = 2048
"""How many recent utterance ids are kept for de-duplication.

The live pipeline already de-duplicates final ASR results (``_finalized``),
so a duplicate for an utterance older than this window is not a realistic
event; the bound exists so a multi-hour session cannot grow one set forever.
"""


def estimate_tokens(text: str) -> int:
    """Conservative token estimate for mixed Persian/English text."""
    if not text:
        return 0
    persian = sum(1 for char in text if "\u0600" <= char <= "\u06ff")
    other = len(text) - persian
    return max(1, math.ceil(persian / 2 + other / 4))


@dataclass(slots=True)
class Message:
    id: int
    role: str
    content: str
    status: str = STATUS_COMPLETE
    utterance_id: int | None = None
    turn_id: int | None = None
    created_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if self.role not in (ROLE_USER, ROLE_ASSISTANT):
            raise ValueError(f"invalid role: {self.role!r}")
        if self.status not in STATUSES:
            raise ValueError(f"invalid status: {self.status!r}")

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "role": self.role,
            "content": self.content,
            "status": self.status,
            "utterance_id": self.utterance_id,
            "turn_id": self.turn_id,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Message":
        return cls(
            id=int(data["id"]),
            role=str(data["role"]),
            content=str(data["content"]),
            status=str(data.get("status", STATUS_COMPLETE)),
            utterance_id=data.get("utterance_id"),
            turn_id=data.get("turn_id"),
            created_at=float(data.get("created_at", time.time())),
        )

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.content) + MESSAGE_OVERHEAD_TOKENS


class SessionInUse(RuntimeError):
    """A second process attempted to write one session's JSONL concurrently."""


class SessionLease:
    """OS-backed, non-blocking per-session lock with a diagnostic PID file.

    OS locks are released on process death (no stale lockfile reclamation race).
    The .lock file persists; its PID is informational only, never an authority.
    """

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        self.handle = os.fdopen(descriptor, "r+b")
        try:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            self.handle.close()
            raise SessionInUse(f"session_in_use: {path.stem}") from exc
        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write(str(os.getpid()).encode("ascii") + b"\n")
        self.handle.flush()

    def close(self) -> None:
        if self.handle.closed:
            return
        self.handle.seek(0)
        self.handle.truncate()
        self.handle.flush()
        if os.name == "nt":
            import msvcrt
            self.handle.seek(0)
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()

    def __enter__(self) -> SessionLease:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


class NullStore:
    """Persistence disabled (``PVAI_HISTORY_PERSIST=false``)."""

    enabled = False
    root: Path | None = None
    error: str | None = None

    def append(self, session_id: str, record: dict[str, Any]) -> bool:
        return False

    def load(self, session_id: str) -> list[dict[str, Any]]:
        return []

    def sessions(self) -> list[dict[str, Any]]:
        return []

    def latest_session(self, prefix: str = "") -> str | None:
        return None

    def count_messages(self, session_id: str) -> int:
        return 0

    def purge_older_than(self, days: int, *, now: float | None = None) -> list[str]:
        return []

    def delete(self, session_id: str, *, raise_on_locked: bool = False) -> bool:
        return False

    def delete_all(self) -> int:
        return 0


class ConversationStore:
    """Append-only local JSONL store, one file per session."""

    enabled = True

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.error: str | None = None
        self._lock = threading.Lock()
        self._count_cache: dict[Path, tuple[int, int, int]] = {}
        # The directory is created lazily on the first write: constructing the
        # store (readiness, API startup, tests) must not touch the disk.

    @staticmethod
    def sanitize(session_id: str) -> str:
        """Validate a session identifier (filesystem-safe, traversal-free).

        Identifiers are **rejected** rather than silently rewritten: rewriting
        could map two different requested sessions onto the same file.

        The alphabet is ASCII ``[A-Za-z0-9_-]`` only, and Windows device names
        (``CON``, ``NUL``, ``COM1``…) are refused because ``<name>.jsonl``
        cannot be created for them on Windows.  Case is preserved but not
        treated as significant: ``s-demo`` and ``S-DEMO`` would address the same
        file on a case-insensitive filesystem, so a session id whose
        lower-cased form already exists is rejected instead of silently sharing
        (and interleaving) another session's log.  That check needs the
        directory, so it happens on the write path in :meth:`append`.
        """
        raw = str(session_id).strip()
        cleaned = "".join(char for char in raw if char in SESSION_ID_ALPHABET)
        if not cleaned or cleaned != raw:
            raise ValueError(
                "session id may only contain ASCII letters, digits, '-' and '_'"
            )
        if len(cleaned) > 64:
            raise ValueError("session id must be at most 64 characters")
        if cleaned.lower() in WINDOWS_RESERVED_NAMES:
            raise ValueError(f"session id {cleaned!r} is a reserved device name")
        return cleaned

    def claim(self, session_id: str) -> SessionLease:
        """Reserve this file before reading ids and until the writer closes."""
        return SessionLease(self.root / f"{self.sanitize(session_id).lower()}.lock")

    def _colliding_case(self, session_id: str) -> str | None:
        """An existing session id that differs only by case, if any."""
        try:
            existing = {
                path.stem.lower(): path.stem for path in self.root.glob("*.jsonl")
            }
        except OSError:
            return None
        return existing.get(session_id.lower())

    def path(self, session_id: str) -> Path:
        return self.root / f"{self.sanitize(session_id)}.jsonl"

    def append(self, session_id: str, record: dict[str, Any]) -> bool:
        try:
            path = self.path(session_id)
        except ValueError:
            return False
        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            try:
                self.root.mkdir(parents=True, exist_ok=True)
                collision = self._colliding_case(session_id)
                if collision is not None and collision != session_id:
                    # Even when path.exists() is True on a case-insensitive
                    # filesystem, this identifier may name someone else's file.
                    self.error = (
                        f"session id {session_id!r} differs only by case from "
                        f"existing session {collision!r}"
                    )
                    return False
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            except OSError as exc:
                self.error = f"conversation write failed: {exc}"
                return False
        self.error = None
        return True

    def load(self, session_id: str) -> list[dict[str, Any]]:
        try:
            path = self.path(session_id)
        except ValueError:
            return []
        if not path.is_file():
            return []
        records: list[dict[str, Any]] = []
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue  # a torn tail line must not destroy the session
                    if isinstance(record, dict):
                        records.append(record)
        except OSError as exc:
            self.error = f"conversation read failed: {exc}"
            return []
        return records

    def count_messages(self, session_id: str) -> int:
        """Number of message records in a session, without parsing every line.

        ``sessions()`` only needs the count, and a long session is megabytes of
        JSON; ``json.loads`` on every line just to test ``record["kind"]`` made
        listing sessions needlessly expensive in an async request handler.
        Every record this store writes is a single line that starts with its
        ``kind`` and ends with the object's closing brace (see
        :meth:`Conversation._append`).  A line matching both is counted without
        being parsed; anything else — a torn tail from a crash mid-write, a
        hand-edited file, a future record type — falls back to ``json.loads``,
        so the fast path can never inflate a count.
        """
        try:
            path = self.path(session_id)
        except ValueError:
            return 0
        return self._count_messages(path)

    @staticmethod
    def _count_messages(path: Path) -> int:
        try:
            handle = path.open("rb")
        except OSError:
            return 0
        total = 0
        with handle:
            for raw in handle:
                line = raw.strip()
                if not line:
                    continue
                if line.startswith(b'{"kind": "message"') and line.endswith(b"}"):
                    total += 1
                    continue
                try:
                    record = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue  # a torn tail line must not destroy the count
                if isinstance(record, dict) and record.get("kind") == "message":
                    total += 1
        return total

    def sessions(self) -> list[dict[str, Any]]:
        if not self.root.is_dir():
            return []
        found: list[dict[str, Any]] = []
        paths = set(self.root.glob("*.jsonl"))
        with self._lock:
            for removed in self._count_cache.keys() - paths:
                del self._count_cache[removed]
        for path in sorted(paths):
            try:
                stat = path.stat()
            except OSError:
                continue
            with self._lock:
                cached = self._count_cache.get(path)
            fingerprint = (stat.st_size, stat.st_mtime_ns)
            if cached is None or cached[:2] != fingerprint:
                count = self._count_messages(path)
                with self._lock:
                    self._count_cache[path] = (*fingerprint, count)
            else:
                count = cached[2]
            found.append(
                {
                    "session_id": path.stem,
                    "messages": count,
                    "updated_at": stat.st_mtime,
                    "bytes": stat.st_size,
                }
            )
        return found

    def latest_session(self, prefix: str = "") -> str | None:
        """Session id of the most recently written session, if any.

        Used by the local live voice loop to resume where the user left off.
        """
        if not self.root.is_dir():
            return None
        try:
            paths = list(self.root.glob(f"{prefix}*.jsonl"))
            if not paths:
                return None
            return max(paths, key=lambda path: path.stat().st_mtime).stem
        except OSError:
            return None

    def purge_older_than(self, days: int, *, now: float | None = None) -> list[str]:
        """Delete sessions untouched for ``days`` days; return the removed ids.

        ``PVAI_HISTORY_RETENTION_DAYS`` defaults to 30 (0 explicitly disables).
        Plain-text transcripts otherwise stay on disk indefinitely.
        """
        if days <= 0 or not self.root.is_dir():
            return []
        cutoff = (now if now is not None else time.time()) - days * 86400
        removed: list[str] = []
        for entry in self.sessions():
            if entry["updated_at"] > cutoff:
                continue
            if self.delete(entry["session_id"]):
                removed.append(entry["session_id"])
        return removed

    def delete(self, session_id: str, *, raise_on_locked: bool = False) -> bool:
        try:
            path = self.path(session_id)
            if not path.is_file():
                return False
            # Do not purge/delete an active conversation while its writer owns
            # the JSONL (including writers in another process).
            with self.claim(session_id):
                path.unlink()
            with self._lock:
                self._count_cache.pop(path, None)
            self.error = None
            return True
        except SessionInUse:
            if raise_on_locked:
                raise
            self.error = "session_in_use"
            return False
        except (ValueError, FileNotFoundError):
            return False
        except OSError as exc:
            self.error = f"conversation delete failed: {exc}"
            return False

    def delete_all(self) -> int:
        removed = 0
        for entry in self.sessions():
            if self.delete(entry["session_id"]):
                removed += 1
        return removed


def open_store(settings: Any) -> ConversationStore | NullStore:
    """Build the store described by ``settings`` (never raises)."""
    if not getattr(settings, "history_persist", False):
        return NullStore()
    root = Path(getattr(settings, "data_dir", "data")) / "conversations"
    try:
        return ConversationStore(root)
    except Exception:  # pragma: no cover - defensive, constructor already guards
        return NullStore()


class Conversation:
    """Message log for one session plus the token-aware model context."""

    def __init__(
        self,
        *,
        session_id: str | None = None,
        conversation_id: str | None = None,
        max_messages: int = 24,
        token_budget: int = 1536,
        max_stored_messages: int = 500,
        include_interrupted: bool = False,
        store: ConversationStore | NullStore | None = None,
        clock: Any = time.time,
    ) -> None:
        self.session_id = session_id or f"s-{uuid.uuid4().hex[:12]}"
        self.conversation_id = conversation_id or uuid.uuid4().hex[:12]
        self.max_messages = max(2, int(max_messages))
        self.token_budget = max(64, int(token_budget))
        # RAM bound for a long-running conversation.  The JSONL log on disk is
        # still append-only (nothing is lost for inspection); only the in-memory
        # window is trimmed, and it is far larger than the token budget ever
        # uses, so context quality is unaffected.
        self.max_stored_messages = max(8, int(max_stored_messages))
        self.include_interrupted = bool(include_interrupted)
        self.store = store or NullStore()
        self._lease: SessionLease | None = None
        self._clock = clock
        self.messages: list[Message] = []
        self._next_id = 1
        self._next_turn = 1
        self._utterance_ids: set[int] = set()
        self._utterance_order: deque[int] = deque(maxlen=MAX_TRACKED_UTTERANCE_IDS)
        # Highest utterance id ever recorded.  A resumed conversation must
        # report it, otherwise the pipeline restarts its own counter at 1 and
        # every new utterance collides with a loaded one and is dropped as a
        # duplicate — silently, because dedupe looks like a healthy path.
        self._latest_utterance_id = 0
        self._lock = threading.Lock()

    # ------------------------------------------------------------- lifecycle
    def close(self) -> None:
        if self._lease is not None:
            self._lease.close()
            self._lease = None

    def next_utterance_id(self) -> int:
        """First utterance id that cannot collide with anything already stored."""
        with self._lock:
            return self._latest_utterance_id + 1

    def new_turn_id(self) -> int:
        with self._lock:
            turn = self._next_turn
            self._next_turn += 1
        return turn

    def _append(self, message: Message) -> Message:
        with self._lock:
            self.messages.append(message)
            overflow = len(self.messages) - self.max_stored_messages
            if overflow > 0:
                del self.messages[:overflow]
        self.store.append(self.session_id, {"kind": "message", **message.as_dict()})
        return message

    def _track_utterance(self, utterance_id: int) -> None:
        """Remember an utterance id, evicting the oldest beyond the window."""
        self._utterance_ids.add(utterance_id)
        self._utterance_order.append(utterance_id)
        if len(self._utterance_ids) != len(self._utterance_order):
            # The bounded deque evicted its oldest entry: rebuild the lookup set
            # so the dedupe window cannot grow without limit.
            self._utterance_ids = set(self._utterance_order)

    def _new_id(self) -> int:
        with self._lock:
            identifier = self._next_id
            self._next_id += 1
        return identifier

    def add_user(
        self,
        text: str,
        *,
        utterance_id: int | None = None,
        turn_id: int | None = None,
    ) -> Message | None:
        """Record a user message once per ``utterance_id`` (returns ``None`` if duplicate)."""
        content = (text or "").strip()
        if not content:
            return None
        if utterance_id is not None:
            with self._lock:
                if utterance_id in self._utterance_ids:
                    return None
                self._track_utterance(utterance_id)
                self._latest_utterance_id = max(self._latest_utterance_id, utterance_id)
        message = Message(
            id=self._new_id(),
            role=ROLE_USER,
            content=content,
            utterance_id=utterance_id,
            turn_id=turn_id,
            created_at=self._clock(),
        )
        return self._append(message)

    def finish_assistant(
        self,
        text: str,
        *,
        status: str = STATUS_COMPLETE,
        turn_id: int | None = None,
        utterance_id: int | None = None,
    ) -> Message | None:
        """Commit an assistant reply with an explicit lifecycle status."""
        content = (text or "").strip()
        if status not in STATUSES:
            raise ValueError(f"invalid status: {status!r}")
        if not content:
            return None
        if turn_id is not None:
            with self._lock:
                if any(
                    message.role == ROLE_ASSISTANT and message.turn_id == turn_id
                    for message in self.messages
                ):
                    return None
        message = Message(
            id=self._new_id(),
            role=ROLE_ASSISTANT,
            content=content,
            status=status,
            turn_id=turn_id,
            utterance_id=utterance_id,
            created_at=self._clock(),
        )
        return self._append(message)

    def record_turn(self, user_text: str, assistant_text: str, **kwargs: Any) -> tuple[Any, Any]:
        """Convenience for the non-streaming API path."""
        return (
            self.add_user(user_text, **{k: v for k, v in kwargs.items() if k == "turn_id"}),
            self.finish_assistant(assistant_text, **kwargs),
        )

    # --------------------------------------------------------------- context
    def eligible(self) -> Iterator[Message]:
        for message in self.messages:
            if message.role == ROLE_USER:
                yield message
            elif message.status == STATUS_COMPLETE or self.include_interrupted:
                yield message

    def context(self) -> list[dict[str, str]]:
        """Token-aware, newest-last message list for the LLM (system excluded)."""
        candidates = list(self.eligible())[-self.max_messages :]
        selected: list[Message] = []
        used = 0
        for message in reversed(candidates):
            cost = message.tokens
            if selected and used + cost > self.token_budget:
                break
            selected.append(message)
            used += cost
        selected.reverse()
        # Chat templates expect the conversation to start with the user.
        while selected and selected[0].role != ROLE_USER:
            selected.pop(0)
        if not selected:
            # Never send an empty prompt: the newest user turn matters more than
            # a strict budget when a single message exceeds it.
            fallback = next(
                (message for message in reversed(candidates) if message.role == ROLE_USER),
                None,
            )
            if fallback is not None:
                selected = [fallback]
        return [{"role": message.role, "content": message.content} for message in selected]

    def context_with_system(self, system_prompt: str | None) -> list[dict[str, str]]:
        messages = self.context()
        if system_prompt:
            return [{"role": "system", "content": system_prompt}, *messages]
        return messages

    @property
    def tokens(self) -> int:
        return sum(message.tokens for message in self.eligible())

    def last_user(self) -> Message | None:
        for message in reversed(self.messages):
            if message.role == ROLE_USER:
                return message
        return None

    def clear(self) -> None:
        with self._lock:
            self.messages.clear()
            self._utterance_ids.clear()
            self._utterance_order.clear()

    # ------------------------------------------------------------ persistence
    def summary(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "conversation_id": self.conversation_id,
            "messages": len(self.messages),
            "tokens_estimated": self.tokens,
            "persisted": bool(self.store.enabled),
            "store_error": getattr(self.store, "error", None),
            "last_message_at": self.messages[-1].created_at if self.messages else None,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.summary(),
            "messages": [message.as_dict() for message in self.messages],
        }

    @classmethod
    def load(
        cls,
        store: ConversationStore | NullStore,
        session_id: str,
        *,
        conversation_id: str | None = None,
        **options: Any,
    ) -> "Conversation":
        conversation = cls(
            session_id=session_id,
            conversation_id=conversation_id,
            store=store,
            **options,
        )
        for record in store.load(session_id):
            if record.get("kind") != "message":
                continue
            try:
                message = Message.from_dict(record)
            except (KeyError, ValueError, TypeError):
                continue
            conversation.messages.append(message)
            conversation._next_id = max(conversation._next_id, message.id + 1)
            if message.utterance_id is not None:
                conversation._track_utterance(message.utterance_id)
                conversation._latest_utterance_id = max(
                    conversation._latest_utterance_id, message.utterance_id
                )
            if message.turn_id is not None:
                conversation._next_turn = max(conversation._next_turn, message.turn_id + 1)
        if len(conversation.messages) > conversation.max_stored_messages:
            conversation.messages = conversation.messages[-conversation.max_stored_messages :]
        return conversation


def load_conversations(store: ConversationStore | NullStore) -> Iterable[dict[str, Any]]:
    """Session summaries for the inspection CLI and the API."""
    return store.sessions()


def resume_target(
    settings: Any,
    store: ConversationStore | NullStore,
    session_id: str | None,
    resume: bool | None = None,
    *, local: bool = False,
) -> str | None:
    """Which session id should be *loaded* (``None`` = start empty).

    * An explicit ``session_id`` always resumes that session — that is what a
      device asking for ``session_id`` in ``hello`` and an API caller passing
      ``session_id`` both mean.
    * Without one, the local live loop resumes only when
      ``PVAI_HISTORY_RESUME`` is on (the default): the session named by
      ``PVAI_VOICE_SESSION_ID`` if set, otherwise the most recently written one.
      This is what makes a restart continue the conversation instead of
      silently starting over (``docs/STATUS.md`` requirement 17).
    * ``PVAI_HISTORY_RESUME=off`` keeps the old behaviour: every process start
      is a fresh session, and only explicit ids resume.
    * ``resume=False`` disables the implicit default for one caller.  The LAN
      transport needs this: a device that sends no ``session_id`` must get its
      own new session, never the file another device (or the local loop) was
      last writing to.
    """
    if local and session_id is not None and not session_id.startswith("pc-"):
        session_id = "pc-" + session_id
    if session_id is not None:
        return session_id
    if resume is False:
        return None
    if not isinstance(store, ConversationStore) or not store.enabled:
        return None
    if resume is None and str(getattr(settings, "history_resume", "on")).lower() == "off":
        return None
    explicit = str(getattr(settings, "voice_session_id", "") or "").strip()
    if explicit:
        return (explicit if explicit.startswith("pc-") else "pc-" + explicit) if local else explicit
    return store.latest_session(prefix="pc-" if local else "")


def create_conversation(
    settings: Any, *, session_id: str | None = None, resume: bool | None = None,
    local: bool = False, exclusive: bool = False,
) -> Conversation:
    """Build a conversation honouring the configured limits and persistence.

    Persistence is opt-in through ``PVAI_HISTORY_PERSIST`` (default on) and is
    strictly local: one JSONL file per session under ``PVAI_DATA_DIR``. Nothing
    is summarised into long-term personal memory and nothing leaves the machine.

    A conversation that is not resumed must never be *built on top of* existing
    records: message ids, ``turn_id`` and ``utterance_id`` all restart at 1, so
    appending to the old file produced duplicate keys and an empty dedupe set.
    Therefore any session that will be written to is loaded through
    :meth:`Conversation.load` (see :func:`resume_target` for which one).
    """
    store = open_store(settings)
    options = {
        "max_messages": max(2, int(getattr(settings, "history_max_messages", 24))),
        "token_budget": max(64, int(getattr(settings, "history_token_budget", 1536))),
        "max_stored_messages": max(
            8, int(getattr(settings, "history_max_stored_messages", 500))
        ),
        "include_interrupted": bool(getattr(settings, "history_include_interrupted", False)),
    }
    target = resume_target(settings, store, session_id, resume, local=local)
    chosen = target
    if chosen is None:
        chosen = ("pc-" if local else "s-") + uuid.uuid4().hex[:12]
    lease = store.claim(chosen) if exclusive and isinstance(store, ConversationStore) else None
    try:
        conversation = (Conversation.load(store, chosen, **options) if target is not None
                        else Conversation(session_id=chosen, store=store, **options))
        conversation._lease = lease
        return conversation
    except BaseException:
        if lease is not None:
            lease.close()
        raise


def purge_expired(settings: Any, store: ConversationStore | NullStore | None = None) -> list[str]:
    """Apply ``PVAI_HISTORY_RETENTION_DAYS``; returns the removed session ids."""
    resolved = store if store is not None else open_store(settings)
    if not isinstance(resolved, ConversationStore):
        return []
    days = int(getattr(settings, "history_retention_days", 0) or 0)
    if days <= 0:
        return []
    return resolved.purge_older_than(days)
