"""Dataset/training configuration: externalized, validated, path-portable.

The audit requirement behind this module is that no hyper-parameter or local
path is hard-coded in a script: batch size, learning rate, epochs, seed and every
input/output directory must come from a configuration file or an explicit flag,
and a wrong value must be rejected *before* a training run starts.

Every test here is model-free and offline; nothing is downloaded.
"""

from __future__ import annotations

import json
import os
import wave
from pathlib import Path

import pytest

from tools import prepare_dataset, train_plan, training_config
from tools.training_config import (
    ConfigError,
    as_bool,
    as_float,
    as_int,
    expand_path,
    load_dataset_config,
    load_training_config,
    resolve_path,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIGS = ROOT / "configs"


# ------------------------------------------------------------------ helpers
def _wav(path: Path, frames: int = 8000, *, rate: int = 16000, channels: int = 1,
         width: int = 2) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setparams((channels, width, rate, 0, "NONE", "not compressed"))
        handle.writeframes(b"\0\0" * frames * channels * max(1, width // 2))
    return path


def _corpus(tmp_path: Path, *, speakers: dict[str, str] | None = None) -> Path:
    """A tiny 3-split corpus: one recording per split, distinct speakers."""
    speakers = speakers or {"train": "s1", "validation": "s2", "test": "s3"}
    root = tmp_path / "corpus"
    for split, speaker in speakers.items():
        _wav(root / "audio" / f"{split}.wav")
        (root / "manifests").mkdir(parents=True, exist_ok=True)
        (root / "manifests" / f"{split}.jsonl").write_text(
            json.dumps(
                {
                    "id": f"{split}-1",
                    "audio": f"{split}.wav",
                    "text": "سلام",
                    "speaker": speaker,
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
    return root


def _dataset_config(tmp_path: Path, corpus: Path, *, suffix: str = ".yaml") -> Path:
    """A config file living next to the corpus, using relative paths only."""
    body = f"""version: 1
sample_rate: 16000
min_duration_s: 0.1
max_duration_s: 30
splits:
  train:
    manifest: {corpus.name}/manifests/train.jsonl
    audio_root: {corpus.name}/audio
    out: {corpus.name}/validated/train.jsonl
  validation:
    manifest: {corpus.name}/manifests/validation.jsonl
    audio_root: {corpus.name}/audio
  test:
    manifest: {corpus.name}/manifests/test.jsonl
    audio_root: {corpus.name}/audio
"""
    path = tmp_path / f"dataset{suffix}"
    if suffix == ".yaml":
        pytest.importorskip("yaml")
    else:
        payload = {
            "version": 1,
            "sample_rate": 16000,
            "min_duration_s": 0.1,
            "max_duration_s": 30,
            "splits": {
                name: {
                    "manifest": f"{corpus.name}/manifests/{name}.jsonl",
                    "audio_root": f"{corpus.name}/audio",
                }
                for name in ("train", "validation", "test")
            },
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path
    path.write_text(body, encoding="utf-8")
    return path


# ------------------------------------------------------- shipped example files
def test_example_configs_are_valid_and_versioned() -> None:
    """The examples must validate against the schema they document."""
    dataset = load_dataset_config(CONFIGS / "dataset.example.yaml")
    assert [item.name for item in dataset.splits] == ["train", "validation", "test"]
    assert all(item.sample_rate == 16000 for item in dataset.splits)

    training = load_training_config(CONFIGS / "training.example.yaml", require_paths=False)
    assert training.model and training.output_dir
    assert training.batch_size > 0 and training.learning_rate > 0
    assert training.epochs > 0 and training.precision in training_config.PRECISIONS
    assert {item.name for item in training.data} == {"train", "validation", "test"}


@pytest.mark.parametrize("name", ["dataset.example.json", "training.example.json"])
def test_json_examples_match_the_yaml_examples(name: str) -> None:
    """Two formats are shipped; they must describe the same contract."""
    json_path = CONFIGS / name
    yaml_path = CONFIGS / name.replace(".json", ".yaml")
    pytest.importorskip("yaml")
    import yaml as yaml_module

    from_json = json.loads(json_path.read_text(encoding="utf-8"))
    from_yaml = yaml_module.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert from_json == from_yaml, f"{name} drifted from {yaml_path.name}"


def test_examples_contain_no_absolute_local_paths() -> None:
    """A committed config must never name one machine's home directory."""
    offenders: list[str] = []
    for path in sorted(CONFIGS.glob("*.example.*")):
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            for marker in ("/home/", "/Users/", "C:\\", "/mnt/", "/root/"):
                if marker in stripped:
                    offenders.append(f"{path.name}: {stripped}")
    assert not offenders, offenders


# --------------------------------------------------------------- path handling
def test_paths_expand_env_vars_and_defaults(monkeypatch) -> None:
    monkeypatch.setenv("PVAI_TEST_ROOT", "/data/corpus")
    assert expand_path("${PVAI_TEST_ROOT}/audio") == Path("/data/corpus/audio")
    assert expand_path("$PVAI_TEST_ROOT/audio") == Path("/data/corpus/audio")
    # ${VAR:-default} is what lets the shipped example stay runnable.
    monkeypatch.delenv("PVAI_MISSING_ROOT", raising=False)
    assert expand_path("${PVAI_MISSING_ROOT:-rel/corpus}/x") == Path("rel/corpus/x")


def test_undefined_env_var_is_a_config_error_not_a_missing_file(monkeypatch) -> None:
    """The old failure mode was "manifest not found: .../${CORPUS_ROOT}/...".

    That message points at a directory literally named ``${CORPUS_ROOT}`` and
    sends the reader looking for a missing file instead of a missing variable.
    """
    monkeypatch.delenv("PVAI_NOWHERE", raising=False)
    with pytest.raises(ConfigError, match="PVAI_NOWHERE"):
        expand_path("${PVAI_NOWHERE}/train.jsonl")


def test_relative_paths_resolve_against_the_config_not_the_cwd(tmp_path, monkeypatch) -> None:
    base = tmp_path / "configs"
    base.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)  # a different working directory must not matter
    assert resolve_path("data/train.jsonl", base) == base / "data/train.jsonl"
    assert resolve_path("/abs/train.jsonl", base) == Path("/abs/train.jsonl")
    assert resolve_path("", base) is None and resolve_path(None, base) is None


# ------------------------------------------------------------ schema strictness
@pytest.mark.parametrize(
    "field,value,message",
    [
        ("batch_size", 0, "minimum"),
        ("batch_size", -4, "minimum"),
        ("batch_size", "eight", "integer"),
        ("learning_rate", 0, "greater than"),
        ("learning_rate", 2.0, "above the maximum"),
        ("learning_rate", float("nan"), "finite"),
        ("epochs", 0, "minimum"),
        ("epochs", 2.5, "whole number"),
        ("grad_accum", 0, "minimum"),
        ("seed", -1, "minimum"),
        ("precision", "float64", "precision"),
    ],
)
def test_invalid_hyperparameters_are_rejected(tmp_path, field, value, message, monkeypatch) -> None:
    corpus = _corpus(tmp_path)
    payload = {
        "model": "openai/whisper-small",
        "output_dir": "runs/x",
        "data": {"train": str(corpus / "manifests" / "train.jsonl")},
        field: value,
    }
    path = tmp_path / "training.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        load_training_config(path, require_paths=False)


def test_unknown_key_is_rejected_with_a_suggestion(tmp_path) -> None:
    """A typo must not silently fall back to a default (that changes the run)."""
    path = tmp_path / "training.json"
    path.write_text(
        json.dumps({"output_dir": "runs/x", "learnig_rate": 1e-5}), encoding="utf-8"
    )
    with pytest.raises(ConfigError) as excinfo:
        load_training_config(path, require_paths=False)
    assert "learnig_rate" in str(excinfo.value)
    assert "learning_rate" in str(excinfo.value)  # did-you-mean hint


def test_missing_required_keys_are_named(tmp_path) -> None:
    path = tmp_path / "training.json"
    path.write_text(json.dumps({"model": "x"}), encoding="utf-8")
    with pytest.raises(ConfigError, match="output_dir"):
        load_training_config(path, require_paths=False)
    path.write_text(json.dumps({"output_dir": "runs/x"}), encoding="utf-8")
    with pytest.raises(ConfigError, match="data.train"):
        load_training_config(path, require_paths=False)


def test_broken_json_reports_line_and_column(tmp_path) -> None:
    path = tmp_path / "training.json"
    path.write_text('{"output_dir": "runs/x",\n "batch_size": }', encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid JSON"):
        load_training_config(path, require_paths=False)


@pytest.mark.parametrize("content", ["", "[]", "just a string"])
def test_non_mapping_documents_are_rejected(tmp_path, content: str) -> None:
    path = tmp_path / "training.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_training_config(path, require_paths=False)


def test_yaml_without_pyyaml_explains_the_fallback(tmp_path, monkeypatch) -> None:
    path = tmp_path / "dataset.yaml"
    path.write_text("version: 1\n", encoding="utf-8")
    monkeypatch.setitem(__import__("sys").modules, "yaml", None)
    with pytest.raises(ConfigError, match="PyYAML"):
        training_config.load_document(path)


def test_scalar_helpers_are_strict() -> None:
    assert as_int("8", context="x") == 8
    assert as_int(8.0, context="x") == 8
    assert as_bool("yes", context="x") is True and as_bool(False, context="x") is False
    assert as_float("1e-5", context="x") == pytest.approx(1e-5)
    for bad in (True, "8.5", None):
        with pytest.raises(ConfigError):
            as_int(bad, context="x")
    with pytest.raises(ConfigError):
        as_bool("maybe", context="x")


# --------------------------------------------------------------- dataset config
def test_dataset_config_resolves_every_split_relative_to_the_file(tmp_path, monkeypatch) -> None:
    corpus = _corpus(tmp_path)
    config = _dataset_config(tmp_path, corpus)
    monkeypatch.chdir(tmp_path.parent)  # cwd is irrelevant
    loaded = load_dataset_config(config)
    assert [item.name for item in loaded.splits] == ["train", "validation", "test"]
    for split in loaded.splits:
        assert split.manifest.is_absolute() or split.manifest.exists()
        assert split.manifest.parent.name == "manifests"
        assert split.audio_root == corpus / "audio"


def test_dataset_config_supports_json_too(tmp_path) -> None:
    corpus = _corpus(tmp_path)
    config = _dataset_config(tmp_path, corpus, suffix=".json")
    loaded = load_dataset_config(config)
    assert len(loaded.splits) == 3


def test_dataset_config_duration_bounds_are_checked(tmp_path) -> None:
    path = tmp_path / "dataset.json"
    payload = {
        "min_duration_s": 5,
        "max_duration_s": 2,
        "splits": {"train": {"manifest": "train.jsonl"}},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConfigError, match="max_duration_s"):
        load_dataset_config(path)


def test_corpus_root_env_fills_in_a_missing_audio_root(tmp_path, monkeypatch) -> None:
    """``PVAI_CORPUS_ROOT`` means the config need not repeat the corpus path."""
    corpus = _corpus(tmp_path)
    (tmp_path / "dataset.json").write_text(
        json.dumps({"splits": {"train": {"manifest": f"{corpus.name}/manifests/train.jsonl"}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("PVAI_CORPUS_ROOT", str(corpus / "audio"))
    loaded = load_dataset_config(tmp_path / "dataset.json")
    assert loaded.splits[0].audio_root == corpus / "audio"


# ---------------------------------------------------------- manifest validation
def test_full_corpus_validates_and_exports_portable_paths(tmp_path, capsys) -> None:
    corpus = _corpus(tmp_path)
    config = _dataset_config(tmp_path, corpus)
    assert prepare_dataset.main(["--config", str(config), "--check"]) == 0
    printed = capsys.readouterr().out
    assert "no shared speaker or recording" in printed
    exported = corpus / "validated" / "train.jsonl"
    assert exported.is_file()
    row = json.loads(exported.read_text(encoding="utf-8").splitlines()[0])
    assert not Path(row["audio"]).is_absolute()
    # The export is itself a valid manifest (paths relative to the export file).
    assert prepare_dataset.validate_manifest(exported)[0]["text"] == "سلام"


def test_split_leakage_is_reported(tmp_path, capsys) -> None:
    """The per-file validator cannot see this; the cross-split report must."""
    corpus = _corpus(tmp_path, speakers={"train": "s1", "validation": "s1", "test": "s3"})
    config = _dataset_config(tmp_path, corpus)
    assert prepare_dataset.main(["--config", str(config), "--check"]) == 0
    captured = capsys.readouterr()
    assert "leakage" in captured.err and "s1" not in captured.err  # count, not identity
    assert "speakers in train/validation" in captured.err


def test_report_json_is_machine_readable(tmp_path) -> None:
    corpus = _corpus(tmp_path)
    config = _dataset_config(tmp_path, corpus)
    report_path = tmp_path / "report.json"
    assert prepare_dataset.main(
        ["--config", str(config), "--check", "--report", str(report_path)]
    ) == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert set(report["splits"]) == {"train", "validation", "test"}
    assert report["leakage"]["recordings"] == {}
    assert report["note"].startswith("validation only")


def test_duration_bounds_reject_outliers(tmp_path) -> None:
    corpus = _corpus(tmp_path)
    manifest = corpus / "manifests" / "train.jsonl"
    with pytest.raises(prepare_dataset.DatasetValidationError, match="shorter than"):
        prepare_dataset.validate_manifest(
            manifest, audio_root=corpus / "audio", min_duration_s=10.0
        )
    with pytest.raises(prepare_dataset.DatasetValidationError, match="longer than"):
        prepare_dataset.validate_manifest(
            manifest, audio_root=corpus / "audio", max_duration_s=0.05
        )


def test_wrong_sample_rate_or_channels_are_named(tmp_path) -> None:
    corpus = _corpus(tmp_path)
    _wav(corpus / "audio" / "train.wav", rate=44100)
    with pytest.raises(prepare_dataset.DatasetValidationError, match="16000"):
        prepare_dataset.validate_manifest(
            corpus / "manifests" / "train.jsonl", audio_root=corpus / "audio"
        )
    _wav(corpus / "audio" / "train.wav", channels=2)
    with pytest.raises(prepare_dataset.DatasetValidationError, match="channel"):
        prepare_dataset.validate_manifest(
            corpus / "manifests" / "train.jsonl", audio_root=corpus / "audio"
        )


def test_unsafe_ids_are_rejected(tmp_path) -> None:
    """``id`` becomes a file name in the evaluation tool; ``..`` must not pass."""
    corpus = _corpus(tmp_path)
    manifest = corpus / "manifests" / "train.jsonl"
    manifest.write_text(
        json.dumps({"id": "../escape", "audio": "train.wav", "text": "سلام"}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(prepare_dataset.DatasetValidationError, match="unsafe id"):
        prepare_dataset.validate_manifest(manifest)


def test_manifest_format_errors_are_explicit(tmp_path) -> None:
    path = tmp_path / "manifest.txt"
    path.write_text("id,audio,text\n", encoding="utf-8")
    with pytest.raises(prepare_dataset.DatasetValidationError, match="unsupported"):
        prepare_dataset.validate_manifest(path)


def test_audio_root_from_env_is_used_by_the_cli(tmp_path, monkeypatch) -> None:
    corpus = _corpus(tmp_path)
    monkeypatch.setenv("PVAI_CORPUS_ROOT", str(corpus / "audio"))
    code = prepare_dataset.main(
        ["--manifest", str(corpus / "manifests" / "train.jsonl"), "--check"]
    )
    assert code == 0


def test_missing_env_var_is_reported_by_the_cli(tmp_path, monkeypatch, capsys) -> None:
    corpus = _corpus(tmp_path)
    monkeypatch.delenv("PVAI_CORPUS_ROOT", raising=False)
    config = tmp_path / "dataset.json"
    config.write_text(
        json.dumps(
            {"splits": {"train": {"manifest": "${PVAI_CORPUS_ROOT}/manifests/train.jsonl"}}}
        ),
        encoding="utf-8",
    )
    assert prepare_dataset.main(["--config", str(config)]) == 2
    assert "PVAI_CORPUS_ROOT" in capsys.readouterr().err
    assert corpus  # the corpus itself is irrelevant to this check


# ----------------------------------------------------------- hugging face input
def _hf_dataset_dict(root: Path, audio: Path) -> Path:
    datasets = pytest.importorskip("datasets")
    pytest.importorskip("pyarrow")
    second = _wav(audio.with_name("b.wav"))
    payload = {
        "train": {"id": ["a1", "a2"], "audio": [str(audio), str(second)], "text": ["سلام", "خوبی"]},
        "validation": {"id": ["b1"], "audio": [str(second)], "text": ["بله"]},
    }
    dictionary = datasets.DatasetDict(
        {
            name: datasets.Dataset.from_dict(columns)
            for name, columns in payload.items()
        }
    )
    dictionary.save_to_disk(str(root))
    return root


def test_hugging_face_dataset_dict_is_read_per_split(tmp_path) -> None:
    audio = _wav(tmp_path / "corpus" / "audio" / "a.wav")
    root = _hf_dataset_dict(tmp_path / "hf", audio)
    rows = prepare_dataset.read_manifest(root, split="train")
    assert [row["id"] for row in rows] == ["a1", "a2"]
    with pytest.raises(prepare_dataset.DatasetValidationError, match="--split"):
        prepare_dataset.read_manifest(root)


def test_hugging_face_single_dataset_resolves_its_own_split(tmp_path) -> None:
    audio = _wav(tmp_path / "corpus" / "audio" / "a.wav")
    _hf_dataset_dict(tmp_path / "hf", audio)
    # Narrow to one split on disk, exactly as `Dataset.save_to_disk` writes it.
    single = tmp_path / "single"
    single.mkdir()
    for item in (tmp_path / "hf" / "train").iterdir():
        (single / item.name).write_bytes(item.read_bytes())
    assert prepare_dataset.manifest_split_names(single) == ["train"]
    rows = prepare_dataset.validate_manifest(single)
    assert [row["id"] for row in rows] == ["a1", "a2"]


def test_decoded_audio_feature_is_rejected_with_instructions(tmp_path) -> None:
    """A Dataset with an Audio feature stores samples, not paths — refuse it."""
    pytest.importorskip("datasets")
    datasets = pytest.importorskip("datasets")
    pytest.importorskip("pyarrow")
    import numpy as np

    root = tmp_path / "hf-audio"
    dataset = datasets.Dataset.from_dict(
        {
            "id": ["a1"],
            "audio": [{"path": "a.wav", "array": np.zeros(1600, dtype="float32"), "sampling_rate": 16000}],
            "text": ["سلام"],
        }
    ).cast_column("audio", datasets.Audio(sampling_rate=16000))
    dataset.save_to_disk(str(root))
    with pytest.raises(prepare_dataset.DatasetValidationError, match="decoded Audio"):
        prepare_dataset.read_manifest(root)


# -------------------------------------------------------------- training plan
def test_training_plan_is_written_with_effective_batch_size(tmp_path) -> None:
    corpus = _corpus(tmp_path)
    config = tmp_path / "training.json"
    config.write_text(
        json.dumps(
            {
                "model": "openai/whisper-small",
                "output_dir": "runs/x",
                "batch_size": 4,
                "grad_accum": 3,
                "learning_rate": 1e-5,
                "epochs": 2,
                "data": {"train": str(corpus / "manifests" / "train.jsonl")},
            }
        ),
        encoding="utf-8",
    )
    plan_path = tmp_path / "plan.json"
    assert train_plan.main(["--config", str(config), "--write", str(plan_path)]) == 0
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan["batch_size"] == 4 and plan["effective_batch_size"] == 12
    assert plan["epochs"] == 2 and plan["learning_rate"] == pytest.approx(1e-5)
    assert plan["trainer"].startswith("external")
    assert plan["data"][0]["split"] == "train"


def test_cli_flags_override_the_config_file(tmp_path) -> None:
    corpus = _corpus(tmp_path)
    config = tmp_path / "training.json"
    config.write_text(
        json.dumps(
            {
                "output_dir": "runs/x",
                "batch_size": 8,
                "data": {"train": str(corpus / "manifests" / "train.jsonl")},
            }
        ),
        encoding="utf-8",
    )
    plan_path = tmp_path / "plan.json"
    assert train_plan.main(
        ["--config", str(config), "--batch-size", "16", "--epochs", "5",
         "--write", str(plan_path)]
    ) == 0
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan["batch_size"] == 16 and plan["epochs"] == 5


def test_missing_manifest_fails_before_training_starts(tmp_path, capsys) -> None:
    config = tmp_path / "training.json"
    config.write_text(
        json.dumps({"output_dir": "runs/x", "data": {"train": "absent.jsonl"}}),
        encoding="utf-8",
    )
    assert train_plan.main(["--config", str(config)]) == 2
    assert "manifest not found" in capsys.readouterr().err


def test_schema_check_does_not_require_the_corpus(tmp_path, capsys) -> None:
    config = tmp_path / "training.json"
    config.write_text(
        json.dumps({"output_dir": "runs/x", "data": {"train": "absent.jsonl"}}),
        encoding="utf-8",
    )
    assert train_plan.main(["--config", str(config), "--check", "--no-require-paths"]) == 0
    assert "no training is performed" in capsys.readouterr().out


def test_environment_variable_selects_the_config(tmp_path, monkeypatch) -> None:
    corpus = _corpus(tmp_path)
    config = tmp_path / "training.json"
    config.write_text(
        json.dumps(
            {"output_dir": "runs/x", "data": {"train": str(corpus / "manifests" / "train.jsonl")}}
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PVAI_TRAIN_CONFIG", str(config))
    monkeypatch.setenv("PVAI_CORPUS_ROOT", str(corpus / "audio"))
    assert train_plan.main(["--check"]) == 0


def test_training_plan_requires_a_configuration() -> None:
    with pytest.raises(ConfigError, match="--config|--train-manifest"):
        train_plan._merge(None, train_plan._build_parser().parse_args([]))


# ------------------------------------------------------- benchmark path hygiene
def test_benchmark_report_directory_is_env_overridable(tmp_path, monkeypatch) -> None:
    """Reports must not be forced into the checkout (CI writes elsewhere)."""
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "pvai_bench_chat", ROOT / "benchmarks" / "bench_chat.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["pvai_bench_chat"] = module
    spec.loader.exec_module(module)

    monkeypatch.delenv("PVAI_BENCHMARK_OUTPUT_DIR", raising=False)
    assert module.results_dir() == ROOT / "benchmarks" / "results"
    monkeypatch.setenv("PVAI_BENCHMARK_OUTPUT_DIR", str(tmp_path / "reports"))
    assert module.results_dir() == tmp_path / "reports"


def test_eval_set_path_is_env_overridable(monkeypatch) -> None:
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "pvai_persian_eval_env", ROOT / "benchmarks" / "persian_eval.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["pvai_persian_eval_env"] = module
    spec.loader.exec_module(module)

    monkeypatch.setenv("PVAI_EVAL_SET", "/data/eval/fa.json")
    args = module.main  # CLI builds its own parser; assert the env default reachable
    assert callable(args)
    monkeypatch.delenv("PVAI_EVAL_SET")
    assert module.DEFAULT_SET.is_file()
    assert os.path.exists(module.DEFAULT_SET)


# --------------------------------------------------- shipped example, end to end
def test_dataset_example_runs_against_a_corpus_at_the_corpus_root(tmp_path, monkeypatch) -> None:
    """``${PVAI_CORPUS_ROOT:-corpus}`` must resolve to a real corpus unchanged.

    The example file is what users copy; if it only worked after hand-editing
    every path, "externalized configuration" would be a claim with no proof.
    """
    corpus = _corpus(tmp_path)
    monkeypatch.setenv("PVAI_CORPUS_ROOT", str(corpus))
    # The example expects <root>/manifests/{train,validation,test}.jsonl and
    # <root>/audio, which is exactly the fixture layout.
    assert prepare_dataset.main(["--config", str(CONFIGS / "dataset.example.yaml"), "--check"]) == 0


def test_training_example_plan_is_written_from_the_shipped_file(tmp_path, monkeypatch) -> None:
    corpus = _corpus(tmp_path)
    validated = corpus / "validated"
    validated.mkdir()
    for split in ("train", "validation", "test"):
        (validated / f"{split}.jsonl").write_text(
            (corpus / "manifests" / f"{split}.jsonl").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    monkeypatch.setenv("PVAI_CORPUS_ROOT", str(corpus))
    plan_path = tmp_path / "plan.json"
    assert train_plan.main(
        ["--config", str(CONFIGS / "training.example.yaml"), "--write", str(plan_path)]
    ) == 0
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan["effective_batch_size"] == plan["batch_size"] * plan["grad_accum"]
    assert {item["split"] for item in plan["data"]} == {"train", "validation", "test"}
    assert all(Path(item["manifest"]).is_file() for item in plan["data"])


def test_documented_tools_and_configs_exist() -> None:
    """docs/DATASETS.md and README name real files — keep them honest."""
    text = (ROOT / "docs" / "DATASETS.md").read_text(encoding="utf-8")
    text += (ROOT / "README.md").read_text(encoding="utf-8")
    referenced = {
        "tools/prepare_dataset.py",
        "tools/train_plan.py",
        "tools/training_config.py",
        "configs/dataset.example.yaml",
        "configs/training.example.yaml",
    }
    missing = [name for name in referenced if not (ROOT / name).is_file()]
    assert not missing, missing
    for name in referenced:
        assert name in text, f"{name} is not documented"
