"""Configuration files for the offline data/training tooling (JSON or YAML).

Why this module exists
----------------------
``tools/prepare_dataset.py`` used to be configured entirely by flags, and the
hyper-parameters a trainer needs (batch size, learning rate, epochs, seed,
output directory) existed nowhere except in prose in ``docs/DATASETS.md``.  That
made two failure modes easy: a re-run that silently used a different batch size
than the run it was compared with, and a hand-written manifest path that only
existed on one machine.

Everything here is deliberately small and dependency-light:

* **One schema, two formats.** ``.json`` always works (stdlib).  ``.yaml`` works
  when PyYAML is installed (it is a dev/tooling dependency, not a runtime one)
  and otherwise fails with a message that says how to proceed.
* **Paths are portable.** Every path value is expanded (``${CORPUS_ROOT}``,
  ``$HOME``, ``~``) and, when relative, resolved **against the configuration
  file's directory** — never against the shell's working directory.  The same
  config therefore works from any ``cd``.
* **Typos are errors.** An unknown key is rejected with a suggestion instead of
  being ignored: a mistyped ``learnig_rate`` would otherwise silently fall back
  to a default and change the experiment.
* **Ranges are checked here, not on the GPU.** ``batch_size`` must be positive,
  ``learning_rate`` must be in ``(0, 1]``, ``epochs`` must be positive, and so
  on.  A numerically invalid config is a config error, not a training crash
  twenty minutes in.

No training loop lives in this repository; :class:`TrainingConfig` validates the
*contract* between this data tooling and an external trainer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import get_close_matches
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

#: Environment variables that can point at a configuration file.  They are
#: read by the ``tools/`` entry points (never by the server runtime), so a
#: deployment without this tooling does not depend on them.
DATASET_CONFIG_ENV = "PVAI_DATASET_CONFIG"
TRAINING_CONFIG_ENV = "PVAI_TRAIN_CONFIG"
#: Root of the recording corpus; used when a config/CLI omits ``audio_root``.
CORPUS_ROOT_ENV = "PVAI_CORPUS_ROOT"

SUPPORTED_SUFFIXES = frozenset({".json", ".yaml", ".yml"})
PRECISIONS = frozenset({"fp32", "fp16", "bf16", "int8"})


class ConfigError(ValueError):
    """A configuration file is missing, unreadable, or invalid."""


# --------------------------------------------------------------------- paths
_VARIABLE = re.compile(
    r"\$(?:\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)(?::-?(?P<default>[^}]*))?\}"
    r"|(?P<bare>[A-Za-z_][A-Za-z0-9_]*))"
)


def expand_path(value: str | os.PathLike[str]) -> Path:
    """Expand ``~``, ``$VAR``/``${VAR}`` and ``${VAR:-default}``.

    Unlike :func:`os.path.expandvars`, an **undefined** variable without a
    default is an error: leaving ``${CORPUS_ROOT}`` in the path turns a missing
    environment variable into a confusing "manifest not found" pointing at a
    directory literally named ``${CORPUS_ROOT}``.  ``${VAR:-default}`` is
    supported so an example config can ship a sensible relative default.
    """
    text = str(value)
    unresolved: list[str] = []

    def substitute(match: re.Match[str]) -> str:
        name = match.group("braced") or match.group("bare")
        default = match.group("default")
        current = os.environ.get(name)
        if current:
            return current
        if default is not None:
            return default
        unresolved.append(name)
        return match.group(0)

    text = _VARIABLE.sub(substitute, text)
    if unresolved:
        unique = sorted(set(unresolved))
        raise ConfigError(
            f"undefined environment variable(s) in path {str(value)!r}: {unique}; "
            "set them (e.g. PVAI_CORPUS_ROOT) or use ${VAR:-default} in the config"
        )
    return Path(text).expanduser()


def resolve_path(
    value: str | os.PathLike[str] | None,
    base: Path,
    *,
    context: str = "path",
) -> Path | None:
    """Resolve ``value`` relative to ``base`` (the config file's directory)."""
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        candidate = expand_path(raw)
    except (OSError, ValueError) as exc:  # pragma: no cover - OS specific
        raise ConfigError(f"{context}: invalid path {raw!r} ({exc})") from exc
    if candidate.is_absolute():
        return candidate
    return (base / candidate)


def resolve_config_path(value: str | os.PathLike[str], *, context: str = "config") -> Path:
    """Locate a configuration file given on the command line or by env var."""
    path = expand_path(value)
    if not path.is_file():
        raise ConfigError(f"{context}: file not found: {path}")
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ConfigError(
            f"{context}: unsupported extension {path.suffix!r}; "
            f"use one of {sorted(SUPPORTED_SUFFIXES)}"
        )
    return path


# -------------------------------------------------------------------- loading
def load_document(path: str | os.PathLike[str], *, context: str = "config") -> dict[str, Any]:
    """Read a JSON or YAML mapping from ``path``.

    A YAML file requires PyYAML; when it is absent the error names the fallback
    (a ``.json`` config) instead of raising a bare ``ImportError``.
    """
    resolved = resolve_config_path(path, context=context)
    try:
        text = resolved.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise ConfigError(f"{context}: cannot read {resolved}: {exc}") from exc

    suffix = resolved.suffix.lower()
    if suffix == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConfigError(
                f"{context}: {resolved} is not valid JSON "
                f"(line {exc.lineno}, column {exc.colno}): {exc.msg}"
            ) from exc
    else:
        yaml = _yaml_module(context=context, path=resolved)
        try:
            data = yaml.safe_load(text)
        except Exception as exc:  # noqa: BLE001 - the parser raises many types
            raise ConfigError(f"{context}: {resolved} is not valid YAML: {exc}") from exc

    if data is None:
        raise ConfigError(f"{context}: {resolved} is empty; expected a mapping")
    if not isinstance(data, Mapping):
        raise ConfigError(
            f"{context}: {resolved} must contain a mapping at the top level, "
            f"got {type(data).__name__}"
        )
    return dict(data)


def _yaml_module(*, context: str, path: Path):
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise ConfigError(
            f"{context}: {path} is YAML but PyYAML is not installed. "
            "Install the development requirements (`python -m pip install -r "
            "requirements-dev.txt`) or save the same settings as JSON."
        ) from exc
    return yaml


# ------------------------------------------------------------- type checking
def reject_unknown(
    mapping: Mapping[str, Any],
    allowed: Iterable[str],
    *,
    context: str,
) -> None:
    """Fail on a key nobody reads — a typo must not become a silent default."""
    known = sorted(set(allowed))
    for key in mapping:
        if key in known:
            continue
        suggestion = get_close_matches(str(key), known, n=1)
        hint = f" (did you mean {suggestion[0]!r}?)" if suggestion else ""
        raise ConfigError(f"{context}: unknown key {key!r}{hint}; allowed keys: {known}")


def _present(mapping: Mapping[str, Any], key: str) -> bool:
    return key in mapping and mapping[key] is not None


def as_str(
    value: Any,
    *,
    context: str,
    default: str | None = None,
    allow_empty: bool = False,
) -> str:
    if value is None:
        if default is None:
            raise ConfigError(f"{context}: a value is required")
        return default
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ConfigError(f"{context}: expected a string, got {type(value).__name__}")
    text = str(value).strip()
    if not text and not allow_empty:
        raise ConfigError(f"{context}: must not be empty")
    return text


def as_bool(value: Any, *, context: str, default: bool | None = None) -> bool:
    if value is None:
        if default is None:
            raise ConfigError(f"{context}: a value is required")
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "on", "1"}:
            return True
        if lowered in {"false", "no", "off", "0"}:
            return False
    raise ConfigError(f"{context}: expected a boolean (true/false), got {value!r}")


