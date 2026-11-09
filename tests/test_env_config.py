"""``.env`` parsing: inline comments, silent fallbacks, and where the file is found.

The bug these tests pin down: ``.env.example`` documents most variables with a
trailing ``# comment``.  ``load_dotenv`` kept the comment as part of the value,
so every numeric parse failed — and the failure was invisible, because the
``_env_int``/``_env_float`` helpers fell back to their default.  ``Copy-Item
.env.example .env`` (the documented first step) therefore produced a service
that ignored ``PVAI_N_THREADS`` and friends while looking correctly configured.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from server.config import Settings, load_dotenv

# The repository root, computed here so this module does not depend on a symbol
# that only the fixed implementation exposes.
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Imported defensively so this module still *collects* against the previous
# implementation: every test below must then fail on behaviour (a silent
# fallback, an unfound .env) instead of erroring out at import time and hiding
# whether the rest of the suite ran.
try:  # pragma: no cover - exercised only against the old code
    from server.config import ENV_ISSUES, dotenv_candidates, strip_inline_comment
except ImportError:  # pragma: no cover

    def strip_inline_comment(value: str) -> str:  # type: ignore[no-redef]
        raise AssertionError("server.config.strip_inline_comment is missing")

    def dotenv_candidates(path=None):  # type: ignore[no-redef]
        raise AssertionError("server.config.dotenv_candidates is missing")

    ENV_ISSUES = None  # type: ignore[assignment]

ENV_EXAMPLE = PROJECT_ROOT / ".env.example"


@pytest.fixture(autouse=True)
def _clean_environment() -> None:
    """No inherited ``PVAI_*`` value may leak into these tests."""
    saved = {key: os.environ.pop(key) for key in list(os.environ) if key.startswith("PVAI_")}
    if ENV_ISSUES is not None:
        ENV_ISSUES.clear()
    yield
    for key in [key for key in os.environ if key.startswith("PVAI_")]:
        del os.environ[key]
    os.environ.update(saved)
    if ENV_ISSUES is not None:
        ENV_ISSUES.clear()


def _intended_values(path: Path) -> dict[str, str]:
    """The value an author meant: everything before an inline `` #`` comment."""
    intended: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw = line.split("=", 1)
        intended[key.strip()] = strip_inline_comment(raw)
    return intended


# --------------------------------------------------------------- the headline
def test_copying_env_example_produces_the_values_it_documents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``Copy-Item .env.example .env`` must not silently lose a single value.

    Every variable whose text parses as a number must come back as that number.
    Before the fix ``PVAI_N_THREADS=2   # …`` became ``0`` and the failure was
    invisible.
    """
    copied = tmp_path / ".env"
    copied.write_text(ENV_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    settings = Settings.from_env()

    numeric: dict[str, float] = {
        "PVAI_N_CTX": settings.n_ctx,
        "PVAI_N_THREADS": settings.n_threads,
        "PVAI_N_GPU_LAYERS": settings.n_gpu_layers,
        "PVAI_TEMPERATURE": settings.temperature,
        "PVAI_MAX_TOKENS": settings.max_tokens,
        "PVAI_TIMEOUT": settings.request_timeout_s,
        "PVAI_ASR_THREADS": settings.asr_threads,
        "PVAI_VOICE_VAD_THRESHOLD": settings.voice_vad_threshold,
        "PVAI_VOICE_ASR_INTERVAL": settings.voice_asr_interval_s,
        "PVAI_VOICE_RECORD_SECONDS": settings.voice_record_seconds,
        "PVAI_AEC_DELAY_MS": settings.aec_delay_ms,
        "PVAI_AUDIO_REOPEN_STABLE_FRAMES": settings.audio_reopen_stable_frames,
        "PVAI_AUDIO_STALL_S": settings.audio_stall_s,
    }
    intended = _intended_values(ENV_EXAMPLE)
    checked = 0
    for name, actual in numeric.items():
        if name not in intended:
            continue
        want = float(intended[name])
        assert float(actual) == want, f"{name}: file says {want}, settings say {actual}"
        checked += 1
    assert checked >= 10, f"only {checked} numeric variables were covered"

    # A copy must be a *clean* configuration: nothing was rejected or clamped.
    assert settings.env_issues == (), settings.env_issues


def test_env_example_has_no_inline_comments_left_to_bite_anyone() -> None:
    """Comments belong on their own line; a value never ends in whitespace + #."""
    for number, line in enumerate(
        ENV_EXAMPLE.read_text(encoding="utf-8").splitlines(), start=1
    ):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        _, raw = stripped.split("=", 1)
        assert "#" not in raw, f".env.example:{number} still has an inline comment: {line}"


