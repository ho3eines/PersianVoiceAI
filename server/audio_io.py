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
* a stall watchdog (no callback for ``PVAI_AUDIO_STALL_S``) and a bounded
  reopen policy (``PVAI_AUDIO_REOPEN_ATTEMPTS``).

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


async def pump(
    duplex: Any,
    pipeline: Any,
    settings: Any,
    *,
    aec: Any = None,
    metrics: Any = None,
    sleep: Callable[[float], Any] | None = None,
) -> None:
    """Feed captured frames through AEC into the pipeline (single owner)."""
    import asyncio

    pause = sleep or asyncio.sleep
    blocks = max(1, int(settings.audio_block_ms))
    stall_limit = float(settings.audio_stall_s)
    while True:
        now = time.monotonic()
        last = getattr(duplex, "last_frame_at", None)
        if last is not None and stall_limit > 0 and now - last > stall_limit:
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


async def stream_forever(
    sd: Any,
    duplex: Any,
    settings: Any,
    pipeline: Any,
    *,
    aec: Any = None,
    metrics: Any = None,
    on_open: Callable[[StreamPlan], None] | None = None,
) -> None:
    """Open the duplex stream and keep it alive; reopen on stall/PortAudio error."""
    import asyncio

    plan = plan_stream(sd, settings)
    portaudio_error = getattr(sd, "PortAudioError", None)
    attempts = 0
    while True:
        try:
            with sd.RawStream(**plan.parameters(duplex.callback)):
                if on_open is not None:
                    on_open(plan)
                attempts = 0
                await pump(duplex, pipeline, settings, aec=aec, metrics=metrics)
        except AudioStalled as exc:
            attempts += 1
            if attempts > int(settings.audio_reopen_attempts):
                raise VoiceError(str(exc)) from exc
            if metrics is not None:
                metrics.increment("audio_reopen_count")
            drain_capture(duplex)
            await asyncio.sleep(min(2.0, 0.25 * attempts))
        except Exception as exc:
            if portaudio_error is not None and isinstance(exc, portaudio_error):
                attempts += 1
                if attempts > int(settings.audio_reopen_attempts):
                    raise VoiceError(
                        "دستگاه صوتی قطع شد یا در دسترس نیست؛ اتصال و دستگاه پیش‌فرض را بررسی کنید."
                    ) from exc
                if metrics is not None:
                    metrics.increment("audio_reopen_count")
                drain_capture(duplex)
                await asyncio.sleep(min(2.0, 0.25 * attempts))
                continue
            raise
