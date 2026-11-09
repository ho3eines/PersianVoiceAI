"""Every operator script must work on a POSIX shell, not only PowerShell.

The project was Windows-first: all launchers were ``.ps1``, which left Linux and
macOS users with no supported entry point even though the Python beneath is
portable.  These tests keep the bash equivalents syntactically valid and stop
them drifting away from the PowerShell originals.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"

#: Every PowerShell launcher that has a bash counterpart.
PARITY = {
    "start_server": ("start_server.sh", "start_server.ps1"),
    "chat": ("chat.sh", "chat.ps1"),
    "benchmark": ("benchmark.sh", "benchmark.ps1"),
    "start_llama": ("start_llama.sh", "start_llama.ps1"),
}

BASH_SCRIPTS = sorted(TOOLS.glob("*.sh"))


def test_the_expected_scripts_exist() -> None:
    names = {path.name for path in BASH_SCRIPTS}
    for bash_name, _ in PARITY.values():
        assert bash_name in names, f"{bash_name} is missing from tools/"


@pytest.mark.parametrize("script", BASH_SCRIPTS, ids=lambda p: p.name)
def test_script_parses_under_bash(script: Path) -> None:
    """`bash -n` catches the quoting and heredoc bugs that silently pass review."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is not installed")
    completed = subprocess.run(
        [bash, "-n", str(script)], capture_output=True, text=True, timeout=30
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("script", BASH_SCRIPTS, ids=lambda p: p.name)
def test_script_is_executable(script: Path) -> None:
    assert script.stat().st_mode & 0o111, f"{script.name} is not marked executable"


@pytest.mark.parametrize(
    "script", BASH_SCRIPTS, ids=lambda p: p.name
)
def test_script_uses_a_portable_python_lookup(script: Path) -> None:
    """Hard-coding .venv/bin/python is fine; it must still be overridable."""
    text = script.read_text()
    assert "PVAI_PYTHON" in text, f"{script.name} ignores PVAI_PYTHON"


@pytest.mark.parametrize("key", sorted(PARITY))
def test_bash_and_powershell_versions_both_ship(key: str) -> None:
    bash_name, powershell_name = PARITY[key]
    assert (TOOLS / bash_name).exists(), bash_name
    assert (TOOLS / powershell_name).exists(), powershell_name


def test_no_powershell_only_launcher_is_left_unmatched() -> None:
    """A `.ps1` without a `.sh` means a Windows-only path nobody noticed."""
    unmatched = [
        script.name
        for script in TOOLS.glob("*.ps1")
        if not (TOOLS / (script.stem + ".sh")).exists()
    ]
    # setup_ollama.ps1 is Ollama-specific and deliberately not ported; see
    # docs/PLATFORMS.md.  Anything new appearing here must be a decision.
    assert sorted(unmatched) == ["setup_ollama.ps1"], unmatched


def test_the_readme_documents_both_platforms() -> None:
    readme = (ROOT / "README.md").read_text()
    assert "tools/start_server.sh" in readme
    assert "tools\\start_server.ps1" in readme


# --------------------------------------------------------------- repositories
def test_requirements_do_not_ship_test_tooling_in_runtime() -> None:
    """pytest in requirements.txt would land in every deployment."""
    runtime = (ROOT / "requirements.txt").read_text().lower()
    assert "pytest" not in runtime
    assert "pytest" in (ROOT / "requirements-dev.txt").read_text().lower()


def test_dev_requirements_build_on_the_runtime_set() -> None:
    """Otherwise `pip install -r requirements-dev.txt` gives an unrunnable venv."""
    assert "-r requirements.txt" in (ROOT / "requirements-dev.txt").read_text()


def test_voice_requirements_stay_optional() -> None:
    """The voice stack must not leak into the runtime set."""
    runtime = (ROOT / "requirements.txt").read_text().lower()
    for package in ("piper", "faster-whisper", "sounddevice", "numpy", "scipy"):
        assert package not in runtime, package


def test_a_licence_file_exists_and_states_the_licence() -> None:
    licence = ROOT / "LICENSE"
    assert licence.exists()
    text = licence.read_text()
    assert "GNU GENERAL PUBLIC LICENSE" in text
    assert "Version 3" in text


def test_third_party_document_covers_every_model_and_piper() -> None:
    """The GPL of piper-tts is the reason for the project licence; it must be named."""
    text = (ROOT / "THIRD_PARTY.md").read_text()
    for name in ("piper-tts", "GPL-3.0-or-later", "Qwen3-4B", "Whisper", "Apache-2.0", "MIT"):
        assert name in text, name


def test_no_stray_api_capture_is_committed_at_the_root() -> None:
    """response.json was a debug capture from a real chat completion."""
    assert not (ROOT / "response.json").exists()
