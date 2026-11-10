"""Startup validation and readiness reporting.

The old service only exposed ``/health`` (backend reachability).  Nothing told
the user whether the Whisper model files existed, whether the chosen Piper
voice was complete, whether the native AEC library was loadable, or whether the
optional audio dependencies were installed — failures surfaced only when the
live client crashed, sometimes with a traceback.

``collect`` performs *static* local checks (file presence, importability,
configuration validity) and merges an optional backend health report.  It never
downloads anything and never raises: a broken installation must produce a
readable checklist, not an exception.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

VOICE_DEPENDENCIES = ("sounddevice", "faster_whisper", "piper", "numpy", "scipy")


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, ModuleNotFoundError):
        return False


def check_configuration(settings: Any) -> dict[str, Any]:
    errors = list(settings.config_errors())
    return {
        "ok": not errors,
        "detail": "تنظیمات معتبر است." if not errors else "؛ ".join(errors),
        "errors": errors,
    }


def check_asr(settings: Any) -> dict[str, Any]:
    path = Path(settings.asr_model_path)
    missing = [name for name in ("model.bin",) if not (path / name).is_file()]
    if not path.is_dir():
        return {"ok": False, "detail": f"مسیر مدل Whisper موجود نیست: {path}", "path": str(path)}
    if missing:
        return {
            "ok": False,
            "detail": "فایل‌های مدل Whisper ناقص است: " + ", ".join(missing),
            "path": str(path),
        }
    return {"ok": True, "detail": "مدل Whisper محلی آماده است.", "path": str(path)}


def check_tts(settings: Any) -> dict[str, Any]:
    path = Path(settings.tts_model_for(settings.tts_voice))
    if not path.is_file():
        return {
            "ok": False,
            "detail": f"مدل Piper برای صدای «{settings.tts_voice}» موجود نیست: {path}",
            "path": str(path),
            "voice": settings.tts_voice,
        }
    config = Path(str(path) + ".json")
    if not config.is_file():
        return {
            "ok": False,
            "detail": f"فایل تنظیمات Piper موجود نیست: {config}",
            "path": str(path),
            "voice": settings.tts_voice,
        }
    return {
        "ok": True,
        "detail": "مدل Piper محلی آماده است.",
        "path": str(path),
        "voice": settings.tts_voice,
    }


def check_dependencies() -> dict[str, Any]:
    missing = [name for name in VOICE_DEPENDENCIES if not _module_available(name)]
    return {
        "ok": not missing,
        "detail": "وابستگی‌های صوتی نصب است."
        if not missing
        else "وابستگی صوتی نصب نشده: " + ", ".join(missing) + " (requirements-voice.txt)",
        "missing": missing,
    }


def check_aec(settings: Any) -> dict[str, Any]:
    from .aec import aec_status

    status = aec_status(settings)
    if not status.get("enabled"):
        return {"ok": True, "skipped": True, **status}
    return {"ok": bool(status.get("available")), **status}


def check_ws(settings: Any) -> dict[str, Any]:
    """LAN voice transport status (opt-in, token required)."""
    if not getattr(settings, "ws_enabled", False):
        return {
            "ok": True,
            "skipped": True,
            "enabled": False,
            "detail": "انتقال WebSocket صوتی خاموش است (PVAI_WS_ENABLED).",
        }
    if not getattr(settings, "ws_token", ""):
        return {
            "ok": False,
            "enabled": True,
            "detail": "PVAI_WS_ENABLED روشن است ولی PVAI_WS_TOKEN خالی است؛ "
            "اتصال دستگاه‌ها عمداً رد می‌شود.",
        }
    return {
        "ok": True,
        "enabled": True,
        "detail": "انتقال WebSocket صوتی آماده است (احراز هویت با توکن دستگاه).",
        "path": "/ws/voice",
        "firmware": False,
    }


def check_storage(settings: Any) -> dict[str, Any]:
    if not getattr(settings, "history_persist", False):
        return {"ok": True, "skipped": True, "detail": "ذخیره‌سازی محلی خاموش است."}
    root = Path(getattr(settings, "data_dir", "data")) / "conversations"
    # Check the nearest existing ancestor instead of creating directories as a
    # side effect of a readiness probe.
    probe = root
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        writable = probe.is_dir() and probe.exists()
    except OSError:
        writable = False
    if not writable:
        return {
            "ok": False,
            "detail": f"مسیر دادهٔ محلی قابل نوشتن نیست: {root}",
            "path": str(root),
        }
    return {"ok": True, "detail": "ذخیره‌سازی محلی آماده است.", "path": str(root)}


def collect(settings: Any, *, backend: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a readiness report (``ready`` is true only when every check passes)."""
    checks: dict[str, Any] = {
        "configuration": check_configuration(settings),
        "asr_model": check_asr(settings),
        "tts_model": check_tts(settings),
        "dependencies": check_dependencies(),
        "aec": check_aec(settings),
        "storage": check_storage(settings),
        "ws_voice": check_ws(settings),
    }
    if backend is not None:
        reachable = bool(backend.get("reachable"))
        available = bool(backend.get("model_available"))
        checks["llm_backend"] = {
            "ok": reachable and available,
            "reachable": reachable,
            "model_available": available,
            "detail": "llama-server و مدل در دسترس‌اند."
            if reachable and available
            else "llama-server در دسترس نیست یا مدل لود نشده است.",
        }
    ready = all(bool(check.get("ok")) for check in checks.values())
    return {
        "ready": ready,
        "checks": checks,
        "voice": {
            "tts_voice": getattr(settings, "tts_voice", ""),
            "asr_model": getattr(settings, "asr_model_path", ""),
            "persistence": bool(getattr(settings, "history_persist", False)),
        },
    }
