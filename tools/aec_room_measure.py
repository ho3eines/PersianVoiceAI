"""Measure SpeexDSP echo cancellation on the **real acoustic path**.

``tests/test_aec.py`` drives ``SpeexAEC`` with synthetic frames: it proves the
filter maths, not that the machine's microphone actually hears the speaker and
that the echo is removed from that signal.  This tool closes that gap for the
PC: it plays a stimulus through the default output, captures the *same* stream
from the default input (one duplex ``RawStream``, exactly like
``server/audio_io.py``), pairs every captured block with the PCM that was
actually written to ``outdata`` — the project's AEC reference contract — and
then reports, with ``MEASURED`` labels:

* echo-path SNR and the acoustic delay of the room/device (cross-correlation),
* ERLE (echo return loss enhancement) computed only over frames that actually
  carry echo, so the pauses of a sentence cannot dilute it,
* convergence over time, and the CPU cost of one ``process()`` call,
* a ``PVAI_AEC_DELAY_MS`` sweep replayed through the *same* recording,
* a ``PVAI_AEC_FILTER_MS`` sweep at the best delay,
* how many VAD onsets the raw microphone echo produces versus the AEC output,
* a real acoustic double-talk proxy: the near-end stimulus is emitted by the
  same speaker but is deliberately **not** part of the AEC reference, so the
  filter must treat it as near-end speech and keep it.

The primary stimulus is the assistant's own **Piper speech** (``--stimulus
speech``).  That is not cosmetic: Windows microphone processing adapts to
stationary noise within seconds and removes it, which makes every ERLE number
taken with a steady noise meaningless.  ``--stimulus noise`` exists for
comparison and ``--noise-check`` adds the stationary cross-check.

Nothing is written to disk except the JSON report (``--json-out``); captured
audio is kept in memory and discarded.  Fully offline, no model downloads.

The default input/output are the system defaults (the same devices the live
loop uses).  On a laptop that means the built-in microphone and speakers a few
centimetres apart: a real echo path, but not a living room with a distant
microphone.  Report the device names with the numbers.

Usage (Windows PowerShell):

    .\\.venv\\Scripts\\python.exe tools\\aec_room_measure.py `
        --json-out benchmarks\\results\\aec-room-20261113.json

The machine will make noise for about a minute.  ``--list-devices`` shows the
pairs the tool can use; on a Windows laptop the 16 kHz contract usually leaves
only MME and DirectSound (WASAPI/WDM-KS often accept 48 kHz only).
"""

from __future__ import annotations

import argparse
import json
import math
import queue
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MEASURED = "MEASURED"
NOT_MEASURED = "NOT MEASURED"

RATE = 16000
FRAME_SAMPLES = 320  # 20 ms
FRAME_BYTES = FRAME_SAMPLES * 2


# --------------------------------------------------------------------- helpers
def _band_rms(samples: Any, low: float, high: float) -> float:
    """RMS of ``samples`` inside ``[low, high]`` Hz (zero-phase band-pass)."""
    import numpy as np
    from scipy.signal import butter, sosfiltfilt

    nyquist = RATE / 2.0
    high = min(high, nyquist * 0.99)
    if not 0 < low < high:
        return 0.0
    sos = butter(4, [low / nyquist, high / nyquist], btype="band", output="sos")
    filtered = sosfiltfilt(sos, samples)
    return float(np.sqrt(np.mean(filtered**2) + 1e-20))


def _line_amplitude(samples: Any, frequency: float) -> float:
    """Amplitude of one spectral line (Goertzel), immune to wideband noise."""
    n = len(samples)
    if n < 8:
        return 0.0
    k = int(0.5 + n * frequency / RATE)
    omega = 2.0 * math.pi * k / n
    coefficient = 2.0 * math.cos(omega)
    s1 = s2 = 0.0
    for value in samples:
        s0 = value + coefficient * s1 - s2
        s2 = s1
        s1 = s0
    power = max(0.0, s1 * s1 + s2 * s2 - coefficient * s1 * s2)
    return math.sqrt(power) / n * 2.0


def _db(ratio: float) -> float:
    return round(20.0 * math.log10(max(ratio, 1e-12)), 2)


def _band_signal(samples: Any, low: float, high: float) -> Any:
    """Zero-phase band-pass of ``samples`` (one filter pass, reusable)."""
    import numpy as np
    from scipy.signal import butter, sosfiltfilt

    nyquist = RATE / 2.0
    high = min(high, nyquist * 0.99)
    if not 0 < low < high:
        return np.zeros_like(samples)
    sos = butter(4, [low / nyquist, high / nyquist], btype="band", output="sos")
    return sosfiltfilt(sos, samples)


