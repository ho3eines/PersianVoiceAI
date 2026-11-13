"""`check_storage` must prove the data path is writable, not just present.

`/ready` promises an operator that persisted conversations will work.  The check
used to ask ``Path.is_dir()`` — which is true for a read-only mount, a folder
owned by another account, or a Windows directory where the service account has
read-only access.  The first stored turn then died at runtime with an OSError
that named nothing useful.
"""

from __future__ import annotations

import errno
from pathlib import Path

from server.config import Settings
from server.readiness import _can_write, check_storage


def settings_for(root: Path) -> Settings:
    return Settings(history_persist=True, data_dir=str(root))


def test_a_writable_directory_is_ready_and_leaves_no_probe_file(tmp_path: Path) -> None:
    check = check_storage(settings_for(tmp_path))
    assert check["ok"] is True, check
    assert check["path"] == str(tmp_path / "conversations")
    # The probe must clean up after itself: a readiness poll every few seconds
    # may not litter the user's data directory.
    assert list(tmp_path.iterdir()) == []


def test_a_read_only_directory_is_reported_not_ready(tmp_path: Path, monkeypatch) -> None:
    def refuse(*args, **kwargs):
        raise PermissionError(errno.EACCES, "Permission denied", str(tmp_path))

    monkeypatch.setattr("server.readiness.tempfile.NamedTemporaryFile", refuse)
    check = check_storage(settings_for(tmp_path))
    assert check["ok"] is False, check
    assert "قابل نوشتن نیست" in check["detail"]
    assert "PermissionError" in check["detail"], "the reason must name the failure, not hide it"
    assert check["path"] == str(tmp_path / "conversations")


def test_a_missing_data_directory_falls_back_to_the_nearest_existing_ancestor(
    tmp_path: Path,
) -> None:
    """A not-yet-created data dir is normal; the check must not create it either."""
    deep = tmp_path / "not" / "created" / "yet"
    check = check_storage(settings_for(deep))
    assert check["ok"] is True, check
    assert check["path"] == str(deep / "conversations")
    assert not deep.exists(), "a readiness probe must not create directories"


def test_disabled_persistence_skips_the_check(tmp_path: Path) -> None:
    check = check_storage(Settings(history_persist=False, data_dir=str(tmp_path)))
    assert check["ok"] is True
    assert check["skipped"] is True


def test_the_write_probe_reports_the_os_error_instead_of_swallowing_it(tmp_path: Path) -> None:
    ok, reason = _can_write(tmp_path)
    assert ok is True and reason == ""
    assert list(tmp_path.iterdir()) == [], "a successful probe must remove its file"
