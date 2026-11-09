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
def test_readme_and_status_agree_on_the_test_count() -> None:
    """Both quote a total; two different totals means one is stale."""
    def totals(text: str) -> set[int]:
        normalised = text.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
        return {
            int(n)
            for n in re.findall(r"(\d+)\s*passed", normalised)
        }

    readme = max(totals((ROOT / "README.md").read_text(encoding="utf-8")))
    status = max(totals((ROOT / "docs" / "STATUS.md").read_text(encoding="utf-8")))
    assert readme == status, (readme, status)


def test_the_documented_test_total_is_not_absurd() -> None:
    """A cheap sanity bound: the claim must be plausible for this repository.

    The exact figure is checked by running the suite; this only catches a
    number edited by a factor of ten, which no manual review reliably spots.
    """
    normalised = (ROOT / "docs" / "STATUS.md").read_text(encoding="utf-8").translate(
        str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
    )
    claimed = max(int(n) for n in re.findall(r"(\d+)\s*passed", normalised))
    defined = sum(
        1
        for path in (ROOT / "tests").glob("test_*.py")
        for _ in re.finditer(r"^def (test_[a-z0-9_]+)", path.read_text(encoding="utf-8"), re.M)
    )
    # Parametrisation multiplies functions into cases, so cases >= functions.
    assert claimed >= defined, (claimed, defined)
    assert claimed < defined * 10, (claimed, defined)


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