def _active_mask(echo_band: Any, *, frame: int = FRAME_SAMPLES) -> tuple[Any, float]:
    """Frames that actually carry echo, and the threshold used to decide.

    A speech run contains pauses; measuring ERLE over the whole window divides
    echo-plus-noise by noise, which pulls the number towards 0 dB and hides the
    real cancellation.  Only frames whose echo-band level exceeds twice the
    quietest 20 % of frames (the pre-playback room floor) count.
    """
    import numpy as np

    count = len(echo_band) // frame
    if count == 0:
        return np.zeros(len(echo_band), dtype=bool), 0.0
    rms = np.array(
        [float(np.sqrt(np.mean(echo_band[i * frame : (i + 1) * frame] ** 2) + 1e-20)) for i in range(count)]
    )
    floor = float(np.sort(rms)[max(0, int(round(0.2 * count)) - 1)])
    threshold = max(2.0 * floor, 1e-4)
    mask = np.zeros(count * frame, dtype=bool)
    for index in np.nonzero(rms > threshold)[0]:
        mask[index * frame : (index + 1) * frame] = True
    return mask, threshold


def _erle(mic: Any, cancelled: Any, low: float, high: float) -> float:
    """Echo return loss enhancement in dB over one band."""
    before = _band_rms(mic, low, high)
    after = _band_rms(cancelled, low, high)
    if before <= 0.0:
        return 0.0
    return round(20.0 * math.log10(before / max(after, 1e-12)), 2)


def _acoustic_delay_ms(
    mic: Any, reference: Any, *, start_s: float = 0.0, max_ms: float = 600.0
) -> tuple[int | None, float]:
    """Lag of the microphone echo behind the played reference (cross-correlation).

    Returns ``(lag_ms, prominence)``.  Prominence is peak correlation divided by
    the mean absolute correlation over the searched lags; a periodic stimulus
    (a steady tone) produces several comparable peaks and a low prominence, so
    the caller can tell a trustworthy estimate from an ambiguous one.  The
    authoritative calibration is the ``PVAI_AEC_DELAY_MS`` sweep, not this.
    """
    import numpy as np
    from scipy.signal import correlate

    if len(mic) < RATE or float(np.std(reference)) < 1e-5:
        return None, 0.0
    window = slice(int(start_s * RATE), len(mic))
    a = reference[window]
    b = mic[window]
    if float(np.std(a)) < 1e-5 or float(np.std(b)) < 1e-5:
        return None, 0.0
    # FFT correlation: the direct O(n²) form takes seconds on a 12 s recording.
    correlation = correlate(b, a, mode="full", method="fft")
    lags = np.arange(-len(a) + 1, len(a))
    limit = int(max_ms / 1000.0 * RATE)
    mask = (lags >= 0) & (lags <= limit)
    if not mask.any():
        return None, 0.0
    searched = correlation[mask]
    peak_index = int(np.argmax(searched))
    mean_absolute = float(np.mean(np.abs(searched))) + 1e-12
    prominence = round(float(searched[peak_index]) / mean_absolute, 2)
    lag_ms = int(round(float(lags[mask][peak_index]) / RATE * 1000.0))
    return lag_ms, prominence


def _frames(pcm: bytes) -> list[bytes]:
    return [pcm[i : i + FRAME_BYTES] for i in range(0, len(pcm) - FRAME_BYTES + 1, FRAME_BYTES)]


# ------------------------------------------------------------------- recording
class Recording:
    """One duplex run: the microphone and the reference the AEC should model."""

    def __init__(self, mic: bytes, reference: bytes, stats: dict[str, Any], overflow: bool) -> None:
        self.mic = mic
        self.reference = reference
        self.stats = stats
        self.overflow = overflow
        self.samples = len(mic) // 2

    def arrays(self) -> tuple[Any, Any]:
        import numpy as np

        mic = np.frombuffer(self.mic, dtype=np.int16).astype("float32") / 32768.0
        reference = np.frombuffer(self.reference, dtype=np.int16).astype("float32") / 32768.0
        return mic, reference


def record_duplex(stimulus: bytes, seconds: float, devices: tuple[Any, Any] = (None, None)) -> Recording:
    """Play ``stimulus`` through the production duplex path and record both sides.

    Uses ``server.aec.DuplexAudio`` itself, so the captured reference is exactly
    the frame that was written to ``outdata`` — not a reconstruction.
    """
    import sounddevice as sd

    from server.aec import DuplexAudio

    duplex = DuplexAudio()
    cancel = threading.Event()
    pairs: list[tuple[bytes, bytes]] = []

    with sd.RawStream(
        samplerate=RATE,
        channels=1,
        dtype="int16",
        blocksize=FRAME_SAMPLES,
        device=devices,
        callback=duplex.callback,
    ):
        duplex.opened()

        def writer() -> None:
            for index in range(0, len(stimulus), FRAME_BYTES):
                frame = stimulus[index : index + FRAME_BYTES].ljust(FRAME_BYTES, b"\0")
                try:
                    duplex.render.put((frame, cancel, threading.Event()), timeout=0.5)
                except queue.Full:
                    break

        threading.Thread(target=writer, name="aec-stimulus", daemon=True).start()
        deadline = time.monotonic() + seconds + 0.4
        while time.monotonic() < deadline:
            try:
                pairs.append(duplex.capture.get(timeout=0.2))
            except queue.Empty:
                continue

    return Recording(
        b"".join(mic for mic, _ in pairs),
        b"".join(reference for _, reference in pairs),
        duplex.stats(),
        duplex.overflow.is_set(),
    )


