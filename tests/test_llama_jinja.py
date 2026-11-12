"""``--jinja`` and the honest answer to "did enable_thinking do anything?".

Two separate failures hide here:

* ``llama-server`` silently ignores ``chat_template_kwargs`` unless it was
  started with ``--jinja``.  The flag is emitted by
  ``python -m server.config --launch``; the shell and PowerShell launchers must
  not be able to drop it, which is what they now check before starting.
* ``/ready`` used to report only a *generation* probe: a model that answered
  cleanly by luck looked fine, and nothing told the operator that the
  ``enable_thinking`` option could never take effect on this template.

The template check reads llama-server's ``/props`` (a static fact about the
served template).  When the template is not readable the answer is
``effective: None`` with the literal ``NOT MEASURED`` — never a guess.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from server.config import Settings
from server.readiness import check_thinking_template, collect
from test_tools_scripts import _usable_bash

ROOT = Path(__file__).resolve().parents[1]

JINJA_TEMPLATE = (
    "{% for message in messages %}{{ message['content'] }}{% endfor %}"
    "{% if enable_thinking %}...{% endif %}"
)
PLAIN_TEMPLATE = "{% for message in messages %}{{ message['content'] }}{% endfor %}"


# --------------------------------------------------------------- launcher plan
def test_the_generated_launcher_command_includes_jinja() -> None:
    command = Settings(model_path="models/qwen.gguf").llama_server_command("llama-server")
    assert "--jinja" in command
    assert command[0] == "llama-server"


def test_the_bash_launcher_prints_a_plan_with_jinja(tmp_path: Path) -> None:
    """The launcher is executed, not just grepped: its real plan is checked."""
    bash = _usable_bash()
    if bash is None:
        pytest.skip("no usable bash interpreter (WSL stub and Git Bash both absent)")
    env = {
        **os.environ,
        "PVAI_PYTHON": sys.executable,
        "PVAI_MODEL_PATH": str(tmp_path / "qwen.gguf"),
        "PVAI_DATA_DIR": str(tmp_path),
    }
    completed = subprocess.run(
        # ``--exe`` skips the PATH lookup for a real llama-server: this test is
        # about the generated plan, not about the binary being installed.
        [bash, str(ROOT / "tools" / "start_llama.sh"), "--exe", sys.executable,
         "--dry-run"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    assert "--jinja" in completed.stdout or "--jinja" in completed.stderr


@pytest.mark.parametrize(
    "name, marker",
    [
        ("start_llama.sh", "omitted --jinja"),
        ("start_llama.ps1", "-notcontains '--jinja'"),
    ],
)
def test_launchers_refuse_to_drop_the_flag(name: str, marker: str) -> None:
    """A plan from anywhere else must not start llama-server without --jinja."""
    text = (ROOT / "tools" / name).read_text(encoding="utf-8")
    assert marker in text, f"{name} has no --jinja guard"
    assert "--jinja" in text


# --------------------------------------------------------- readiness: template
def test_a_template_that_reads_enable_thinking_is_reported_as_effective() -> None:
    report = check_thinking_template(Settings(), {"props": {"chat_template": JINJA_TEMPLATE}})
    assert report["effective"] is True
    assert report["ok"] is True
    assert "warning" not in report


def test_a_template_without_the_variable_warns_about_jinja() -> None:
    report = check_thinking_template(Settings(), {"props": {"chat_template": PLAIN_TEMPLATE}})
    assert report["effective"] is False
    assert report["ok"] is True  # a warning, not a readiness failure
    assert "--jinja" in report["warning"]


def test_an_unreadable_template_is_not_measured_rather_than_guessed() -> None:
    for backend in ({}, {"props": {}}, {"props": {"chat_template": ""}}, None):
        report = check_thinking_template(Settings(), backend)
        assert report["effective"] is None
        assert "NOT MEASURED" in report["detail"]


def test_the_readiness_report_carries_the_template_check() -> None:
    report = collect(
        Settings(data_dir="."),
        backend={
            "reachable": True,
            "model_available": True,
            "props": {"chat_template": PLAIN_TEMPLATE},
        },
    )
    assert report["checks"]["thinking_template"]["effective"] is False
    assert "warning" in report["checks"]["thinking_template"]


class _PropsLlm:
    """A backend that answers /health, /v1/models and /props."""

    def __init__(self, template: str = PLAIN_TEMPLATE) -> None:
        self.template = template

    async def health(self) -> dict[str, Any]:
        from server.llm import LlamaServerClient

        settings = Settings(model_path="qwen.gguf")

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/health":
                return httpx.Response(200, json={"status": "ok"})
            if request.url.path == "/v1/models":
                return httpx.Response(200, json={"data": [{"id": "qwen.gguf"}]})
            if request.url.path == "/props":
                return httpx.Response(200, json={"chat_template": self.template})
            return httpx.Response(404)

        client = LlamaServerClient(settings)
        await client.aclose()
        client._client = httpx.AsyncClient(
            base_url=settings.llama_server_url, transport=httpx.MockTransport(handler)
        )
        try:
            return await client.health()
        finally:
            await client.aclose()

    async def aclose(self) -> None:
        return None


def test_health_reads_the_served_chat_template_from_props() -> None:
    import asyncio

    health = asyncio.run(_PropsLlm(JINJA_TEMPLATE).health())
    assert health["props"]["chat_template"] == JINJA_TEMPLATE
    report = collect(Settings(data_dir="."), backend=health)
    assert report["checks"]["thinking_template"]["effective"] is True


def test_ready_surfaces_the_thinking_warning(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from server.main import create_app

    settings = Settings(data_dir=str(tmp_path), history_persist=False)
    app = create_app(settings=settings, llm=_PropsLlm(PLAIN_TEMPLATE))
    with TestClient(app) as client:
        body = client.get("/ready").json()
    template_check = body["checks"]["thinking_template"]
    assert template_check["effective"] is False
    assert "--jinja" in template_check["warning"]
