"""Windows device names must be refused with *any* extension.

``CON.jsonl`` cannot be created on Windows — the name is reserved by the
filesystem, extension or not (``CON``, ``PRN``, ``AUX``, ``NUL``, ``COM1..9``,
``LPT1..9``; Windows also ignores trailing dots and spaces, so ``con.`` and
``CON `` are the same device).

``str.strip()`` and the ASCII alphabet already stopped the obvious cases, but a
reserved name carrying an extension was rejected for the *wrong* reason — the
alphabet error — or, for a dotted name the store derives itself, not at all.
The guard has to look at the stem, so the answer is the same whether the id
arrives as ``CON``, ``con``, ``CON.jsonl`` or ``con.txt``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from server.conversation import ConversationStore, is_reserved_device_name

RESERVED = ["CON", "PRN", "AUX", "NUL"] + [
    f"{prefix}{index}" for prefix in ("COM", "LPT") for index in range(1, 10)
]

EXTENSIONS = ["", ".jsonl", ".txt", ".lock", ".tar.gz", ".", " ", ".jsonl.tmp"]


@pytest.mark.parametrize("name", RESERVED)
def test_every_reserved_name_is_recognised(name: str) -> None:
    assert is_reserved_device_name(name)
    assert is_reserved_device_name(name.lower())
    assert is_reserved_device_name(name.capitalize())


@pytest.mark.parametrize("name", RESERVED)
@pytest.mark.parametrize("extension", EXTENSIONS)
def test_a_reserved_name_is_rejected_with_any_extension(name: str, extension: str) -> None:
    with pytest.raises(ValueError, match="reserved"):
        ConversationStore.sanitize(f"{name}{extension}")


@pytest.mark.parametrize("name", ["console", "connection", "COM10", "LPT0", "auxiliary"])
def test_lookalike_names_are_still_allowed(name: str) -> None:
    """Only the exact device stems are reserved; COM10 is a normal file."""
    assert not is_reserved_device_name(name)
    assert ConversationStore.sanitize(name) == name


def test_a_reserved_session_id_cannot_create_or_claim_a_file(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    for name in ("CON", "nul.jsonl", "com1.txt"):
        with pytest.raises(ValueError, match="reserved"):
            store.path(name)
        with pytest.raises(ValueError, match="reserved"):
            store.claim(name)
        assert store.append(name, {"kind": "message", "id": 1}) is False
        assert not list(tmp_path.glob(f"{name.split('.')[0]}*"))


def test_the_lock_path_of_a_reserved_name_is_never_written(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    with pytest.raises(ValueError, match="reserved"):
        store.claim("LPT9")
    assert list(tmp_path.iterdir()) == []