def record_mixed(
    reference_pcm: bytes,
    near_pcm: bytes,
    seconds: float,
    devices: tuple[Any, Any] = (None, None),
) -> Recording:
    """Play ``reference + near`` but tell the AEC only about ``reference``.

    This is the acoustic double-talk proxy: the near-end signal leaves the same
    speaker (so it is a real acoustic path) while being absent from the filter's
    reference, which is what a person talking over the assistant looks like to
    the canceller.  It is not a human voice at a real distance and must be
    reported as a proxy.
    """
    import sounddevice as sd

    from server.aec import DuplexAudio

    reference_frames = _frames(reference_pcm)
    near_frames = _frames(near_pcm)
    total = max(len(reference_frames), len(near_frames))
    duplex = DuplexAudio()
    pairs: list[tuple[bytes, bytes]] = []
    index = {"next": 0}

    def callback(indata: Any, outdata: Any, frames: int, _time: Any, status: Any) -> None:
        position = index["next"]
        index["next"] = position + 1
        if position == 0:
            duplex.opened()
        duplex._measure(frames)  # diagnostics only; the callback contract is unchanged
        duplex.last_frames = int(frames)
        reference = reference_frames[position] if position < len(reference_frames) else bytes(FRAME_BYTES)
        near = near_frames[position] if position < len(near_frames) else bytes(FRAME_BYTES)
        mixed = bytearray(FRAME_BYTES)
        for offset in range(0, FRAME_BYTES, 2):
            value = int.from_bytes(reference[offset : offset + 2], "little", signed=True) + int.from_bytes(
                near[offset : offset + 2], "little", signed=True
            )
            value = max(-32768, min(32767, value))
            mixed[offset : offset + 2] = value.to_bytes(2, "little", signed=True)
        outdata[:] = bytes(mixed)
        if status:
            duplex.overflow.set()
        try:
            duplex.capture.put_nowait((bytes(indata), reference))
        except queue.Full:
            duplex.overflow.set()

    with sd.RawStream(
        samplerate=RATE,
        channels=1,
        dtype="int16",
        blocksize=FRAME_SAMPLES,
        device=devices,
        callback=callback,
    ):
        deadline = time.monotonic() + seconds + 0.4
        while time.monotonic() < deadline and index["next"] < total + 5:
            try:
                pairs.append(duplex.capture.get(timeout=0.2))
            except queue.Empty:
                continue

    return Recording(
        b"".join(mic for mic, _ in pairs),
        b"".join(reference for _, reference in pairs),
        duplex.stats(),
        duplex.overflow.is_set(),
    )


# --------------------------------------------------------------------- stimuli
def speech_band_noise(seconds: float, amplitude: float, seed: int = 7) -> bytes:
    """Deterministic 300–3400 Hz noise: broadband enough to adapt the filter."""
    import numpy as np
    from scipy.signal import butter, sosfiltfilt

    rng = np.random.default_rng(seed)
    count = int(RATE * seconds)
    nyquist = RATE / 2.0
    sos = butter(4, [300 / nyquist, 3400 / nyquist], btype="band", output="sos")
    shaped = sosfiltfilt(sos, rng.standard_normal(count))
    shaped = shaped / (float(np.max(np.abs(shaped))) + 1e-9) * amplitude
    return (shaped * 32767).astype("<i2").tobytes()


def tone(seconds: float, frequency: float, amplitude: float) -> bytes:
    import numpy as np

    count = int(RATE * seconds)
    time_axis = np.arange(count) / RATE
    return (amplitude * np.sin(2 * math.pi * frequency * time_axis) * 32767).astype("<i2").tobytes()


def silence(seconds: float) -> bytes:
    return bytes(int(RATE * seconds) * 2)


