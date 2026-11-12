"""Run a fully local Persian Piper-TTS -> faster-whisper CPU smoke test."""

from __future__ import annotations

import argparse
import json
import os
import time
import unicodedata
import wave
from datetime import datetime
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEXT = "سلام، امروز هوا خوب است."


def _normalize(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text)
    normalized = normalized.replace("ي", "ی").replace("ك", "ک").replace("\u200c", " ")
    normalized = "".join(" " if unicodedata.category(char).startswith("P") else char for char in normalized)
    return " ".join(normalized.split()).split()


def _word_error_rate(reference: str, hypothesis: str) -> float | None:
    ref = _normalize(reference)
    hyp = _normalize(hypothesis)
    if not ref:
        return None

    previous = list(range(len(hyp) + 1))
    for ref_index, ref_word in enumerate(ref, start=1):
        current = [ref_index]
        for hyp_index, hyp_word in enumerate(hyp, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[hyp_index] + 1,
                    previous[hyp_index - 1] + (ref_word != hyp_word),
                )
            )
        previous = current
    return round(previous[-1] / len(ref), 4)


def _arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a local Persian WAV with Piper, then transcribe it with CPU int8 Whisper."
    )
    parser.add_argument("--voice", type=Path, required=True, help="local Piper .onnx voice file")
    parser.add_argument(
        "--asr-model-dir", type=Path, required=True, help="local faster-whisper model directory; no Hub ID accepted"
    )
    parser.add_argument("--output-dir", type=Path, default=ROOT / "models" / "audio-lab")
    parser.add_argument("--text", default=DEFAULT_TEXT, help="synthetic Persian test sentence")
    parser.add_argument("--threads", type=int, default=4)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _arguments(argv)
    voice_path = args.voice.resolve()
    asr_model_dir = args.asr_model_dir.resolve()
    if not voice_path.is_file() or not voice_path.name.lower().endswith(".onnx"):
        raise SystemExit(f"Piper voice .onnx file not found: {voice_path}")
    if not Path(f"{voice_path}.json").is_file():
        raise SystemExit(f"Piper voice config not found: {voice_path}.json")
    if not asr_model_dir.is_dir():
        raise SystemExit(f"Local faster-whisper model directory not found: {asr_model_dir}")
    if args.threads < 1:
        raise SystemExit("--threads must be at least 1")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    wav_path = (args.output_dir / "persian_synthetic.wav").resolve()
    report_path = (args.output_dir / "pc-audio-smoke.json").resolve()

    # Prevent accidental model downloads or telemetry if a dependency is misconfigured.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"

    try:
        from piper import PiperVoice
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise SystemExit(
            "Audio test dependencies are missing. Install them only in the separate audio environment."
        ) from exc

    tts_started = time.perf_counter()
    voice = PiperVoice.load(str(voice_path), use_cuda=False)
    with wave.open(str(wav_path), "wb") as wav_file:
        voice.synthesize_wav(args.text, wav_file)
    tts_ms = round((time.perf_counter() - tts_started) * 1000, 2)

    with wave.open(str(wav_path), "rb") as wav_file:
        audio_seconds = round(wav_file.getnframes() / wav_file.getframerate(), 3)
        sample_rate = wav_file.getframerate()

    load_started = time.perf_counter()
    model = WhisperModel(
        str(asr_model_dir),
        device="cpu",
        compute_type="int8",
        cpu_threads=args.threads,
    )
    asr_load_ms = round((time.perf_counter() - load_started) * 1000, 2)

    asr_started = time.perf_counter()
    segments, info = model.transcribe(
        str(wav_path),
        language="fa",
        beam_size=1,
        vad_filter=False,
    )
    recognized_text = "".join(segment.text for segment in segments).strip()
    asr_ms = round((time.perf_counter() - asr_started) * 1000, 2)

    report = {
        "created_at": datetime.now().astimezone().isoformat(),
        "mode": "local_only",
        "tts": {
            "engine": "Piper",
            "voice_path": str(voice_path),
            "generation_ms": tts_ms,
            "sample_rate_hz": sample_rate,
            "audio_seconds": audio_seconds,
            "wav_path": str(wav_path),
        },
        "asr": {
            "engine": "faster-whisper",
            "model_dir": str(asr_model_dir),
            "device": "cpu",
            "compute_type": "int8",
            "threads": args.threads,
            "language": info.language,
            "language_probability": round(float(info.language_probability), 4),
            "load_ms": asr_load_ms,
            "transcription_ms": asr_ms,
            "real_time_factor": round(asr_ms / (audio_seconds * 1000), 3) if audio_seconds else None,
            "recognized_text": recognized_text,
        },
        "synthetic_test": {
            "reference_text": args.text,
            "word_error_rate": _word_error_rate(args.text, recognized_text),
            "warning": "This measures a synthetic TTS round trip, not recognition quality on real speech.",
        },
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Saved WAV: {wav_path}")
    print(f"Saved report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
