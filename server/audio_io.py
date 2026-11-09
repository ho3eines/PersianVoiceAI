"""Audio device selection, validation and stream recovery for the PC path.

The old runtime opened ``sd.RawStream()`` on the system defaults with a fixed
16 kHz / 320-frame block and no capability check at all.  On a real laptop that
fails in ways the user cannot diagnose: a microphone that only supports 44.1 kHz,
a headset whose input and output live on different host APIs, or a USB device
unplugged mid-conversation (which used to spin forever emitting an "overflow"
error).

This module adds:

* explicit input/output selection by name substring or index
  (``PVAI_AUDIO_INPUT_DEVICE`` / ``PVAI_AUDIO_OUTPUT_DEVICE``),
* validation with ``sounddevice.check_input_settings`` /
  ``check_output_settings`` and an actionable Persian error that lists devices,
* a stall watchdog (no callback for ``PVAI_AUDIO_STALL_S``, measured from the
  last frame *or* from the moment the stream opened) and a bounded reopen
  policy (``PVAI_AUDIO_REOPEN_ATTEMPTS``) that only forgives a reopen once the
  device has proven it is alive by delivering real frames.

Every timestamp here comes from :func:`time.monotonic`, the same clock
``DuplexAudio`` writes; ``time.perf_counter`` has an undefined origin and a
different one on Windows, so mixing the two made the watchdog fire on healthy
streams (or never fire at all).

Nothing here imports ``sounddevice``: the module is injected by the caller so
tests can use a fake and the import stays lazy on machines without PortAudio.
"""

from __future__ import annotations

import queue
import time
from dataclasses import dataclass
from typing import Any, Callable

from .voice import VoiceError

FRAME_BYTES_PER_SAMPLE = 2  # PCM16


@dataclass(frozen=True, slots=True)
class StreamPlan:
    """Resolved duplex stream parameters (already validated)."""

    samplerate: int
    blocksize: int
    input_device: int | None
    output_device: int | None
    input_name: str
    output_name: str

    def parameters(self, callback: Callable[..., Any]) -> dict[str, Any]:
        params: dict[str, Any] = {
            "samplerate": self.samplerate,
            "blocksize": self.blocksize,
            "channels": 1,
            "dtype": "int16",
            "callback": callback,
        }
        if self.input_device is not None or self.output_device is not None:
            params["device"] = (self.input_device, self.output_device)
        return params


class AudioStalled(RuntimeError):
    """No audio callback arrived within the configured watchdog period."""


def _query(sd: Any) -> list[dict[str, Any]]:
    try:
        return list(sd.query_devices())
    except Exception as exc:  # PortAudio missing / no devices
        raise VoiceError(
            "فهرست دستگاه‌های صوتی خوانده نشد؛ نصب PortAudio (بستهٔ sounddevice) و "
            "اتصال میکروفون/بلندگو را بررسی کنید."
        ) from exc


def describe_devices(sd: Any) -> str:
    """Human-readable device list with indices and channel counts."""
    lines = []
    for index, device in enumerate(_query(sd)):
        lines.append(
            f"[{index}] {device.get('name', '?')} "
            f"in={device.get('max_input_channels', 0)} "
            f"out={device.get('max_output_channels', 0)} "
            f"rate={int(device.get('default_samplerate', 0))}"
        )
    return "\n".join(lines) if lines else "(هیچ دستگاه صوتی یافت نشد)"


def resolve_device(sd: Any, spec: str | None, *, kind: str) -> int | None:
    """Resolve a device name substring or index; ``None`` keeps the default."""
    if spec is None or str(spec).strip() == "":
        return None
    text = str(spec).strip()
    devices = _query(sd)
    if text.lstrip("-").isdigit():
        index = int(text)
        if not 0 <= index < len(devices):
            raise VoiceError(
                f"شمارهٔ دستگاه {kind} نامعتبر است (فهرست با `--list-devices`)."
            )
        _require_channels(devices[index], kind, index)
        return index
    wanted = text.lower()
    matches = [
        (index, device)
        for index, device in enumerate(devices)
        if wanted in str(device.get("name", "")).lower()
    ]
    usable = [
        (index, device) for index, device in matches if _channel_key(device, kind) > 0
    ]
    if not usable:
        raise VoiceError(
            f"دستگاه {kind} با نام «{text}» پیدا نشد یا کانال لازم را ندارد.\n"
            f"{describe_devices(sd)}"
        )
    if len(usable) > 1:
        names = ", ".join(f"[{index}] {device.get('name')}" for index, device in usable)
        raise VoiceError(
            f"نام دستگاه {kind} مبهم است ({names})؛ از شمارهٔ دقیق استفاده کنید."
        )
    return usable[0][0]


