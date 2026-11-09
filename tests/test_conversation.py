"""Conversation memory: lifecycle statuses, dedupe, token budget, local persistence."""

from __future__ import annotations

import json
import time
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


# ------------------------------------------------------------------- resume
def test_create_conversation_resumes_an_explicit_session(tmp_path: Path) -> None:
    """The bug: a rebuilt conversation silently started empty on top of a file.

    Rebuilding through ``create_conversation`` (what the API and the WebSocket
    transport both call) must return the stored messages, keep ids monotonic and
    include the previous turn in the model context — otherwise a restart erases
    the conversation while still appending duplicate ids to the same file.
    """
    settings = Settings(data_dir=str(tmp_path), history_persist=True)
    first = create_conversation(settings, session_id="demo")
    first.add_user("سلامِ قبلی", utterance_id=1, turn_id=1)
    first.finish_assistant("درودِ قبلی", turn_id=1, utterance_id=1)
    first.add_user("پرسش دوم", utterance_id=2, turn_id=2)
    first.finish_assistant("پاسخ دوم", turn_id=2, utterance_id=2)

    second = create_conversation(settings, session_id="demo")
    assert [message.content for message in second.messages] == [
        "سلامِ قبلی",
        "درودِ قبلی",
        "پرسش دوم",
        "پاسخ دوم",
    ]
    # Context survives the restart: the model sees the earlier exchange.
    assert [item["content"] for item in second.context()][:2] == [
        "سلامِ قبلی",
        "درودِ قبلی",
    ]
    # Ids continue from the stored maximum instead of restarting at 1.
    message = second.add_user("سلامِ جدید", utterance_id=3)
    assert message is not None and message.id == 5
    assert second.new_turn_id() == 3

    records = [
        json.loads(line)
        for line in (tmp_path / "conversations" / "demo.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    ids = [record["id"] for record in records]
    turn_ids = [record["turn_id"] for record in records if record["turn_id"] is not None]
    assert ids == sorted(set(ids)), ids  # no duplicate ids in the log
    # A turn id is shared by its user and assistant message, so each value
    # appears exactly twice (never three times, which is what a restart that
    # forgot ``_next_turn`` produced).
    assert [turn_ids.count(value) for value in set(turn_ids)] == [2, 2], turn_ids
    assert second.next_utterance_id() == 4


def test_resumed_conversation_deduplicates_stored_utterances(tmp_path: Path) -> None:
    """A replayed final ASR result must still be refused after a restart."""
    settings = Settings(data_dir=str(tmp_path), history_persist=True)
    first = create_conversation(settings, session_id="demo")
    first.add_user("سلام", utterance_id=7)

    second = create_conversation(settings, session_id="demo")
    assert second.add_user("سلام دوباره", utterance_id=7) is None
    assert [message.content for message in second.messages] == ["سلام"]


def test_resume_defaults_to_the_last_session_for_the_local_loop(tmp_path: Path) -> None:
    """A restart of the live loop continues instead of starting over."""
    settings = Settings(data_dir=str(tmp_path), history_persist=True)
    first = create_conversation(settings)
    first.add_user("اول", utterance_id=1)
    first.finish_assistant("پاسخ", turn_id=1)

    resumed = create_conversation(settings)
    assert resumed.session_id == first.session_id
    assert [message.content for message in resumed.messages] == ["اول", "پاسخ"]


def test_history_resume_off_keeps_every_start_fresh(tmp_path: Path) -> None:
    settings = Settings(data_dir=str(tmp_path), history_persist=True, history_resume="off")
    first = create_conversation(settings)
    first.add_user("اول", utterance_id=1)

    fresh = create_conversation(settings)
    assert fresh.session_id != first.session_id
    assert fresh.messages == []
    # An explicit id still resumes: only the implicit default changed.
    explicit = create_conversation(settings, session_id=first.session_id)
    assert [message.content for message in explicit.messages] == ["اول"]


def test_voice_session_id_pins_the_local_session(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=str(tmp_path), history_persist=True, voice_session_id="pc-voice"
    )
    first = create_conversation(settings)
    assert first.session_id == "pc-voice"
    first.add_user("سلام", utterance_id=1)
    assert [m.content for m in create_conversation(settings).messages] == ["سلام"]


def test_resume_with_persistence_off_starts_fresh(tmp_path: Path) -> None:
    settings = Settings(data_dir=str(tmp_path), history_persist=False)
    first = create_conversation(settings)
    assert first.messages == []
    assert first.store.enabled is False


# ----------------------------------------------------------------- sanitize
def test_session_id_rejects_non_ascii_letters_and_digits(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    for value in ("café", "sess-١٢٣", "s-۱", "naïve"):
        with pytest.raises(ValueError):
            store.sanitize(value)


def test_session_id_rejects_windows_reserved_device_names(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    for value in ("CON", "con", "NUL", "aux", "COM1", "com9", "LPT1", "clock$"):
        with pytest.raises(ValueError):
            store.sanitize(value)
    # A longer name that merely starts with a device name is fine.
    assert store.sanitize("console-1") == "console-1"
    assert store.sanitize("com10") == "com10"


def test_session_id_keeps_the_documented_alphabet(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    assert store.sanitize(" s-Demo_1 ") == "s-Demo_1"
    for value in ("../etc/passwd", "a b", "a/b", "a.b", ""):
        with pytest.raises(ValueError):
            store.sanitize(value)


def test_case_insensitive_collision_is_refused_instead_of_sharing_a_file(
    tmp_path: Path,
) -> None:
    """On Windows ``S-DEMO.jsonl`` and ``s-demo.jsonl`` are one file.

    Silently appending there would interleave two sessions (and break id
    uniqueness), so the second id is refused at write time.
    """
    store = ConversationStore(tmp_path)
    assert store.append("s-demo", {"kind": "message", "id": 1, "role": "user", "content": "a"})
    assert store.append("S-DEMO", {"kind": "message", "id": 1, "role": "user", "content": "b"}) is False
    assert "differs only by case" in (store.error or "")
    assert len(store.load("s-demo")) == 1
    # The original id can keep appending normally.
    assert store.append("s-demo", {"kind": "message", "id": 2, "role": "user", "content": "c"})


# ------------------------------------------------------- listing and retention
def test_session_listing_counts_messages_without_parsing_the_whole_file(
    tmp_path: Path,
) -> None:
    store = ConversationStore(tmp_path)
    conversation = Conversation(store=store, session_id="s-count")
    for index in range(25):
        conversation.add_user(f"پیام {index}", utterance_id=index + 1)
    # A torn tail line (a crash mid-write) must not break the listing.
    with (tmp_path / "s-count.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"kind": "message", "id": 26, "role": "us')

    entries = store.sessions()
    assert len(entries) == 1
    assert entries[0]["session_id"] == "s-count"
    assert entries[0]["messages"] == 25
    assert store.count_messages("s-count") == 25


def test_latest_session_follows_the_write_time(tmp_path: Path) -> None:
    import os

    store = ConversationStore(tmp_path)
    assert store.latest_session() is None
    older = Conversation(store=store, session_id="older")
    older.add_user("قدیمی")
    newer = Conversation(store=store, session_id="newer")
    newer.add_user("تازه")
    # Two writes in the same test can share an mtime tick; make the order explicit.
    stale = time.time() - 60
    os.utime(tmp_path / "older.jsonl", (stale, stale))
    assert store.latest_session() == "newer"


def _purge_expired(*args, **kwargs):
    # Imported lazily so the module still collects against older code: the
    # retention tests must *fail* on a missing implementation, not error out
    # during collection and hide whether the rest of the suite runs.
    from server.conversation import purge_expired

    return purge_expired(*args, **kwargs)


def test_retention_purges_only_sessions_older_than_the_limit(tmp_path: Path) -> None:
    import os

    settings = Settings(data_dir=str(tmp_path), history_persist=True, history_retention_days=7)
    old = create_conversation(settings, session_id="old")
    old.add_user("قدیمی", utterance_id=1)
    fresh = create_conversation(settings, session_id="fresh")
    fresh.add_user("تازه", utterance_id=1)

    old_path = tmp_path / "conversations" / "old.jsonl"
    stale = time.time() - 10 * 86400
    os.utime(old_path, (stale, stale))

    removed = _purge_expired(settings)
    assert removed == ["old"]
    assert not old_path.exists()
    assert (tmp_path / "conversations" / "fresh.jsonl").exists()


def test_retention_zero_keeps_everything_and_disabled_persistence_is_a_no_op(
    tmp_path: Path,
) -> None:
    import os

    keep = Settings(data_dir=str(tmp_path), history_persist=True, history_retention_days=0)
    conversation = create_conversation(keep, session_id="keep")
    conversation.add_user("سلام", utterance_id=1)
    path = tmp_path / "conversations" / "keep.jsonl"
    stale = time.time() - 365 * 86400
    os.utime(path, (stale, stale))
    assert _purge_expired(keep) == []
    assert path.exists()

    disabled = Settings(data_dir=str(tmp_path), history_persist=False, history_retention_days=7)
    assert _purge_expired(disabled) == []
    assert path.exists()


def test_settings_reject_an_invalid_voice_session_id() -> None:
    assert Settings(voice_session_id="CON").config_errors()
    assert Settings(voice_session_id="پین").config_errors()
    assert not Settings(voice_session_id="pc-voice").config_errors()
    assert not Settings(voice_session_id="").config_errors()


def test_env_history_resume_and_retention_are_parsed(monkeypatch) -> None:
    monkeypatch.setenv("PVAI_HISTORY_RESUME", "OFF")
    monkeypatch.setenv("PVAI_VOICE_SESSION_ID", "pc-voice")
    monkeypatch.setenv("PVAI_HISTORY_RETENTION_DAYS", "30")
    settings = Settings.from_env()
    assert settings.history_resume == "off"
    assert settings.voice_session_id == "pc-voice"
    assert settings.history_retention_days == 30

    # A typo falls back to the documented default instead of a surprise mode.
    monkeypatch.setenv("PVAI_HISTORY_RESUME", "maybe")
    assert Settings.from_env().history_resume == "on"


def test_live_loop_applies_retention_before_starting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``run_live`` must age transcripts out even if the API never starts.

    A PC that only runs the microphone loop still writes plain-text speech to
    disk; retention cannot be tied to the web app's startup.
    """
    import asyncio
    import os

    from server import streaming_voice

    settings = Settings(
        data_dir=str(tmp_path),
        history_persist=True,
        history_retention_days=7,
        asr_model_path=str(tmp_path / "missing-model"),  # fails right after purge
    )
    old = create_conversation(settings, session_id="ancient")
    old.add_user("قدیمی", utterance_id=1)
    path = tmp_path / "conversations" / "ancient.jsonl"
    stale = time.time() - 30 * 86400
    os.utime(path, (stale, stale))

    with pytest.raises(streaming_voice.VoiceError):
        # Model validation fails next; retention must run before it.
        asyncio.run(streaming_voice.run_live(settings))

    assert not path.exists()
    assert "1 نشست قدیمی‌تر از 7 روز حذف شد." in capsys.readouterr().out


def test_session_listing_reuses_lightweight_message_count_cache(tmp_path, monkeypatch):
    store = ConversationStore(tmp_path)
    store.append('a', {'kind': 'message', 'id': 1, 'role': 'user', 'content': 'x'})
    original = store._count_messages
    calls = []
    def counted(path):
        calls.append(path)
        return original(path)
    monkeypatch.setattr(store, '_count_messages', counted)
    assert store.sessions()[0]['messages'] == 1
    assert store.sessions()[0]['messages'] == 1
    assert len(calls) == 1
    store.append('a', {'kind': 'message', 'id': 2, 'role': 'assistant', 'content': 'y'})
    assert store.sessions()[0]['messages'] == 2


def test_local_resume_never_selects_latest_websocket_session(tmp_path):
    from server.conversation import resume_target, open_store
    settings = Settings(data_dir=str(tmp_path), history_persist=True)
    store = open_store(settings)
    store.append('pc-local', {'kind': 'message', 'id': 1, 'role': 'user', 'content': 'local'})
    store.append('ws-private', {'kind': 'message', 'id': 1, 'role': 'user', 'content': 'private'})
    assert resume_target(settings, store, None, local=True) == 'pc-local'
    assert store.latest_session(prefix='pc-') == 'pc-local'
    assert create_conversation(settings, local=True).session_id == 'pc-local'


def test_fresh_local_session_has_distinct_prefix(tmp_path):
    settings = Settings(data_dir=str(tmp_path), history_persist=True, history_resume='off')
    assert create_conversation(settings, local=True).session_id.startswith('pc-')


def test_session_lease_refuses_second_thread_and_process(tmp_path):
    import subprocess
    import sys
    store = ConversationStore(tmp_path)
    lease = store.claim('shared')
    try:
        with pytest.raises(Exception, match='session_in_use'):
            store.claim('shared')
        code = ("from server.conversation import ConversationStore; "
                f"ConversationStore({str(tmp_path)!r}).claim('shared')")
        completed = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)
        assert completed.returncode != 0
        assert 'session_in_use' in completed.stderr
    finally:
        lease.close()
    with store.claim('shared'):
        pass


def test_session_lease_does_not_follow_lockfile_symlink(tmp_path):
    if __import__('os').name == 'nt':
        pytest.skip('O_NOFOLLOW is POSIX-only')
    victim = tmp_path / 'victim.txt'
    victim.write_text('keep this file')
    (tmp_path / 'unsafe.lock').symlink_to(victim)
    store = ConversationStore(tmp_path)
    with pytest.raises(OSError):
        store.claim('unsafe')
    assert victim.read_text() == 'keep this file'


def test_case_collision_is_rejected_even_when_uppercase_path_exists_on_windows(tmp_path, monkeypatch):
    store = ConversationStore(tmp_path)
    assert store.append('abc', {'kind': 'message', 'id': 1})
    original = Path.exists
    monkeypatch.setattr(Path, 'exists', lambda path: True if path.name == 'AbC.jsonl' else original(path))
    assert store.append('AbC', {'kind': 'message', 'id': 2}) is False
    assert [record['id'] for record in store.load('abc')] == [1]


def test_session_lease_is_exclusive_across_threads(tmp_path):
    import threading
    store = ConversationStore(tmp_path)
    failures = []
    with store.claim('threaded'):
        def compete():
            try:
                with ConversationStore(tmp_path).claim('threaded'):
                    failures.append('unexpected second owner')
            except SessionInUse:
                failures.append('session_in_use')
        from server.conversation import SessionInUse
        thread = threading.Thread(target=compete)
        thread.start(); thread.join(timeout=2)
        assert not thread.is_alive()
    assert failures == ['session_in_use']


def test_delete_can_surface_lock_conflict_without_shared_error_state(tmp_path):
    from server.conversation import SessionInUse
    store = ConversationStore(tmp_path)
    assert store.append('locked', {'kind': 'message', 'id': 1})
    with store.claim('locked'):
        with pytest.raises(SessionInUse):
            store.delete('locked', raise_on_locked=True)
