#!/usr/bin/env python3
"""Persian ASR evaluation: WER/CER with a documented normalization policy.

This tool exists to keep one promise of the audit: *no accuracy claim without a
measurement*.  It never invents a number.

Three modes
-----------

1. ``--list`` — print the evaluation set (40 sentences, 11 categories).

2. ``--hypotheses FILE`` — score pre-existing transcripts against the
   reference set.  ``FILE`` is JSON::

       {"fa-001": "سلام حال شما چطوره", ...}

   or a list of ``{"id": ..., "text": ...}`` objects.  This is the mode used
   when a human has listened to real recordings and typed what they heard, or
   when an ASR run produced a transcript file.  WER/CER are computed with
   :func:`server.persian.evaluate`, i.e. the documented policy
   (NFC, Arabic ي/ك → ی/ک, diacritics removed, digits unified, نیم‌فاصله → space,
   punctuation removed).  Formatting-only differences therefore do **not**
   count as recognition errors.

3. ``--audio-dir DIR`` — transcribe ``DIR/<id>.wav`` with the *real* local
   Whisper model and score the result.  This is the only mode that measures
   actual recognition quality.  It requires:

   * the local faster-whisper model (``PVAI_ASR_MODEL_PATH``),
   * 16 kHz mono PCM16 WAV recordings of each sentence, read by the same
     microphone/speaker setup as the live pipeline.

   If the model or the recordings are missing, the tool prints
   ``NOT MEASURED`` with the reason and exits non-zero.  It must never fall back
   to a fake transcript: a fabricated accuracy number is worse than none.

Nothing here uses the network.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import wave
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # allow `python benchmarks/persian_eval.py`
    sys.path.insert(0, str(ROOT))

from server.persian import (  # noqa: E402
    evaluate_detailed,
    false_accept_examples,
    false_accept_rate,
    normalize_for_eval,
)

DEFAULT_SET = Path(__file__).resolve().parent / "persian_eval_set.json"


# ------------------------------------------------------------------ eval set
def load_set(path: str | Path = DEFAULT_SET) -> dict[str, Any]:
    """Load and validate the evaluation set."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("evaluation set must be a JSON object")
    sentences = data.get("sentences")
    if not isinstance(sentences, list) or not sentences:
        raise ValueError("evaluation set has no sentences")
    seen: set[str] = set()
    for entry in sentences:
        if not isinstance(entry, dict):
            raise ValueError("each evaluation entry must be an object")
        if not isinstance(entry.get("id"), str) or not isinstance(entry.get("text"), str):
            raise ValueError("evaluation id and text must be strings")
        identifier = entry["id"]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", identifier):
            raise ValueError(f"unsafe evaluation id: {identifier!r}")
        text = entry["text"].strip()
        kind = entry.get("kind", "speech")
        if kind == "no_speech" and text:
            raise ValueError("no_speech entries must have empty text")
        if kind not in {"speech", "no_speech"}:
            raise ValueError(f"unknown entry kind {kind!r} in {entry!r}")
        # A no-speech clip (silence, room noise) legitimately has no text: the
        # point of the entry is that transcribing it *is* the failure.
        if not identifier or (kind == "speech" and not text):
            raise ValueError(f"invalid evaluation entry: {entry!r}")
        if identifier in seen:
            raise ValueError(f"duplicate evaluation id: {identifier}")
        seen.add(identifier)
    return data


def references(data: dict[str, Any]) -> dict[str, str]:
    """Reference text for the *speech* entries; no-speech clips are excluded.

    Including them would make every hallucination an insertion and drag WER
    below a meaningful reading; they are scored separately by
    :func:`score_no_speech`.
    """
    return {
        str(item["id"]): str(item["text"])
        for item in data["sentences"]
        if str(item.get("kind", "speech")) != "no_speech"
    }


