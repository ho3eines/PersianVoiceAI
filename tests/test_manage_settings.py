"""Contract of ``/api/manage/settings``: read, validate, write ``.env`` safely.

These tests pin the promises the UI relies on: Persian labels for every field,
secrets that never come back, unknown keys rejected, invalid values refused
*before* anything is written, and an ``.env`` edit that preserves comments and
ordering while reporting which keys need a restart.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from managekit import build_app, local_client, make_settings


def test_settings_groups_are_labelled_and_secrets_are_masked(tmp_path: Path) -> None:
    app, _manager, _models = build_app(tmp_path)
    with local_client(app) as client:
        response = client.get("/api/manage/settings")
        assert response.status_code == 200
        body = response.json()
    assert body["groups"], "the panel returned no settings groups"
    keys = {field["key"] for group in body["groups"] for field in group["fields"]}
    assert "PVAI_ASR_BEAM_SIZE" in keys
    assert "PVAI_API_TOKEN" in keys
    for group in body["groups"]:
        assert group["title_fa"]
        for field in group["fields"]:
            assert field["label_fa"], field["key"]
            assert "help_fa" in field
            if field["secret"]:
                assert field["value"] is None
                assert field["widget"] == "secret"

    token = next(
        field
        for group in body["groups"]
        for field in group["fields"]
        if field["key"] == "PVAI_API_TOKEN"
    )
    assert token["is_set"] is False
    assert token["secret"] is True


def test_update_writes_env_and_reports_restart(tmp_path: Path) -> None:
    app, _manager, _models = build_app(tmp_path)
    with local_client(app) as client:
        response = client.put(
            "/api/manage/settings",
            json={"values": {"PVAI_ASR_BEAM_SIZE": 3}, "apply": True},
        )
        assert response.status_code == 200, response.text
        body = response.json()
    assert body["changed"] == ["PVAI_ASR_BEAM_SIZE"]
    assert body["restart_required"] == ["PVAI_ASR_BEAM_SIZE"]
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "PVAI_ASR_BEAM_SIZE=3" in env_text


def test_unknown_key_is_rejected(tmp_path: Path) -> None:
    app, _manager, _models = build_app(tmp_path)
    with local_client(app) as client:
        response = client.put(
            "/api/manage/settings", json={"values": {"PVAI_NOT_REAL": 1}}
        )
        assert response.status_code == 400
        assert not (tmp_path / ".env").exists()


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("PVAI_AUDIO_SAMPLE_RATE", "8000"),
        ("PVAI_AUDIO_BLOCK_MS", "33"),
        ("PVAI_HOST", "0.0.0.0"),
        ("PVAI_LOG_LEVEL", "verbose"),
        ("PVAI_TTS_VOICE", "robot"),
    ],
)
def test_invalid_values_are_refused_before_writing(
    tmp_path: Path, key: str, value: str
) -> None:
    app, _manager, _models = build_app(tmp_path)
    with local_client(app) as client:
        response = client.put("/api/manage/settings", json={"values": {key: value}})
        assert response.status_code == 422, response.text
    assert not (tmp_path / ".env").exists()


def test_short_secret_is_refused(tmp_path: Path) -> None:
    app, _manager, _models = build_app(tmp_path)
    with local_client(app) as client:
        response = client.put(
            "/api/manage/settings", json={"values": {"PVAI_API_TOKEN": "short"}}
        )
        assert response.status_code == 422


def test_reset_removes_keys_and_restores_defaults(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("PVAI_ASR_BEAM_SIZE=3\nPVAI_TEMPERATURE=0.9\n", encoding="utf-8")
    app, _manager, _models = build_app(tmp_path)
    with local_client(app) as client:
        response = client.post(
            "/api/manage/settings/reset", json={"keys": ["PVAI_ASR_BEAM_SIZE"]}
        )
        assert response.status_code == 200, response.text
        body = response.json()
    assert body["removed"] == ["PVAI_ASR_BEAM_SIZE"]
    remaining = env_path.read_text(encoding="utf-8")
    assert "PVAI_ASR_BEAM_SIZE" not in remaining
    assert "PVAI_TEMPERATURE=0.9" in remaining


def test_env_edit_preserves_comments_order_and_unknown_keys(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# a comment the operator wrote\n"
        "SOME_OTHER_TOOL=keep-me\n"
        "PVAI_TEMPERATURE=0.5  # creativity\n"
        "\n"
        "PVAI_N_CTX=4096\n",
        encoding="utf-8",
    )
    app, _manager, _models = build_app(tmp_path)
    with local_client(app) as client:
        response = client.put(
            "/api/manage/settings", json={"values": {"PVAI_N_CTX": 8192}}
        )
        assert response.status_code == 200, response.text
    text = env_path.read_text(encoding="utf-8")
    assert "# a comment the operator wrote" in text
    assert "SOME_OTHER_TOOL=keep-me" in text
    assert "PVAI_TEMPERATURE=0.5  # creativity" in text
    assert "PVAI_N_CTX=8192" in text
    assert text.index("SOME_OTHER_TOOL") < text.index("PVAI_N_CTX")


def test_atomic_write_leaves_no_temp_files(tmp_path: Path) -> None:
    app, _manager, _models = build_app(tmp_path)
    with local_client(app) as client:
        client.put("/api/manage/settings", json={"values": {"PVAI_N_CTX": 2048}})
    leftovers = [p.name for p in tmp_path.glob(".*.tmp")]
    assert not leftovers, leftovers


def test_empty_value_is_kept_only_for_selected_keys(tmp_path: Path) -> None:
    app, _manager, _models = build_app(tmp_path)
    with local_client(app) as client:
        response = client.put(
            "/api/manage/settings",
            json={"values": {"PVAI_SYSTEM_PROMPT": "", "PVAI_TEMPERATURE": ""}},
        )
        assert response.status_code == 200, response.text
        body = response.json()
    assert body["changed"] == ["PVAI_SYSTEM_PROMPT"]
    text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "PVAI_SYSTEM_PROMPT=" in text
    assert "PVAI_TEMPERATURE" not in text


def test_secret_value_is_never_echoed_back(tmp_path: Path) -> None:
    secret = "a-very-secret-token-1234"
    settings = make_settings(tmp_path, api_token=secret)
    app, _manager, _models = build_app(tmp_path, settings=settings)
    with local_client(app) as client:
        listing = client.get("/api/manage/settings")
    assert secret not in listing.text
    token = next(
        field
        for group in listing.json()["groups"]
        for field in group["fields"]
        if field["key"] == "PVAI_API_TOKEN"
    )
    assert token["value"] is None
    assert token["is_set"] is True


def test_secret_can_be_written_without_being_returned(tmp_path: Path) -> None:
    app, _manager, _models = build_app(tmp_path)
    secret = "a-very-secret-token-1234"
    with local_client(app) as client:
        saved = client.put(
            "/api/manage/settings", json={"values": {"PVAI_API_TOKEN": secret}}
        )
        assert saved.status_code == 200, saved.text
        assert secret not in saved.text
    assert f"PVAI_API_TOKEN={secret}" in (tmp_path / ".env").read_text(encoding="utf-8")

