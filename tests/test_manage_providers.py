"""Contract of ``/api/manage/providers``: local by default, cloud on request.

The offline guarantee is the project's red line: the shipped provider is the
local llama.cpp server, adding a cloud profile is an explicit operator action,
the panel warns before activating one, and ``/status`` flips
``offline_guarantee`` to false while a non-loopback provider is active.  API
keys are write-only and never echoed back.
"""
from __future__ import annotations

from pathlib import Path

from managekit import build_app, local_client
from server.manage_common import SECRET_PLACEHOLDER

CLOUD = {
    "kind": "openai_compat",
    "label_fa": "سرویس آزمایشی",
    "base_url": "https://api.example.com/v1",
    "model": "gpt-test",
    "api_key": "sk-test-key-123456",
    "enabled": True,
}


def test_default_listing_is_local_and_offline(tmp_path: Path) -> None:
    app, _manager, _models = build_app(tmp_path)
    with local_client(app) as client:
        body = client.get("/api/manage/providers").json()
    assert body["active"] == "local"
    local = next(item for item in body["providers"] if item["id"] == "local")
    assert local["offline"] is True
    assert local["deletable"] is False
    assert {kind["id"] for kind in body["kinds"]} == {
        "llama_server",
        "openai_compat",
        "anthropic",
    }
    assert body["note_fa"]


def test_local_provider_is_not_editable(tmp_path: Path) -> None:
    app, _manager, _models = build_app(tmp_path)
    with local_client(app) as client:
        response = client.put("/api/manage/providers/local", json=CLOUD)
        assert response.status_code == 409


def test_invalid_kind_is_rejected(tmp_path: Path) -> None:
    app, _manager, _models = build_app(tmp_path)
    with local_client(app) as client:
        response = client.put(
            "/api/manage/providers/x", json={"kind": "magic", "enabled": True}
        )
        assert response.status_code == 422


def test_cloud_provider_requires_https(tmp_path: Path) -> None:
    app, _manager, _models = build_app(tmp_path)
    body = dict(CLOUD, base_url="http://api.example.com/v1")
    with local_client(app) as client:
        assert client.put("/api/manage/providers/x", json=body).status_code == 422


def test_enabled_provider_without_url_is_rejected(tmp_path: Path) -> None:
    app, _manager, _models = build_app(tmp_path)
    body = dict(CLOUD, base_url="")
    with local_client(app) as client:
        assert client.put("/api/manage/providers/x", json=body).status_code == 422


def test_url_with_credentials_is_rejected(tmp_path: Path) -> None:
    app, _manager, _models = build_app(tmp_path)
    body = dict(CLOUD, base_url="https://user:pass@api.example.com/v1")
    with local_client(app) as client:
        assert client.put("/api/manage/providers/x", json=body).status_code == 422


def test_saved_key_is_write_only(tmp_path: Path) -> None:
    app, _manager, _models = build_app(tmp_path)
    with local_client(app) as client:
        saved = client.put("/api/manage/providers/openai-main", json=CLOUD)
        assert saved.status_code == 200, saved.text
        assert CLOUD["api_key"] not in saved.text
        assert saved.json()["provider"]["api_key_set"] is True
        listing = client.get("/api/manage/providers")
    assert CLOUD["api_key"] not in listing.text
    stored = (tmp_path / "data" / "manage" / "providers.json").read_text(encoding="utf-8")
    assert CLOUD["api_key"] in stored


def test_placeholder_keeps_the_stored_key(tmp_path: Path) -> None:
    app, _manager, _models = build_app(tmp_path)
    with local_client(app) as client:
        client.put("/api/manage/providers/openai-main", json=CLOUD)
        updated = dict(CLOUD, api_key=SECRET_PLACEHOLDER, model="gpt-test-2")
        response = client.put("/api/manage/providers/openai-main", json=updated)
        assert response.status_code == 200, response.text
        assert response.json()["provider"]["api_key_set"] is True
        assert response.json()["provider"]["model"] == "gpt-test-2"


def test_delete_local_is_refused_and_unknown_is_404(tmp_path: Path) -> None:
    app, _manager, _models = build_app(tmp_path)
    with local_client(app) as client:
        assert client.delete("/api/manage/providers/local").status_code == 409
        assert client.delete("/api/manage/providers/ghost").status_code == 404


def test_delete_removes_a_cloud_provider(tmp_path: Path) -> None:
    app, _manager, _models = build_app(tmp_path)
    with local_client(app) as client:
        client.put("/api/manage/providers/openai-main", json=CLOUD)
        removed = client.delete("/api/manage/providers/openai-main")
        assert removed.status_code == 200
        assert client.get("/api/manage/providers/openai-main").status_code == 404


def test_activating_a_disabled_provider_is_refused(tmp_path: Path) -> None:
    app, _manager, _models = build_app(tmp_path)
    with local_client(app) as client:
        disabled = dict(CLOUD, enabled=False)
        client.put("/api/manage/providers/openai-main", json=disabled)
        response = client.post(
            "/api/manage/providers/active", json={"id": "openai-main"}
        )
        assert response.status_code == 409


def test_activating_unknown_provider_is_404(tmp_path: Path) -> None:
    app, _manager, _models = build_app(tmp_path)
    with local_client(app) as client:
        response = client.post("/api/manage/providers/active", json={"id": "ghost"})
        assert response.status_code == 404


def test_activating_cloud_flips_the_offline_guarantee(tmp_path: Path) -> None:
    app, _manager, _models = build_app(tmp_path)
    with local_client(app) as client:
        client.put("/api/manage/providers/openai-main", json=CLOUD)
        activated = client.post(
            "/api/manage/providers/active", json={"id": "openai-main"}
        )
        assert activated.status_code == 200, activated.text
        payload = activated.json()
        assert payload["warning_fa"]
        assert payload["offline_guarantee"] is False

        status = client.get("/api/manage/status").json()
        assert status["offline_guarantee"] is False
        assert status["active_provider"] == "openai-main"

        back = client.post("/api/manage/providers/active", json={"id": "local"})
        assert back.status_code == 200
        assert client.get("/api/manage/status").json()["offline_guarantee"] is True


def test_testing_unknown_provider_is_404(tmp_path: Path) -> None:
    app, _manager, _models = build_app(tmp_path)
    with local_client(app) as client:
        assert client.post("/api/manage/providers/ghost/test", json={}).status_code == 404


def test_provider_file_lives_outside_git_and_under_data_dir(tmp_path: Path) -> None:
    app, _manager, _models = build_app(tmp_path)
    with local_client(app) as client:
        client.put("/api/manage/providers/openai-main", json=CLOUD)
    store = tmp_path / "data" / "manage" / "providers.json"
    assert store.is_file()
    assert app.state.provider_store.path == store
