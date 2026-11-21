"""Install the management panel on the bench app.

``install_management`` is the single place that knows how the routers, the
process manager and the static UI fit together.  The routers never import the
app, so they stay testable in isolation with ``TestClient``.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from .config import PROJECT_ROOT, Settings
from .manage_models import JobRegistry, build_router as build_models_router
from .manage_providers import ProviderStore, build_router as build_providers_router
from .manage_settings import build_router as build_settings_router
from .manage_status import ServiceManager, build_router as build_status_router

CONTROL_DIR = PROJECT_ROOT / "web" / "control"

_ASSETS = {
    "app.js": "application/javascript; charset=utf-8",
    "styles.css": "text/css; charset=utf-8",
}

_NO_CACHE = {"Cache-Control": "no-cache"}
"""The panel ships with the app; a stale cached asset hides a real fix."""


def _reload_from_env(env_path: Path) -> Settings:
    """Re-read ``.env`` so a saved setting reaches the next service start.

    ``Settings.from_env()`` calls ``load_dotenv()`` without ``override``, so a
    value already in ``os.environ`` would win over the edited file.  Loading the
    file with ``override=True`` first makes activation (``PVAI_MODEL_PATH``,
    ``PVAI_ASR_MODEL_PATH``, …) effective on restart instead of silently
    restarting the old model.

    ``os.environ`` is restored afterwards: the panel is a long-running process
    and a reload must not permanently rewrite the environment of unrelated code
    (or of the test suite).
    """
    from dotenv import dotenv_values

    if not env_path.is_file():
        return Settings.from_env()
    values = dotenv_values(env_path)
    saved = dict(os.environ)
    try:
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        return Settings.from_env()
    finally:
        os.environ.clear()
        os.environ.update(saved)


def install_management(
    app: FastAPI,
    settings: Settings,
    *,
    env_path: Path | None = None,
    models_dir: Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8100,
    process_manager: ServiceManager | None = None,
    provider_store: ProviderStore | None = None,
) -> ServiceManager:
    """Wire ``/control`` and ``/api/manage/*`` into ``app``; return the manager."""
    resolved_env = env_path or (PROJECT_ROOT / ".env")
    resolved_models = models_dir or (PROJECT_ROOT / "models")
    started_at = time.time()

    manager = process_manager or ServiceManager(
        settings_provider=lambda: settings,
        reload_settings=lambda: _reload_from_env(resolved_env),
    )
    app.state.service_manager = manager
    app.state.job_registry = JobRegistry()
    resolved_store = provider_store or ProviderStore()
    app.state.provider_store = resolved_store

    app.include_router(
        build_settings_router(settings_provider=lambda: settings, env_path=resolved_env)
    )
    app.include_router(
        build_status_router(
            settings_provider=lambda: settings,
            manager=manager,
            env_path=resolved_env,
            started_at=started_at,
            host=host,
            port=port,
            provider_active_id=resolved_store.active_id,
        )
    )
    app.include_router(
        build_models_router(
            settings_provider=lambda: settings,
            models_dir=resolved_models,
            env_path=resolved_env,
            jobs=app.state.job_registry,
        )
    )
    app.include_router(
        build_providers_router(settings_provider=lambda: settings, store=resolved_store)
    )

    @app.get("/control", include_in_schema=False)
    async def control_page() -> FileResponse:
        page = CONTROL_DIR / "index.html"
        if not page.is_file():
            raise HTTPException(status_code=500, detail="web/control/index.html پیدا نشد.")
        return FileResponse(
            page, media_type="text/html; charset=utf-8", headers=_NO_CACHE
        )

    @app.get("/control/", include_in_schema=False)
    async def control_page_slash() -> FileResponse:
        return await control_page()

    def _serve_asset(asset: str) -> FileResponse:
        media_type = _ASSETS.get(asset)
        if media_type is None:
            raise HTTPException(status_code=404, detail="فایل موجود نیست.")
        path = CONTROL_DIR / asset
        if not path.is_file():
            raise HTTPException(status_code=404, detail="فایل موجود نیست.")
        return FileResponse(path, media_type=media_type, headers=_NO_CACHE)

    @app.get("/control/{asset}", include_in_schema=False)
    async def control_asset(asset: str) -> FileResponse:
        return _serve_asset(asset)

    @app.get("/control/assets/{asset}", include_in_schema=False)
    async def control_asset_nested(asset: str) -> FileResponse:
        # Older cached HTML referenced ``/control/assets/<file>``; keep both
        # spellings working during an upgrade window.
        return _serve_asset(asset)

    return manager


def management_summary(app: FastAPI) -> dict[str, Any]:
    """Small helper used by tests and diagnostics."""
    manager = getattr(app.state, "service_manager", None)
    return {
        "panel": "/control",
        "api_prefix": "/api/manage",
        "owned_services": [
            service_id for service_id in ("llama-server", "voice", "api")
            if manager is not None and manager.is_owned(service_id)
        ],
    }
