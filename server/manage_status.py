"""``/api/manage/status`` — component health and start/stop/restart control.

Safety boundary: the panel only ever signals a process **it started itself**
(a retained ``Popen``).  It never scans for a PID, never uses ``taskkill /IM``
and never runs a shell.  A service that was launched outside the panel is
reported as ``managed: false`` and refuses stop/restart with a Persian message.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException

from .config import PROJECT_ROOT, Settings
from .manage_common import pending_restart
from .readiness import collect as collect_readiness

SERVICE_IDS = ("llama-server", "voice", "api")

_LOG_FILES = {
    "llama-server": "llama-server.out.log",
    "voice": "voice.log",
    "api": "api.out.log",
}

_LABELS = {
    "llama-server": "سرور مدل زبانی (llama.cpp)",
    "voice": "حلقهٔ گفتار کامپیوتر",
    "api": "سرویس API محلی",
}


def _port_of(url: str) -> int | None:
    try:
        from urllib.parse import urlsplit

        return urlsplit(url).port
    except ValueError:
        return None


class ServiceManager:
    """Owns the child processes the panel started, and nothing else."""

    def __init__(
        self,
        *,
        settings_provider: Callable[[], Settings],
        root: Path | None = None,
        reload_settings: Callable[[], Settings] | None = None,
    ) -> None:
        self._settings_provider = settings_provider
        # Called right before a service starts.  Activating a model or saving a
        # setting writes ``.env``; without a re-read the panel would restart
        # llama-server with the *old* model and the UI would lie about it.
        self._reload_settings = reload_settings
        self._root = root or PROJECT_ROOT
        self._lock = threading.Lock()
        self._children: dict[str, subprocess.Popen[bytes]] = {}
        self._started_at: dict[str, float] = {}
        self._last_error: dict[str, str] = {}
        self._log_handles: dict[str, Any] = {}
        # Services the panel has started at least once.  After a clean stop the
        # child is gone, and probing cannot distinguish "never ran" from
        # "stopped by the panel"; this set keeps the reported state honest.
        self._seen: set[str] = set()

    # ------------------------------------------------------------- commands
    def _startup_settings(self) -> Settings:
        if self._reload_settings is None:
            return self._settings_provider()
        try:
            return self._reload_settings()
        except Exception:  # noqa: BLE001 - a broken .env must not block start
            return self._settings_provider()

    def log_path(self, service_id: str) -> Path:
        return self._root / "logs" / _LOG_FILES.get(service_id, f"{service_id}.log")

    def resolve_llama_executable(self, settings: Settings) -> str:
        """Absolute path to llama-server, or a helpful Persian error."""
        candidate = (settings.llama_server_exe or "llama-server").strip() or "llama-server"
        path = Path(candidate)
        if path.is_file():
            return str(path)
        if path.is_absolute() or os.sep in candidate:
            raise ValueError(
                f"اجراپذیر llama-server در مسیر «{candidate}» پیدا نشد. "
                "مقدار PVAI_LLAMA_SERVER_EXE را در تنظیمات اصلاح کنید."
            )
        found = shutil.which(candidate)
        if not found:
            raise ValueError(
                f"برنامهٔ «{candidate}» در PATH پیدا نشد. مسیر کامل را در "
                "PVAI_LLAMA_SERVER_EXE تنظیم کنید."
            )
        return found

    def command_for(self, service_id: str, settings: Settings) -> list[str]:
        if service_id == "llama-server":
            return settings.llama_server_command(self.resolve_llama_executable(settings))
        if service_id == "voice":
            return [sys.executable, "-m", "server.voice"]
        if service_id == "api":
            return [sys.executable, "-m", "server"]
        raise ValueError("سرویس ناشناخته")

    # ------------------------------------------------------------ lifecycle
    def start(self, service_id: str) -> dict[str, Any]:
        settings = self._startup_settings()
        if service_id == "llama-server":
            model = Path(settings.model_path)
            if not model.is_file():
                raise ValueError(
                    f"فایل مدل GGUF پیدا نشد: {settings.model_path}. "
                    "از بخش «مدل‌ها» یک مدل نصب و فعال کنید."
                )
        with self._lock:
            proc = self._children.get(service_id)
            if proc is not None and proc.poll() is None:
                return self._state(service_id, settings)
            try:
                command = self.command_for(service_id, settings)
            except ValueError as exc:
                self._last_error[service_id] = str(exc)
                raise
            log_path = self.log_path(service_id)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            handle = log_path.open("ab")
            try:
                proc = subprocess.Popen(
                    command,
                    cwd=str(self._root),
                    stdin=subprocess.DEVNULL,
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                    shell=False,
                )
            except OSError as exc:
                handle.close()
                self._last_error[service_id] = str(exc)
                raise ValueError(f"اجرای سرویس ناموفق بود: {exc}") from exc
            self._children[service_id] = proc
            self._log_handles[service_id] = handle
            self._started_at[service_id] = time.time()
            self._seen.add(service_id)
            self._last_error.pop(service_id, None)
        return self._state(service_id, settings)

    def stop(self, service_id: str, timeout: float = 8.0) -> dict[str, Any]:
        settings = self._settings_provider()
        with self._lock:
            proc = self._children.get(service_id)
            if proc is None or proc.poll() is not None:
                raise ValueError(
                    "این سرویس توسط پنل مدیریت راه‌اندازی نشده است، پس پنل آن را متوقف نمی‌کند. "
                    "اگر می‌خواهید پنل کنترل کامل داشته باشد، سرویس را از همین صفحه شروع کنید."
                )
            proc.terminate()
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    self._last_error[service_id] = "فرایند پس از مهلت هم بسته نشد."
            handle = self._log_handles.pop(service_id, None)
            if handle is not None:
                try:
                    handle.close()
                except OSError:
                    pass
            self._children.pop(service_id, None)
            self._started_at.pop(service_id, None)
        return self._state(service_id, settings)

    def restart(self, service_id: str) -> dict[str, Any]:
        with self._lock:
            owned = self._children.get(service_id) is not None
        if owned:
            self.stop(service_id)
        return self.start(service_id)

    def shutdown(self) -> None:
        """Stop every child the panel started (called when the app exits)."""
        for service_id in list(self._children):
            try:
                self.stop(service_id)
            except ValueError:
                pass

    # ---------------------------------------------------------------- state
    def is_owned(self, service_id: str) -> bool:
        """True only if this manager started a still-running child."""
        proc = self._children.get(service_id)
        return proc is not None and proc.poll() is None

    def state(self, service_id: str, settings: Settings | None = None) -> dict[str, Any]:
        """Public view of one service, for the API and the dashboard."""
        return self._state(service_id, settings or self._settings_provider())

    def _state(self, service_id: str, settings: Settings) -> dict[str, Any]:
        proc = self._children.get(service_id)
        alive = proc is not None and proc.poll() is None
        exit_code = proc.poll() if proc is not None else None
        port = {
            "llama-server": _port_of(settings.llama_server_url),
            "api": getattr(settings, "api_port", None),
            "voice": None,
        }[service_id]
        health = self._health(service_id, settings, alive)
        if alive:
            state = "running"
        elif proc is not None and exit_code not in (0, None):
            state = "failed"
            self._last_error.setdefault(service_id, f"فرایند با کد {exit_code} خارج شد.")
        elif proc is not None:
            state = "stopped"
        elif service_id in self._seen:
            state = "stopped"
        elif health.get("ok"):
            state = "running"
        else:
            state = "unknown" if health.get("unknown") else "stopped"
        return {
            "id": service_id,
            "label_fa": _LABELS[service_id],
            "state": state,
            "managed": alive,
            "pid": proc.pid if alive and proc is not None else None,
            "port": port,
            "started_at": self._started_at.get(service_id),
            "health": health,
            "last_error": self._last_error.get(service_id),
        }

    def _health(self, service_id: str, settings: Settings, alive: bool) -> dict[str, Any]:
        if service_id == "llama-server":
            probe = _http_json(settings.llama_server_url.rstrip("/") + "/health")
            if probe["ok"]:
                ready = bool((probe.get("data") or {}).get("status") == "ok")
                return {
                    "ok": ready,
                    "detail": "سرور مدل پاسخ می‌دهد." if ready else "سرور پاسخ داد ولی مدل آماده نیست.",
                }
            return {"ok": False, "detail": "سرور مدل در دسترس نیست." if not alive else "در حال بالا آمدن."}
        if service_id == "api":
            # ``/health`` is the cheap liveness route (no token, no model probe).
            # ``/ready`` runs the full checklist and can take seconds, which made
            # a healthy API look "unreachable" behind the 1.5 s probe timeout.
            url = f"http://{getattr(settings, 'api_host', '127.0.0.1')}:{getattr(settings, 'api_port', 8000)}/health"
            probe = _http_json(url, timeout=2.0)
            if probe["ok"]:
                return {"ok": True, "detail": "سرویس API پاسخ می‌دهد."}
            return {"ok": False, "detail": "سرویس API در دسترس نیست.", "unknown": not alive}
        return {
            "ok": alive,
            "detail": "فرایند گفتار در حال اجراست." if alive else "حلقهٔ گفتار اجرا نمی‌شود.",
            "unknown": not alive,
        }

    def read_logs(self, service_id: str, tail: int = 200) -> dict[str, Any]:
        path = self.log_path(service_id)
        limit = max(1, min(int(tail), 2000))
        lines: list[str] = []
        truncated = False
        try:
            with path.open("r", encoding="utf-8", errors="replace") as stream:
                lines = stream.read().splitlines()
        except FileNotFoundError:
            return {"lines": [], "truncated": False, "path": str(path),
                    "detail_fa": "برای این سرویس هنوز گزارشی ثبت نشده است."}
        except OSError as exc:
            raise ValueError(f"خواندن گزارش ناموفق بود: {exc}") from exc
        if len(lines) > limit:
            truncated = True
            lines = lines[-limit:]
        return {"lines": lines, "truncated": truncated, "path": str(path)}


def _http_json(url: str, timeout: float = 1.5) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - loopback only
            body = response.read(65536)
            try:
                return {"ok": 200 <= response.status < 300, "data": json.loads(body.decode("utf-8"))}
            except ValueError:
                return {"ok": 200 <= response.status < 300, "data": None}
    except (urllib.error.URLError, OSError, ValueError):
        return {"ok": False, "data": None}


def _llm_backend_probe(settings: Settings) -> dict[str, Any]:
    health = _http_json(settings.llama_server_url.rstrip("/") + "/health")
    models = _http_json(settings.llama_server_url.rstrip("/") + "/v1/models")
    return {
        "reachable": bool(health["ok"]),
        "model_available": bool(models["ok"]),
    }


def build_router(
    *,
    settings_provider: Callable[[], Settings],
    manager: ServiceManager,
    env_path: Path,
    started_at: float,
    host: str,
    port: int,
    provider_active_id: Callable[[], str] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/manage", tags=["manage"])

    def active_provider() -> str:
        if provider_active_id is not None:
            try:
                return provider_active_id()
            except Exception:  # noqa: BLE001 - the panel must still render
                return "local"
        return _active_provider_name(settings_provider())

    @router.get("/status")
    def status() -> dict[str, Any]:
        settings = settings_provider()
        backend = _llm_backend_probe(settings)
        report = collect_readiness(settings, backend=backend)
        checks = report.get("checks", {})
        services = [manager.state(service_id, settings) for service_id in SERVICE_IDS]
        active_external = any(item["health"]["ok"] and not item["managed"] for item in services)
        return {
            "server": {
                "ready": bool(report.get("ready")),
                "uptime_s": round(time.time() - started_at, 1),
                "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
                "host": host,
                "port": port,
            },
            "offline_guarantee": active_provider() == "local",
            "active_provider": active_provider(),
            "components": {
                "llm": _component(checks.get("llm_backend"), settings.model_path, backend["reachable"]),
                "asr": _component(checks.get("asr_model"), settings.asr_model_path, True),
                "tts": _component(checks.get("tts_model"), settings.tts_model_path, True),
                "aec": _component(checks.get("aec"), getattr(settings, "aec_library", ""), True),
                "audio": _component(checks.get("dependencies"), "", True),
            },
            "services": services,
            "restart_required": pending_restart(settings, env_path),
            "external_processes": active_external,
            "usage_notes_fa": _USAGE_NOTES_FA,
        }

    @router.post("/services/{service_id}/{action}")
    def service_action(service_id: str, action: str) -> dict[str, Any]:
        if service_id not in SERVICE_IDS:
            raise HTTPException(status_code=404, detail="سرویس ناشناخته است.")
        if action not in {"start", "stop", "restart"}:
            raise HTTPException(status_code=404, detail="عملیات ناشناخته است.")
        if action == "stop" and not manager.is_owned(service_id):
            settings = settings_provider()
            state = manager.state(service_id, settings)
            if state["state"] == "running":
                raise HTTPException(
                    status_code=409,
                    detail="این سرویس بیرون از پنل اجرا شده است. برای کنترل کامل، آن را ببندید و از همین صفحه شروع کنید.",
                )
        try:
            if action == "start":
                result = manager.start(service_id)
            elif action == "stop":
                result = manager.stop(service_id)
            else:
                result = manager.restart(service_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "id": service_id,
            "state": result["state"],
            "detail": f"عملیات «{action}» برای {_LABELS[service_id]} انجام شد.",
            "service": result,
        }

    @router.get("/services/{service_id}/logs")
    def service_logs(service_id: str, tail: int = 200) -> dict[str, Any]:
        if service_id not in SERVICE_IDS:
            raise HTTPException(status_code=404, detail="سرویس ناشناخته است.")
        try:
            return manager.read_logs(service_id, tail)
        except ValueError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    return router


def _component(check: dict[str, Any] | None, model: str, reachable: bool) -> dict[str, Any]:
    if not check:
        return {"state": "unknown", "detail": "وضعیت این بخش بررسی نشده است.", "model": Path(model).name if model else ""}
    state = "ready" if check.get("ok") else ("unconfigured" if not reachable else "error")
    return {
        "state": state,
        "detail": str(check.get("detail", "")),
        "model": Path(str(model)).name if model else "",
    }


def _offline_guarantee(settings: Settings) -> bool:
    """True unless a cloud provider profile was explicitly activated."""
    try:
        from .manage_providers import active_provider_is_local

        return active_provider_is_local()
    except Exception:  # noqa: BLE001 - the panel must still render
        return True


def _active_provider_name(settings: Settings) -> str:
    try:
        from .manage_providers import active_provider_id

        return active_provider_id()
    except Exception:  # noqa: BLE001
        return "local"


_USAGE_NOTES_FA = [
    "پنل فقط سرویس‌هایی را متوقف می‌کند که خودش راه‌اندازی کرده باشد.",
    "تغییر تنظیمات فقط پس از راه‌اندازی مجدد سرویس اعمال می‌شود.",
    "همهٔ داده‌ها روی همین دستگاه می‌ماند مگر خودتان یک ارائه‌دهندهٔ ابری را فعال کنید.",
]
