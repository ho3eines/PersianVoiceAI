"""Contract of ``/api/manage/models``: inspect, activate, delete and download.

The panel must recognise which installed model is active, write an
``.env`` value that the service can actually resolve, refuse to delete the
active model, and validate a download request before any network work starts.
"""
from __future__ import annotations

from pathlib import Path

from managekit import build_app, install_asr_model, local_client, make_settings
from server.config import PROJECT_ROOT
from server.manage_models import CATALOG, _activation_value


def test_models_kind_is_validated(tmp_path: Path) -> None:
    app, _manager, _models = build_app(tmp_path)
    with local_client(app) as client:
        assert client.get("/api/manage/models?kind=nope").status_code == 422


def test_catalog_lists_installable_whisper_models(tmp_path: Path) -> None:
    app, _manager, _models = build_app(tmp_path)
    with local_client(app) as client:
        body = client.get("/api/manage/models?kind=asr").json()
    catalog_ids = {entry["id"] for entry in body["catalog"]}
    assert {"whisper-small", "whisper-large-v3-turbo"} <= catalog_ids
    assert body["kinds"]
    assert body["source_note_fa"]


def test_catalog_covers_every_model_kind(tmp_path: Path) -> None:
    """The UI must never show an empty catalog for an offered kind."""
    app, _manager, _models = build_app(tmp_path)
    with local_client(app) as client:
        for kind in ("llm", "asr", "tts"):
            body = client.get(f"/api/manage/models?kind={kind}").json()
            assert body["catalog"], f"catalog for {kind} is empty"


def test_catalog_ids_are_unique_and_files_are_flat() -> None:
    ids = [entry.id for entry in CATALOG]
    assert len(ids) == len(set(ids))
    for entry in CATALOG:
        for file in entry.files:
            assert "/" not in file.name, f"{entry.id}: {file.name} would nest directories"
            assert file.url.startswith("https://"), file.url


def test_llm_and_tts_catalog_files_pin_digest_and_size() -> None:
    """A weight file is only trustworthy when the publisher digest is pinned."""
    for entry in CATALOG:
        if entry.kind not in {"llm", "tts"}:
            continue
        for file in entry.files:
            assert file.sha256 and len(file.sha256) == 64, f"{entry.id}/{file.name}"
            assert file.size and file.size > 0, f"{entry.id}/{file.name}"


def test_downloaded_tts_voice_is_indexed_and_activatable(tmp_path: Path) -> None:
    """A catalog voice lands as ``models/<id>/<file>.onnx`` and can be activated."""
    entry = next(item for item in CATALOG if item.kind == "tts" and item.id == "amir")
    models_dir = tmp_path / "models"
    destination = models_dir / entry.id
    destination.mkdir(parents=True)
    for file in entry.files:
        (destination / file.name).write_bytes(b"\x00" * 8)
    settings = make_settings(tmp_path)
    app, _manager, _models = build_app(tmp_path, settings=settings, models_dir=models_dir)
    with local_client(app) as client:
        body = client.get("/api/manage/models?kind=tts").json()
        installed = {item["id"]: item for item in body["installed"]}
        assert installed["fa_IR-amir-medium"]["valid"] is True
        response = client.post(
            "/api/manage/models/activate",
            json={"kind": "tts", "id": "fa_IR-amir-medium"},
        )
        assert response.status_code == 200, response.text
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    # Outside ``PROJECT_ROOT`` the value is absolute; inside it would be
    # ``models/amir/fa_IR-amir-medium.onnx``.  Either way it must point at the
    # downloaded file, not at a bare voice name.
    assert "PVAI_TTS_MODEL_PATH=" in env_text
    assert "amir/fa_IR-amir-medium.onnx" in env_text


def test_active_asr_model_is_resolved(tmp_path: Path) -> None:
    models_dir = tmp_path / "models"
    install_asr_model(models_dir, "whisper-small")
    install_asr_model(models_dir, "whisper-large-v3-turbo")
    settings = make_settings(tmp_path, asr_model_path="models/whisper-small")
    app, _manager, _models = build_app(tmp_path, settings=settings, models_dir=models_dir)
    with local_client(app) as client:
        body = client.get("/api/manage/models?kind=asr").json()
    assert body["active_id"] == "whisper-small"
    active = [item for item in body["installed"] if item["active"]]
    assert [item["id"] for item in active] == ["whisper-small"]


def test_active_asr_model_resolves_from_absolute_path(tmp_path: Path) -> None:
    models_dir = tmp_path / "models"
    directory = install_asr_model(models_dir, "whisper-small")
    install_asr_model(models_dir, "whisper-large-v3-turbo")
    settings = make_settings(tmp_path, asr_model_path=str(directory))
    app, _manager, _models = build_app(tmp_path, settings=settings, models_dir=models_dir)
    with local_client(app) as client:
        body = client.get("/api/manage/models?kind=asr").json()
    assert body["active_id"] == "whisper-small"


def test_active_id_is_empty_when_nothing_matches(tmp_path: Path) -> None:
    models_dir = tmp_path / "models"
    install_asr_model(models_dir, "whisper-small")
    settings = make_settings(tmp_path, asr_model_path="")
    app, _manager, _models = build_app(tmp_path, settings=settings, models_dir=models_dir)
    with local_client(app) as client:
        body = client.get("/api/manage/models?kind=asr").json()
    assert body["active_id"] == ""


def test_activation_value_is_project_relative() -> None:
    """Production ``models/`` lives under the project root; ``.env`` gets ``models/...``."""
    models_dir = PROJECT_ROOT / "models"
    assert (
        _activation_value("asr", Path("whisper-small"), models_dir)
        == "models/whisper-small"
    )
    assert (
        _activation_value("llm", Path("qwen3.5-4b/model.gguf"), models_dir)
        == "models/qwen3.5-4b/model.gguf"
    )


