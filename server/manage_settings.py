"""``/api/manage/settings`` — read and edit the local ``.env`` from the panel.

Every write is atomic, validated before it touches the disk, and reported with
the set of keys that need a service restart (this process keeps the ``Settings``
snapshot it started with).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .config import Settings
from .manage_common import (
    env_issues,
    known_keys,
    pending_restart,
    persist_env_updates,
    remove_env_keys,
    settings_groups,
    validate_updates,
)


class SettingsUpdate(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    apply: bool = True


class SettingsReset(BaseModel):
    keys: list[str] = Field(default_factory=list)


def build_router(*, settings_provider: Callable[[], Settings], env_path: Path) -> APIRouter:
    """``settings_provider`` returns the snapshot of the *running* process."""
    router = APIRouter(prefix="/api/manage", tags=["manage"])

    def _snapshot() -> Settings:
        try:
            return settings_provider()
        except Exception as exc:  # noqa: BLE001 - surfaced as a Persian message
            raise HTTPException(status_code=500, detail=f"خواندن تنظیمات ناموفق بود: {exc}") from exc

    @router.get("/settings")
    def get_settings() -> dict[str, Any]:
        settings = _snapshot()
        return {
            "groups": settings_groups(settings, env_path),
            "env_path": str(env_path),
            "env_issues": env_issues(settings),
            "restart_required": pending_restart(settings, env_path),
            "restart_hint_fa": "هیچ‌کدام از این تنظیمات بدون راه‌اندازی مجدد سرویس اعمال نمی‌شود.",
        }

    @router.put("/settings")
    def update_settings(payload: SettingsUpdate) -> dict[str, Any]:
        allowed = known_keys()
        unknown = sorted(key for key in payload.values if key not in allowed)
        if unknown:
            raise HTTPException(status_code=400, detail=f"تنظیم ناشناخته: {', '.join(unknown)}")
        values = {key: value for key, value in payload.values.items() if value != "" or _allows_empty(key)}
        try:
            validate_updates(values)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        try:
            changed = persist_env_updates(env_path, values)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "changed": changed,
            "restart_required": sorted(changed),
            "applied_live": [],
            "env_issues": env_issues(_snapshot()),
            "detail_fa": (
                "تنظیمات ذخیره شد. برای اعمال، سرویس را از بخش «سرویس‌ها» دوباره راه‌اندازی کنید."
                if changed
                else "تغییری ثبت نشد."
            ),
        }

    @router.post("/settings/reset")
    def reset_settings(payload: SettingsReset) -> dict[str, Any]:
        allowed = known_keys()
        unknown = sorted(key for key in payload.keys if key not in allowed)
        if unknown:
            raise HTTPException(status_code=400, detail=f"تنظیم ناشناخته: {', '.join(unknown)}")
        try:
            removed = remove_env_keys(env_path, payload.keys)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "removed": removed,
            "restart_required": removed,
            "detail_fa": "مقادیر انتخاب‌شده از فایل حذف شدند و پیش‌فرض کد برمی‌گردد.",
        }

    return router


_EMPTY_ALLOWED = frozenset({
    "PVAI_SYSTEM_PROMPT",
    "PVAI_ASR_INITIAL_PROMPT",
    "PVAI_ASR_HOTWORDS",
    "PVAI_ASR_HALLUCINATION_PHRASES",
    "PVAI_AUDIO_INPUT_DEVICE",
    "PVAI_AUDIO_OUTPUT_DEVICE",
    "PVAI_VOICE_SESSION_ID",
    "PVAI_SSL_CERTFILE",
    "PVAI_SSL_KEYFILE",
    "PVAI_TTS_MODEL_FEMALE",
    "PVAI_TTS_MODEL_MALE",
    "PVAI_AEC_LIBRARY",
})
"""Keys where an empty string is a meaningful value, not "leave it alone"."""


def _allows_empty(key: str) -> bool:
    return key in _EMPTY_ALLOWED