def _channel_key(device: dict[str, Any], kind: str) -> int:
    key = "max_input_channels" if kind == "ورودی" else "max_output_channels"
    try:
        return int(device.get(key, 0))
    except (TypeError, ValueError):
        return 0


def _require_channels(device: dict[str, Any], kind: str, index: int) -> None:
    if _channel_key(device, kind) <= 0:
        raise VoiceError(
            f"دستگاه [{index}] «{device.get('name', '?')}» کانال {kind} ندارد."
        )


def plan_stream(sd: Any, settings: Any) -> StreamPlan:
    """Resolve and validate the duplex stream described by ``settings``."""
    devices = _query(sd)
    if not devices:
        raise VoiceError("هیچ دستگاه صوتی به سیستم متصل نیست.")
    input_index = resolve_device(sd, settings.audio_input_device, kind="ورودی")
    output_index = resolve_device(sd, settings.audio_output_device, kind="خروجی")
    rate = int(settings.audio_sample_rate)
    blocksize = int(settings.block_frames())

    def _check(kind: str, index: int | None) -> None:
        checker = getattr(
            sd, "check_input_settings" if kind == "ورودی" else "check_output_settings", None
        )
        if checker is None:  # fake device module in tests
            return
        options: dict[str, Any] = {"samplerate": rate, "channels": 1, "dtype": "int16"}
        if index is not None:
            options["device"] = index
        try:
            checker(**options)
        except Exception as exc:
            name = devices[index]["name"] if index is not None else "دستگاه پیش‌فرض"
            raise VoiceError(
                f"دستگاه {kind} «{name}» نرخ {rate}Hz تک‌کاناله را نمی‌پذیرد؛ "
                f"PVAI_AUDIO_SAMPLE_RATE یا دستگاه دیگری را انتخاب کنید. "
                f"جزئیات: {exc}"
            ) from exc

    _check("ورودی", input_index)
    _check("خروجی", output_index)
    return StreamPlan(
        samplerate=rate,
        blocksize=blocksize,
        input_device=input_index,
        output_device=output_index,
        input_name=(
            devices[input_index]["name"]
            if input_index is not None
            else str(getattr(getattr(sd, "default", None), "device", (None, None))[0])
        ),
        output_name=(
            devices[output_index]["name"]
            if output_index is not None
            else str(getattr(getattr(sd, "default", None), "device", (None, None))[1])
        ),
    )


def drain_capture(duplex: Any) -> int:
    """Drop buffered capture frames (used after overflow or a stall)."""
    dropped = 0
    while True:
        try:
            duplex.capture.get_nowait()
            dropped += 1
        except queue.Empty:
            return dropped


def _stall_baseline(duplex: Any) -> float | None:
    """Instant the stall watchdog must measure against, on one clock.

    ``stall_baseline()`` is preferred (it falls back to the moment the stream
    opened, so a device that opens and never delivers a single callback is still
    detected).  A plain ``last_frame_at`` object keeps working, which is what the
    older tests and fakes provide.
    """
    provider = getattr(duplex, "stall_baseline", None)
    if callable(provider):
        return provider()
    return getattr(duplex, "last_frame_at", None)


def reset_stream_state(duplex: Any, pipeline: Any = None, aec: Any = None) -> None:
    """Forget everything that belonged to the stream that just died.

    A reopen is not a resume: the microphone may have changed device, the
    detector may be holding half an utterance from before the gap, and the AEC
    filter holds an echo path that no longer exists.  Leaving them alone is how
    a stale ``Utterance`` (and a wrong AEC delay) survived a device unplug.
    """
    reset = getattr(duplex, "reset_state", None)
    if callable(reset):
        reset()
    else:  # plain fake: at least never carry buffered frames across the gap
        drain_capture(duplex)
    if aec is not None:
        reset_aec = getattr(aec, "reset", None)
        if callable(reset_aec):
            reset_aec()
    if pipeline is not None:
        reset_pipeline = getattr(pipeline, "reset", None)
        if callable(reset_pipeline):
            reset_pipeline()
            return
        detector = getattr(pipeline, "detector", None)
        reset_detector = getattr(detector, "reset", None)
        if callable(reset_detector):
            reset_detector()


SYSTEM_METRICS_INTERVAL_S = 1.0
"""How often the live coordinator publishes system/queue gauges."""