def piper_speech(settings: Any, amplitude: float) -> tuple[bytes, str] | None:
    """Synthesize realistic Persian speech with the local Piper voice, or ``None``.

    The assistant's own output is the honest stimulus for this measurement: a
    steady test noise is *not* what the room hears in normal use, and Windows
    microphone "noise suppression" adapts to steady noise within seconds (the
    echo then disappears from the capture and every ERLE number becomes
    meaningless).  Speech-like, non-stationary audio survives that processing.

    The chunk is resampled to the 16 kHz duplex contract and scaled to
    ``amplitude`` so louder runs stay comparable.
    """
    import math

    import numpy as np
    from scipy.signal import resample_poly

    try:
        from server.live_voice import load_piper_voice

        voice = load_piper_voice(settings, settings.tts_voice)
        text = (
            "سلام. امروز هوا خوب است و من در خانه هستم. "
            "لطفاً بگویید فردا چه کاری انجام دهم و چه ساعتی به تو زنگ بزنم. "
            "دمای اتاق الان بیست و یک درجه است."
        )
        pcm = bytearray()
        rate = 22050
        for chunk in voice.synthesize(text):
            rate = int(getattr(chunk, "sample_rate", 22050))
            pcm.extend(chunk.audio_int16_bytes)
        if len(pcm) < FRAME_BYTES * 10:
            return None
        samples = np.frombuffer(bytes(pcm), dtype="<i2").astype("float32")
        if rate != RATE:
            divisor = math.gcd(rate, RATE)
            samples = resample_poly(samples, RATE // divisor, rate // divisor)
        peak = float(np.max(np.abs(samples))) or 1.0
        samples = samples / peak * amplitude * 32767.0
        return np.clip(np.rint(samples), -32768, 32767).astype("<i2").tobytes(), text
    except Exception as exc:  # noqa: BLE001 - reported as NOT MEASURED, not a crash
        print(f"Piper speech stimulus unavailable: {type(exc).__name__}: {exc}")
        return None


# -------------------------------------------------------------------- analysis
def replay_erle(
    recording: Recording,
    *,
    library: str,
    filter_ms: int,
    delay_ms: int,
    low: float,
    high: float,
    analysis_start_s: float,
    analysis_end_s: float,
) -> dict[str, Any]:
    """Replay one recording through a fresh ``SpeexAEC`` and measure ERLE."""
    import numpy as np

    from server.aec import SpeexAEC

    aec = SpeexAEC(library, filter_ms=filter_ms, delay_ms=delay_ms)
    mic, _ = recording.arrays()
    frames = _frames(recording.mic)
    references = _frames(recording.reference)
    output = bytearray()
    spent_ms = 0.0
    try:
        for microphone, reference in zip(frames, references):
            started = time.perf_counter()
            output.extend(aec.process(microphone, reference))
            spent_ms += (time.perf_counter() - started) * 1000.0
    finally:
        aec.close()

    cancelled = np.frombuffer(bytes(output), dtype=np.int16).astype("float32") / 32768.0
    start = int(analysis_start_s * RATE)
    end = min(int(analysis_end_s * RATE), len(mic), len(cancelled))
    echo_band = _band_signal(mic[start:end], low, high)
    cancelled_band = _band_signal(cancelled[start:end], low, high)
    mask, threshold = _active_mask(echo_band)
    usable = len(mask)
    echo_band = echo_band[:usable]
    cancelled_band = cancelled_band[:usable]
    if mask.any():
        active_echo = echo_band[mask]
        active_cancelled = cancelled_band[mask]
        erle_band = round(
            20.0 * math.log10(
                max(float(np.sqrt(np.mean(active_echo**2))), 1e-12)
                / max(float(np.sqrt(np.mean(active_cancelled**2))), 1e-12)
            ),
            2,
        )
        total_echo = _band_signal(mic[start:end], 60.0, 7000.0)[:usable][mask]
        total_cancelled = _band_signal(cancelled[start:end], 60.0, 7000.0)[:usable][mask]
        erle_total = round(
            20.0 * math.log10(
                max(float(np.sqrt(np.mean(total_echo**2))), 1e-12)
                / max(float(np.sqrt(np.mean(total_cancelled**2))), 1e-12)
            ),
            2,
        )
    else:
        erle_band = erle_total = 0.0
    return {
        "filter_ms": filter_ms,
        "delay_ms": delay_ms,
        "erle_db": erle_band,
        "erle_all_db": _erle(mic[start:end], cancelled[start:end], low, high),
        "erle_total_db": erle_total,
        "active_frames": int(mask.sum() // FRAME_SAMPLES),
        "active_threshold": round(float(threshold), 5),
        "cpu_ms_per_frame": round(spent_ms / max(len(frames), 1), 4),
        "rtf": round(spent_ms / (len(frames) * 20.0), 4),
        "output": cancelled,
    }


def convergence(
    recording: Recording,
    cancelled: Any,
    *,
    low: float,
    high: float,
    step_s: float = 0.5,
) -> list[dict[str, float | None]]:
    """Per-window ERLE, but only where the echo is actually present.

    A pause between sentences carries room noise, not echo; reporting an ERLE
    there divides noise by noise and produces meaningless (often negative)
    values.  Such windows are reported as ``None``.
    """
    import numpy as np

    mic, _ = recording.arrays()
    mic_band = _band_signal(mic, low, high)
    cancelled_band = _band_signal(cancelled, low, high)
    _, threshold = _active_mask(mic_band)
    rows: list[dict[str, float | None]] = []
    step = int(step_s * RATE)
    for start in range(0, min(len(mic), len(cancelled)) - step, step):
        window = slice(start, start + step)
        echo = float(np.sqrt(np.mean(mic_band[window] ** 2) + 1e-20))
        value = None
        if echo > threshold:
            after = float(np.sqrt(np.mean(cancelled_band[window] ** 2) + 1e-20))
            value = round(20.0 * math.log10(max(echo, 1e-12) / max(after, 1e-12)), 2)
        rows.append({"t_s": round(start / RATE, 2), "erle_db": value, "echo_rms": round(echo, 5)})
    return rows


def vad_onsets(pcm: bytes, settings: Any) -> dict[str, int]:
    """Count VAD boundaries for one PCM stream (the detector is fed 20 ms frames)."""
    from server.vad import ONSET, REJECTED, SEGMENT, build_vad

    detector = build_vad(settings)
    counts = {"onset": 0, "segment": 0, "rejected": 0}
    for index, block in enumerate(_frames(pcm)):
        boundary, _segment = detector.feed_event(block, at=index * 0.02)
        if boundary == ONSET:
            counts["onset"] += 1
        elif boundary == SEGMENT:
            counts["segment"] += 1
        elif boundary == REJECTED:
            counts["rejected"] += 1
    return counts


# ------------------------------------------------------------------------ main
def _arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Real acoustic path AEC measurement (microphone + speaker, fully local)."
    )
    parser.add_argument(
        "--stimulus",
        choices=("speech", "noise"),
        default="speech",
        help="primary echo stimulus: local Piper speech (default) or band-limited noise",
    )
    parser.add_argument("--seconds", type=float, default=12.0, help="noise stimulus duration")
    parser.add_argument("--amplitude", type=float, default=0.30, help="stimulus peak amplitude (0,1)")
    parser.add_argument("--near-amplitude", type=float, default=0.15, help="double-talk near-end level")
    parser.add_argument("--near-frequency", type=float, default=2500.0, help="double-talk near-end line")
    parser.add_argument("--filter-ms", type=int, default=250)
    parser.add_argument("--delay-ms", type=int, default=40, help="the configured/default delay to report")
    parser.add_argument("--delays", default=",".join(str(value) for value in range(0, 501, 20)))
    parser.add_argument("--library", default=None, help="SpeexDSP library (default: PVAI_AEC_LIBRARY)")
    parser.add_argument("--input-device", default=None, help="input device index or name (default: system default)")
    parser.add_argument("--output-device", default=None, help="output device index or name (default: system default)")
    parser.add_argument("--list-devices", action="store_true", help="print the duplex device table and exit")
    parser.add_argument(
        "--noise-check",
        action="store_true",
        help="also run a stationary-noise echo (Windows mic processing usually removes it)",
    )
    parser.add_argument("--skip-doubletalk", action="store_true")
    parser.add_argument("--json-out", type=Path, default=None)
    return parser.parse_args(argv)


def _list_devices(sd: Any) -> int:
    default = sd.default.device
    print(f"{'idx':>3}  {'api':<20} {'in':>3} {'out':>3}  name")
    for index, device in enumerate(sd.query_devices()):
        api = sd.query_hostapis(device["hostapi"])["name"]
        marker = " *" if index in tuple(default) else ""
        print(
            f"{index:>3}  {api:<20} {device['max_input_channels']:>3} "
            f"{device['max_output_channels']:>3}  {device['name']}{marker}"
        )
    print("\n* = system default; pass one input and one output index to --input-device/--output-device")
    return 0


def _resolve_device(sd: Any, spec: str | None, *, kind: str) -> int | None:
    """Resolve a device index or a case-insensitive name substring."""
    if spec is None or str(spec).strip() == "":
        return None
    text = str(spec).strip()
    try:
        index = int(text)
    except ValueError:
        lowered = text.lower()
        matches = [
            number
            for number, device in enumerate(sd.query_devices())
            if lowered in str(device["name"]).lower()
            and int(device.get("max_input_channels" if kind == "input" else "max_output_channels", 0)) > 0
        ]
        if not matches:
            raise SystemExit(f"no {kind} device matches {spec!r} (use --list-devices)")
        if len(matches) > 1:
            raise SystemExit(f"{kind} device {spec!r} is ambiguous: {matches} (use an index)")
        index = matches[0]
    devices = sd.query_devices()
    if not 0 <= index < len(devices):
        raise SystemExit(f"{kind} device index {index} is out of range (use --list-devices)")
    device = devices[index]
    channels = int(device.get("max_input_channels" if kind == "input" else "max_output_channels", 0))
    if channels <= 0:
        raise SystemExit(f"device [{index}] {device['name']!r} has no {kind} channels")
    return index


def _resolve_library(settings: Any, explicit: str | None) -> str:
    from server.aec import probe_aec

    configured = explicit or getattr(settings, "aec_library", "")
    if configured and not Path(configured).is_absolute():
        configured = str((ROOT / configured).resolve())
    status = probe_aec(configured)
    if not status.get("available"):
        raise SystemExit(f"SpeexDSP unavailable: {status.get('detail')} (library={configured!r})")
    return status["library"]


def main(argv: Sequence[str] | None = None) -> int:
    args = _arguments(argv)
    if not 0 < args.amplitude <= 0.9:
        raise SystemExit("--amplitude must be in (0, 0.9]")
    if args.seconds < 4:
        raise SystemExit("--seconds must be at least 4 (the filter needs time to adapt)")

    try:
        import sounddevice as sd
    except Exception as exc:  # noqa: BLE001
        print(f"sounddevice unavailable: {exc}")
        return 2

    from server.config import Settings

    settings = Settings.from_env()
    library = _resolve_library(settings, args.library)
    if args.list_devices:
        return _list_devices(sd)
    if args.input_device is None and args.output_device is None:
        input_index, output_index = sd.default.device
    else:
        input_index = _resolve_device(sd, args.input_device, kind="input")
        output_index = _resolve_device(sd, args.output_device, kind="output")
    if input_index is None:
        input_index = sd.default.device[0]
    if output_index is None:
        output_index = sd.default.device[1]
    devices = (input_index, output_index)
    input_name = str(sd.query_devices(input_index)["name"])
    output_name = str(sd.query_devices(output_index)["name"])
    host_api = str(sd.query_hostapis(sd.query_devices(input_index)["hostapi"])["name"])
    print(f"AEC library: {library}")
    print(f"duplex: in=[{input_index}] {input_name} ({host_api})  out=[{output_index}] {output_name}")
    print(f"stimulus: {args.stimulus} @ peak {args.amplitude}; the machine will make noise")
    print()

    rows: list[dict[str, Any]] = []

    def add(metric: str, value: Any, unit: str, status: str = MEASURED, note: str = "") -> None:
        rows.append({"metric": metric, "value": value, "unit": unit, "status": status, "note": note})
        print(f"  {metric:<34} {value!s:>12} {unit:<6} {status:<13} {note}")

    report: dict[str, Any] = {
        "created_at": datetime.now().astimezone().isoformat(),
        "mode": "local_only",
        "device": {
            "input": input_name,
            "output": output_name,
            "input_index": input_index,
            "output_index": output_index,
            "sample_rate_hz": RATE,
            "block_ms": 20,
            "host_api": str(sd.query_hostapis(sd.query_devices(input_index)["hostapi"])["name"]),
        },
        "settings": {
            "filter_ms": args.filter_ms,
            "delay_ms": args.delay_ms,
            "library": library,
        },
    }

    # ---------------------------------------------------- 1. primary echo run
    print("[1/5] primary echo run …")
    primary_pcm: bytes
    primary_text = ""
    if args.stimulus == "speech":
        built = piper_speech(settings, args.amplitude)
        if built is None:
            print("falling back to the noise stimulus (Piper unavailable)")
            primary_pcm = speech_band_noise(args.seconds, args.amplitude)
        else:
            primary_pcm, primary_text = built
    else:
        primary_pcm = speech_band_noise(args.seconds, args.amplitude)
    duration = len(primary_pcm) / 2 / RATE
    stimulus = silence(1.0) + primary_pcm
    primary_run = record_duplex(stimulus, duration + 2.0, devices)
    report["echo_run"] = {
        "stimulus": args.stimulus,
        "seconds": round(duration, 2),
        "text": primary_text,
        "stats": primary_run.stats,
        "overflow": primary_run.overflow,
    }
    add("frames_captured", len(_frames(primary_run.mic)), "frames", MEASURED)
    add("frame_mismatch", primary_run.stats.get("frame_mismatch"), "count", MEASURED)
    add("callback_overflow", int(primary_run.overflow), "flag", MEASURED)
    add("max_jitter_ms", primary_run.stats.get("max_jitter_ms"), "ms", MEASURED)
    add("echo_seconds", round(duration, 2), "s", MEASURED, f"{args.stimulus} stimulus")

    mic, reference = primary_run.arrays()
    acoustic_delay, prominence = _acoustic_delay_ms(mic, reference, start_s=1.2)
    report["acoustic_delay_ms"] = acoustic_delay
    report["acoustic_delay_prominence"] = prominence
    add(
        "acoustic_delay_ms",
        acoustic_delay,
        "ms",
        MEASURED,
        f"mic echo vs played reference (prominence {prominence})",
    )
    analysis_start = min(3.5, max(1.2, duration * 0.25))
    analysis_end = duration + 0.6
    echo_window = slice(int(analysis_start * RATE), int(analysis_end * RATE))
    echo_rms = _band_rms(mic[echo_window], 300.0, 3000.0)
    room_rms = _band_rms(mic[slice(0, int(0.9 * RATE))], 300.0, 3000.0)
    echo_snr_db = _db(echo_rms / max(room_rms, 1e-9))
    report["echo_path_snr_db"] = echo_snr_db
    add("echo_path_snr_db", echo_snr_db, "dB", MEASURED, "echo band vs pre-playback room noise")
    add("echo_band_rms", round(echo_rms, 5), "rms", MEASURED, "300–3000 Hz at the microphone")

    # ----------------------------------------------- 2. PVAI_AEC_DELAY_MS sweep
    # The configured delay is only a hint: the true offset is device latency plus
    # the room, and on Windows MME it can be several hundred milliseconds — far
    # outside the filter's tail, in which case AEC cancels nothing at all.  The
    # sweep on one recording is cheaper and repeatable compared with replaying
    # the stimulus once per delay.
    print("[2/5] PVAI_AEC_DELAY_MS sweep on the same recording …")
    sweep: list[dict[str, Any]] = []
    for delay_ms in _parse_delays(args.delays):
        result = replay_erle(
            primary_run,
            library=library,
            filter_ms=args.filter_ms,
            delay_ms=delay_ms,
            low=300.0,
            high=2000.0,
            analysis_start_s=analysis_start,
            analysis_end_s=analysis_end,
        )
        sweep.append(
            {
                "delay_ms": delay_ms,
                "erle_db": result["erle_db"],
                "erle_total_db": result["erle_total_db"],
            }
        )
    report["delay_sweep"] = sweep
    best = max(sweep, key=lambda item: item["erle_db"])
    add("best_delay_ms", best["delay_ms"], "ms", MEASURED, f"ERLE {best['erle_db']} dB")
    add("configured_delay_ms", args.delay_ms, "ms", MEASURED, "PVAI_AEC_DELAY_MS default in .env")

    # ------------------------------------------- 3. default versus best delay
    print("[3/5] ERLE at the configured delay and at the calibrated one …")
    default_run = replay_erle(
        primary_run,
        library=library,
        filter_ms=args.filter_ms,
        delay_ms=args.delay_ms,
        low=300.0,
        high=2000.0,
        analysis_start_s=analysis_start,
        analysis_end_s=analysis_end,
    )
    add(
        "erle_db",
        default_run["erle_db"],
        "dB",
        MEASURED,
        f"300–2000 Hz at the configured {args.delay_ms} ms delay",
    )
    add(
        "erle_db_calibrated",
        best["erle_db"],
        "dB",
        MEASURED,
        f"300–2000 Hz at the calibrated {best['delay_ms']} ms delay",
    )
    calibrated = replay_erle(
        primary_run,
        library=library,
        filter_ms=args.filter_ms,
        delay_ms=best["delay_ms"],
        low=300.0,
        high=2000.0,
        analysis_start_s=analysis_start,
        analysis_end_s=analysis_end,
    )
    add(
        "erle_total_db",
        calibrated["erle_total_db"],
        "dB",
        MEASURED,
        "60–7000 Hz at the calibrated delay",
    )
    add("aec_cpu_ms_per_frame", calibrated["cpu_ms_per_frame"], "ms", MEASURED)
    add("aec_rtf", calibrated["rtf"], "x", MEASURED, "process() time / 20 ms of audio")
    report["erle"] = {
        "configured_delay_ms": args.delay_ms,
        "configured_erle_db": default_run["erle_db"],
        "calibrated_delay_ms": best["delay_ms"],
        "calibrated_erle_db": best["erle_db"],
        "calibrated_erle_total_db": calibrated["erle_total_db"],
        "cpu_ms_per_frame": calibrated["cpu_ms_per_frame"],
        "rtf": calibrated["rtf"],
    }
    report["convergence"] = convergence(primary_run, calibrated["output"], low=300.0, high=2000.0)
    report["erle_at_10s_db"] = next(
        (row["erle_db"] for row in report["convergence"] if row["t_s"] >= 10.0),
        None,
    )

    print("[4/5] PVAI_AEC_FILTER_MS sweep at the calibrated delay …")
    filters: list[dict[str, Any]] = []
    for filter_ms in sorted({args.filter_ms, 500, 1000}):
        result = replay_erle(
            primary_run,
            library=library,
            filter_ms=filter_ms,
            delay_ms=best["delay_ms"],
            low=300.0,
            high=2000.0,
            analysis_start_s=analysis_start,
            analysis_end_s=analysis_end,
        )
        filters.append(
            {
                "filter_ms": filter_ms,
                "erle_db": result["erle_db"],
                "cpu_ms_per_frame": result["cpu_ms_per_frame"],
                "rtf": result["rtf"],
            }
        )
    report["filter_sweep"] = filters
    best_filter = max(filters, key=lambda item: item["erle_db"])
    add(
        "best_filter_ms",
        best_filter["filter_ms"],
        "ms",
        MEASURED,
        f"ERLE {best_filter['erle_db']} dB, CPU {best_filter['cpu_ms_per_frame']} ms/frame",
    )

    # --------------------------------------------------------- VAD false trigger
    print("[5/5] VAD on the raw echo versus the calibrated AEC output …")
    import numpy as np

    raw_echo_pcm = np.frombuffer(primary_run.mic, dtype=np.int16).tobytes()
    cancelled_pcm = (
        np.clip(np.rint(calibrated["output"] * 32768.0), -32768, 32767).astype("<i2").tobytes()
    )
    vad_raw = vad_onsets(raw_echo_pcm, settings)
    vad_aec = vad_onsets(cancelled_pcm, settings)
    report["vad"] = {"raw": vad_raw, "aec": vad_aec}
    add("vad_onsets_raw_echo", vad_raw["onset"], "count", MEASURED, "no AEC: echo triggers the detector")
    add("vad_onsets_after_aec", vad_aec["onset"], "count", MEASURED, "same detector, AEC output")
    add("vad_rejected_raw", vad_raw["rejected"], "count", MEASURED)
    add("vad_rejected_after_aec", vad_aec["rejected"], "count", MEASURED)

    # ------------------------------------------- stationary-noise cross-check
    if args.noise_check:
        print("[+] stationary-noise cross-check (device processing often removes it) …")
        noise_pcm = speech_band_noise(6.0, args.amplitude)
        noise_run = record_duplex(silence(0.5) + noise_pcm, 7.5, devices)
        noise_mic, noise_ref = noise_run.arrays()
        noise_result = replay_erle(
            noise_run,
            library=library,
            filter_ms=args.filter_ms,
            delay_ms=best["delay_ms"],
            low=300.0,
            high=3000.0,
            analysis_start_s=2.0,
            analysis_end_s=6.0,
        )
        noise_echo_rms = _band_rms(noise_mic[int(3.0 * RATE) : int(6.0 * RATE)], 300.0, 3000.0)
        noise_room_rms = _band_rms(noise_mic[int(0.1 * RATE) : int(0.5 * RATE)], 300.0, 3000.0)
        noise_snr = _db(noise_echo_rms / max(noise_room_rms, 1e-9))
        add("noise_echo_snr_db", noise_snr, "dB", MEASURED, "stationary noise, same device")
        add("noise_erle_db", noise_result["erle_db"], "dB", MEASURED, "stationary noise at the calibrated delay")
        report["noise_crosscheck"] = {
            "echo_snr_db": noise_snr,
            "erle_db": noise_result["erle_db"],
            "note": (
                "Windows/Realtek microphone processing adapts to stationary noise within "
                "seconds, so this SNR is much lower than for speech and the ERLE number "
                "must not be compared with the speech one."
            ),
        }

    # ------------------------------------------------------- double-talk proxy
    if args.skip_doubletalk:
        add("doubletalk_near_retention_db", None, "dB", NOT_MEASURED, "--skip-doubletalk")
    else:
        print("[+] acoustic double-talk proxy …")
        seconds = min(8.0, max(5.0, len(primary_pcm) / 2 / RATE))
        reference = silence(0.5) + primary_pcm[: int(seconds * RATE) * 2]
        reference = reference.ljust(int((seconds + 0.5) * RATE) * 2, b"\0")
        near = silence(2.0) + tone(seconds - 4.0, args.near_frequency, args.near_amplitude) + silence(1.5)
        near = near[: len(reference)].ljust(len(reference), b"\0")
        mixed = record_mixed(reference, near, seconds + 1.5, devices)
        near_only = record_duplex(near, seconds + 1.5, devices)
        mixed_result = replay_erle(
            mixed,
            library=library,
            filter_ms=args.filter_ms,
            delay_ms=best["delay_ms"],
            low=300.0,
            high=3000.0,
            analysis_start_s=1.0,
            analysis_end_s=seconds,
        )
        mixed_mic, _ = mixed.arrays()
        near_mic, _ = near_only.arrays()
        window = slice(int(2.5 * RATE), int(seconds - 0.5 * RATE))
        near_before = _line_amplitude(mixed_mic[window], args.near_frequency)
        near_after = _line_amplitude(mixed_result["output"][window], args.near_frequency)
        retention_db = _db(near_after / max(near_before, 1e-9))
        near_reference = _line_amplitude(near_mic[window], args.near_frequency)
        add("doubletalk_near_line_before", round(near_before, 5), "amp", MEASURED, "at the microphone")
        add("doubletalk_near_line_after", round(near_after, 5), "amp", MEASURED, "AEC output")
        add("doubletalk_near_retention_db", retention_db, "dB", MEASURED, "0 dB = near-end untouched")
        add("doubletalk_echo_erle_db", mixed_result["erle_db"], "dB", MEASURED, "300–3000 Hz under double-talk")
        report["doubletalk"] = {
            "near_frequency_hz": args.near_frequency,
            "near_amplitudes": {"before": round(near_before, 5), "after": round(near_after, 5)},
            "near_retention_db": retention_db,
            "echo_erle_db": mixed_result["erle_db"],
            "near_only_reference_amp": round(near_reference, 5),
            "proxy_warning": (
                "Near-end was emitted by the same speaker without being part of the AEC "
                "reference. It exercises the canceller acoustically but is not human speech "
                "at a real distance; do not call it a human double-talk measurement."
            ),
        }

    report["metrics"] = rows
    report["warning"] = (
        "MEASURED on this machine, this room and this run only. Built-in microphone and "
        "speakers of the same laptop: a real acoustic path, not a distant-microphone "
        "living room, not ESP32-S3, and not a human double-talk test. Captured audio was "
        "not written to disk."
    )

    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nSaved report: {args.json_out}")
    return 0


def _parse_delays(spec: str) -> list[int]:
    values: list[int] = []
    for item in str(spec or "").split(","):
        item = item.strip()
        if not item:
            continue
        value = int(item)
        if value < 0 or value > 500:
            raise SystemExit("--delays entries must be between 0 and 500 ms")
        values.append(value)
    return sorted(set(values))


if __name__ == "__main__":
    raise SystemExit(main())
