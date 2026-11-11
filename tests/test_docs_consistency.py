"""Documentation must describe the code that exists, not a remembered version.

Every check here caught a real discrepancy when it was written:

* the env-var tables had drifted from ``Settings``,
* ``STATUS.md`` said "3 SpeexDSP + 2 numpy" skips when it is the other way round,
* a test count said 585 when the suite had 592,
* ``ISSUE_REGISTER.md`` pointed at a test name that did not exist,
* the repository map omitted ``server/security.py`` and the bash scripts.

A docs/code mismatch is not cosmetic: this project's rule is that an unverified
claim is forbidden, so stale numbers are a correctness bug and not a typo.
"""

from __future__ import annotations

import re
import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = [ROOT / "README.md", ROOT / "AGENTS.md", ROOT / "THIRD_PARTY.md"] + sorted(
    (ROOT / "docs").glob("*.md")
)


#: A declaration in .env.example, commented out or not.  A commented line is
#: still documentation -- PVAI_SYSTEM_PROMPT ships commented so the default in
#: server/config.py is used, but it is documented all the same.
_DECLARED = re.compile(r"^\s*#?\s*(PVAI_[A-Z0-9_]+)=", re.M)


def _declared_in_example() -> set[str]:
    return set(_DECLARED.findall((ROOT / ".env.example").read_text(encoding="utf-8")))


def _env_names_read_by_code() -> set[str]:
    """Extract configuration lookups, not names mentioned in comments/docstrings.

    Script-only PVAI_PYTHON is documented separately in PLATFORMS.md.
    """
    names: set[str] = set()
    for path in (ROOT / "server").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            first = node.args[0]
            if (isinstance(first, ast.Constant) and isinstance(first.value, str)
                    and re.fullmatch(r"PVAI_[A-Z0-9_]+", first.value)):
                names.add(first.value)
    return names


# --------------------------------------------------------------- env variables
def test_every_env_var_in_the_example_is_real() -> None:
    """.env.example must not promise a switch that nothing reads."""
    declared = _declared_in_example()
    unknown = declared - _env_names_read_by_code()
    assert not unknown, f".env.example documents unread variables: {sorted(unknown)}"


def test_every_env_var_the_code_reads_is_in_the_example() -> None:
    missing = _env_names_read_by_code() - _declared_in_example()
    assert not missing, f"code reads undocumented variables: {sorted(missing)}"


def test_configuration_reference_matches_the_example_file() -> None:
    """The two tables are edited by hand; they must not drift apart."""
    conf = set(re.findall(r"`(PVAI_[A-Z0-9_]+)`", (ROOT / "docs" / "CONFIGURATION.md").read_text(encoding="utf-8")))
    example = _declared_in_example()
    assert conf == example, {
        "only in CONFIGURATION.md": sorted(conf - example),
        "only in .env.example": sorted(example - conf),
    }

    from server.config import Settings

    phrase_line = next(
        line for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        if line.startswith("PVAI_ASR_HALLUCINATION_PHRASES=")
    )
    assert phrase_line.split("=", 1)[1] == Settings().asr_hallucination_phrases
    platform_doc = (ROOT / "docs/PLATFORMS.md").read_text(encoding="utf-8")
    assert "PVAI_PYTHON" in platform_doc


# ------------------------------------------------------------------ file paths
def test_every_source_file_named_in_the_docs_exists() -> None:
    pattern = re.compile(
        r"`((?:server|tools|tests|benchmarks)/[A-Za-z0-9_./-]+\.(?:py|ps1|sh|json))`"
    )
    broken: dict[str, list[str]] = {}
    for doc in DOCS:
        for match in pattern.finditer(doc.read_text(encoding="utf-8")):
            if not (ROOT / match.group(1)).exists():
                broken.setdefault(match.group(1), []).append(doc.name)
    assert not broken, f"documented files that do not exist: {broken}"


def test_every_test_reference_points_at_a_real_test() -> None:
    """`file.py::name` in a status table is a claim; it must resolve."""
    defined: dict[str, str] = {}
    for path in (ROOT / "tests").glob("test_*.py"):
        for match in re.finditer(r"^def (test_[a-z0-9_]+)", path.read_text(encoding="utf-8"), re.M):
            defined[match.group(1)] = path.name

    pattern = re.compile(r"`(tests/[\w]+\.py)::(test_[\w]+)`")
    broken = []
    for doc in DOCS:
        for match in pattern.finditer(doc.read_text(encoding="utf-8")):
            filename, test_name = match.group(1), match.group(2)
            if test_name not in defined:
                broken.append(f"{doc.name}: {filename}::{test_name} (undefined)")
            elif defined[test_name] != Path(filename).name:
                broken.append(f"{doc.name}: {filename}::{test_name} is in {defined[test_name]}")
    assert not broken, broken


def test_the_repository_map_lists_every_server_module() -> None:
    """AGENTS.md is the entry point; an incomplete map misleads every later agent."""
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    modules = {
        path.name
        for path in (ROOT / "server").glob("*.py")
        if path.name not in {"__init__.py"}
    }
    missing = sorted(name for name in modules if name not in agents)
    assert not missing, f"server modules absent from the AGENTS.md map: {missing}"


# --------------------------------------------------------------- claims/numbers
#: The one line in ``docs/STATUS.md`` that carries the suite total.  It exists
#: because a hard-coded number rots: the count is read from pytest itself, so a
#: stale total is a failing test instead of a wrong claim.
_TOTAL_MARK = re.compile(
    r"تعداد تست‌های مجموعه \(pytest --collect-only\): \*\*(\d+)\*\*"
)