def as_int(
    value: Any,
    *,
    context: str,
    default: int | None = None,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if value is None:
        if default is None:
            raise ConfigError(f"{context}: a value is required")
        result = default
    else:
        if isinstance(value, bool):
            raise ConfigError(f"{context}: expected an integer, got a boolean")
        if isinstance(value, float):
            if not math.isfinite(value) or not float(value).is_integer():
                raise ConfigError(f"{context}: expected a whole number, got {value!r}")
            result = int(value)
        elif isinstance(value, int):
            result = value
        elif isinstance(value, str) and value.strip().lstrip("+-").isdigit():
            result = int(value.strip())
        else:
            raise ConfigError(f"{context}: expected an integer, got {value!r}")
    if minimum is not None and result < minimum:
        raise ConfigError(f"{context}: {result} is below the minimum {minimum}")
    if maximum is not None and result > maximum:
        raise ConfigError(f"{context}: {result} is above the maximum {maximum}")
    return result


def as_float(
    value: Any,
    *,
    context: str,
    default: float | None = None,
    minimum: float | None = None,
    maximum: float | None = None,
    exclusive_minimum: bool = False,
) -> float:
    if value is None:
        if default is None:
            raise ConfigError(f"{context}: a value is required")
        result = float(default)
    else:
        if isinstance(value, bool):
            raise ConfigError(f"{context}: expected a number, got a boolean")
        try:
            result = float(value)
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"{context}: expected a number, got {value!r}") from exc
    if not math.isfinite(result):
        raise ConfigError(f"{context}: must be a finite number, got {value!r}")
    if minimum is not None:
        if exclusive_minimum and result <= minimum:
            raise ConfigError(f"{context}: {result} must be greater than {minimum}")
        if not exclusive_minimum and result < minimum:
            raise ConfigError(f"{context}: {result} is below the minimum {minimum}")
    if maximum is not None and result > maximum:
        raise ConfigError(f"{context}: {result} is above the maximum {maximum}")
    return result


