"""Capture from the real PC microphone, run the real VAD and Whisper, and report numbers.

This is the hardware half of the voice stack that ``pytest`` deliberately replaces
with fakes.  It records nothing to disk by default (live audio is never persisted),
prints a labeled report, and is safe to run offline.

Usage (Windows PowerShell):

    $env:PYTHONIOENCODING = "utf-8"
    .\\.venv\\Scripts\\python.exe tools\\mic_probe.py --seconds 12

Speak a Persian sentence while it runs.  Every number is either ``MEASURED`` on
this machine with the real device/model, or ``NOT MEASURED`` with the reason.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MEASURED = "MEASURED"
NOT_MEASURED = "NOT MEASURED"


def _arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Real microphone -> VAD -> Whisper probe (fully local).")
    parser.add_argument("--seconds", type=float, default=12.0, help="capture duration")
    parser.add_argument("--device", default=None, help="input device name or index (default: system default)")
    parser.add_argument("--sample-rate", type=int, default=16000)
    parser.add_argument(
        "--device-rate",
        type=int,
        default=None,
        help="capture at this device rate then resample to --sample-rate "
        "(Windows MME often lacks true 16 kHz support)",
    )
    parser.add_argument("--block-ms", type=int, default=20)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--keep-wav", type=Path, default=None, help="optional WAV to keep for inspection")
    parser.add_argument("--no-asr", action="store_true", help="capture + VAD only, skip Whisper")
    parser.add_argument(
        "--max-segments",
        type=int,
        default=3,
        help="decode at most this many VAD segments; Whisper pads to a fixed "
        "30 s window, so this is the only thing that bounds the run time",
    )
    return parser.parse_args(argv)


def _resolve_device(sd: Any, spec: str | None) -> int | None:
    if spec is None or spec == "":
        return None
    try:
        return int(spec)
    except ValueError:
        pass
    wanted = spec.strip().lower()
    for index, device in enumerate(sd.query_devices()):
        if wanted in str(device["name"]).lower() and int(device.get("max_input_channels", 0)) > 0:
            return index
    raise SystemExit(f"No input device matches {spec!r}")


def main(argv: Sequence[str] | None = None) -> int:
    args = _arguments(argv)
    if args.seconds <= 0:
        raise SystemExit("--seconds must be positive")
    device_rate = int(args.device_rate or args.sample_rate)
    if device_rate <= 0 or args.sample_rate <= 0:
        raise SystemExit("sample rates must be positive")
    if args.block_ms <= 0:
        raise SystemExit("--block-ms must be positive")
    if args.block_ms not in {10, 20, 40}:
        # Same set the service accepts (``Settings.validate``): the report is
        # only comparable to a live session if the probe blocks like one.
        raise SystemExit("--block-ms must be one of 10, 20 or 40")
    block_bytes = int(device_rate * args.block_ms / 1000) * 2

    # Never let a misconfigured dependency reach the network.
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

    try:
        import sounddevice as sd
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        print(f"sounddevice unavailable: {exc}")
        return 2

    from server.audio_io import describe_devices
    from server.config import Settings
    from server.vad import REJECTED, SEGMENT, ONSET, block_rms, build_vad

    settings = Settings.from_env()
    if settings.audio_block_ms != args.block_ms:
        # The VAD counts *frames* and converts them to milliseconds with the
        # block length it was built with.  ``--block-ms 40`` while the detector
        # still believed 20 ms made every segment twice as long as configured
        # and the reported onset/endpoint delays wrong.  The probe's own block
        # length is what the capture loop actually feeds, so it wins.
        settings = replace(settings, audio_block_ms=args.block_ms)
    device = _resolve_device(sd, args.device)

    rows: list[dict[str, Any]] = []

    def add(metric: str, value: Any, unit: str, status: str, note: str = "") -> None:
        rows.append({"metric": metric, "value": value, "unit": unit, "status": status, "note": note})

    blocks: list[bytes] = []
    capture_gaps: list[float] = []
    stream_info: dict[str, Any] = {}

    def callback(indata: Any, frames: int, _time: Any, status: Any) -> None:
        if status:
            stream_info.setdefault("status_flags", set()).add(str(status))
        now = time.monotonic()
        last = stream_info.get("last_callback_at")
        if last is not None:
            # Deviation from the nominal block period: positive means late.
            capture_gaps.append((now - last) * 1000.0 - args.block_ms)
        stream_info["last_callback_at"] = now
        blocks.append(bytes(indata))

    print(describe_devices(sd))
    print()
    try:
        with sd.RawInputStream(
            samplerate=device_rate,
            blocksize=int(device_rate * args.block_ms / 1000),
            device=device,
            dtype="int16",
            channels=1,
            callback=callback,
        ) as _stream:
            print(f"Recording {args.seconds:g}s — speak Persian now…")
            time.sleep(args.seconds)
    except Exception as exc:  # noqa: BLE001 - hardware failure is a result, not a crash
        print(f"input stream failed: {exc}")
        return 3

    gaps = [abs(value) for value in capture_gaps]
    add("captured_blocks", len(blocks), "blocks", MEASURED)
    add("captured_seconds", round(len(blocks) * args.block_ms / 1000.0, 3), "s", MEASURED)
    if gaps:
        add("callback_gap_p95_ms", round(statistics.quantiles(gaps, n=20)[-1], 3), "ms", MEASURED)
        add("callback_gap_max_ms", round(max(gaps), 3), "ms", MEASURED)
    add("device_rate_hz", device_rate, "Hz", MEASURED, "what the device was opened at")
    add("asr_rate_hz", args.sample_rate, "Hz", MEASURED, "rate handed to VAD/ASR")

    # ------------------------------------------------- device-rate normalisation
    if device_rate != args.sample_rate and blocks:
        try:
            import numpy as np
            from scipy.signal import resample_poly

            joined = np.frombuffer(b"".join(blocks), dtype="<i2").astype(np.float32)
            divisor = __import__("math").gcd(device_rate, args.sample_rate)
            converted = resample_poly(
                joined, args.sample_rate // divisor, device_rate // divisor
            )
            pcm = np.clip(np.rint(converted), -32768, 32767).astype("<i2").tobytes()
            asr_block_bytes = int(args.sample_rate * args.block_ms / 1000) * 2
            blocks = [pcm[i:i + asr_block_bytes] for i in range(0, len(pcm), asr_block_bytes)]
            blocks = [block for block in blocks if len(block) == asr_block_bytes]
            add("resampled_blocks", len(blocks), "blocks", MEASURED, "after device-rate conversion")
        except Exception as exc:  # noqa: BLE001 - report, do not crash the probe
            add("resampled_blocks", None, "blocks", NOT_MEASURED, str(exc)[:120])

    # ---------------------------------------------------------------- real VAD
    detector = build_vad(settings)
    segments: list[bytes] = []
    onsets = 0
    rejected = 0
    onset_levels: list[float] = []
    peak_level = 0.0
    started_at: float | None = None
    for index, block in enumerate(blocks):
        at = index * args.block_ms / 1000.0
        boundary, segment = detector.feed_event(block, at=at)
        peak_level = max(peak_level, detector.last_rms)
        if boundary == ONSET:
            onsets += 1
            started_at = at
            onset_levels.append(
                round(detector.last_boundary.rms if detector.last_boundary else 0.0, 5)
            )
        elif boundary == SEGMENT and segment is not None:
            segments.append(segment)
            started_at = None
        elif boundary == REJECTED:
            rejected += 1
            started_at = None

    add("vad_onsets", onsets, "count", MEASURED)
    add("vad_rejected_utterances", rejected, "count", MEASURED, "too short: cough/keyboard")
    add("vad_complete_segments", len(segments), "count", MEASURED)
    add("vad_noise_floor", round(detector.noise_floor, 5), "rms", MEASURED)
    add("vad_onset_level", round(detector.onset_level, 5), "rms", MEASURED)
    # Median, not the last block: ``last_rms`` is one 20 ms block and a peak
    # measured that way changes every run.
    if blocks:
        levels = [block_rms(block) for block in blocks]
        add("capture_rms_median", round(statistics.median(levels), 5), "rms", MEASURED)
        add("capture_rms_peak", round(max(levels), 5), "rms", MEASURED, "loudest 20 ms block")
    if onset_levels:
        add("vad_first_onset_rms", onset_levels[0], "rms", MEASURED)
    if segments:
        lengths = [len(segment) / 2 / args.sample_rate for segment in segments]
        add("vad_segment_s_median", round(statistics.median(lengths), 3), "s", MEASURED)
        add("vad_segment_s_max", round(max(lengths), 3), "s", MEASURED)
        add("vad_segment_s_min", round(min(lengths), 3), "s", MEASURED)
    if started_at is not None:
        add("vad_trailing_open_utterance", 1, "flag", MEASURED, "speech still open at capture end")

    if args.keep_wav is not None and blocks:
        import wave

        args.keep_wav.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(args.keep_wav), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(args.sample_rate)
            wav.writeframes(b"".join(blocks))
        print(f"Saved capture: {args.keep_wav}")

    # ---------------------------------------------------------------- real ASR
    transcripts: list[dict[str, Any]] = []
    if args.no_asr:
        add("asr_ms_first_call", None, "ms", NOT_MEASURED, "--no-asr")
    elif not segments:
        add("asr_ms_first_call", None, "ms", NOT_MEASURED, "no complete utterance was detected")
    else:
        from server.voice import LocalAudio, VoiceError

        audio = LocalAudio(settings)
        try:
            load_started = time.perf_counter()
            audio.warmup()
            add("asr_model_load_ms", round((time.perf_counter() - load_started) * 1000, 1), "ms", MEASURED)
        except VoiceError as exc:
            add("asr_ms", None, "ms", NOT_MEASURED, str(exc).split("؛")[0][:200])
            audio = None
        if audio is not None:
            try:
                # Bound the decode work: on a 2-core CPU every call costs the
                # same regardless of clip length, so 6 segments can take minutes.
                for position, segment in enumerate(segments[: max(1, args.max_segments)], start=1):
                    started = time.perf_counter()
                    text = audio.transcribe_pcm(segment)
                    elapsed = (time.perf_counter() - started) * 1000
                    audio_s = len(segment) / 2 / args.sample_rate
                    transcripts.append(
                        {
                            "segment": position,
                            "audio_s": round(audio_s, 3),
                            "ms": round(elapsed, 1),
                            # faster-whisper pads every call to a fixed 30 s
                            # window, so this is decode cost per *call*, not
                            # per second of speech.  Kept out of ``rtf`` on
                            # purpose: dividing by a 1.8 s clip would report
                            # rtf=8 for the same work that scores 0.5 on 11 s.
                            "rtf": round(elapsed / (audio_s * 1000), 3) if audio_s else None,
                            "text": text,
                        }
                    )
                totals = sum(item["audio_s"] for item in transcripts)
                spent = sum(item["ms"] for item in transcripts)
                add("asr_calls", len(transcripts), "calls", MEASURED)
                add("asr_segments_available", len(segments), "segments", MEASURED,
                    "VAD output; not all are decoded unless --max-segments allows")
                add("asr_total_audio_s", round(totals, 3), "s", MEASURED)
                add("asr_total_ms", round(spent, 1), "ms", MEASURED)
                # The only honest aggregate: total wall time / total audio.
                add("asr_rtf_overall", round(spent / (totals * 1000), 3) if totals else None, "x", MEASURED,
                    "all segments, wall time / audio seconds")
                add("asr_ms_per_call", round(spent / len(transcripts), 1), "ms", MEASURED,
                    "fixed cost dominates short segments")
                first = transcripts[0]
                add("asr_ms_first_call", first["ms"], "ms", MEASURED, "first segment")
                add("asr_transcript", first["text"], "", MEASURED, "verbatim output")
            except VoiceError as exc:
                add("asr_ms_first_call", None, "ms", NOT_MEASURED, str(exc).split("؛")[0][:200])
            finally:
                audio.close()

    report = {
        "created_at": datetime.now().astimezone().isoformat(),
        "mode": "local_only",
        "device": {
            "input": args.device if args.device is not None else "system default",
            "sample_rate_hz": args.sample_rate,
            "block_ms": args.block_ms,
            "block_bytes": block_bytes,
            "status_flags": sorted(str(item) for item in stream_info.get("status_flags", set())),
        },
        "metrics": rows,
        "transcripts": transcripts,
        "warning": (
            "Live microphone audio was not written to disk unless --keep-wav was given. "
            "These numbers are MEASURED on this machine for this run only; they are not "
            "a claim about ESP32-S3 hardware or about Persian ASR quality in general."
        ),
    }

    width = max((len(row["metric"]) for row in rows), default=8)
    print()
    print(f"{'metric':<{width}}  {'value':>12}  {'unit':<7}  status        note")
    for row in rows:
        value = "" if row["value"] is None else str(row["value"])
        if len(value) > 12:
            value = value[:11] + "…"
        print(
            f"{row['metric']:<{width}}  {value:>12}  {row['unit']:<7}  "
            f"{row['status']:<13} {row['note']}"
        )
    for item in transcripts:
        print(f"\n[segment {item['segment']}] {item['text']}")

    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nSaved report: {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