# -------------------------------------------------------------------- scoring
def load_hypotheses(path: str | Path) -> dict[str, str]:
    """Accept ``{id: text}`` or ``[{"id": ..., "text": ...}]`` JSON."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        candidates = raw.get("hypotheses", raw) if isinstance(raw.get("hypotheses"), dict) else raw
        if isinstance(candidates, dict):
            if any(not isinstance(key, str) or not key.strip() or not isinstance(value, str)
                   for key, value in candidates.items()):
                raise ValueError("hypotheses must map non-empty string ids to string text")
            return candidates
        raise ValueError("hypotheses JSON object must map id -> text")
    if isinstance(raw, list):
        mapping: dict[str, str] = {}
        for item in raw:
            if not isinstance(item, dict) or "id" not in item or "text" not in item:
                raise ValueError("each hypothesis needs an id and a text field")
            identifier, text = item["id"], item["text"]
            if not isinstance(identifier, str) or not identifier.strip() or not isinstance(text, str):
                raise ValueError("hypothesis id and text must be strings (id non-empty)")
            if identifier in mapping:
                raise ValueError(f"duplicate hypothesis id: {identifier}")
            mapping[identifier] = text
        return mapping
    raise ValueError("hypotheses file must be a JSON object or list")


def score(reference: dict[str, str], hypothesis: dict[str, str]) -> dict[str, Any]:
    """Score available ``id``s; report missing ones instead of silently skipping."""
    pairs: list[tuple[str, str]] = []
    ids: list[str] = []
    missing: list[str] = []
    for identifier, text in reference.items():
        if identifier in hypothesis:
            pairs.append((text, hypothesis[identifier]))
            ids.append(identifier)
        else:
            missing.append(identifier)
    if not pairs:
        raise ValueError("no hypothesis matches an id in the evaluation set")
    result = evaluate_detailed(pairs)
    return {
        "sentences": len(pairs),
        "missing_ids": missing,
        "wer": result["wer"],
        "cer": result["cer"],
        "substitutions": result["substitutions"],
        "deletions": result["deletions"],
        "insertions": result["insertions"],
        "reference_words": result["word_units"],
        "reference_chars": result["char_units"],
        "ids": ids,
    }


def score_by_category(
    data: dict[str, Any], hypothesis: dict[str, str]
) -> dict[str, dict[str, Any]]:
    """Per-category error rates (the categories are the ones in the JSON)."""
    categories: dict[str, list[tuple[str, str]]] = {}
    for entry in data["sentences"]:
        if str(entry.get("kind", "speech")) == "no_speech":
            continue  # scored as a false accept, not as an error rate
        identifier = str(entry["id"])
        text = hypothesis.get(identifier)
        if text is None:
            continue
        categories.setdefault(str(entry.get("category", "other")), []).append((entry["text"], text))
    report: dict[str, dict[str, Any]] = {}
    for name, pairs in sorted(categories.items()):
        result = evaluate_detailed(pairs)
        report[name] = {
            "sentences": len(pairs),
            "wer": result["wer"],
            "cer": result["cer"],
            "reference_words": result["word_units"],
        }
    return report


# ----------------------------------------------------------------- audio mode
def read_wav_mono16k(path: Path) -> bytes:
    """Read a PCM16 mono 16 kHz WAV; raise a clear error otherwise.

    A truncated or non-WAV file used to escape as a raw ``wave.Error`` /
    ``EOFError`` traceback in the middle of a scoring run, so the operator got
    no hint about *which* recording was broken.  Everything unreadable is
    re-raised as ``ValueError`` naming the file, which is what the callers
    already handle.  The strict format policy is deliberate (see
    ``docs/PC_VOICE.md``): no silent resampling, no guessed channel layout.
    """
    try:
        with wave.open(str(path), "rb") as handle:
            channels = handle.getnchannels()
            width = handle.getsampwidth()
            rate = handle.getframerate()
            declared = handle.getnframes()
            frames = handle.readframes(declared)
    except (OSError, EOFError, wave.Error) as exc:
        raise ValueError(f"{path.name}: unreadable WAV ({exc})") from exc
    if channels != 1 or width != 2:
        raise ValueError(
            f"{path.name}: expected mono 16-bit PCM, got {channels} channel(s), "
            f"{width * 8}-bit"
        )
    if rate != 16000:
        raise ValueError(f"{path.name}: expected 16000 Hz, got {rate} Hz")
    if not frames:
        # Zero samples transcribe to nothing, which would be scored as a silent
        # false rejection instead of the recording problem it is.
        raise ValueError(f"{path.name}: WAV contains no audio frames")
    expected = declared * channels * width
    if len(frames) != expected:
        raise ValueError(
            f"{path.name}: truncated WAV ({len(frames)} of {expected} bytes)"
        )
    return frames


def transcribe_with_local_model(
    audio_dir: Path, reference: dict[str, str], settings: Any
) -> tuple[dict[str, str], list[str]]:
    """Transcribe ``<id>.wav`` with the real local Whisper model."""
    from server.voice import LocalAudio, VoiceError

    audio = LocalAudio(settings)
    audio.validate()
    hypotheses: dict[str, str] = {}
    missing: list[str] = []
    try:
        for identifier in reference:
            path = audio_dir / f"{identifier}.wav"
            if not path.is_file():
                missing.append(identifier)
                continue
            try:
                hypotheses[identifier] = audio.transcribe_pcm(read_wav_mono16k(path))
            except (ValueError, VoiceError) as exc:
                raise SystemExit(f"error: {exc}") from exc
    finally:
        # The model holds hundreds of megabytes; a scoring run must not leak it.
        close = getattr(audio, "close", None)
        if callable(close):
            close()
    return hypotheses, missing


def transcribe_no_speech(audio_dir: Path, settings: Any) -> dict[str, Any]:
    """Transcribe real silence/noise clips and report the false-accept rate.

    Every ``*.wav`` in the directory is expected to contain no speech (a silent
    room, a fan, a TV).  Nothing is scored with WER: there is no reference text.
    What matters is whether the assistant would have started a turn.
    """
    from server.voice import LocalAudio, VoiceError

    files = sorted(audio_dir.glob("*.wav"))
    transcripts: list[str] = []
    unreadable: list[str] = []
    audio = LocalAudio(settings) if files else None
    if audio is not None:
        audio.validate()
    try:
        for path in files:
            try:
                transcripts.append(audio.transcribe_pcm(read_wav_mono16k(path)))
            except (ValueError, VoiceError) as exc:
                unreadable.append(f"{path.name}: {exc}")
    finally:
        if audio is not None:
            close = getattr(audio, "close", None)
            if callable(close):
                close()
    report = false_accept_rate(transcripts)
    report["examples"] = false_accept_examples(transcripts)
    report["unreadable"] = unreadable
    return report


def score_speech_rejections(data: dict[str, Any], hypothesis: dict[str, str]) -> dict[str, Any]:
    """Share of *recorded speech* clips whose filtered ASR returned no words.

    Missing hypotheses are reported, never scored as rejections. Use only
    transcripts from actual recordings to call this a measured ASR rate.
    """
    ids = [str(item["id"]) for item in data["sentences"]
           if item.get("kind", "speech") == "speech"]
    available = [key for key in ids if key in hypothesis]
    rejected = sum(not normalize_for_eval(hypothesis[key]).strip() for key in available)
    return {
        "clips": len(available),
        "rejected": rejected,
        "false_reject_rate": round(rejected / len(available), 4) if available else 0.0,
        "missing_ids": [key for key in ids if key not in hypothesis],
    }


def score_no_speech(
    data: dict[str, Any], hypothesis: dict[str, str]
) -> dict[str, Any]:
    """False-accept rate on the negative set (entries with empty ``text``).

    WER cannot express this: an empty reference makes every hypothesis an
    insertion, and the ratio says nothing about how *often* it happened.  The
    negative entries carry ``"text": ""`` and describe silence, room noise or a
    TV — the cases where Whisper invents a sentence and the assistant answers
    a question nobody asked.
    """
    ids = [str(entry["id"]) for entry in data.get("sentences", [])
           if str(entry.get("kind", "speech")) == "no_speech"]
    clips = [hypothesis[key] for key in ids if key in hypothesis]
    report = false_accept_rate(clips)
    report["missing_ids"] = [key for key in ids if key not in hypothesis]
    report["examples"] = false_accept_examples(clips)
    return report


# ---------------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Persian ASR evaluation (WER/CER) with the project normalization policy"
    )
    parser.add_argument("--set", default=str(DEFAULT_SET), help="evaluation set JSON")
    parser.add_argument("--list", action="store_true", help="print the evaluation set")
    parser.add_argument("--hypotheses", help="JSON transcript file to score")
    parser.add_argument("--audio-dir", help="directory of <id>.wav recordings")
    parser.add_argument("--json-out", help="write the report as JSON")
    parser.add_argument(
        "--no-speech-dir",
        help="directory of silence/noise wavs; reports the false-accept rate",
    )
    parser.add_argument(
        "--strict", action="store_true", help="fail when any reference id is missing"
    )
    args = parser.parse_args(argv)

    try:
        data = load_set(args.set)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    data.setdefault("normalization_policy", "server.persian.normalize_for_eval")
    reference = references(data)

    if args.list:
        print(f"# {len(reference)} sentences — policy: {data.get('normalization_policy', 'server.persian.normalize_for_eval')}")
        for entry in data["sentences"]:
            print(f"{entry['id']}\t{entry.get('category', 'other')}\t{entry['text']}")
        return 0

    if not args.hypotheses and not args.audio_dir:
        print("NOT MEASURED — no hypotheses supplied.")
        print("Provide --hypotheses FILE (pre-scored transcripts) or --audio-dir DIR")
        print("with 16 kHz mono PCM16 <id>.wav recordings and the local Whisper model.")
        print(f"Reference set: {args.set} ({len(reference)} sentences).")
        return 3

    missing_audio: list[str] = []
    if args.audio_dir:
        from server.config import Settings

        settings = Settings.from_env()
        hypotheses, missing_audio = transcribe_with_local_model(
            Path(args.audio_dir), reference, settings
        )
        if not hypotheses:
            print("NOT MEASURED — no <id>.wav recordings found in", args.audio_dir)
            return 3
    else:
        try:
            hypotheses = load_hypotheses(args.hypotheses)
        except (OSError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    try:
        report = score(reference, hypotheses)
    except ValueError as exc:
        print(f"error: {exc}")
        return 2

    report["modes"] = {
        "hypotheses": bool(args.hypotheses),
        "audio": bool(args.audio_dir),
    }
    report["normalization_policy"] = data["normalization_policy"]
    report["missing_audio"] = missing_audio
    report["by_category"] = score_by_category(data, hypotheses)
    report["no_speech"] = score_no_speech(data, hypotheses)
    report["speech_rejections"] = score_speech_rejections(data, hypotheses)
    if args.no_speech_dir:
        from server.config import Settings

        report["no_speech_audio"] = transcribe_no_speech(
            Path(args.no_speech_dir), Settings.from_env()
        )

    print("Persian ASR evaluation")
    print(f"  sentences scored : {report['sentences']}")
    print(f"  WER              : {report['wer'] * 100:.2f} %")
    print(f"  CER              : {report['cer'] * 100:.2f} %")
    print(
        "  edits            : "
        f"{report['substitutions']} sub / {report['deletions']} del / "
        f"{report['insertions']} ins over {report['reference_words']} words"
    )
    if report["missing_ids"]:
        print(f"  missing ids      : {len(report['missing_ids'])} (excluded, see --strict)")
    for name, values in report["by_category"].items():
        print(f"    {name:<14} WER {values['wer'] * 100:6.2f} %  CER {values['cer'] * 100:6.2f} %")
    no_speech = report["no_speech"]
    if no_speech["measured"]:
        print(
            f"  false accepts    : {no_speech['accepted']}/{no_speech['clips']}"
            f" = {no_speech['false_accept_rate'] * 100:.1f} %"
            " (no-speech clips that produced text)"
        )
        for example in no_speech["examples"][:5]:
            print(f"    hallucinated   : {example[:70]}")
    audio_report = report.get("no_speech_audio")
    if audio_report is not None:
        if audio_report["measured"]:
            print(
                f"  false accepts    : {audio_report['accepted']}/{audio_report['clips']}"
                f" = {audio_report['false_accept_rate'] * 100:.1f} % (from --no-speech-dir)"
            )
        else:
            print("  false accepts    : NOT MEASURED — no silence/noise wavs found")
    else:
        print(
            "  false accepts    : NOT MEASURED — pass --no-speech-dir with silence/"
            "noise recordings"
        )
    print(f"  policy           : {data['normalization_policy']}")

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"  report written   : {args.json_out}")

    if args.strict and (report["missing_ids"] or missing_audio):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
