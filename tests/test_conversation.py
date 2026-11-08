"""Conversation memory: lifecycle statuses, dedupe, token budget, local persistence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.config import Settings
from server.conversation import (
    Conversation,
    ConversationStore,
    Message,
    NullStore,
    STATUS_COMPLETE,
    STATUS_FAILED,
    STATUS_INTERRUPTED,
    create_conversation,
    estimate_tokens,
    open_store,
)


def test_user_message_is_deduplicated_by_utterance_id() -> None:
    conversation = Conversation()
    assert conversation.add_user("سلام", utterance_id=5) is not None
    assert conversation.add_user("سلام", utterance_id=5) is None
    assert conversation.add_user("سلام", utterance_id=6) is not None
    assert len(conversation.messages) == 2


def test_empty_text_is_never_recorded() -> None:
    conversation = Conversation()
    assert conversation.add_user("   ") is None
    assert conversation.finish_assistant("") is None
    assert conversation.messages == []


def test_interrupted_reply_has_explicit_status_and_is_excluded_by_default() -> None:
    conversation = Conversation()
    conversation.add_user("سلام")
    message = conversation.finish_assistant(
        "پاسخ نیمه", status=STATUS_INTERRUPTED, turn_id=1
    )
    assert message is not None and message.status == STATUS_INTERRUPTED
    assert [item["content"] for item in conversation.context()] == ["سلام"]
    including = Conversation(include_interrupted=True)
    including.add_user("سلام")
    including.finish_assistant("پاسخ نیمه", status=STATUS_INTERRUPTED, turn_id=1)
    assert [item["content"] for item in including.context()] == ["سلام", "پاسخ نیمه"]


def test_assistant_turn_is_committed_once() -> None:
    conversation = Conversation()
    conversation.add_user("سلام")
    assert conversation.finish_assistant("یک", turn_id=3) is not None
    assert conversation.finish_assistant("یک", turn_id=3) is None
    assert len([m for m in conversation.messages if m.role == "assistant"]) == 1


def test_failed_reply_can_be_recorded_without_entering_context() -> None:
    conversation = Conversation()
    conversation.add_user("سلام")
    conversation.finish_assistant("خطا", status=STATUS_FAILED, turn_id=2)
    assert [item["content"] for item in conversation.context()] == ["سلام"]
    assert conversation.messages[-1].status == STATUS_FAILED


def test_context_is_truncated_by_tokens_not_only_message_count() -> None:
    conversation = Conversation(max_messages=50, token_budget=400)
    for index in range(10):
        conversation.add_user("پیام " + ("الف" * 40) + str(index))
        conversation.finish_assistant("پاسخ " + ("ب" * 40) + str(index))
    context = conversation.context()
    assert len(context) < 20
    assert sum(estimate_tokens(item["content"]) + 4 for item in context) <= 400
    # The newest exchange always survives; the oldest is what gets dropped.
    assert context[-1]["content"].endswith("9")


def test_context_keeps_the_prompt_even_when_it_exceeds_the_budget() -> None:
    conversation = Conversation(token_budget=64)
    conversation.add_user("قدیمی")
    conversation.finish_assistant("پاسخ قدیمی")
    conversation.add_user("پرسش تازه " + ("م" * 300))
    context = conversation.context()
    assert len(context) == 1
    assert context[0]["role"] == "user"
    assert context[0]["content"].startswith("پرسش تازه")


def test_context_never_starts_with_an_assistant_message() -> None:
    conversation = Conversation(max_messages=2)
    conversation.add_user("قدیمی")
    conversation.finish_assistant("پاسخ قدیمی")
    conversation.add_user("تازه")
    conversation.finish_assistant("پاسخ تازه")
    context = conversation.context()
    assert context[0]["role"] == "user"


def test_context_respects_the_message_cap() -> None:
    conversation = Conversation(max_messages=4)
    for index in range(10):
        conversation.add_user(f"پیام {index}")
        conversation.finish_assistant(f"پاسخ {index}")
    assert len(conversation.context()) <= 4


def test_token_estimate_is_conservative_for_persian() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("سلام") == 2  # four Persian characters → ~2 tokens
    assert estimate_tokens("hello world") >= 2


def test_system_prompt_prepend() -> None:
    conversation = Conversation()
    conversation.add_user("سلام")
    messages = conversation.context_with_system("شما یک دستیار هستید")
    assert messages[0] == {"role": "system", "content": "شما یک دستیار هستید"}
    assert messages[1]["role"] == "user"


def test_invalid_message_role_or_status_is_rejected() -> None:
    with pytest.raises(ValueError):
        Message(id=1, role="system", content="x")
    with pytest.raises(ValueError):
        Message(id=1, role="user", content="x", status="unknown")


# ------------------------------------------------------------------ persistence
def test_session_is_persisted_and_reloaded(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / "conversations")
    conversation = Conversation(store=store, session_id="s-test")
    conversation.add_user("سلام", utterance_id=1)
    conversation.finish_assistant("درود", turn_id=1)

    restored = Conversation.load(store, "s-test")
    assert [m.content for m in restored.messages] == ["سلام", "درود"]
    assert restored.messages[0].utterance_id == 1
    assert restored.messages[1].status == STATUS_COMPLETE
    # Ids continue instead of colliding with the restored messages.
    new_message = restored.add_user("دوباره", utterance_id=2)
    assert new_message is not None and new_message.id == 3
    assert restored.add_user("دوباره", utterance_id=2) is None


def test_store_lists_and_deletes_sessions(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / "conversations")
    for session in ("s-a", "s-b"):
        conversation = Conversation(store=store, session_id=session)
        conversation.add_user("سلام")
    sessions = {entry["session_id"]: entry for entry in store.sessions()}
    assert set(sessions) == {"s-a", "s-b"}
    assert sessions["s-a"]["messages"] == 1
    assert store.delete("s-a") is True
    assert store.delete("s-a") is False
    assert [entry["session_id"] for entry in store.sessions()] == ["s-b"]
    assert store.delete_all() == 1


def test_store_rejects_path_traversal(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / "conversations")
    for identifier in ("../../etc/passwd", "../nope", "s/../../x", "", "a" * 65):
        with pytest.raises(ValueError):
            store.path(identifier)
        assert store.append(identifier, {"kind": "message"}) is False
        assert store.load(identifier) == []
        assert store.delete(identifier) is False
    assert store.path("s-safe_1") == (tmp_path / "conversations" / "s-safe_1.jsonl").resolve()


def test_store_tolerates_torn_lines(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / "conversations")
    conversation = Conversation(store=store, session_id="s-t")
    conversation.add_user("اول")
    path = store.path("s-t")
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"kind": "message", "id": 99')  # torn write
    restored = Conversation.load(store, "s-t")
    assert [m.content for m in restored.messages] == ["اول"]


def test_persistence_can_be_disabled(tmp_path: Path) -> None:
    settings = Settings(history_persist=False, data_dir=str(tmp_path))
    store = open_store(settings)
    assert isinstance(store, NullStore)
    conversation = create_conversation(settings)
    conversation.add_user("سلام")
    assert conversation.summary()["persisted"] is False
    assert list(tmp_path.iterdir()) == []


def test_create_conversation_uses_configured_limits(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=str(tmp_path),
        history_persist=True,
        history_max_messages=6,
        history_token_budget=256,
        history_include_interrupted=True,
    )
    conversation = create_conversation(settings)
    assert conversation.max_messages == 6
    assert conversation.token_budget == 256
    assert conversation.include_interrupted is True
    conversation.add_user("سلام")
    files = list((tmp_path / "conversations").glob("*.jsonl"))
    assert files  # the first write created the directory and the file
    record = json.loads(files[0].read_text(encoding="utf-8").splitlines()[0])
    assert record["kind"] == "message" and record["role"] == "user"


# ------------------------------------------------------- bounded RAM (24/7 use)
def test_in_memory_window_is_bounded_while_the_disk_log_stays_complete(
    tmp_path: Path,
) -> None:
    """A long conversation must not grow RAM without limit (requirement 20).

    The JSONL log is append-only by design (``/api/sessions`` still shows the
    whole session), but the in-memory list is capped so an always-on assistant
    cannot leak memory across hours of speech.
    """
    store = ConversationStore(tmp_path / "conversations")
    conversation = Conversation(store=store, session_id="s-long", max_stored_messages=10)
    for index in range(50):
        conversation.add_user(f"پیام {index}", utterance_id=index + 1)
    assert len(conversation.messages) == 10
    assert conversation.messages[-1].content == "پیام 49"

    on_disk = store.load("s-long")
    assert len(on_disk) == 50  # nothing was lost for inspection
    assert on_disk[0]["content"] == "پیام 0"

    restored = Conversation.load(store, "s-long", max_stored_messages=10)
    assert len(restored.messages) == 10
    assert restored.messages[0].content == "پیام 40"
    # Ids stay monotonic after a trimmed reload, so turn tracking still works.
    assert restored.add_user("تازه", utterance_id=999) is not None
    assert restored.messages[-1].id > 50


def test_utterance_dedupe_window_is_bounded() -> None:
    from server.conversation import MAX_TRACKED_UTTERANCE_IDS

    conversation = Conversation()
    for index in range(MAX_TRACKED_UTTERANCE_IDS + 20):
        conversation.add_user(f"متن {index}", utterance_id=index + 1)
    assert len(conversation._utterance_ids) <= MAX_TRACKED_UTTERANCE_IDS
    # The most recent ids are still deduplicated.
    latest = MAX_TRACKED_UTTERANCE_IDS + 20
    assert conversation.add_user("تکرار", utterance_id=latest) is None