def as_mapping(value: Any, *, context: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigError(f"{context}: expected a mapping, got {type(value).__name__}")
    return dict(value)


def as_list(value: Any, *, context: str) -> list[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise ConfigError(f"{context}: expected a list, got {type(value).__name__}")
    return list(value)


# ------------------------------------------------------------- dataset config
_SPLIT_KEYS = ("manifest", "audio_root", "sample_rate", "out", "min_duration_s", "max_duration_s")
_DATASET_KEYS = ("version", "sample_rate", "min_duration_s", "max_duration_s", "splits")
_TRAINING_KEYS = (
    "version", "model", "output_dir", "seed", "batch_size", "grad_accum",
    "learning_rate", "epochs", "warmup_steps", "precision", "data", "notes",
)
_DATA_KEYS = ("sample_rate", "min_duration_s", "max_duration_s", "train", "validation", "test")


@dataclass(frozen=True, slots=True)
class SplitConfig:
    """One validated manifest plus where its recordings and output live."""

    name: str
    manifest: Path
    audio_root: Path | None
    out: Path | None
    sample_rate: int
    min_duration_s: float = 0.0
    max_duration_s: float | None = None


@dataclass(frozen=True, slots=True)
class DatasetConfig:
    """A multi-split dataset configuration (``configs/dataset.example.yaml``)."""

    source: Path | None
    splits: tuple[SplitConfig, ...]
    base: Path
    notes: str = ""

    def split(self, name: str) -> SplitConfig | None:
        return next((item for item in self.splits if item.name == name), None)


@dataclass(frozen=True, slots=True)
class TrainingConfig:
    """The hyper-parameter contract an *external* trainer must honour.

    This repository contains no training loop (see ``docs/DATASETS.md``): the
    values are validated, reported and written to a resolved plan so the trainer
    that consumes the manifests cannot silently disagree with the run it is
    compared against.
    """

    source: Path | None
    model: str
    output_dir: Path
    base: Path
    seed: int = 13
    batch_size: int = 8
    grad_accum: int = 1
    learning_rate: float = 1e-5
    epochs: int = 3
    warmup_steps: int = 100
    precision: str = "fp32"
    data: tuple[SplitConfig, ...] = ()
    notes: str = ""
    unknown: dict[str, Any] = field(default_factory=dict, compare=False)

    def split(self, name: str) -> SplitConfig | None:
        return next((item for item in self.data if item.name == name), None)

    def effective_batch_size(self) -> int:
        return self.batch_size * max(1, self.grad_accum)

    def plan(self) -> dict[str, Any]:
        """A JSON-serialisable, machine-readable training plan."""
        return {
            "schema_version": 1,
            "config": str(self.source) if self.source is not None else None,
            "model": self.model,
            "output_dir": str(self.output_dir),
            "seed": self.seed,
            "batch_size": self.batch_size,
            "grad_accum": self.grad_accum,
            "effective_batch_size": self.effective_batch_size(),
            "learning_rate": self.learning_rate,
            "epochs": self.epochs,
            "warmup_steps": self.warmup_steps,
            "precision": self.precision,
            "data": [
                {
                    "split": item.name,
                    "manifest": str(item.manifest),
                    "audio_root": str(item.audio_root) if item.audio_root else None,
                    "sample_rate": item.sample_rate,
                }
                for item in self.data
            ],
            "notes": self.notes,
            "trainer": "external (this repository performs no training)",
        }


def _split_from_mapping(
    name: str,
    payload: Any,
    *,
    base: Path,
    context: str,
    default_sample_rate: int,
    default_min: float,
    default_max: float | None,
) -> SplitConfig:
    """Build one :class:`SplitConfig` from a ``splits.<name>`` mapping."""
    data = as_mapping(payload, context=context)
    reject_unknown(data, _SPLIT_KEYS, context=context)
    if not _present(data, "manifest"):
        raise ConfigError(f"{context}: 'manifest' is required")
    manifest = resolve_path(data["manifest"], base, context=f"{context}.manifest")
    assert manifest is not None  # _present() above
    audio_root = resolve_path(data.get("audio_root"), base, context=f"{context}.audio_root")
    if audio_root is None:
        corpus_root = os.getenv(CORPUS_ROOT_ENV)
        if corpus_root:
            audio_root = resolve_path(corpus_root, base, context=f"{CORPUS_ROOT_ENV}")
    out = resolve_path(data.get("out"), base, context=f"{context}.out")
    sample_rate = as_int(
        data.get("sample_rate"),
        context=f"{context}.sample_rate",
        default=default_sample_rate,
        minimum=1000,
        maximum=192000,
    )
    minimum = as_float(
        data.get("min_duration_s"),
        context=f"{context}.min_duration_s",
        default=default_min,
        minimum=0.0,
    )
    maximum = (
        as_float(
            data["max_duration_s"],
            context=f"{context}.max_duration_s",
            exclusive_minimum=True,
            minimum=0.0,
        )
        if _present(data, "max_duration_s")
        else default_max
    )
    if maximum is not None and maximum <= minimum:
        raise ConfigError(
            f"{context}: max_duration_s ({maximum}) must be greater than "
            f"min_duration_s ({minimum})"
        )
    return SplitConfig(
        name=name,
        manifest=manifest,
        audio_root=audio_root,
        out=out,
        sample_rate=sample_rate,
        min_duration_s=minimum,
        max_duration_s=maximum,
    )


def load_dataset_config(path: str | os.PathLike[str]) -> DatasetConfig:
    """Load and validate ``configs/dataset.example.yaml``-shaped configuration."""
    resolved = resolve_config_path(path, context="dataset config")
    data = load_document(resolved, context="dataset config")
    reject_unknown(data, _DATASET_KEYS, context="dataset config")
    base = resolved.parent
    sample_rate = as_int(
        data.get("sample_rate"),
        context="dataset config: sample_rate",
        default=16000,
        minimum=1000,
        maximum=192000,
    )
    minimum = as_float(
        data.get("min_duration_s"),
        context="dataset config: min_duration_s",
        default=0.0,
        minimum=0.0,
    )
    maximum = (
        as_float(
            data["max_duration_s"],
            context="dataset config: max_duration_s",
            exclusive_minimum=True,
            minimum=0.0,
        )
        if _present(data, "max_duration_s")
        else None
    )
    if maximum is not None and maximum <= minimum:
        raise ConfigError(
            f"dataset config: max_duration_s ({maximum}) must be greater than "
            f"min_duration_s ({minimum})"
        )
    raw_splits = data.get("splits")
    if raw_splits is None:
        raise ConfigError(
            "dataset config: 'splits' is required "
            "(e.g. splits: {train: {manifest: manifests/train.jsonl}})"
        )
    splits_mapping = as_mapping(raw_splits, context="dataset config: splits")
    if not splits_mapping:
        raise ConfigError("dataset config: 'splits' must not be empty")
    splits: list[SplitConfig] = []
    for name, payload in splits_mapping.items():
        split_name = as_str(name, context="dataset config: split name")
        splits.append(
            _split_from_mapping(
                split_name,
                payload,
                base=base,
                context=f"dataset config: splits.{split_name}",
                default_sample_rate=sample_rate,
                default_min=minimum,
                default_max=maximum,
            )
        )
    names = [item.name for item in splits]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ConfigError(f"dataset config: duplicate split names: {duplicates}")
    return DatasetConfig(
        source=resolved,
        splits=tuple(splits),
        base=base,
        notes=as_str(data.get("notes"), context="dataset config: notes", default="", allow_empty=True),
    )


def load_training_config(
    path: str | os.PathLike[str],
    *,
    require_paths: bool = True,
) -> TrainingConfig:
    """Load and validate the external-trainer contract.

    ``require_paths=False`` validates the schema only; that is what a test (or a
    ``--check`` run before the corpus exists) needs, and it keeps the shipped
    example honest without shipping fake recordings.
    """
    resolved = resolve_config_path(path, context="training config")
    data = load_document(resolved, context="training config")
    reject_unknown(data, _TRAINING_KEYS, context="training config")
    base = resolved.parent

    model = as_str(data.get("model"), context="training config: model", default="whisper-small")
    if not _present(data, "output_dir"):
        raise ConfigError("training config: 'output_dir' is required")
    output_dir = resolve_path(data["output_dir"], base, context="training config: output_dir")
    assert output_dir is not None

    data_block = as_mapping(data.get("data", {}), context="training config: data")
    reject_unknown(data_block, _DATA_KEYS, context="training config: data")
    sample_rate = as_int(
        data_block.get("sample_rate"),
        context="training config: data.sample_rate",
        default=16000,
        minimum=1000,
        maximum=192000,
    )
    minimum = as_float(
        data_block.get("min_duration_s"),
        context="training config: data.min_duration_s",
        default=0.0,
        minimum=0.0,
    )
    maximum = (
        as_float(
            data_block["max_duration_s"],
            context="training config: data.max_duration_s",
            exclusive_minimum=True,
            minimum=0.0,
        )
        if _present(data_block, "max_duration_s")
        else None
    )
    splits: list[SplitConfig] = []
    for name in ("train", "validation", "test"):
        if not _present(data_block, name):
            if name == "train":
                raise ConfigError("training config: data.train (a manifest path) is required")
            continue
        manifest = resolve_path(
            data_block[name], base, context=f"training config: data.{name}"
        )
        assert manifest is not None
        splits.append(
            SplitConfig(
                name=name,
                manifest=manifest,
                audio_root=None,
                out=None,
                sample_rate=sample_rate,
                min_duration_s=minimum,
                max_duration_s=maximum,
            )
        )
    for item in splits:
        _check_manifest_path(item.manifest, context=f"training config: data.{item.name}",
                             require_paths=require_paths)

    precision = as_str(
        data.get("precision"), context="training config: precision", default="fp32"
    ).lower()
    if precision not in PRECISIONS:
        raise ConfigError(
            f"training config: precision {precision!r} is not one of {sorted(PRECISIONS)}"
        )

    return TrainingConfig(
        source=resolved,
        model=model,
        output_dir=output_dir,
        base=base,
        seed=as_int(data.get("seed"), context="training config: seed", default=13,
                    minimum=0, maximum=2**31 - 1),
        batch_size=as_int(data.get("batch_size"), context="training config: batch_size",
                          default=8, minimum=1, maximum=4096),
        grad_accum=as_int(data.get("grad_accum"), context="training config: grad_accum",
                          default=1, minimum=1, maximum=1024),
        learning_rate=as_float(
            data.get("learning_rate"), context="training config: learning_rate",
            default=1e-5, minimum=0.0, maximum=1.0, exclusive_minimum=True,
        ),
        epochs=as_int(data.get("epochs"), context="training config: epochs",
                      default=3, minimum=1, maximum=10000),
        warmup_steps=as_int(data.get("warmup_steps"), context="training config: warmup_steps",
                            default=100, minimum=0, maximum=1_000_000),
        precision=precision,
        data=tuple(splits),
        notes=as_str(data.get("notes"), context="training config: notes", default="",
                     allow_empty=True),
    )


def _check_manifest_path(path: Path, *, context: str, require_paths: bool) -> None:
    """A manifest is a file (JSON/JSONL/CSV) or a Hugging Face ``save_to_disk`` dir."""
    if not require_paths:
        return
    if path.is_file():
        if path.suffix.lower() not in {".json", ".jsonl", ".csv"}:
            raise ConfigError(
                f"{context}: {path} has unsupported extension {path.suffix!r}; "
                "expected .json, .jsonl or .csv (or a Hugging Face save_to_disk directory)"
            )
        return
    if path.is_dir():
        return  # validated in depth by tools/prepare_dataset.py
    raise ConfigError(
        f"{context}: manifest not found: {path} "
        f"(set {CORPUS_ROOT_ENV} to move the corpus root, or fix the path in the config)"
    )


# --------------------------------------------------------------- plan output
def write_plan(config: TrainingConfig, destination: str | os.PathLike[str]) -> Path:
    """Write the resolved plan as JSON (the trainer's machine-readable input)."""
    path = expand_path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(config.plan(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def summary(config: TrainingConfig) -> str:
    """One-line human summary for the CLI (never claims training ran)."""
    splits = ", ".join(f"{item.name}={item.manifest}" for item in config.data)
    return (
        f"model={config.model} batch={config.batch_size} (x{config.grad_accum}="
        f"{config.effective_batch_size()}) lr={config.learning_rate:g} "
        f"epochs={config.epochs} seed={config.seed} precision={config.precision} "
        f"out={config.output_dir} data[{splits}]"
    )