def _live_collected_tests() -> int | None:
    """Total tests pytest collects right now; ``None`` if collection fails.

    Collection only — nothing is executed — so this stays cheap and skips do not
    change the number.  ``addopts`` is cleared so a caller's ``-k``/``-q``
    options cannot alter what is counted.
    """
    completed = subprocess.run(
        [
            sys.executable, "-m", "pytest", "--collect-only", "-q",
            "-p", "no:cacheprovider", "-o", "addopts=",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=900,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if completed.returncode != 0:
        return None
    match = re.search(r"^(\d+) tests? collected", completed.stdout, re.M)
    return int(match.group(1)) if match else None


def test_the_documented_suite_total_is_the_live_collected_count() -> None:
    """The number in STATUS.md is compared with ``pytest --collect-only``.

    Once collection is clean, the assertion is exact: add a test and this fails
    until the documented number is updated.  When the suite cannot even be
    collected (an optional dependency is missing in this environment) the total
    is NOT MEASURED and the check says so instead of guessing.
    """
    live = _live_collected_tests()
    if live is None:
        pytest.skip(
            "the suite does not collect cleanly here; the suite total is NOT MEASURED"
        )
    text = (ROOT / "docs" / "STATUS.md").read_text(encoding="utf-8")
    match = _TOTAL_MARK.search(text)
    assert match, (
        "docs/STATUS.md must carry the suite total on the marked line "
        "(see _TOTAL_MARK in tests/test_docs_consistency.py)"
    )
    assert int(match.group(1)) == live, (
        f"docs/STATUS.md claims {match.group(1)} tests, "
        f"pytest --collect-only collected {live}"
    )


def test_no_second_document_contradicts_the_collected_total() -> None:
    """One authoritative number; other files must not claim a different one."""
    live = _live_collected_tests()
    if live is None:
        pytest.skip("the suite does not collect cleanly here")
    pattern = re.compile(r"(?:مجموع|کل)\s*(?:تست‌ها|تست‌های مجموعه)?\D{0,12}(\d{2,5})\s*تست")
    for name in ("README.md", "AGENTS.md", "docs/ISSUE_REGISTER.md", "docs/STATUS.md"):
        text = (ROOT / name).read_text(encoding="utf-8").translate(
            str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
        )
        for match in pattern.finditer(text):
            assert int(match.group(1)) == live, (
                f"{name} states a suite total of {match.group(1)}, collected {live}"
            )


def test_no_current_document_pins_a_fixed_pass_count() -> None:
    """Run pytest to learn the live total; stale hard-coded totals are forbidden."""
    for name in ("README.md", "AGENTS.md", "docs/STATUS.md", "docs/ISSUE_REGISTER.md"):
        text = (ROOT / name).read_text(encoding="utf-8").translate(
            str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
        assert not re.search(r"\d+\s+passed", text), name


@pytest.mark.parametrize("name", ["README.md", "AGENTS.md", "LICENSE", "THIRD_PARTY.md"])
def test_the_entry_point_files_exist(name: str) -> None:
    assert (ROOT / name).exists()


def test_no_document_claims_the_api_is_unauthenticated() -> None:
    """Stale wording that the security work invalidated; it under-secures deploys."""
    offenders = [
        doc.name
        for doc in DOCS
        if "احراز هویت ندارد" in doc.read_text(encoding="utf-8")
    ]
    assert not offenders, offenders


def test_every_relative_documentation_link_resolves() -> None:
    """A dead link is a claim that the reader can find the thing; they cannot."""
    broken = []
    for doc in DOCS:
        for match in re.finditer(r"\]\(([^)#][^)]*?)\)", doc.read_text(encoding="utf-8")):
            target = match.group(1).split("#")[0]
            if not target or target.startswith(("http", "mailto", "/")):
                continue
            if not (doc.parent / target).resolve().exists():
                broken.append(f"{doc.name} -> {target}")
    assert not broken, broken


def test_current_docs_do_not_pin_a_stale_pytest_pass_count() -> None:
    """A changing parametrized suite must not advertise a hard-coded count."""
    for name in ('README.md', 'AGENTS.md', 'docs/STATUS.md', 'docs/ISSUE_REGISTER.md'):
        text = (ROOT / name).read_text(encoding='utf-8').translate(
            str.maketrans('۰۱۲۳۴۵۶۷۸۹', '0123456789'))
        assert not re.search(r'\d+\s+passed', text), name


def test_unified_guide_is_up_to_date() -> None:
    from tools.build_docs import OUTPUT, render
    assert OUTPUT.read_text(encoding='utf-8') == render(), 'Run python tools/build_docs.py'


def test_numeric_configuration_defaults_match_code() -> None:
    """Prevent hand-maintained numeric default tables from drifting again."""
    text = (ROOT / 'docs/CONFIGURATION.md').read_text(encoding='utf-8')
    tree = ast.parse((ROOT / 'server/config.py').read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in {'_env_int', '_env_float'} and len(node.args) >= 2
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[1], ast.Constant)):
            continue
        name, default = node.args[0].value, node.args[1].value
        row = re.search(r'\| `' + name + r'` \| `([^`]+)`', text)
        assert row is not None, f'No numeric default documented for {name}'
        assert float(row.group(1)) == default, (name, row.group(1), default)


def test_voice_requirements_are_in_license_table() -> None:
    text = (ROOT / 'THIRD_PARTY.md').read_text(encoding='utf-8')
    for line in (ROOT / 'requirements-voice.txt').read_text(encoding='utf-8').splitlines():
        if not line or line.startswith(('#', '-')):
            continue
        package = re.split(r'[<>=!~\[]', line)[0]
        assert f'`{package}`' in text, f'Missing licence entry: {package}'
