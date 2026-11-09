#!/usr/bin/env python3
"""Validate a training configuration and emit a resolved, reproducible plan.

There is **no training loop in this repository** (ASR runs through
CTranslate2, TTS through ONNX Runtime; see ``docs/DATASETS.md``).  What this tool
does is make the contract with an *external* trainer explicit: batch size,
learning rate, epochs, seed, precision, output directory and the manifests it
must read are validated here, printed, and can be written as JSON so the trainer
cannot quietly disagree with the run it is compared against.

    # schema only (works without a corpus; what CI checks)
    python tools/train_plan.py --config configs/training.example.yaml --check --no-require-paths

    # validate a real corpus and hand the plan to a trainer
    PVAI_CORPUS_ROOT=/corpus python tools/train_plan.py \
        --config configs/training.yaml --write runs/whisper-small/plan.json

Every value can also come from flags, so a one-off run needs no file.  Flags win
over the configuration file, and nothing is ever downloaded.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # allow `python tools/train_plan.py`
    sys.path.insert(0, str(ROOT))

from tools.training_config import (  # noqa: E402
    TRAINING_CONFIG_ENV,
    ConfigError,
    TrainingConfig,
    as_float,
    as_int,
    as_str,
    expand_path,
    load_training_config,
    resolve_path,
    summary,
    write_plan,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate a training configuration and emit a resolved plan "
                    "(this repository performs no training)."
    )
    parser.add_argument("--config", type=expand_path,
                        help=f"training config (.json/.yaml); env: {TRAINING_CONFIG_ENV}")
    parser.add_argument("--check", action="store_true",
                        help="validate and print the plan; write nothing")
    parser.add_argument("--no-require-paths", action="store_true",
                        help="validate the schema only (corpus/manifests may be absent)")
    parser.add_argument("--write", type=expand_path, metavar="PLAN_JSON",
                        help="write the resolved plan as JSON for the external trainer")
    # Overrides; each is optional and wins over the configuration file.
    parser.add_argument("--model", help="base checkpoint, e.g. openai/whisper-small")
    parser.add_argument("--output-dir", type=expand_path, help="trainer output directory")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--batch-size", type=int)
    parser.add_argument("--grad-accum", type=int)
    parser.add_argument("--learning-rate", type=float)
    parser.add_argument("--epochs", type=int)
    parser.add_argument("--warmup-steps", type=int)
    parser.add_argument("--precision", choices=("fp32", "fp16", "bf16", "int8"))
    parser.add_argument("--train-manifest", type=expand_path)
    parser.add_argument("--validation-manifest", type=expand_path)
    parser.add_argument("--test-manifest", type=expand_path)
    return parser


def _merge(base: TrainingConfig | None, args: argparse.Namespace) -> TrainingConfig:
    """Apply CLI overrides on top of the config file (flags win)."""
    path_values = {
        "output_dir": args.output_dir,
        "train": args.train_manifest,
        "validation": args.validation_manifest,
        "test": args.test_manifest,
    }
    if base is None:
        if not path_values["train"]:
            raise ConfigError(
                "a training configuration is required: pass --config (see "
                "configs/training.example.yaml) or --train-manifest"
            )
        if not path_values["output_dir"]:
            raise ConfigError("--output-dir is required without --config")
        output_dir = expand_path(path_values["output_dir"])
        base_dir = Path.cwd()
        data = tuple(
            item
            for item in _extra_splits(args, base_dir)
        )
        return TrainingConfig(
            source=None,
            model=as_str(args.model, context="--model", default="whisper-small"),
            output_dir=output_dir,
            base=base_dir,
            seed=as_int(args.seed, context="--seed", default=13, minimum=0, maximum=2**31 - 1),
            batch_size=as_int(args.batch_size, context="--batch-size", default=8,
                              minimum=1, maximum=4096),
            grad_accum=as_int(args.grad_accum, context="--grad-accum", default=1,
                              minimum=1, maximum=1024),
            learning_rate=as_float(args.learning_rate, context="--learning-rate",
                                   default=1e-5, minimum=0.0, maximum=1.0,
                                   exclusive_minimum=True),
            epochs=as_int(args.epochs, context="--epochs", default=3, minimum=1,
                          maximum=10000),
            warmup_steps=as_int(args.warmup_steps, context="--warmup-steps", default=100,
                                minimum=0, maximum=1_000_000),
            precision=(args.precision or "fp32").lower(),
            data=data,
        )

    from dataclasses import replace

    overrides: dict[str, Any] = {}
    if args.model:
        overrides["model"] = as_str(args.model, context="--model")
    if args.output_dir:
        overrides["output_dir"] = expand_path(args.output_dir)
    if args.seed is not None:
        overrides["seed"] = as_int(args.seed, context="--seed", minimum=0, maximum=2**31 - 1)
    if args.batch_size is not None:
        overrides["batch_size"] = as_int(args.batch_size, context="--batch-size",
                                         minimum=1, maximum=4096)
    if args.grad_accum is not None:
        overrides["grad_accum"] = as_int(args.grad_accum, context="--grad-accum",
                                          minimum=1, maximum=1024)
    if args.learning_rate is not None:
        overrides["learning_rate"] = as_float(
            args.learning_rate, context="--learning-rate", minimum=0.0, maximum=1.0,
            exclusive_minimum=True,
        )
    if args.epochs is not None:
        overrides["epochs"] = as_int(args.epochs, context="--epochs", minimum=1,
                                     maximum=10000)
    if args.warmup_steps is not None:
        overrides["warmup_steps"] = as_int(args.warmup_steps, context="--warmup-steps",
                                            minimum=0, maximum=1_000_000)
    if args.precision:
        overrides["precision"] = args.precision.lower()

    data = list(base.data)
    for name, value in (
        ("train", args.train_manifest),
        ("validation", args.validation_manifest),
        ("test", args.test_manifest),
    ):
        if value is None:
            continue
        manifest = value if value.is_absolute() else Path.cwd() / value
        existing = base.split(name)
        if existing is None:
            data.append(
                type(base.data[0])(  # SplitConfig
                    name=name,
                    manifest=manifest,
                    audio_root=None,
                    out=None,
                    sample_rate=base.data[0].sample_rate if base.data else 16000,
                    min_duration_s=0.0,
                    max_duration_s=None,
                )
            )
        else:
            data[data.index(existing)] = replace(existing, manifest=manifest)
    overrides["data"] = tuple(data)
    return replace(base, **overrides)


def _extra_splits(args: argparse.Namespace, base_dir: Path):
    from tools.training_config import SplitConfig

    for name in ("train", "validation", "test"):
        value = getattr(args, f"{name}_manifest")
        if value is None:
            continue
        manifest = value if Path(value).is_absolute() else base_dir / Path(value)
        yield SplitConfig(
            name=name,
            manifest=resolve_path(manifest, base_dir, context=f"--{name}-manifest") or manifest,
            audio_root=None,
            out=None,
            sample_rate=16000,
        )


def _resolve_config(explicit: Path | None) -> Path | None:
    import os

    if explicit is not None:
        return explicit
    from_env = os.getenv(TRAINING_CONFIG_ENV, "").strip()
    return expand_path(from_env) if from_env else None


def _run(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    require_paths = not args.no_require_paths
    config_path = _resolve_config(args.config)
    base = (
        load_training_config(config_path, require_paths=False) if config_path else None
    )
    config = _merge(base, args)
    if require_paths:
        from tools.training_config import _check_manifest_path

        for item in config.data:
            _check_manifest_path(item.manifest, context=f"data.{item.name}",
                                 require_paths=True)

    print(f"props: {summary(config)}")
    for item in config.data:
        print(f"  data.{item.name}: {item.manifest}")
    print("note: no training is performed by this repository; run the external trainer "
          f"with {args.write or 'the plan below'}.")
    if args.write is not None:
        written = write_plan(config, args.write)
        print(f"plan written to {written}")
    elif not args.check:
        print(json.dumps(config.plan(), ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return _run(argv)
    except (ConfigError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
