"""Contract of ``/api/manage/status`` and the service start/stop/restart API.

The panel may only signal a process it started itself.  These tests pin that
boundary: a service launched outside the panel is reported as unmanaged and
refuses to stop, unknown ids/actions are refused, and ``shutdown`` reaps exactly
the children the manager owns.
"""
from __future__ import annotations

import sys
from pathlib import Path

from managekit import build_app, local_client, make_settings
from server import manage_status

SLEEPER = [sys.executable, "-c", "import time; time.sleep(120)"]


def _stub_commands(manager) -> None:  # noqa: ANN001 - tiny local helper
    manager.command_for = lambda service_id, settings: list(SLEEPER)  # type: ignore[assignment]


def test_status_reports_components_services_and_offline_guarantee(
    tmp_path: Path,
) -> None:
    app, _manager, _models = build_app(tmp_path)
    with local_client(app) as client:
        response = client.get("/api/manage/status")
        assert response.status_code == 200, response.text
        body = response.json()
    assert body["offline_guarantee"] is True
    assert body["server"]["ready"] in (True, False)
    for name in ("llm", "asr", "tts", "aec", "audio"):
        assert body["components"][name]["state"]
    service_ids = {service["id"] for service in body["services"]}
    assert service_ids == {"llama-server", "voice", "api"}
    assert all(service["label_fa"] for service in body["services"])


def test_owned_service_start_stop_restart(tmp_path: Path) -> None:
    app, manager, _models = build_app(tmp_path)
    _stub_commands(manager)
    with local_client(app) as client:
        started = client.post("/api/manage/services/voice/start")
        assert started.status_code == 200, started.text
        assert started.json()["state"] == "running"
        assert manager.is_owned("voice")

        again = client.post("/api/manage/services/voice/start")
        assert again.status_code == 200 and again.json()["state"] == "running"

        restarted = client.post("/api/manage/services/voice/restart")
        assert restarted.status_code == 200 and restarted.json()["state"] == "running"

        stopped = client.post("/api/manage/services/voice/stop")
        assert stopped.status_code == 200, stopped.text
        assert stopped.json()["state"] == "stopped"
        assert not manager.is_owned("voice")


def test_stop_refuses_a_service_the_panel_does_not_own(tmp_path: Path) -> None:
    app, manager, _models = build_app(tmp_path)
    with local_client(app) as client:
        response = client.post("/api/manage/services/voice/stop")
        assert response.status_code == 409
    assert not manager.is_owned("voice")


def test_unknown_service_and_action_are_rejected(tmp_path: Path) -> None:
    app, _manager, _models = build_app(tmp_path)
    with local_client(app) as client:
        assert client.post("/api/manage/services/nope/start").status_code == 404
        assert client.post("/api/manage/services/voice/explode").status_code == 404


def test_llama_start_without_a_model_file_is_refused(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, model_path=str(tmp_path / "missing.gguf"))
    app, _manager, _models = build_app(tmp_path, settings=settings)
    with local_client(app) as client:
        response = client.post("/api/manage/services/llama-server/start")
        assert response.status_code == 409


def test_llama_command_is_built_from_settings(tmp_path: Path) -> None:
    model = tmp_path / "models" / "demo.gguf"
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_bytes(b"GGUF")
    exe = tmp_path / "bin" / "llama-server"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b"x")
    settings = make_settings(
        tmp_path,
        model_path=str(model),
        llama_server_exe=str(exe),
        n_threads=3,
        n_ctx=2048,
    )
    _app, manager, _models = build_app(tmp_path, settings=settings)
    command = manager.command_for("llama-server", settings)
    assert command[0] == str(exe)
    assert "-m" in command and str(model) in command
    assert "-c" in command and "2048" in command
    assert "-t" in command and "3" in command


def test_missing_llama_executable_is_reported_as_409(tmp_path: Path) -> None:
    model = tmp_path / "models" / "demo.gguf"
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_bytes(b"GGUF")
    settings = make_settings(
        tmp_path,
        model_path=str(model),
        llama_server_exe="not-a-real-llama-binary-xyz",
    )
    app, _manager, _models = build_app(tmp_path, settings=settings)
    with local_client(app) as client:
        response = client.post("/api/manage/services/llama-server/start")
        assert response.status_code == 409
        assert "PVAI_LLAMA_SERVER_EXE" in response.json()["detail"]