def test_activate_writes_a_resolvable_path(tmp_path: Path) -> None:
    models_dir = tmp_path / "models"
    install_asr_model(models_dir, "whisper-small")
    install_asr_model(models_dir, "whisper-large-v3-turbo")
    settings = make_settings(tmp_path, asr_model_path="models/whisper-small")
    app, _manager, _models = build_app(tmp_path, settings=settings, models_dir=models_dir)
    with local_client(app) as client:
        response = client.post(
            "/api/manage/models/activate",
            json={"kind": "asr", "id": "whisper-large-v3-turbo"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["restart_required"] == ["PVAI_ASR_MODEL_PATH"]
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    line = next(line for line in env_text.splitlines() if line.startswith("PVAI_ASR_MODEL_PATH"))
    value = line.split("=", 1)[1].strip().strip('"')
    assert (Path(value) / "model.bin").is_file()


def test_activate_incomplete_model_is_refused(tmp_path: Path) -> None:
    models_dir = tmp_path / "models"
    directory = models_dir / "broken"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "model.bin").write_bytes(b"\x00" * 8)
    app, _manager, _models = build_app(tmp_path, models_dir=models_dir)
    with local_client(app) as client:
        response = client.post(
            "/api/manage/models/activate", json={"kind": "asr", "id": "broken"}
        )
        assert response.status_code == 409


def test_activate_unknown_model_is_404(tmp_path: Path) -> None:
    app, _manager, _models = build_app(tmp_path)
    with local_client(app) as client:
        response = client.post(
            "/api/manage/models/activate", json={"kind": "asr", "id": "ghost"}
        )
        assert response.status_code == 404


def test_delete_active_model_is_refused(tmp_path: Path) -> None:
    models_dir = tmp_path / "models"
    install_asr_model(models_dir, "whisper-small")
    settings = make_settings(tmp_path, asr_model_path="models/whisper-small")
    app, _manager, _models = build_app(tmp_path, settings=settings, models_dir=models_dir)
    with local_client(app) as client:
        response = client.delete("/api/manage/models/asr/whisper-small")
        assert response.status_code == 409
    assert (models_dir / "whisper-small" / "model.bin").is_file()


def test_delete_inactive_model_removes_its_files(tmp_path: Path) -> None:
    models_dir = tmp_path / "models"
    install_asr_model(models_dir, "whisper-small")
    install_asr_model(models_dir, "whisper-large-v3-turbo")
    settings = make_settings(tmp_path, asr_model_path="models/whisper-small")
    app, _manager, _models = build_app(tmp_path, settings=settings, models_dir=models_dir)
    with local_client(app) as client:
        response = client.delete("/api/manage/models/asr/whisper-large-v3-turbo")
        assert response.status_code == 200, response.text
        assert response.json()["removed"] == "whisper-large-v3-turbo"
    assert not (models_dir / "whisper-large-v3-turbo").exists()
    assert (models_dir / "whisper-small" / "model.bin").is_file()


def test_download_rejects_unknown_catalog_entry(tmp_path: Path) -> None:
    app, _manager, _models = build_app(tmp_path)
    with local_client(app) as client:
        response = client.post(
            "/api/manage/models/download", json={"kind": "asr", "catalog_id": "ghost"}
        )
        assert response.status_code == 404


def test_download_rejects_kind_mismatch(tmp_path: Path) -> None:
    app, _manager, _models = build_app(tmp_path)
    with local_client(app) as client:
        response = client.post(
            "/api/manage/models/download",
            json={"kind": "llm", "catalog_id": "whisper-small"},
        )
        assert response.status_code == 422


def test_download_rejects_custom_request_without_files(tmp_path: Path) -> None:
    app, _manager, _models = build_app(tmp_path)
    with local_client(app) as client:
        response = client.post(
            "/api/manage/models/download", json={"kind": "llm", "name": "mine"}
        )
        assert response.status_code == 422


def test_download_rejects_non_https_url(tmp_path: Path) -> None:
    app, _manager, _models = build_app(tmp_path)
    with local_client(app) as client:
        response = client.post(
            "/api/manage/models/download",
            json={
                "kind": "llm",
                "name": "mine",
                "files": [{"url": "http://example.com/x.gguf"}],
            },
        )
        assert response.status_code == 422


def test_download_rejects_unsafe_name(tmp_path: Path) -> None:
    app, _manager, _models = build_app(tmp_path)
    with local_client(app) as client:
        response = client.post(
            "/api/manage/models/download", json={"kind": "llm", "name": "../etc"}
        )
        assert response.status_code == 422


def test_existing_target_is_refused_without_overwrite(tmp_path: Path) -> None:
    models_dir = tmp_path / "models"
    install_asr_model(models_dir, "whisper-small")
    app, _manager, _models = build_app(tmp_path, models_dir=models_dir)
    with local_client(app) as client:
        response = client.post(
            "/api/manage/models/download",
            json={"kind": "asr", "catalog_id": "whisper-small"},
        )
        assert response.status_code == 409


def test_unknown_job_is_404(tmp_path: Path) -> None:
    app, _manager, _models = build_app(tmp_path)
    with local_client(app) as client:
        assert client.get("/api/manage/jobs/nope").status_code == 404


def test_catalog_entries_carry_sha256_for_weights() -> None:
    """A pinned digest is what makes a mirror download trustworthy."""
    turbo = next(entry for entry in CATALOG if entry.id == "whisper-large-v3-turbo")
    weight = next(file for file in turbo.files if file.name == "model.bin")
    assert isinstance(weight.sha256, str) and len(weight.sha256) == 64
