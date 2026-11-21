"""Shared helpers for the management-panel tests.

Every test here builds its own app against a temporary ``.env`` and a temporary
models directory, so nothing touches the developer's real configuration, no
model is loaded and no network call is made.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.config import Settings
from server.manage_api import install_management
from server.manage_providers import ProviderStore
from server.manage_status import ServiceManager


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    """Settings that never write conversation history and live under ``tmp_path``."""
    base: dict[str, Any] = {
        "history_persist": False,
        "data_dir": str(tmp_path / "data"),
    }
    base.update(overrides)
    return Settings(**base)


def build_app(
    tmp_path: Path,
    *,
    settings: Settings | None = None,
    models_dir: Path | None = None,
) -> tuple[FastAPI, ServiceManager, Path]:
    """Return ``(app, manager, models_dir)`` wired exactly like the real panel."""
    resolved = settings or make_settings(tmp_path)
    env_path = tmp_path / ".env"
    resolved_models = models_dir or (tmp_path / "models")
    app = FastAPI()
    manager = ServiceManager(settings_provider=lambda: resolved, root=tmp_path)
    install_management(
        app,
        resolved,
        env_path=env_path,
        models_dir=resolved_models,
        host="127.0.0.1",
        port=8100,
        process_manager=manager,
        provider_store=ProviderStore(tmp_path / "data" / "manage" / "providers.json"),
    )
    return app, manager, resolved_models


def local_client(app: FastAPI) -> TestClient:
    """A ``TestClient`` whose peer looks like loopback, as the app requires."""
    return TestClient(app, client=("127.0.0.1", 50500))


def install_asr_model(models_dir: Path, name: str = "whisper-small") -> Path:
    """Create a minimal, complete CTranslate2 Whisper directory."""
    directory = models_dir / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "model.bin").write_bytes(b"\x00" * 32)
    (directory / "config.json").write_text("{}", encoding="utf-8")
    (directory / "tokenizer.json").write_text("{}", encoding="utf-8")
    return directory


def install_gguf(models_dir: Path, name: str = "demo.gguf") -> Path:
    path = models_dir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"GGUF" + b"\x00" * 16)
    return path
