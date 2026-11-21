"""``/api/manage/providers`` — connection profiles for the model backend.

The shipped default is the local llama.cpp server, and it stays the default:
adding an external provider is an explicit operator action, the panel warns
before it is activated, and ``/status`` keeps reporting ``offline_guarantee:
false`` while a cloud profile is active.

API keys are write-only.  They live in ``data/manage/providers.json`` (git-ignored,
``0600`` where the filesystem supports it) and are never echoed back.
"""
from __future__ import annotations

import json
import os
import re
import socket
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .config import PROJECT_ROOT, Settings, require_local_http_url
from .manage_common import SECRET_PLACEHOLDER

PROVIDER_KINDS = ("llama_server", "openai_compat", "anthropic")

LOCAL_PROVIDER_ID = "local"

DEFAULT_STORE = PROJECT_ROOT / "data" / "manage" / "providers.json"

_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")

_LABELS = {
    "llama_server": "مدل محلی (llama.cpp)",
    "openai_compat": "سازگار با OpenAI",
    "anthropic": "Anthropic",
}


class ProviderStore:
    """Write-only secrets in a small JSON document."""

    def __init__(self, path: Path = DEFAULT_STORE) -> None:
        self.path = path
        self._lock = threading.Lock()

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"active": LOCAL_PROVIDER_ID, "providers": {}}
        except (OSError, ValueError) as exc:
            raise ValueError(f"فایل ارائه‌دهنده‌ها خواندنی نیست: {exc}") from exc
        if not isinstance(data, dict):
            return {"active": LOCAL_PROVIDER_ID, "providers": {}}
        providers = data.get("providers")
        if not isinstance(providers, dict):
            providers = {}
        return {"active": str(data.get("active") or LOCAL_PROVIDER_ID), "providers": providers}

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".providers-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.chmod(temp_name, 0o600)
            except OSError:
                pass
            os.replace(temp_name, self.path)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass

    def raw(self, provider_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._read()["providers"].get(provider_id)

    def active_id(self) -> str:
        with self._lock:
            data = self._read()
        active = data["active"]
        return active if active in data["providers"] or active == LOCAL_PROVIDER_ID else LOCAL_PROVIDER_ID

    def set_active(self, provider_id: str) -> None:
        with self._lock:
            data = self._read()
            data["active"] = provider_id
            self._write(data)

    def put(self, provider_id: str, entry: dict[str, Any]) -> None:
        with self._lock:
            data = self._read()
            data["providers"][provider_id] = entry
            self._write(data)

    def delete(self, provider_id: str) -> bool:
        with self._lock:
            data = self._read()
            if provider_id not in data["providers"]:
                return False
            del data["providers"][provider_id]
            if data["active"] == provider_id:
                data["active"] = LOCAL_PROVIDER_ID
            self._write(data)
            return True

    def all(self) -> dict[str, Any]:
        with self._lock:
            return self._read()


_store = ProviderStore()


def active_provider_id() -> str:
    """Id of the provider the operator selected (``local`` by default)."""
    try:
        return _store.active_id()
    except ValueError:
        return LOCAL_PROVIDER_ID


def active_provider_is_local() -> bool:
    """True while no cloud profile is active; drives ``offline_guarantee``."""
    try:
        return active_provider_id() == LOCAL_PROVIDER_ID
    except ValueError:
        return True


class ProviderRequest(BaseModel):
    kind: str = "openai_compat"
    label_fa: str = ""
    base_url: str = ""
    model: str = ""
    api_key: str | None = None
    enabled: bool = False


class ActiveRequest(BaseModel):
    id: str


class TestRequest(BaseModel):
    pass


def _local_public(settings: Settings) -> dict[str, Any]:
    return {
        "id": LOCAL_PROVIDER_ID,
        "kind": "llama_server",
        "label_fa": _LABELS["llama_server"],
        "base_url": settings.llama_server_url,
        "model": Path(settings.model_path).name,
        "api_key_set": False,
        "enabled": True,
        "offline": True,
        "deletable": False,
        "last_test": None,
        "note_fa": "پیش‌فرض پروژه؛ هیچ داده‌ای از دستگاه خارج نمی‌شود.",
    }


def _public(provider_id: str, entry: dict[str, Any], settings: Settings) -> dict[str, Any]:
    kind = str(entry.get("kind", "openai_compat"))
    base_url = str(entry.get("base_url", ""))
    return {
        "id": provider_id,
        "kind": kind,
        "label_fa": str(entry.get("label_fa") or _LABELS.get(kind, provider_id)),
        "base_url": base_url,
        "model": str(entry.get("model", "")),
        "api_key_set": bool(entry.get("api_key")),
        "enabled": bool(entry.get("enabled", False)),
        "offline": base_url.strip() == "" or _is_loopback_url(base_url),
        "deletable": True,
        "last_test": entry.get("last_test"),
        "note_fa": (
            "این ارائه‌دهنده بیرون از دستگاه است؛ با فعال‌سازی، متن گفتگو از رایانهٔ شما خارج می‌شود."
            if not _is_loopback_url(base_url)
            else "آدرس محلی است و داده از دستگاه خارج نمی‌شود."
        ),
    }


def _is_loopback_url(url: str) -> bool:
    host = urllib.parse.urlsplit(url).hostname or ""
    return host in {"127.0.0.1", "localhost", "::1"}


def _validate(provider_id: str, payload: ProviderRequest) -> dict[str, Any]:
    if not _SAFE_ID.fullmatch(provider_id):
        raise ValueError("شناسه باید با حرف یا عدد شروع شود و فقط حروف، عدد، نقطه، خط تیره و زیرخط داشته باشد.")
    if payload.kind not in PROVIDER_KINDS:
        raise ValueError("نوع ارائه‌دهنده نامعتبر است.")
    base_url = payload.base_url.strip()
    if payload.kind == "llama_server":
        if base_url:
            require_local_http_url(base_url, variable="base_url")
    elif payload.enabled or base_url:
        parsed = urllib.parse.urlsplit(base_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("برای ارائه‌دهندهٔ بیرونی، نشانی باید HTTPS و بدون نام کاربری/رمز باشد.")
    if payload.api_key is not None and len(payload.api_key) > 4096:
        raise ValueError("کلید API بیش از حد بلند است.")
    return {
        "kind": payload.kind,
        "label_fa": payload.label_fa.strip()[:80],
        "base_url": base_url,
        "model": payload.model.strip()[:200],
        "enabled": bool(payload.enabled),
    }


def _probe(entry: dict[str, Any], timeout: float = 8.0) -> dict[str, Any]:
    """One cheap request: list models.  No conversation text is ever sent."""
    base = str(entry.get("base_url", "")).rstrip("/")
    if not base:
        return {"ok": False, "latency_ms": None, "detail": "نشانی سرویس تنظیم نشده است."}
    kind = str(entry.get("kind", "openai_compat"))
    url = base + "/models"
    headers = {"User-Agent": "PersianVoiceAI-model-manager/1"}
    key = entry.get("api_key") or ""
    if kind == "anthropic":
        headers["anthropic-version"] = "2023-06-01"
        if key:
            headers["x-api-key"] = key
    elif key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(url, headers=headers, method="GET")
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - operator-supplied URL
            response.read(4096)
            latency = (time.perf_counter() - started) * 1000
            ok = 200 <= response.status < 300
            return {
                "ok": ok,
                "latency_ms": round(latency, 1),
                "detail": "اتصال برقرار است." if ok else f"پاسخ HTTP {response.status}",
            }
    except urllib.error.HTTPError as exc:
        return {
            "ok": False,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "detail": f"سرویس پاسخ {exc.code} داد؛ کلید یا نشانی را بررسی کنید.",
        }
    except (urllib.error.URLError, socket.timeout, OSError, ValueError) as exc:
        return {
            "ok": False,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "detail": f"اتصال برقرار نشد ({type(exc).__name__}).",
        }


def build_router(
    *,
    settings_provider: Callable[[], Settings],
    store: ProviderStore | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/manage", tags=["manage"])
    repository = store or _store

    def _snapshot() -> dict[str, Any]:
        try:
            return repository.all()
        except ValueError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.get("/providers")
    def list_providers() -> dict[str, Any]:
        settings = settings_provider()
        data = _snapshot()
        items = [_local_public(settings)]
        items += [
            _public(provider_id, entry, settings)
            for provider_id, entry in sorted(data["providers"].items())
        ]
        return {
            "active": data["active"],
            "providers": items,
            "kinds": [
                {"id": "llama_server", "label_fa": _LABELS["llama_server"]},
                {"id": "openai_compat", "label_fa": _LABELS["openai_compat"]},
                {"id": "anthropic", "label_fa": _LABELS["anthropic"]},
            ],
            "note_fa": (
                "پیش‌فرض پروژه کاملاً محلی است. افزودن ارائه‌دهندهٔ بیرونی فقط با ثبت نشانی HTTPS "
                "و تأیید آگاهانهٔ شما ممکن است و کلید ذخیره‌شده هرگز در پاسخ‌ها نمایش داده نمی‌شود."
            ),
        }

    @router.put("/providers/{provider_id}")
    def save_provider(provider_id: str, payload: ProviderRequest) -> dict[str, Any]:
        settings = settings_provider()
        if provider_id == LOCAL_PROVIDER_ID:
            raise HTTPException(
                status_code=409,
                detail="ارائه‌دهندهٔ محلی از تنظیمات ساخته می‌شود و قابل ویرایش نیست.",
            )
        try:
            entry = _validate(provider_id, payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        previous = repository.raw(provider_id) or {}
        if payload.api_key is None or payload.api_key == SECRET_PLACEHOLDER:
            entry["api_key"] = str(previous.get("api_key", ""))
        else:
            entry["api_key"] = payload.api_key
        entry["last_test"] = previous.get("last_test")
        repository.put(provider_id, entry)
        return {
            "provider": _public(provider_id, entry, settings),
            "detail_fa": "ارائه‌دهنده ذخیره شد. کلید ذخیره‌شده هرگز در پاسخ‌ها نمایش داده نمی‌شود.",
        }

    @router.delete("/providers/{provider_id}")
    def delete_provider(provider_id: str) -> dict[str, Any]:
        if provider_id == LOCAL_PROVIDER_ID:
            raise HTTPException(status_code=409, detail="ارائه‌دهندهٔ محلی قابل حذف نیست.")
        if not repository.delete(provider_id):
            raise HTTPException(status_code=404, detail="این ارائه‌دهنده پیدا نشد.")
        return {"deleted": provider_id, "detail_fa": "ارائه‌دهنده و کلید ذخیره‌شدهٔ آن حذف شد."}

    @router.post("/providers/{provider_id}/test")
    def test_provider(provider_id: str, payload: TestRequest | None = None) -> dict[str, Any]:
        settings = settings_provider()
        if provider_id == LOCAL_PROVIDER_ID:
            entry = {"kind": "llama_server", "base_url": settings.llama_server_url}
            result = _probe(entry)
        else:
            entry = repository.raw(provider_id)
            if entry is None:
                raise HTTPException(status_code=404, detail="این ارائه‌دهنده پیدا نشد.")
            result = _probe(entry)
            entry["last_test"] = {**result, "at": time.time()}
            repository.put(provider_id, entry)
        return result

    @router.post("/providers/active")
    def set_active(payload: ActiveRequest) -> dict[str, Any]:
        settings = settings_provider()
        provider_id = payload.id.strip()
        if provider_id == LOCAL_PROVIDER_ID:
            repository.set_active(LOCAL_PROVIDER_ID)
            return {"active": LOCAL_PROVIDER_ID, "detail_fa": "پردازش محلی فعال شد."}
        entry = repository.raw(provider_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="این ارائه‌دهنده پیدا نشد.")
        if not entry.get("enabled"):
            raise HTTPException(status_code=409, detail="این ارائه‌دهنده غیرفعال است؛ اول آن را فعال کنید.")
        repository.set_active(provider_id)
        warning = None
        if not _is_loopback_url(str(entry.get("base_url", ""))):
            warning = (
                "از این پس متن گفتگو (و در مسیر صوتی، رونویسی) ممکن است از رایانهٔ شما "
                "خارج شود. برای بازگشت به حالت کاملاً محلی، ارائه‌دهندهٔ «محلی» را فعال کنید."
            )
        return {
            "active": provider_id,
            "offline_guarantee": _is_loopback_url(str(entry.get("base_url", ""))),
            "warning_fa": warning,
        }

    @router.get("/providers/{provider_id}")
    def get_provider(provider_id: str) -> dict[str, Any]:
        settings = settings_provider()
        if provider_id == LOCAL_PROVIDER_ID:
            return {"provider": _local_public(settings)}
        entry = repository.raw(provider_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="این ارائه‌دهنده پیدا نشد.")
        return {"provider": _public(provider_id, entry, settings)}

    return router
