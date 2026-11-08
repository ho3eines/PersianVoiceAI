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

    def delete(self, session_id: str) -> bool:
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
        # The directory is created lazily on the first write: constructing the
        # store (readiness, API startup, tests) must not touch the disk.

    @staticmethod
    def sanitize(session_id: str) -> str:
        """Validate a session identifier (filesystem-safe, traversal-free).

        Identifiers are **rejected** rather than silently rewritten: rewriting
        could map two different requested sessions onto the same file.
        """
        raw = str(session_id).strip()
        cleaned = "".join(
            char for char in raw if char.isalnum() or char in {"-", "_"}
        )
        if not cleaned or cleaned != raw:
            raise ValueError(
                "session id may only contain letters, digits, '-' and '_'"
            )
        if len(cleaned) > 64:
            raise ValueError("session id must be at most 64 characters")
        return cleaned

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

    def sessions(self) -> list[dict[str, Any]]:
        if not self.root.is_dir():
            return []
        found: list[dict[str, Any]] = []
        for path in sorted(self.root.glob("*.jsonl")):
            try:
                stat = path.stat()
                records = self.load(path.stem)
            except OSError:
                continue
            messages = [record for record in records if record.get("kind") == "message"]
            found.append(
                {
                    "session_id": path.stem,
                    "messages": len(messages),
                    "updated_at": stat.st_mtime,
                    "bytes": stat.st_size,
                }
            )
        return found

    def delete(self, session_id: str) -> bool:
        try:
            path = self.path(session_id)
        except ValueError:
            return False
        try:
            path.unlink()
            return True
        except FileNotFoundError:
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
        self._clock = clock
        self.messages: list[Message] = []
        self._next_id = 1
        self._next_turn = 1
        self._utterance_ids: set[int] = set()
        self._utterance_order: deque[int] = deque(maxlen=MAX_TRACKED_UTTERANCE_IDS)
        self._lock = threading.Lock()

    # ------------------------------------------------------------- lifecycle
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
            if message.turn_id is not None:
                conversation._next_turn = max(conversation._next_turn, message.turn_id + 1)
        if len(conversation.messages) > conversation.max_stored_messages:
            conversation.messages = conversation.messages[-conversation.max_stored_messages :]
        return conversation


def load_conversations(store: ConversationStore | NullStore) -> Iterable[dict[str, Any]]:
    """Session summaries for the inspection CLI and the API."""
    return store.sessions()


def create_conversation(settings: Any, *, session_id: str | None = None) -> Conversation:
    """Build a conversation honouring the configured limits and persistence.

    Persistence is opt-in through ``PVAI_HISTORY_PERSIST`` (default on) and is
    strictly local: one JSONL file per session under ``PVAI_DATA_DIR``. Nothing
    is summarised into long-term personal memory and nothing leaves the machine.
    """
    store = open_store(settings)
    return Conversation(
        session_id=session_id,
        max_messages=max(2, int(getattr(settings, "history_max_messages", 24))),
        token_budget=max(64, int(getattr(settings, "history_token_budget", 1536))),
        max_stored_messages=max(
            8, int(getattr(settings, "history_max_stored_messages", 500))
        ),
        include_interrupted=bool(getattr(settings, "history_include_interrupted", False)),
        store=store,
    )
