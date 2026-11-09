#!/usr/bin/env python3
"""Validate local ASR manifests (JSON/JSONL/CSV/Hugging Face) — no training.

Two ways to run it
------------------
``--config`` (a JSON/YAML file, see ``configs/dataset.example.yaml``) validates
every split in one pass; explicit flags validate a single manifest and override
the config value of the same name::

    python tools/prepare_dataset.py --config configs/dataset.yaml --check
    python tools/prepare_dataset.py --config configs/dataset.yaml --split train --out ...
    python tools/prepare_dataset.py --manifest manifests/train.jsonl --audio-root audio/

The config path can also come from ``PVAI_DATASET_CONFIG`` and the corpus root
from ``PVAI_CORPUS_ROOT``, so no absolute path has to be baked into a script.

What "valid" means
------------------
Every record carries a non-empty ``id``, ``audio`` and ``text``.  Recordings are
opened with the standard library only and must be PCM16 mono at the configured
rate, non-empty, not truncated, and within the configured duration bounds.  An
optional ``speaker``/``split`` field is checked when present, because leaking one
speaker across train/validation is the most common way a fine-tune silently
reports a number it did not earn.

Inputs are never modified.  ``--out`` writes a portable JSONL export whose audio
paths are relative to the export file, so the corpus can be moved as a whole.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import wave
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # allow `python tools/prepare_dataset.py`
    sys.path.insert(0, str(ROOT))

from tools.training_config import (  # noqa: E402
    CORPUS_ROOT_ENV,
    DATASET_CONFIG_ENV,
    ConfigError,
    DatasetConfig,
    SplitConfig,
    expand_path,
    resolve_path,
    load_dataset_config,
)

MANIFEST_SUFFIXES = frozenset({".json", ".jsonl", ".csv"})
REQUIRED_FIELDS = ("id", "audio", "text")
#: Optional fields that are validated when present and preserved on export.
OPTIONAL_FIELDS = ("speaker", "split", "duration_s", "source")


class DatasetValidationError(ValueError):
    """A manifest or one of its recordings violates the data contract."""


def expanded_path(value: str | Path) -> Path:
    """``${VAR}``/``~`` expansion for paths coming from a config or the CLI."""
    return expand_path(value)


# ------------------------------------------------------------------- manifests
def _read_rows(path: Path) -> list[Any]:
    """Read a manifest as a list of raw records (any supported format)."""
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            if path.suffix.lower() == ".csv":
                reader = csv.DictReader(handle)
                if not reader.fieldnames or not set(REQUIRED_FIELDS) <= set(reader.fieldnames):
                    raise ValueError("CSV header must include id,audio,text")
                return [dict(row) for row in reader]
            if path.suffix.lower() == ".json":
                return json.load(handle)
            if path.suffix.lower() == ".jsonl":
                return [json.loads(line) for line in handle if line.strip()]
    except (OSError, UnicodeError, ValueError, csv.Error) as exc:
        raise DatasetValidationError(f"{path}: cannot read manifest: {exc}") from exc
    raise DatasetValidationError(
        f"{path}: unsupported manifest format {path.suffix!r}; "
        f"supported: {sorted(MANIFEST_SUFFIXES)} (or a Hugging Face save_to_disk directory)"
    )


def _hf_split_rows(path: Path) -> dict[str, list[dict[str, Any]]]:
    """Rows of a Hugging Face ``save_to_disk`` directory, per split.

    Two layouts are read, matching what ``datasets`` actually writes:

    * ``DatasetDict.save_to_disk`` -> ``dataset_dict.json`` lists the split
      names, and each split is a sub-directory;
    * ``Dataset.save_to_disk``     -> ``dataset_info.json`` + ``state.json`` in
      the directory itself (reported as the single split ``train``).

    Per-split ``state.json`` names the arrow shard(s) and ``dataset_info.json``
    names the features.  Only local files are read: no Hub download, and
    ``datasets`` itself is not required at validation time (PyArrow is, because
    it wrote the shards).  The ``audio`` column must already be a path string —
    a decoded ``Audio`` feature means the corpus lives inside the arrow file,
    which this tool deliberately does not accept (see docs/DATASETS.md).
    """
    dict_path = path / "dataset_dict.json"
    if dict_path.is_file():
        try:
            listing = json.loads(dict_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise DatasetValidationError(f"{dict_path}: cannot read: {exc}") from exc
        names = listing.get("splits") if isinstance(listing, dict) else None
        if not isinstance(names, list) or not names:
            raise DatasetValidationError(f"{dict_path}: expected a non-empty 'splits' list")
        return {
            str(name): _hf_one_split(path / str(name), split=str(name)) for name in names
        }
    return {"train": _hf_one_split(path, split="train")}


def _hf_one_split(directory: Path, *, split: str) -> list[dict[str, Any]]:
    """Validated rows of one ``Dataset.save_to_disk`` directory."""
    info_path = directory / "dataset_info.json"
    state_path = directory / "state.json"
    if not info_path.is_file():
        raise DatasetValidationError(
            f"{directory}: not a Hugging Face save_to_disk directory "
            "(dataset_info.json is missing)"
        )
    info = _read_json_object(info_path)
    state = _read_json_object(state_path) if state_path.is_file() else {}
    features = info.get("features")
    if isinstance(features, dict) and isinstance(features.get("audio"), dict):
        audio_type = str(features["audio"].get("_type", ""))
        if audio_type.lower() in {"audio", "image", "array2d", "array3d"}:
            raise DatasetValidationError(
                f"{info_path}: the 'audio' column is a decoded {audio_type} feature; "
                "export the recordings as WAV files and store their paths as strings "
                "(docs/DATASETS.md §4)"
            )
    files: list[str] = []
    for entry in state.get("_data_files", []) if isinstance(state.get("_data_files"), list) else []:
        if isinstance(entry, dict) and isinstance(entry.get("filename"), str):
            files.append(entry["filename"])
    if not files:
        # Fall back to the shards next to the metadata (naming is stable:
        # data-00000-of-000NN.arrow).
        files = sorted(item.name for item in directory.glob("*.arrow"))
    if not files:
        raise DatasetValidationError(
            f"{directory}: no .arrow shard found; re-save with Dataset.save_to_disk()"
        )
    rows: list[dict[str, Any]] = []
    for filename in files:
        rows.extend(_read_arrow_rows(directory / filename))
    if not rows:
        raise DatasetValidationError(f"{directory}: split {split!r} contains no rows")
    for row in rows:
        if not isinstance(row.get("audio"), str):
            raise DatasetValidationError(
                f"{directory}: split {split!r} column 'audio' must be a path string, "
                f"got {type(row.get('audio')).__name__} (docs/DATASETS.md §4)"
            )
    return rows


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DatasetValidationError(f"{path}: cannot read JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise DatasetValidationError(f"{path}: expected a JSON object")
    return data


def _read_arrow_rows(path: Path) -> list[dict[str, Any]]:
    """Read one Arrow IPC shard; PyArrow is required and the error says so."""
    if not path.is_file():
        raise DatasetValidationError(f"{path}: arrow shard listed in state.json is missing")
    try:
        import pyarrow.ipc  # type: ignore[import-not-found]
    except ImportError as exc:
        raise DatasetValidationError(
            f"{path}: reading a Hugging Face save_to_disk directory needs pyarrow "
            "(`python -m pip install pyarrow`), or export a JSONL manifest instead"
        ) from exc
    try:
        with path.open("rb") as handle:
            table = pyarrow.ipc.open_stream(handle).read_all()
    except Exception as exc:  # noqa: BLE001 - pyarrow raises several types
        raise DatasetValidationError(f"{path}: cannot read arrow data: {exc}") from exc
    missing = [name for name in REQUIRED_FIELDS if name not in table.column_names]
    if missing:
        raise DatasetValidationError(
            f"{path}: missing column(s) {missing}; found {list(table.column_names)}"
        )
    keep = [name for name in table.column_names if name in {*REQUIRED_FIELDS, *OPTIONAL_FIELDS}]
    return [dict(row) for row in table.select(keep).to_pylist()]


def manifest_split_names(path: Path) -> list[str]:
    """Split names a manifest can provide (``["train"]`` for a plain file)."""
    if path.is_dir() and (path / "dataset_dict.json").is_file():
        return sorted(_hf_split_rows(path))
    if path.is_dir():
        return ["train"]
    return []


def read_manifest(path: Path, *, split: str | None = None) -> list[dict[str, Any]]:
    """Records of one manifest, accepting files and HF ``save_to_disk`` dirs."""
    if path.is_dir():
        parsed = _hf_split_rows(path)
        if split is not None:
            if split not in parsed:
                raise DatasetValidationError(
                    f"{path}: split {split!r} not found (available: {sorted(parsed)})"
                )
            return parsed[split]
        if len(parsed) == 1:
            return next(iter(parsed.values()))
        raise DatasetValidationError(
            f"{path}: contains several splits {sorted(parsed)}; pass --split to "
            "choose one, or use a single-split save_to_disk directory"
        )
    if not path.is_file():
        raise DatasetValidationError(f"{path}: manifest not found")
    rows = _read_rows(path)
    if not isinstance(rows, list) or not rows:
        raise DatasetValidationError(f"{path}: expected a non-empty list of records")
    return rows


# ------------------------------------------------------------------ validation
def resolve_audio_root(audio_root: Path | str | None, manifest: Path) -> Path:
    """Base directory for relative ``audio`` paths of one manifest.

    The default is next to the manifest; ``PVAI_CORPUS_ROOT`` is the escape
    hatch for a corpus that lives on another drive, and an explicit
    ``--audio-root`` always wins.  This keeps one rule for the API and the CLI
    so a library caller cannot silently resolve somewhere else than the tool.
    """
    if audio_root is None or (isinstance(audio_root, str) and not audio_root.strip()):
        from_env = os.getenv(CORPUS_ROOT_ENV, "").strip()
        if from_env:
            return expand_path(from_env).resolve()
        return manifest.parent
    return expand_path(audio_root).resolve()


def _wav_duration_s(path: Path, frames: int, sample_rate: int) -> float:
    return frames / float(sample_rate)


def _validate_row_fields(row: Any, *, context: str) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise DatasetValidationError(f"{context}: expected an object, got {type(row).__name__}")
    for field in REQUIRED_FIELDS:
        value = row.get(field)
        if not isinstance(value, str) or not value.strip():
            raise DatasetValidationError(f"{context}: {field} must be a non-empty string")
    for field in OPTIONAL_FIELDS:
        if field in row and row[field] is not None and not isinstance(row[field], (str, int, float)):
            raise DatasetValidationError(
                f"{context}: optional field {field} must be a string or number"
            )
    text = row["text"].strip()
    if not text:
        raise DatasetValidationError(f"{context}: text must contain a transcript")
    return row


def validate_manifest(
    path: Path,
    *,
    audio_root: Path | None = None,
    sample_rate: int = 16000,
    min_duration_s: float = 0.0,
    max_duration_s: float | None = None,
    split: str | None = None,
) -> list[dict[str, Any]]:
    """Validate one manifest against the WAV/label contract; never modify audio.

    Duration bounds are *reported*, not enforced by rewriting: a too-short or
    too-long clip is a labelling decision for the operator, and silently
    dropping it would change the dataset without a record.
    """
    if sample_rate <= 0:
        raise DatasetValidationError("sample rate must be positive")
    if min_duration_s < 0:
        raise DatasetValidationError("min_duration_s must not be negative")
    if max_duration_s is not None and max_duration_s <= min_duration_s:
        raise DatasetValidationError("max_duration_s must be greater than min_duration_s")

    path = expanded_path(path).resolve()
    root = resolve_audio_root(audio_root, path)
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    recordings: set[Path] = set()

    for index, row in enumerate(read_manifest(path, split=split), 1):
        context = f"{path}: record {index}"
        row = _validate_row_fields(row, context=context)
        identifier = row["id"].strip()
        if identifier in seen:
            raise DatasetValidationError(f"{context}: duplicate id {identifier!r}")
        if any(separator in identifier for separator in ("..", "/", "\\", "\x00")):
            # The id is used to build `<id>.wav` paths by the evaluation tool.
            raise DatasetValidationError(f"{context}: unsafe id {identifier!r}")
        seen.add(identifier)

        audio = expanded_path(row["audio"])
        audio = (root / audio).resolve() if not audio.is_absolute() else audio.resolve()
        if audio in recordings:
            raise DatasetValidationError(f"{context}: duplicate recording {audio}")
        recordings.add(audio)

        try:
            with wave.open(str(audio), "rb") as handle:
                channels = handle.getnchannels()
                width = handle.getsampwidth()
                rate = handle.getframerate()
                count = handle.getnframes()
                frames = handle.readframes(count)
        except (OSError, EOFError, wave.Error, ValueError) as exc:
            raise DatasetValidationError(f"{context}: {audio}: {exc}") from exc
        if (channels, width, rate) != (1, 2, sample_rate):
            raise DatasetValidationError(
                f"{context}: {audio}: expected PCM16 mono {sample_rate} Hz WAV, "
                f"got {channels} channel(s), {width * 8}-bit, {rate} Hz "
                "(convert it deliberately: docs/DATASETS.md §3)"
            )
        if count <= 0 or len(frames) != count * 2:
            raise DatasetValidationError(f"{context}: {audio}: empty or truncated audio")
        duration = _wav_duration_s(audio, count, sample_rate)
        if duration < min_duration_s:
            raise DatasetValidationError(
                f"{context}: {audio}: {duration:.2f}s is shorter than "
                f"min_duration_s={min_duration_s:g}"
            )
        if max_duration_s is not None and duration > max_duration_s:
            raise DatasetValidationError(
                f"{context}: {audio}: {duration:.2f}s is longer than "
                f"max_duration_s={max_duration_s:g}; split the recording"
            )

        record: dict[str, Any] = {
            "id": identifier,
            "audio": str(audio),
            "text": row["text"].strip(),
            "duration_s": round(duration, 3),
        }
        if split is not None:
            record["split"] = split
        for field in OPTIONAL_FIELDS:
            if field in row and row[field] is not None and field not in record:
                record[field] = row[field]
        if "speaker" not in record:
            record.pop("speaker", None)
        result.append(record)

    _check_split_isolation(path, result, split=split)
    return result


def _check_split_isolation(
    path: Path, rows: list[dict[str, Any]], *, split: str | None
) -> None:
    """A declared ``split`` field must not contradict the manifest being read."""
    if split is None:
        declared = {str(row.get("split", "")).strip().lower() for row in rows}
        declared.discard("")
        if len(declared) > 1:
            raise DatasetValidationError(
                f"{path}: records declare several splits {sorted(declared)}; "
                "one manifest must describe one split (pass --split or split the file)"
            )
        return
    for row in rows:
        declared = str(row.get("split", "") or "").strip().lower()
        if declared and declared != split.strip().lower():
            raise DatasetValidationError(
                f"{path}: record {row['id']!r} declares split {declared!r} but is "
                f"being validated as {split!r}"
            )


def validate_split(split: SplitConfig) -> list[dict[str, Any]]:
    """Validate a :class:`SplitConfig` from a dataset configuration file.

    An empty ``name`` means "whatever the manifest says it is": a Hugging Face
    ``DatasetDict`` still requires an explicit ``--split`` (ambiguity is an
    error, not a guess), while a single ``Dataset`` directory resolves to its
    own split name.
    """
    name = split.name
    if not name:
        available = manifest_split_names(split.manifest)
        if len(available) == 1:
            name = available[0]
    return validate_manifest(
        split.manifest,
        audio_root=split.audio_root,
        sample_rate=split.sample_rate,
        min_duration_s=split.min_duration_s,
        max_duration_s=split.max_duration_s,
        split=name or None,
    )


# ---------------------------------------------------------------------- export
def export_rows(rows: list[dict[str, Any]], output: Path) -> Path:
    """Write a portable JSONL export with audio paths relative to ``output``."""
    output = output.resolve()
    portable = []
    for row in rows:
        audio = Path(row["audio"])
        entry = dict(row)
        entry["audio"] = Path(os.path.relpath(audio, output.parent)).as_posix()
        portable.append(entry)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in portable),
        encoding="utf-8",
    )
    return output


def _guard_output(output: Path, rows: list[dict[str, Any]], manifest: Path) -> Path:
    resolved = output.resolve()
    if resolved == manifest.resolve():
        raise DatasetValidationError("output must not overwrite the manifest")
    if any(resolved == Path(row["audio"]).resolve() for row in rows):
        raise DatasetValidationError("output must not overwrite a recording")
    return resolved


def _leak_report(splits: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Overlap between splits — the failure mode a per-file validator misses."""
    by_split = {
        name: {str(row.get("speaker", "")).strip() for row in rows}
        for name, rows in splits.items()
    }
    by_audio = {
        name: {str(row["audio"]) for row in rows} for name, rows in splits.items()
    }
    speakers: dict[str, list[str]] = {}
    audio: dict[str, list[str]] = {}
    names = sorted(splits)
    for index, first in enumerate(names):
        for second in names[index + 1:]:
            shared = (by_split[first] - {""}) & (by_split[second] - {""})
            if shared:
                speakers[f"{first}/{second}"] = sorted(shared)
            shared_audio = by_audio[first] & by_audio[second]
            if shared_audio:
                audio[f"{first}/{second}"] = sorted(shared_audio)
    return {
        "speakers": speakers,
        "recordings": audio,
        "note": "a recording or speaker in two splits invalidates the held-out number"
        if (speakers or audio)
        else "no shared speaker/recording between splits",
    }