async def pump(
    duplex: Any,
    pipeline: Any,
    settings: Any,
    *,
    aec: Any = None,
    metrics: Any = None,
    sleep: Callable[[float], Any] | None = None,
    sampler: Any = None,
    metrics_interval_s: float = SYSTEM_METRICS_INTERVAL_S,
) -> None:
    """Feed captured frames through AEC into the pipeline (single owner).

    Besides the audio path, this is where the *system* metrics required by the
    audit are published while the assistant actually runs: callback jitter
    (measured by the audio callback itself), capture/speech backlog, and
    process CPU/RAM.  Before this, ``audio_callback_jitter_ms``,
    ``queue_depth``, ``cpu_usage_percent`` and ``ram_usage_mb`` existed only as
    declared names, so a live session could not answer "is the machine
    saturated?" without running the offline benchmark.
    """
    import asyncio

    from .resources import create_sampler, sample_process_usage

    pause = sleep or asyncio.sleep
    stall_limit = float(settings.audio_stall_s)
    usage_sampler = sampler
    if metrics is not None and usage_sampler is None:
        usage_sampler = create_sampler()
    next_system_sample = 0.0
    callbacks_seen = 0
    watch_started = time.monotonic()
    while True:
        now = time.monotonic()
        # The watchdog must always have an origin.  If the stream was opened
        # through a path that never stamped one (``DuplexAudio()`` used
        # directly, a fake, or an open that raced this loop), fall back to the
        # moment the watchdog started instead of silently disabling the check —
        # a device that opens and never delivers a callback produced no
        # ``last_frame_at`` at all, so the old ``if last is not None`` guard
        # meant "dead microphone" was indistinguishable from "healthy".
        last = _stall_baseline(duplex)
        if last is None:
            last = watch_started
        if stall_limit > 0 and now - last > stall_limit:
            raise AudioStalled(
                f"جریان صوتی بیش از {stall_limit:g} ثانیه فریم نداد؛ دستگاه قطع شده است."
            )
        if getattr(duplex, "overflow", None) is not None and duplex.overflow.is_set():
            if metrics is not None:
                metrics.increment("audio_overflow_count")
            pipeline.overflow()
            if aec is not None:
                aec.reset()
            drain_capture(duplex)
            duplex.overflow.clear()
        now_monotonic = time.monotonic()
        if metrics is not None and now_monotonic >= next_system_sample:
            next_system_sample = now_monotonic + max(0.05, float(metrics_interval_s))
            stats = getattr(duplex, "stats", None)
            if callable(stats):
                try:
                    snapshot = stats()
                    metrics.observe(
                        "audio_callback_jitter_ms", float(snapshot["jitter_ms"])
                    )
                    total = int(snapshot.get("callbacks") or 0)
                    if total > callbacks_seen:
                        metrics.increment("audio_callback_count", total - callbacks_seen)
                        callbacks_seen = total
                except (KeyError, TypeError, ValueError):
                    pass
            backlog = duplex.capture.qsize() if hasattr(duplex, "capture") else 0
            speech = getattr(pipeline, "speech", None)
            if speech is not None and hasattr(speech, "qsize"):
                backlog += speech.qsize()
            metrics.set("queue_depth", backlog)
            usage = sample_process_usage(usage_sampler)
            if usage.cpu_percent is not None:
                metrics.set("cpu_usage_percent", usage.cpu_percent)
            if usage.ram_mb is not None:
                metrics.set("ram_usage_mb", usage.ram_mb)
        try:
            microphone, reference = duplex.capture.get_nowait()
        except queue.Empty:
            await pause(0.005)
            continue
        if aec is not None:
            started = time.perf_counter()
            cleaned = aec.process(microphone, reference)
            if metrics is not None:
                metrics.observe("aec_process_ms", (time.perf_counter() - started) * 1000)
        else:
            cleaned = microphone
        pipeline.feed(cleaned)  # AEC always precedes VAD, ASR and barge-in.
        await pause(0)


PROGRESS_FRAMES = 50
"""Frames a reopen must observe before it counts as "the device is healthy".

One second of 20 ms blocks.  Anything less means the stream opened and then went
quiet again (muted microphone, exclusive-mode device, Bluetooth headset that
hands audio to another endpoint), which must consume the reopen budget instead
of resetting it — otherwise the loop reopens forever and never tells the user
why nothing is being heard.
"""


def _progress_frames(settings: Any) -> int:
    """Minimum delivered frames before an attempt counts as progress."""
    configured = getattr(settings, "audio_reopen_stable_frames", None)
    if configured is None:
        return PROGRESS_FRAMES
    try:
        return max(1, int(configured))
    except (TypeError, ValueError):
        return PROGRESS_FRAMES