def test_service_logs_are_tailed(tmp_path: Path) -> None:
    app, manager, _models = build_app(tmp_path)
    log_path = manager.log_path("voice")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("\n".join(f"line {i}" for i in range(10)), encoding="utf-8")
    with local_client(app) as client:
        response = client.get("/api/manage/services/voice/logs?tail=3")
        assert response.status_code == 200, response.text
        body = response.json()
    assert body["lines"] == ["line 7", "line 8", "line 9"]
    assert body["truncated"] is True


def test_shutdown_reaps_only_owned_children(tmp_path: Path) -> None:
    app, manager, _models = build_app(tmp_path)
    _stub_commands(manager)
    with local_client(app) as client:
        assert client.post("/api/manage/services/voice/start").status_code == 200
    assert manager.is_owned("voice")
    manager.shutdown()
    assert not manager.is_owned("voice")


def test_state_marks_unmanaged_and_unknown_for_external_services(
    tmp_path: Path,
) -> None:
    app, manager, _models = build_app(tmp_path)
    with local_client(app) as client:
        body = client.get("/api/manage/status").json()
    voice = next(s for s in body["services"] if s["id"] == "voice")
    assert voice["managed"] is False
    assert voice["state"] in {"stopped", "unknown", "running", "failed"}


def test_api_health_probes_the_cheap_liveness_route(
    tmp_path: Path, monkeypatch
) -> None:
    """``/ready`` runs the full checklist and can exceed the probe timeout.

    A healthy API must therefore be detected through ``/health``; otherwise the
    panel reports "در دسترس نیست" for a service that is answering.
    """
    probed: list[str] = []

    def fake_http_json(url: str, timeout: float = 1.5) -> dict[str, object]:
        probed.append(url)
        return {"ok": True, "data": {"status": "ok"}}

    monkeypatch.setattr(manage_status, "_http_json", fake_http_json)
    settings = make_settings(tmp_path)
    app, _manager, _models = build_app(tmp_path, settings=settings)
    with local_client(app) as client:
        body = client.get("/api/manage/status").json()
    api = next(s for s in body["services"] if s["id"] == "api")
    assert api["health"]["ok"] is True
    assert any(url.endswith("/health") for url in probed)
    assert not any(url.endswith("/ready") for url in probed)


def test_start_uses_settings_reloaded_from_env(tmp_path: Path) -> None:
    """Activating a model writes ``.env``; the next start must use that file.

    Without a reload the panel restarts llama-server with the old model while
    the UI claims the new one is active.
    """
    old_model = tmp_path / "models" / "old.gguf"
    new_model = tmp_path / "models" / "new.gguf"
    old_model.parent.mkdir(parents=True, exist_ok=True)
    old_model.write_bytes(b"GGUF")
    new_model.write_bytes(b"GGUF")
    exe = tmp_path / "bin" / "llama-server"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b"x")
    stale = make_settings(tmp_path, model_path=str(old_model), llama_server_exe=str(exe))
    fresh = make_settings(tmp_path, model_path=str(new_model), llama_server_exe=str(exe))
    manager = manage_status.ServiceManager(
        settings_provider=lambda: stale,
        root=tmp_path,
        reload_settings=lambda: fresh,
    )
    manager.command_for = lambda service_id, settings: list(SLEEPER)  # type: ignore[assignment]
    recorded: list[str] = []
    original = manager.command_for

    def spy(service_id: str, settings):  # noqa: ANN001, ANN202 - local test helper
        recorded.append(str(settings.model_path))
        return original(service_id, settings)

    manager.command_for = spy  # type: ignore[assignment]
    manager.start("llama-server")
    try:
        assert recorded == [str(new_model)], "start must use the reloaded .env"
    finally:
        manager.shutdown()


def test_reload_from_env_reads_the_file_and_restores_the_environment(
    tmp_path: Path, monkeypatch
) -> None:
    from server.manage_api import _reload_from_env

    env_path = tmp_path / ".env"
    env_path.write_text("PVAI_MODEL_PATH=models/activated.gguf\n", encoding="utf-8")
    monkeypatch.delenv("PVAI_MODEL_PATH", raising=False)
    settings = _reload_from_env(env_path)
    assert settings.model_path == "models/activated.gguf"
    import os

    assert "PVAI_MODEL_PATH" not in os.environ, "the reload must not leak into os.environ"