# ------------------------------------------------------------------------- CLI
def _add_manifest_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--manifest", type=expanded_path,
                        help="one manifest (.json/.jsonl/.csv or HF save_to_disk dir)")
    parser.add_argument("--audio-root", type=expanded_path,
                        help="relative recordings resolve here; default: manifest directory "
                             f"(falls back to ${CORPUS_ROOT_ENV})")
    parser.add_argument("--sample-rate", type=int, help="expected rate; default 16000")
    parser.add_argument("--min-duration-s", type=float, help="reject shorter clips")
    parser.add_argument("--max-duration-s", type=float, help="reject longer clips")
    parser.add_argument("--out", type=expanded_path, help="portable validated JSONL export")
    parser.add_argument("--split", help="split name (required for a multi-split HF dir)")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate local ASR manifests and optionally export portable JSONL; "
                    "no training is performed."
    )
    parser.add_argument("--config", type=expanded_path,
                        help=f"dataset config (.json/.yaml); env: {DATASET_CONFIG_ENV}")
    parser.add_argument("--check", action="store_true",
                        help="validate only; also print the cross-split leak report")
    parser.add_argument("--report", type=expanded_path,
                        help="write the validation report as JSON")
    _add_manifest_arguments(parser)
    return parser


def _config_path(explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit
    from_env = os.getenv(DATASET_CONFIG_ENV, "").strip()
    if from_env:
        return expanded_path(from_env)
    return None


def _split_from_cli(args: argparse.Namespace, config: DatasetConfig | None) -> SplitConfig:
    """One split from explicit flags, filling missing values from the config."""
    if args.manifest is None:
        raise DatasetValidationError(
            "--manifest (or --config with --split) is required"
        )
    base = Path.cwd()
    fallback = (
        config.split(args.split) if config is not None and args.split else None
    )
    if fallback is None and config is not None and len(config.splits) == 1:
        fallback = config.splits[0]
    manifest = args.manifest
    if not manifest.is_absolute():
        manifest = (base / manifest)
    if not manifest.exists():
        raise DatasetValidationError(f"{manifest}: manifest not found")

    sample_rate = args.sample_rate
    if sample_rate is None:
        sample_rate = fallback.sample_rate if fallback else 16000
    min_duration = args.min_duration_s
    if min_duration is None:
        min_duration = fallback.min_duration_s if fallback else 0.0
    max_duration = args.max_duration_s
    if max_duration is None and fallback is not None:
        max_duration = fallback.max_duration_s
    audio_root = args.audio_root
    if audio_root is None:
        audio_root = fallback.audio_root if fallback else None
    if audio_root is None:
        from_env = os.getenv(CORPUS_ROOT_ENV, "").strip()
        if from_env:
            audio_root = resolve_path(from_env, base, context=CORPUS_ROOT_ENV)
    return SplitConfig(
        # None (not a placeholder name) so a Hugging Face directory keeps its
        # own split name and a single-split dataset is accepted as-is.
        name=args.split or (fallback.name if fallback else None),
        manifest=manifest,
        audio_root=audio_root,
        out=args.out if args.out is not None else (fallback.out if fallback else None),
        sample_rate=sample_rate,
        min_duration_s=min_duration,
        max_duration_s=max_duration,
    )


def _run(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    config_path = _config_path(args.config)
    config: DatasetConfig | None = None
    if config_path is not None:
        config = load_dataset_config(config_path)

    results: dict[str, list[dict[str, Any]]] = {}
    exported: dict[str, str] = {}
    if config is not None and args.manifest is None:
        splits = list(config.splits)
        if args.split:
            chosen = config.split(args.split)
            if chosen is None:
                raise DatasetValidationError(
                    f"--split {args.split!r} is not in {config_path} "
                    f"(available: {[item.name for item in config.splits]})"
                )
            splits = [chosen]
        if args.out is not None:
            raise DatasetValidationError(
                "--out needs --split when --config describes several splits"
            )
    else:
        single = _split_from_cli(args, config)
        splits = [single]

    manifests: dict[str, str] = {}
    for split in splits:
        rows = validate_split(split)
        resolved = split.name
        if not resolved:  # a single-split Hugging Face dataset names itself
            available = manifest_split_names(split.manifest)
            resolved = available[0] if len(available) == 1 else "custom"
        results[resolved] = rows
        manifests[resolved] = str(split.manifest)
        destination = args.out if len(splits) == 1 and args.out is not None else split.out
        if destination is not None:
            target = destination if destination.is_absolute() else Path.cwd() / destination
            target = _guard_output(target, rows, split.manifest)
            exported[resolved] = str(export_rows(rows, target))

    report = {
        "schema_version": 1,
        "config": str(config.source) if config is not None and config.source else None,
        "splits": {
            name: {
                "manifest": manifests[name],
                "records": len(rows),
                "exported_to": exported.get(name),
                "speakers": sorted(
                    {str(row.get("speaker", "")).strip() for row in rows} - {""}
                ),
            }
            for name, rows in results.items()
        },
        "leakage": _leak_report(results),
        "note": "validation only; no audio was modified and no training was performed",
    }
    if args.report is not None:
        target = args.report if args.report.is_absolute() else Path.cwd() / args.report
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    for name, rows in results.items():
        print(f"{name}: validated {len(rows)} recordings (no training performed).")
    if args.check or len(results) > 1:
        leakage = report["leakage"]
        shared_speakers = leakage["speakers"]
        shared_audio = leakage["recordings"]
        if shared_speakers or shared_audio:
            print("warning: split leakage detected", file=sys.stderr)
            for pair, items in shared_speakers.items():
                print(f"  speakers in {pair}: {len(items)} shared", file=sys.stderr)
            for pair, items in shared_audio.items():
                print(f"  recordings in {pair}: {len(items)} shared", file=sys.stderr)
        else:
            print("split hygiene: no shared speaker or recording between splits.")
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return _run(argv)
    except (DatasetValidationError, ConfigError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