def _callbacks(duplex: Any) -> int:
    try:
        return int(getattr(duplex, "callbacks", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _note_open(duplex: Any) -> None:
    marker = getattr(duplex, "opened", None)
    if callable(marker):
        marker()


async def stream_forever(
    sd: Any,
    duplex: Any,
    settings: Any,
    pipeline: Any,
    *,
    aec: Any = None,
    metrics: Any = None,
    on_open: Callable[[StreamPlan], None] | None = None,
    reopen_delay: Callable[[int], float] | None = None,
) -> None:
    """Open the duplex stream and keep it alive; reopen on stall/PortAudio error.

    The reopen budget (``PVAI_AUDIO_REOPEN_ATTEMPTS``) is only refunded once an
    attempt has delivered ``PROGRESS_FRAMES`` real frames.  Resetting it as soon
    as ``RawStream.__enter__`` returned (the old behaviour) meant a device that
    opens and produces nothing — mute, exclusive mode, a Bluetooth headset that
    moved the endpoint — never exhausted the budget: it reopened every few
    hundred milliseconds for as long as the process lived, and the user saw no
    error at all.

    Every reopen also calls :func:`reset_stream_state`: the detector may hold
    half an utterance recorded before the gap and the AEC filter an echo path
    that no longer exists, so resuming as if nothing happened both fabricated a
    transcript across the gap and fed the filter a wrong reference.
    """
    plan = plan_stream(sd, settings)
    portaudio_error = getattr(sd, "PortAudioError", None)
    limit = int(getattr(settings, "audio_reopen_attempts", 0))
    minimum = _progress_frames(settings)
    delay_for = reopen_delay or (lambda attempt: min(2.0, 0.25 * attempt))
    attempts = 0
    while True:
        frames_before = _callbacks(duplex)
        try:
            with sd.RawStream(**plan.parameters(duplex.callback)):
                _note_open(duplex)
                if on_open is not None:
                    on_open(plan)
                await pump(duplex, pipeline, settings, aec=aec, metrics=metrics)
            # ``pump`` only returns on cancellation; treat it as a clean stop.
            return
        except AudioStalled as exc:
            # A device that opens and never delivers a frame is the case the
            # stall watchdog exists for; it must be as fatal as a stalling one.
            attempts = _retry(frames_before, duplex, minimum, attempts)
            if metrics is not None:
                metrics.increment("audio_reopen_count")
            if attempts > limit:
                raise VoiceError(
                    "دستگاه صوتی وصل است ولی صدا نمی‌دهد "
                    f"(بعد از {limit} بار تلاش برای بازگشایی). ورودی صوتی را بررسی کنید: "
                    "میکروفون بی‌صدا (mute)، انحصاری‌بودن دستگاه (exclusive mode) یا هدستی "
                    "که کانال ورودی را به برنامهٔ دیگری داده است. اگر دستگاه درست است، "
                    "PVAI_AUDIO_INPUT_DEVICE را تنظیم کنید."
                ) from exc
            await _recover(duplex, pipeline, settings, aec, metrics, attempts, delay_for)
            continue
        except Exception as exc:
            if portaudio_error is None or not isinstance(exc, portaudio_error):
                raise
            attempts = _retry(frames_before, duplex, minimum, attempts)
            if metrics is not None:
                metrics.increment("audio_reopen_count")
            if attempts > limit:
                raise VoiceError(
                    "دستگاه صوتی قطع شد یا در دسترس نیست؛ اتصال و دستگاه پیش‌فرض را بررسی کنید."
                ) from exc
            await _recover(duplex, pipeline, settings, aec, metrics, attempts, delay_for)
            continue


def _retry(frames_before: int, duplex: Any, minimum: int, attempts: int) -> int:
    """Next attempt number: a stream that delivered real frames starts over.

    The distinction matters because the two failures look identical from the
    outside ("no audio now") but need opposite responses: a device that just
    died after working needs one reopen, while a device that has never delivered
    a single frame will not heal by being reopened.
    """
    if _callbacks(duplex) - frames_before >= minimum:
        return 1
    return attempts + 1


async def _recover(
    duplex: Any,
    pipeline: Any,
    settings: Any,
    aec: Any,
    metrics: Any,
    attempt: int,
    delay_for: Callable[[int], float],
) -> None:
    """Tear down everything tied to the dead stream and back off before retrying."""
    import asyncio

    if metrics is not None:
        metrics.set("audio_reopen_attempts", attempt)
    drain_capture(duplex)
    reset_stream_state(duplex, pipeline, aec)
    await asyncio.sleep(delay_for(attempt))
