#!/usr/bin/env python3
"""Validate local ASR manifests and optionally export portable JSONL; no training."""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import wave
from pathlib import Path
from typing import Any


class DatasetValidationError(ValueError):
    """A manifest or one of its recordings violates the data contract."""


def expanded_path(value: str | Path) -> Path:
    return Path(os.path.expandvars(str(value))).expanduser()


def read_manifest(path: Path) -> list[dict[str, Any]]:
    """Accept UTF-8 JSON arrays, JSONL records, or header-based CSV."""
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            if path.suffix.lower() == '.csv':
                reader = csv.DictReader(handle)
                if not reader.fieldnames or not {'id', 'audio', 'text'} <= set(reader.fieldnames):
                    raise ValueError('CSV header must include id,audio,text')
                rows = list(reader)
            elif path.suffix.lower() == '.json':
                rows = json.load(handle)
            elif path.suffix.lower() == '.jsonl':
                rows = [json.loads(line) for line in handle if line.strip()]
            else:
                raise ValueError('supported formats: .json, .jsonl, .csv')
    except (OSError, UnicodeError, ValueError, csv.Error) as exc:
        raise DatasetValidationError(f'{path}: cannot read manifest: {exc}') from exc
    if not isinstance(rows, list) or not rows:
        raise DatasetValidationError(f'{path}: expected a non-empty list of records')
    return rows


def validate_manifest(path: Path, *, audio_root: Path | None = None,
                      sample_rate: int = 16000) -> list[dict[str, str]]:
    """Validate PCM16 mono WAVs; do not silently modify training recordings."""
    if sample_rate <= 0:
        raise DatasetValidationError('sample rate must be positive')
    path = expanded_path(path).resolve()
    root = expanded_path(audio_root).resolve() if audio_root is not None else path.parent
    result = []
    seen: set[str] = set()
    recordings: set[Path] = set()
    for index, row in enumerate(read_manifest(path), 1):
        context = f'{path}: record {index}'
        if not isinstance(row, dict):
            raise DatasetValidationError(f'{context}: expected an object')
        for field in ('id', 'audio', 'text'):
            if not isinstance(row.get(field), str) or not row[field].strip():
                raise DatasetValidationError(f'{context}: {field} must be a non-empty string')
        identifier = row['id'].strip()
        if identifier in seen:
            raise DatasetValidationError(f'{context}: duplicate id {identifier!r}')
        seen.add(identifier)
        audio = expanded_path(row['audio'])
        audio = (root / audio).resolve()
        if audio in recordings:
            raise DatasetValidationError(f'{context}: duplicate recording {audio}')
        recordings.add(audio)
        try:
            with wave.open(str(audio), 'rb') as handle:
                if (handle.getnchannels(), handle.getsampwidth(), handle.getframerate()) != (1, 2, sample_rate):
                    raise ValueError(f'expected PCM16 mono {sample_rate} Hz WAV')
                count = handle.getnframes()
                if count <= 0 or len(handle.readframes(count)) != count * 2:
                    raise ValueError('empty or truncated audio')
        except (OSError, EOFError, wave.Error, ValueError) as exc:
            raise DatasetValidationError(f'{context}: {audio}: {exc}') from exc
        result.append({'id': identifier, 'audio': str(audio), 'text': row['text'].strip()})
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=expanded_path)
    parser.add_argument('--audio-root', type=expanded_path,
                        help='relative recordings resolve here; default: manifest directory')
    parser.add_argument('--sample-rate', type=int, default=16000)
    parser.add_argument('--out', type=expanded_path, help='optional validated JSONL export')
    args = parser.parse_args(argv)
    try:
        rows = validate_manifest(args.manifest, audio_root=args.audio_root,
                                 sample_rate=args.sample_rate)
        if args.out:
            output = args.out.resolve()
            if output == args.manifest.resolve() or any(output == Path(row['audio']) for row in rows):
                raise DatasetValidationError('output must not overwrite the manifest or audio')
            # Store paths relative to the exported manifest, not this machine's home.
            for row in rows:
                row['audio'] = Path(os.path.relpath(row['audio'], output.parent)).as_posix()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows),
                              encoding='utf-8')
        print(f'Validated {len(rows)} recordings (no training performed).')
        return 0
    except (DatasetValidationError, OSError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