# ------------------------------------------------------------- comment parsing
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2          # 0 = auto", "2"),
        ("5   # فقط حالت عیب‌یابی", "5"),
        ("Microphone  # device", "Microphone"),
        ("plain", "plain"),
        ("abc#def", "abc#def"),  # no space before # -> part of the value
        ('"a # b"', '"a # b"'),  # quoted: never a comment
        ("'x # y'", "'x # y'"),
        ("# whole value is a comment", ""),
        ("", ""),
    ],
)
def test_strip_inline_comment(raw: str, expected: str) -> None:
    assert strip_inline_comment(raw) == expected


def test_commented_values_survive_a_round_trip(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "\n".join(
            [
                "PVAI_N_THREADS=3        # three logical threads",
                'PVAI_SYSTEM_PROMPT="یک # دو"',
                "PVAI_AUDIO_INPUT_DEVICE=Realtek Microphone",
                "PVAI_AEC_DELAY_MS=55   # calibrated on this laptop",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    load_dotenv(env)
    assert os.environ["PVAI_N_THREADS"] == "3"
    assert os.environ["PVAI_SYSTEM_PROMPT"] == "یک # دو"
    # A device name keeps its spaces; only whitespace + # starts a comment.
    assert os.environ["PVAI_AUDIO_INPUT_DEVICE"] == "Realtek Microphone"
    assert os.environ["PVAI_AEC_DELAY_MS"] == "55"


def test_existing_environment_still_wins_over_the_file(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("PVAI_N_THREADS=8\n", encoding="utf-8")
    os.environ["PVAI_N_THREADS"] = "1"
    load_dotenv(env)
    assert os.environ["PVAI_N_THREADS"] == "1"


# --------------------------------------------------- loud rejection / clamping
def test_invalid_number_is_reported_not_silently_defaulted(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("PVAI_N_THREADS", "four")
    with caplog.at_level("WARNING"):
        settings = Settings.from_env()

    assert settings.n_threads == 0  # documented default
    assert any(name == "PVAI_N_THREADS" for name, _ in settings.env_issues)
    assert any("PVAI_N_THREADS" in message for message in caplog.messages)
    # And the problem is visible through the readiness/startup channel.
    assert any("PVAI_N_THREADS" in error for error in settings.config_errors())


def test_clamped_value_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PVAI_AEC_DELAY_MS", "100000")  # maximum is 500
    settings = Settings.from_env()
    assert settings.aec_delay_ms == 500
    assert ("PVAI_AEC_DELAY_MS",) == tuple(
        name for name, reason in settings.env_issues if "clamped" in reason
    )


def test_invalid_boolean_and_choice_are_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PVAI_WS_ENABLED", "maybe")
    monkeypatch.setenv("PVAI_HISTORY_RESUME", "sometimes")
    settings = Settings.from_env()
    assert settings.ws_enabled is False
    assert settings.history_resume == "on"
    reported = {name for name, _ in settings.env_issues}
    assert {"PVAI_WS_ENABLED", "PVAI_HISTORY_RESUME"} <= reported


def test_a_clean_environment_reports_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PVAI_N_THREADS", "2")
    monkeypatch.setenv("PVAI_AEC_DELAY_MS", "40")
    assert Settings.from_env().env_issues == ()


def test_issues_do_not_accumulate_across_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PVAI_N_THREADS", "bad")
    first = Settings.from_env()
    assert first.env_issues
    monkeypatch.delenv("PVAI_N_THREADS")
    second = Settings.from_env()
    assert second.env_issues == (), "a later clean read inherited stale problems"


def test_ready_endpoint_surfaces_rejected_values(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """/ready must show the problem instead of claiming a valid configuration."""
    from fastapi.testclient import TestClient

    from server.main import create_app

    monkeypatch.setenv("PVAI_N_THREADS", "not-a-number")
    monkeypatch.setenv("PVAI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PVAI_AUDIO_INPUT_DEVICE", "")
    settings = Settings.from_env()
    with TestClient(create_app(settings=settings, llm=None)) as client:
        body = client.get("/ready").json()
    configuration = body["checks"]["configuration"]
    assert configuration["ok"] is False
    assert any("PVAI_N_THREADS" in error for error in configuration["errors"])


# ------------------------------------------------------------------ file lookup
def test_dotenv_is_found_from_any_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Running from another directory used to ignore the project's ``.env``."""
    monkeypatch.chdir(tmp_path)
    candidates = dotenv_candidates()
    assert tmp_path / ".env" in candidates
    assert PROJECT_ROOT / ".env" in candidates


def test_current_directory_env_wins_over_the_project_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text("PVAI_N_THREADS=7\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    load_dotenv()
    assert os.environ["PVAI_N_THREADS"] == "7"


def test_explicit_path_is_used_verbatim(tmp_path: Path) -> None:
    env = tmp_path / "custom.env"
    env.write_text("PVAI_N_THREADS=5\n", encoding="utf-8")
    assert dotenv_candidates(env) == [env]
    load_dotenv(env)
    assert os.environ["PVAI_N_THREADS"] == "5"


def test_a_missing_dotenv_is_not_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    load_dotenv(tmp_path / "does-not-exist.env")  # must not raise
    assert Settings.from_env() is not None


# ------------------------------------------------------- llama-server launch
def test_launch_command_uses_the_configured_flags() -> None:
    settings = Settings(
        model_path="models/x.gguf",
        llama_server_url="http://127.0.0.1:9090",
        n_ctx=2048,
        n_threads=4,
        n_gpu_layers=0,
    )
    assert settings.llama_server_command() == [
        "llama-server",
        "-m",
        "models/x.gguf",
        "-c",
        "2048",
        "--host",
        "127.0.0.1",
        "--port",
        "9090",
        "-t",
        "4",
        "-ngl",
        "0",
    ]


def test_launch_command_omits_threads_when_auto() -> None:
    """``PVAI_N_THREADS=0`` means "llama-server decides", not ``-t 0``."""
    command = Settings(n_threads=0).llama_server_command()
    assert "-t" not in command
    assert "-ngl" in command


def test_the_file_flags_and_the_documented_manual_command_agree() -> None:
    """README used ``-t 4`` while .env.example said 2: one of them had to go."""
    intended = _intended_values(ENV_EXAMPLE)
    assert intended["PVAI_N_THREADS"] == "4", (
        "docs/SPEC.md measured on 4 threads (2 physical cores x 2); README and "
        "PC_VOICE pass -t 4, so the example must match"
    )
    for doc in ("README.md", "docs/PC_VOICE.md"):
        text = (PROJECT_ROOT / doc).read_text(encoding="utf-8")
        # A *command* must no longer restate the thread count; prose that warns
        # about the mismatch is fine.
        commands = [
            line
            for line in text.splitlines()
            if "llama-server" in line and "-t " in line and not line.lstrip().startswith((">", "#"))
        ]
        assert not commands, f"{doc} still hard-codes a thread count: {commands}"


def test_launch_cli_prints_the_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from server.config import _print_launch

    monkeypatch.setenv("PVAI_MODEL_PATH", "models/m.gguf")
    monkeypatch.setenv("PVAI_N_THREADS", "4")
    assert _print_launch(["--launch"]) == 0
    printed = capsys.readouterr().out
    assert printed.startswith("llama-server -m models/m.gguf")
    assert "-t 4" in printed


def test_launch_cli_warns_about_rejected_values(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from server.config import _print_launch

    monkeypatch.setenv("PVAI_N_THREADS", "many")
    assert _print_launch(["--launch"]) == 0
    assert "PVAI_N_THREADS" in capsys.readouterr().err
