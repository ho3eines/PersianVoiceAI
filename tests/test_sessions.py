"""Local session inspection/deletion CLI (privacy: inspect and erase)."""

from __future__ import annotations

from pathlib import Path

import pytest

from server import sessions
from server.config import Settings
from server.conversation import create_conversation


@pytest.fixture()
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PVAI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PVAI_HISTORY_PERSIST", "true")
    return tmp_path


def seed(tmp_path: Path) -> str:
    settings = Settings(data_dir=str(tmp_path), history_persist=True)
    conversation = create_conversation(settings, session_id="s-demo")
    conversation.add_user("سلام", utterance_id=1)
    conversation.finish_assistant("درود بر شما", turn_id=1)
    return conversation.session_id


def test_list_and_show(data_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    seed(data_dir)
    assert sessions.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "s-demo" in out and "messages=2" in out

    assert sessions.main(["show", "s-demo"]) == 0
    out = capsys.readouterr().out
    assert "شما: سلام" in out
    assert "دستیار: درود بر شما" in out


def test_show_marks_interrupted_replies(data_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    seed(data_dir)
    settings = Settings(data_dir=str(data_dir), history_persist=True)
    conversation = create_conversation(settings, session_id="s-cut")
    conversation.add_user("سلام")
    conversation.finish_assistant("نیمه‌کاره", status="interrupted", turn_id=1)
    assert sessions.main(["show", "s-cut"]) == 0
    assert "[interrupted]" in capsys.readouterr().out


def test_show_missing_session_fails(data_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert sessions.main(["show", "s-absent"]) == 1
    assert "پیدا نشد" in capsys.readouterr().err


def test_delete_and_delete_all(data_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    seed(data_dir)
    assert sessions.main(["delete", "s-demo"]) == 0
    assert capsys.readouterr().out.strip() == "حذف شد."
    assert sessions.main(["delete", "s-demo"]) == 1
    seed(data_dir)
    assert sessions.main(["delete-all"]) == 0
    assert "1" in capsys.readouterr().out


def test_disabled_persistence_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                          capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("PVAI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PVAI_HISTORY_PERSIST", "false")
    assert sessions.main(["list"]) == 1
    assert "خاموش" in capsys.readouterr().err
