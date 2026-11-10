"""Runtime configuration for the Persian Voice AI local service (llama.cpp backend).

Every setting can be overridden with an environment variable so the same
code runs on a workstation, in tests, and later on a small local server.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

logger = logging.getLogger("persian_voice_ai.config")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
"""(repository root, i.e. the directory that holds ``.env.example``)."""

DEFAULT_MODEL_PATH = str(
    Path(__file__).resolve().parents[1]
    / "models"
    / "qwen3.5-4b"
    / "Qwen3.5-4B-Q4_K_M.gguf"
)
DEFAULT_LLAMA_SERVER_URL = "http://127.0.0.1:8080"
DEFAULT_API_HOST = "127.0.0.1"
DEFAULT_API_PORT = 8000

DEFAULT_SYSTEM_PROMPT = (
    "تو «Persian Voice AI» هستی؛ یک دستیار هوش مصنوعی محلی که روی رایانه کاربر اجرا می‌شود. "
    "همیشه به فارسی روان، طبیعی و درست پاسخ بده، مگر اینکه کاربر صریحاً زبان دیگری بخواهد. "
    "پاسخ را تا حد ممکن کوتاه، دقیق و مفید بده؛ مگر اینکه کاربر توضیح بیشتری بخواهد. "
    "از تعارف اضافه و تکرار پرسش پرهیز کن. "
    "اگر پاسخ چیزی را نمی‌دانی، صادقانه بگو که نمی‌دانی. "
    "برای پرسش‌های فنی، پاسخ را گام‌به‌گام و روشن بنویس. "
    # The reply is read aloud by a TTS voice: formatting is noise to it.  The
    # pipeline cleans up after the model anyway (``persian.prepare_for_tts``),
    # but a model that never emits it produces better speech than one that does.
    "پاسخ تو با صدا خوانده می‌شود؛ پس از markdown، ستاره، عنوان، فهرست شماره‌دار، "
    "جدول، لینک و ایموجی استفاده نکن و پاسخ را در قالب متن سادهٔ روان بنویس. "
    "ارقام و نمادها را به شکل قابل‌خواندن بنویس: ساعت را «ساعت ده و سی دقیقه»، "
    "درصد را «بیست و پنج درصد» و عدد را با حروف (مثلاً «سه») بنویس."
)


#: Phrases Whisper emits on silence and noise, with no speaker behind them.
#: They are dangerous here because a non-empty ``asr_final`` starts a whole LLM
#: turn: Whisper hallucinating one sentence on a TV in the next room makes the
#: assistant answer a question nobody asked.  Matched against the *normalised*
#: transcript, so Arabic letter forms and diacritics do not hide them.
#: Comma-separated in ``PVAI_ASR_HALLUCINATION_PHRASES``; empty disables the
#: filter (not recommended).
DEFAULT_HALLUCINATION_PHRASES = ",".join(
    [
        "زیرنویس",
        "زیرنویس‌ها",
        "ممنون از تماشای شما",
        "ممنون که تماشا کردید",
        "از تماشای شما متشکرم",
        "اشتراک‌گذاری کنید",
        "لایک و سابسکرایب",
        "کانال ما را دنبال کنید",
        "این ویدیو را ببینید",
        "صفحهٔ بعد",
        "ادامه در قسمت بعد",
        "به امید دیدار",
        "خداحافظ",
        "تشکر از توجه شما",
        "ترجمهٔ فارسی",
        "سمت راست صفحه",
        "Thanks for watching",
        "Subscribe",
    ]
)


def strip_inline_comment(value: str) -> str:
    """Drop a trailing `` # comment`` from a ``.env`` value.

    ``.env.example`` documents most variables on the same line
    (``PVAI_N_THREADS=2   # 0 = auto``).  Keeping the comment as part of the
    value made every numeric parse fail, and the failure was invisible because
    the helpers fell back to their default: copying the example to ``.env``
    silently turned ``n_threads=2`` into ``0``.

    A ``#`` only starts a comment when it is the first non-space character or
    follows whitespace, and never inside quotes, so a legitimate value such as
    ``PVAI_SYSTEM_PROMPT="a # b"`` or ``#`` inside a device name survives.
    """
    quote: str | None = None
    for index, char in enumerate(value):
        if quote is not None:
            if char == quote:
                quote = None
            continue
        if char in {'"', "'"}:
            quote = char
            continue
        if char == "#" and (index == 0 or value[index - 1].isspace()):
            # Index 0 means the whole value is a comment (``KEY=  # note``).
            return value[:index].strip() if index else ""
    return value.strip()


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def dotenv_candidates(path: Path | None = None) -> list[Path]:
    """Where ``.env`` is searched for, most specific first.

    ``Path.cwd()`` alone was a trap: running ``python -m server`` from another
    directory (a scheduled task, an IDE, a script in ``tools/``) silently
    ignored the project's ``.env`` and started with defaults.  The project root
    is therefore always searched, after the current directory so an explicit
    ``cd`` into a deployment folder still wins.
    """
    if path is not None:
        return [Path(path)]
    seen: list[Path] = []
    for candidate in (Path.cwd() / ".env", PROJECT_ROOT / ".env"):
        if candidate not in seen:
            seen.append(candidate)
    return seen


DOTENV_LOADED: list[str] = []
"""Paths actually read by :func:`load_dotenv` (diagnostics and tests)."""


def load_dotenv(path: Path | None = None) -> None:
    """Load a simple ``KEY=VALUE`` .env file without extra dependencies.

    Existing environment variables always win, so explicit shell settings
    override the file.  Values may carry an inline ``#`` comment, which is
    stripped unless it is quoted (see :func:`strip_inline_comment`).

    With no explicit ``path`` both ``.env`` files are tried — the current
    directory first, then the project root — and the first existing one wins,
    so the service no longer depends on the working directory.
    """
    for env_path in dotenv_candidates(path):
        if not env_path.is_file():
            continue
        try:
            lines = env_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = _unquote(strip_inline_comment(value))
            if key and key not in os.environ:
                os.environ[key] = value
        DOTENV_LOADED.append(str(env_path))
        return


def _env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else default


def _env_choice(name: str, default: str, allowed: set[str]) -> str:
    """One of ``allowed`` (lower-cased); an unknown value keeps the default.

    A typo must not silently change a privacy-relevant behaviour, so the
    fallback is the documented default rather than the raw value.
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    return value if value in allowed else default


ENV_ISSUES: list[tuple[str, str]] = []
"""``(variable, reason)`` for every rejected or clamped environment value.

Module-level rather than per-``Settings`` because the helpers run while the
dataclass is being constructed; ``Settings.from_env`` snapshots it into
:attr:`Settings.env_issues` and then clears it, so the problems are reported
once instead of accumulating in a long-lived process.
"""


def _record(name: str, reason: str) -> None:
    ENV_ISSUES.append((name, reason))
    logger.warning("configuration ignored for %s: %s", name, reason)


def _env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else default


def _env_choice(name: str, default: str, allowed: set[str]) -> str:
    """One of ``allowed`` (lower-cased); an unknown value keeps the default."""
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in allowed:
        return value
    _record(name, f"{raw.strip()!r} is not one of {sorted(allowed)}; using {default!r}")
    return default


def _parse_number(name: str, raw: str, default: float | int, *, cast):
    """Shared numeric parsing: invalid values are rejected *loudly*."""
    try:
        return cast(raw)
    except ValueError:
        _record(name, f"{raw!r} is not a valid number; using the default {default!r}")
        return default


def _clamp(name: str, value, minimum, maximum):
    if minimum is not None and value < minimum:
        _record(name, f"{value!r} is below the minimum {minimum!r}; clamped")
        return minimum
    if maximum is not None and value > maximum:
        _record(name, f"{value!r} is above the maximum {maximum!r}; clamped")
        return maximum
    return value


def _env_int(
    name: str, default: int, *, minimum: int | None, maximum: int | None
) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    value = _parse_number(name, raw.strip(), default, cast=int)
    return _clamp(name, value, minimum, maximum)


def _env_float(
    name: str, default: float, *, minimum: float | None, maximum: float | None
) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    value = _parse_number(name, raw.strip(), default, cast=float)
    return _clamp(name, value, minimum, maximum)


_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}
_NONE_VALUES = {"none", "auto", "default"}


def _env_optional_bool(name: str, default: bool | None) -> bool | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    if normalized in _NONE_VALUES:
        return None
    _record(
        name,
        f"{raw.strip()!r} is not a boolean (true/false, 1/0, yes/no, on/off); "
        f"using the default {default!r}",
    )
    return default


VALID_TTS_VOICES = ("male", "female")

VALID_ASR_DEVICES = frozenset({"auto", "cpu", "cuda", "mps"})
"""Devices ``PVAI_ASR_DEVICE`` may name.

A typo such as ``PVAI_ASR_DEVICE=gpu`` used to reach CTranslate2 verbatim and
fail only when the model was loaded, with a message about an unknown device
that never mentioned the variable.  Restricting the value here makes the
mistake visible in ``/ready`` and in the startup log instead.
"""


LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1", ""})


def is_loopback_host(host: str | None) -> bool:
    """Whether binding this address keeps the service off the network.

    An empty host means "fastapi/uvicorn default", which is loopback.
    ``0.0.0.0`` and ``::`` are explicitly *not* loopback: they are the
    documented way to reach a LAN device and the reason REST needs a token.
    """
    name = (host or "").strip().lower().strip("[]")
    if name in LOOPBACK_HOSTS:
        return True
    return name.startswith("127.")


def parse_device_tokens(spec: str) -> dict[str, str]:
    """Parse ``PVAI_WS_TOKENS`` into ``{token: device_id}``.

    Format: ``"esp32-kitchen:tokenA,esp32-study:tokenB"``.  The split is on the
    *first* colon so a token may itself contain ``:``; ``device_id`` may not
    (it would be ambiguous to revoke).  Entries that do not match are reported
    through :data:`ENV_ISSUES` instead of being silently dropped — a malformed
    token list looks exactly like an authentication failure otherwise.
    """
    tokens: dict[str, str] = {}
    for entry in (spec or "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        if ":" not in entry:
            _record("PVAI_WS_TOKENS", f"entry {entry!r} is missing the 'device_id:token' colon")
            continue
        device_id, token = entry.split(":", 1)
        device_id, token = device_id.strip(), token.strip()
        if not device_id or not token:
            _record("PVAI_WS_TOKENS", f"entry {entry!r} has an empty device id or token")
            continue
        if token in tokens:
            _record("PVAI_WS_TOKENS", f"token for {device_id!r} is a duplicate and was ignored")
            continue
        tokens[token] = device_id
    return tokens


def _host_of(url: str) -> str:
    from urllib.parse import urlsplit

    return urlsplit(url).hostname or "127.0.0.1"


def _port_of(url: str) -> int:
    from urllib.parse import urlsplit

    return urlsplit(url).port or 8080


@dataclass(frozen=True, slots=True)
class Settings:
    """Immutable service settings.

    Defaults are chosen for the target laptop (2 physical cores / 4 threads,
    16 GB RAM, no usable CUDA). Every value can be overridden with a ``PVAI_*``
    environment variable; see ``.env.example`` and ``docs/CONFIGURATION.md``.
    """

    model_path: str = DEFAULT_MODEL_PATH
    llama_server_url: str = DEFAULT_LLAMA_SERVER_URL
    n_ctx: int = 4096
    # These three are consumed by ``llama_server_command`` / ``tools/start_llama.ps1``
    # (-c, -t, -ngl).  They are *not* read by the HTTP client: llama-server picks
    # its own defaults when it is started by hand, so the values here and the
    # flags you pass must agree or the server wins silently.
    n_threads: int = 0  # 0 = let llama-server decide (-t omitted)
    n_gpu_layers: int = 0  # 0 = CPU only; >0 offloads to a supported backend
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    temperature: float = 0.7
    max_tokens: int = 512
    request_timeout_s: float = 180.0
    api_host: str = DEFAULT_API_HOST
    api_port: int = DEFAULT_API_PORT
    ssl_certfile: str = ""
    ssl_keyfile: str = ""
    log_level: str = "INFO"

    # ---- ASR (faster-whisper / CTranslate2, local files only) ----
    asr_model_path: str = "models/whisper-small"
    asr_device: str = "cpu"
    asr_compute_type: str = "int8"
    asr_threads: int = 2  # explicit budget: never leave LLM/TTS without cores
    asr_beam_size: int = 1
    asr_vad_filter: bool = False  # Whisper VAD is not our endpointing authority
    asr_language: str = "fa"  # or "auto": let Whisper decide per segment
    asr_initial_prompt: str = ""  # biases spelling/script; empty = none
    asr_no_speech_threshold: float = 0.6  # faster-whisper's default, made explicit
    asr_log_prob_threshold: float = -1.0  # mean logprob below this = unreliable
    asr_compression_ratio_threshold: float = 2.4  # repetition loops
    asr_hallucination_phrases: str = DEFAULT_HALLUCINATION_PHRASES
    asr_max_no_speech_prob: float = 0.75  # drop a whole segment above this
    asr_partial_window_s: float = 3.0  # bounded overlapping window for partials
    asr_max_final_jobs: int = 16  # warning threshold; finals are never dropped
    asr_warmup: bool = True

    # ---- TTS (Piper, local Persian weights) ----
    tts_model_path: str = "models/piper/fa_IR.onnx"
    tts_model_female: str = ""
    tts_model_male: str = ""
    tts_voice: str = "female"

    # ---- VAD / endpointing ----
    voice_vad_threshold: float = 0.015
    voice_vad_offset_ratio: float = 0.6  # hysteresis: offset = onset * ratio
    voice_vad_onset_ms: int = 60
    voice_vad_silence_ms: int = 600
    voice_vad_pre_roll_ms: int = 200
    voice_vad_min_speech_ms: int = 160
    voice_vad_max_segment_s: float = 15.0
    voice_vad_noise_adapt: bool = True
    voice_vad_noise_multiplier: float = 2.5
    voice_barge_in_margin: float = 1.5
    voice_barge_in_ms: int = 200

    # ---- live pipeline ----
    voice_asr_interval_s: float = 0.8
    voice_speech_queue: int = 8  # bounded sentence queue (backpressure to SSE)
    voice_record_seconds: float = 5.0  # legacy --turn-based mode

    # ---- audio transport ----
    audio_input_device: str = ""  # name substring or index; empty = system default
    audio_output_device: str = ""
    audio_sample_rate: int = 16000
    audio_block_ms: int = 20
    audio_stall_s: float = 2.0
    audio_reopen_attempts: int = 3
    audio_reopen_stable_frames: int = 50
    """Frames a reopened stream must deliver before it counts as healthy.

    Without this a device that opens and never produces audio (mute, exclusive
    mode, a Bluetooth endpoint that moved) refunds the reopen budget on every
    attempt and the recovery loop never terminates.
    """

    # ---- AEC ----
    aec_enabled: bool = True
    aec_library: str = ""
    aec_filter_ms: int = 250
    aec_delay_ms: int = 40

    # ---- conversation memory (local only) ----
    history_persist: bool = True
    data_dir: str = "data"
    history_max_messages: int = 24
    history_max_stored_messages: int = 500  # RAM window; disk log stays complete
    history_token_budget: int = 1536
    history_include_interrupted: bool = False
    # Resume behaviour.  ``on`` keeps one rolling session for the local live
    # voice loop (``PVAI_VOICE_SESSION_ID`` or the last stored session), so a
    # restart continues the conversation instead of starting over.  The API
    # always resumes the explicit ``session_id`` it is given.
    history_resume: str = "on"
    voice_session_id: str = ""
    # 0 disables pruning; otherwise sessions untouched for this many days are
    # deleted at startup and by ``python -m server.sessions purge``.
    history_retention_days: int = 30

    # ---- REST authentication ----
    # Bearer token for every REST route except /health.  Empty means "no auth",
    # which is only tolerated while the API binds to loopback: config_errors()
    # refuses to start a non-loopback bind without a token.
    api_token: str = ""

    # ---- rate limiting (per client address) ----
    api_rate_limit_per_s: float = 20.0  # sustained requests/second; 0 disables
    api_rate_burst: int = 40
    ws_connect_rate_per_min: float = 10.0  # new device connections/minute
    ws_frame_rate_per_s: float = 200.0  # capture frames/second per connection

    # ---- LAN voice transport (WebSocket, ESP32-S3 readiness) ----
    ws_enabled: bool = False  # opt-in: needs device token(s) and a trusted LAN
    ws_token: str = ""  # PVAI_WS_TOKEN; empty => every connection is refused
    # One token per device: ``PVAI_WS_TOKENS="esp32-a:tokenA,esp32-b:tokenB"``.
    # When set it replaces the shared ``ws_token``, so revoking one device does
    # not require reflashing every other device.
    ws_tokens: str = ""
    ws_max_connections: int = 4  # concurrent device pipelines (each is a model)
    ws_heartbeat_s: float = 15.0  # server ping interval; 0 disables
    ws_idle_timeout_s: float = 60.0  # close a connection that sent nothing
    ws_playback_pacing: bool = True  # send playback at real-time rate, not burst
    ws_playback_credit_ms: float = 200.0  # max audio sent ahead of real time
    ws_allow_shared_tokens: bool = False  # opt-in escape hatch, documented as unsafe

    # ---- observability ----
    metrics_enabled: bool = True
    llm_enable_thinking: bool = False

    env_issues: tuple[tuple[str, str], ...] = field(default=(), compare=False)
    """``(variable, reason)`` for every ``PVAI_*`` value that was rejected or clamped.

    Populated by :meth:`from_env`.  Exposed through :meth:`config_errors` so
    ``/ready`` and the startup log show *which* variable was wrong — the old
    behaviour was to fall back to a default in silence, which made a typo (or a
    whole ``.env`` copy) look like a working configuration.
    """

    # ---------------------------------------------------------------- helpers
    def device_tokens(self) -> dict[str, str]:
        """``{token: device_id}`` for per-device authentication (may be empty)."""
        return parse_device_tokens(self.ws_tokens)

    def _device_token_errors(self) -> list[tuple[str, str]]:
        """Token-list problems, reported without mutating the global issue log."""
        errors: list[tuple[str, str]] = []
        seen: dict[str, str] = {}
        for entry in (self.ws_tokens or "").split(","):
            entry = entry.strip()
            if not entry:
                continue
            if ":" not in entry:
                errors.append(("PVAI_WS_TOKENS", f"entry {entry!r} needs 'device_id:token'"))
                continue
            device_id, token = (part.strip() for part in entry.split(":", 1))
            if not device_id or not token:
                errors.append(("PVAI_WS_TOKENS", f"entry {entry!r} has an empty part"))
            elif token in seen:
                errors.append(
                    ("PVAI_WS_TOKENS", f"duplicate token for {device_id!r} and {seen[token]!r}")
                )
            else:
                seen[token] = device_id
        return errors

    def llama_server_command(self, executable: str = "llama-server") -> list[str]:
        """The llama-server command line implied by these settings.

        Used by ``tools/start_llama.ps1`` (``python -m server.config --launch``)
        so ``PVAI_N_THREADS`` / ``PVAI_N_GPU_LAYERS`` / ``PVAI_N_CTX`` actually
        take effect instead of sitting unused in the dataclass while the README
        told the user to pass different flags by hand.
        """
        command = [
            executable,
            "-m",
            self.model_path,
            "-c",
            str(self.n_ctx),
            "--jinja",
            "--host",
            _host_of(self.llama_server_url),
            "--port",
            str(_port_of(self.llama_server_url)),
        ]
        if self.n_threads > 0:  # 0 means "llama-server decides"
            command += ["-t", str(self.n_threads)]
        command += ["-ngl", str(self.n_gpu_layers)]
        return command

    def tts_model_for(self, voice: str | None = None) -> str:
        """Resolve the Piper model path for a voice name (``male``/``female``)."""
        name = (voice or self.tts_voice or "female").strip().lower()
        if name == "male" and self.tts_model_male:
            return self.tts_model_male
        if name == "female" and self.tts_model_female:
            return self.tts_model_female
        return self.tts_model_path

    def block_frames(self) -> int:
        """Number of samples in one audio block at the configured sample rate."""
        return max(1, int(self.audio_sample_rate * self.audio_block_ms / 1000))

    def config_errors(self) -> list[str]:
        """Static configuration problems that do not depend on installed files."""
        errors: list[str] = []
        if self.tts_voice.strip().lower() not in VALID_TTS_VOICES:
            errors.append(
                f"PVAI_TTS_VOICE باید male یا female باشد (مقدار فعلی: {self.tts_voice!r})."
            )
        if self.audio_block_ms not in {10, 20, 40}:
            errors.append("PVAI_AUDIO_BLOCK_MS باید ۱۰، ۲۰ یا ۴۰ باشد.")
        if self.audio_sample_rate != 16000:
            # The live path feeds PCM straight into VAD/Whisper (16 kHz
            # contract); a different capture rate would be transcribed as if it
            # were 16 kHz, producing silently wrong Persian text.
            errors.append(
                "PVAI_AUDIO_SAMPLE_RATE باید 16000 باشد؛ قرارداد ASR/AEC پروژه "
                "PCM16 تک‌کاناله 16kHz است (بازنمونه‌برداری خودکار پیاده نشده)."
            )
        if self.asr_partial_window_s < self.voice_asr_interval_s * 2:
            errors.append(
                "PVAI_ASR_PARTIAL_WINDOW_S بسیار کوچک است؛ حداقل دو برابر PVAI_VOICE_ASR_INTERVAL."
            )
        if self.voice_vad_offset_ratio <= 0 or self.voice_vad_offset_ratio > 1:
            errors.append("PVAI_VOICE_VAD_OFFSET_RATIO باید بین ۰ و ۱ باشد.")
        if self.history_token_budget < 128:
            errors.append("PVAI_HISTORY_TOKEN_BUDGET باید حداقل ۱۲۸ باشد.")
        # Never expose an unauthenticated API off-loopback: the REST surface
        # can read every transcript, delete sessions and burn the local LLM.
        if bool(self.ssl_certfile) != bool(self.ssl_keyfile):
            errors.append("PVAI_SSL_CERTFILE و PVAI_SSL_KEYFILE باید با هم تنظیم شوند.")
        for name, file in (("PVAI_SSL_CERTFILE", self.ssl_certfile), ("PVAI_SSL_KEYFILE", self.ssl_keyfile)):
            if file and not Path(file).is_file():
                errors.append(f"{name} یافت نشد: {file}")
        api_token_present = bool(self.api_token)
        if not is_loopback_host(self.api_host) and not api_token_present:
            errors.append(
                f"PVAI_HOST={self.api_host} (غیر loopback) بدون PVAI_API_TOKEN مجاز نیست؛ "
                "REST بدون احراز هویت کل رونوشت مکالمه‌ها را افشا می‌کند. "
                "PVAI_API_TOKEN را تنظیم کنید یا روی 127.0.0.1 بمانید."
            )
        if self.ws_enabled and not (self.ws_token or self.ws_tokens):
            errors.append(
                "PVAI_WS_ENABLED=true بدون PVAI_WS_TOKEN/PVAI_WS_TOKENS مجاز نیست؛ "
                "API صوتی بی‌احراز هویت روی شبکه باز نمی‌شود."
            )
        if self.ws_enabled and self.ws_token and not self.ws_allow_shared_tokens:
            errors.append(
                "PVAI_WS_TOKEN مشترک برای همهٔ دستگاه‌هاست؛ به‌جای آن PVAI_WS_TOKENS را "
                "با یک توکن per-device تنظیم کنید (یا اگر خطر را پذیرفته‌اید "
                "PVAI_WS_ALLOW_SHARED_TOKENS=true بگذارید)."
            )
        for name, reason in self._device_token_errors():
            errors.append(f"{name}: {reason}")
        # (The TLS advice for a non-loopback WS bind lives in ``config_warnings``:
        # it is a real risk but a deliberate choice, and refusing to start would
        # make the documented home-LAN setup impossible.)
        # Rejected or clamped environment values are reported, not swallowed.
        # This is the difference between "PVAI_AEC_DELAY_MS=40 # calibrated" (a
        # typo) and a service that starts with a value the user never chose.
        for name, reason in self.env_issues:
            errors.append(f"{name}: {reason}")
        if self.voice_session_id:
            from .conversation import ConversationStore

            try:
                ConversationStore.sanitize(self.voice_session_id)
            except ValueError:
                errors.append(
                    "PVAI_VOICE_SESSION_ID نامعتبر است؛ فقط حروف انگلیسی، رقم، «-» و «_» "
                    "(حداکثر ۶۴ نویسه و نه نام دستگاه رزروشدهٔ ویندوز)."
                )
        return errors

    def config_warnings(self) -> list[str]:
        """Risks worth printing on startup that must not stop the service.

        Kept apart from :meth:`config_errors` because that one is a hard gate:
        ``run_live`` raises on it and ``/ready`` reports the installation as
        broken.  A LAN ``ws://`` bind without TLS is a deliberate, documented
        choice — it deserves a loud warning, not a refusal to start.
        """
        warnings: list[str] = []
        if (self.ws_enabled and not is_loopback_host(self.api_host) and self.api_token
                and not (self.ssl_certfile and self.ssl_keyfile)):
            warnings.append(
                "PVAI_WS_ENABLED روی نشانی غیر loopback و بدون TLS است؛ توکن دستگاه و "
                "صدای مکالمه به‌صورت plaintext در شبکه می‌رود. برای شبکهٔ غیرمورد‌اعتماد "
                "reverse proxy با https/wss بگذارید."
            )
        if not self.api_token and is_loopback_host(self.api_host):
            warnings.append(
                "PVAI_API_TOKEN تنظیم نشده است؛ REST فقط چون روی loopback است بدون توکن "
                "کار می‌کند و هر برنامهٔ محلی به آن دسترسی دارد."
            )
        if self.ws_token and self.ws_allow_shared_tokens:
            # Duplicate *different* tokens are already a hard config error; a
            # single shared token is a conscious choice, so it is only warned.
            warnings.append(
                "PVAI_WS_TOKEN مشترک است؛ با PVAI_WS_TOKENS هر دستگاه توکن خودش را دارد و "
                "بستن دسترسی یکی بقیه را قطع نمی‌کند."
            )
        return warnings

    @classmethod
    def from_env(cls) -> "Settings":
        """Build settings from ``PVAI_*`` environment variables.

        Problems found while parsing (an unparsable number, a clamped value, an
        unknown enum) are attached as :attr:`env_issues` instead of being
        silently replaced by a default.
        """
        load_dotenv()
        del ENV_ISSUES[:]  # only this call's problems belong to this instance
        settings = cls(
            model_path=_env_str("PVAI_MODEL_PATH", DEFAULT_MODEL_PATH),
            llama_server_url=_env_str("PVAI_LLAMA_SERVER_URL", DEFAULT_LLAMA_SERVER_URL).rstrip("/"),
            n_ctx=_env_int("PVAI_N_CTX", 4096, minimum=512, maximum=32768),
            n_threads=_env_int("PVAI_N_THREADS", 0, minimum=0, maximum=64),
            n_gpu_layers=_env_int("PVAI_N_GPU_LAYERS", 0, minimum=0, maximum=999),
            system_prompt=_env_str("PVAI_SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT),
            temperature=_env_float("PVAI_TEMPERATURE", 0.7, minimum=0.0, maximum=2.0),
            max_tokens=_env_int("PVAI_MAX_TOKENS", 512, minimum=1, maximum=4096),
            request_timeout_s=_env_float("PVAI_TIMEOUT", 180.0, minimum=5.0, maximum=1800.0),
            api_host=_env_str("PVAI_HOST", DEFAULT_API_HOST),
            api_port=_env_int("PVAI_PORT", DEFAULT_API_PORT, minimum=1, maximum=65535),
            ssl_certfile=_env_str("PVAI_SSL_CERTFILE", ""),
            ssl_keyfile=_env_str("PVAI_SSL_KEYFILE", ""),
            asr_model_path=_env_str("PVAI_ASR_MODEL_PATH", "models/whisper-small"),
            asr_device=_env_choice("PVAI_ASR_DEVICE", "cpu", VALID_ASR_DEVICES),
            asr_compute_type=_env_str("PVAI_ASR_COMPUTE_TYPE", "int8"),
            asr_threads=_env_int("PVAI_ASR_THREADS", 2, minimum=1, maximum=32),
            asr_beam_size=_env_int("PVAI_ASR_BEAM_SIZE", 1, minimum=1, maximum=5),
            asr_vad_filter=bool(_env_optional_bool("PVAI_ASR_VAD_FILTER", False)),
            asr_language=_env_str("PVAI_ASR_LANGUAGE", "fa").strip().lower() or "auto",
            asr_initial_prompt=_env_str("PVAI_ASR_INITIAL_PROMPT", ""),
            asr_no_speech_threshold=_env_float(
                "PVAI_ASR_NO_SPEECH_THRESHOLD", 0.6, minimum=0.0, maximum=1.0
            ),
            asr_log_prob_threshold=_env_float(
                "PVAI_ASR_LOG_PROB_THRESHOLD", -1.0, minimum=-5.0, maximum=0.0
            ),
            asr_compression_ratio_threshold=_env_float(
                "PVAI_ASR_COMPRESSION_RATIO", 2.4, minimum=1.0, maximum=10.0
            ),
            asr_hallucination_phrases=_env_str(
                "PVAI_ASR_HALLUCINATION_PHRASES", DEFAULT_HALLUCINATION_PHRASES
            ),
            asr_max_no_speech_prob=_env_float(
                "PVAI_ASR_MAX_NO_SPEECH_PROB", 0.75, minimum=0.0, maximum=1.0
            ),
            asr_partial_window_s=_env_float("PVAI_ASR_PARTIAL_WINDOW_S", 3.0, minimum=1.0, maximum=30.0),
            asr_max_final_jobs=_env_int("PVAI_ASR_MAX_FINAL_JOBS", 16, minimum=1, maximum=256),
            asr_warmup=bool(_env_optional_bool("PVAI_ASR_WARMUP", True)),
            tts_model_path=_env_str("PVAI_TTS_MODEL_PATH", "models/piper/fa_IR.onnx"),
            tts_model_female=_env_str("PVAI_TTS_MODEL_FEMALE", ""),
            tts_model_male=_env_str("PVAI_TTS_MODEL_MALE", ""),
            tts_voice=_env_str("PVAI_TTS_VOICE", "female").lower(),
            voice_vad_threshold=_env_float("PVAI_VOICE_VAD_THRESHOLD", 0.015, minimum=0.001, maximum=0.5),
            voice_vad_offset_ratio=_env_float("PVAI_VOICE_VAD_OFFSET_RATIO", 0.6, minimum=0.05, maximum=1.0),
            voice_vad_onset_ms=_env_int("PVAI_VOICE_VAD_ONSET_MS", 60, minimum=20, maximum=1000),
            voice_vad_silence_ms=_env_int("PVAI_VOICE_SILENCE_MS", 600, minimum=100, maximum=5000),
            voice_vad_pre_roll_ms=_env_int("PVAI_VOICE_VAD_PRE_ROLL_MS", 200, minimum=0, maximum=1000),
            voice_vad_min_speech_ms=_env_int("PVAI_VOICE_VAD_MIN_SPEECH_MS", 160, minimum=20, maximum=2000),
            voice_vad_max_segment_s=_env_float("PVAI_VOICE_VAD_MAX_SEGMENT_S", 15.0, minimum=1.0, maximum=60.0),
            voice_vad_noise_adapt=bool(_env_optional_bool("PVAI_VOICE_VAD_NOISE_ADAPT", True)),
            voice_vad_noise_multiplier=_env_float("PVAI_VOICE_VAD_NOISE_MULTIPLIER", 2.5, minimum=1.0, maximum=10.0),
            voice_barge_in_margin=_env_float("PVAI_VOICE_BARGE_IN_MARGIN", 1.5, minimum=1.0, maximum=10.0),
            voice_barge_in_ms=_env_int("PVAI_VOICE_BARGE_IN_MS", 200, minimum=20, maximum=2000),
            voice_asr_interval_s=_env_float("PVAI_VOICE_ASR_INTERVAL", 0.8, minimum=0.3, maximum=5.0),
            voice_speech_queue=_env_int("PVAI_VOICE_SPEECH_QUEUE", 8, minimum=1, maximum=64),
            voice_record_seconds=_env_float("PVAI_VOICE_RECORD_SECONDS", 5.0, minimum=1.0, maximum=30.0),
            audio_input_device=_env_str("PVAI_AUDIO_INPUT_DEVICE", ""),
            audio_output_device=_env_str("PVAI_AUDIO_OUTPUT_DEVICE", ""),
            audio_sample_rate=_env_int("PVAI_AUDIO_SAMPLE_RATE", 16000, minimum=8000, maximum=48000),
            audio_block_ms=_env_int("PVAI_AUDIO_BLOCK_MS", 20, minimum=10, maximum=80),
            audio_stall_s=_env_float("PVAI_AUDIO_STALL_S", 2.0, minimum=0.2, maximum=30.0),
            audio_reopen_attempts=_env_int("PVAI_AUDIO_REOPEN_ATTEMPTS", 3, minimum=0, maximum=10),
            audio_reopen_stable_frames=_env_int(
                "PVAI_AUDIO_REOPEN_STABLE_FRAMES", 50, minimum=1, maximum=10000
            ),
            history_persist=bool(_env_optional_bool("PVAI_HISTORY_PERSIST", True)),
            data_dir=_env_str("PVAI_DATA_DIR", "data"),
            history_max_messages=_env_int("PVAI_HISTORY_MAX_MESSAGES", 24, minimum=2, maximum=200),
            history_max_stored_messages=_env_int(
                "PVAI_HISTORY_MAX_STORED", 500, minimum=8, maximum=100000
            ),
            history_token_budget=_env_int("PVAI_HISTORY_TOKEN_BUDGET", 1536, minimum=128, maximum=16384),
            history_include_interrupted=bool(_env_optional_bool("PVAI_HISTORY_INCLUDE_INTERRUPTED", False)),
            history_resume=_env_choice(
                "PVAI_HISTORY_RESUME", "on", {"on", "off"}
            ),
            voice_session_id=_env_str("PVAI_VOICE_SESSION_ID", ""),
            history_retention_days=_env_int(
                "PVAI_HISTORY_RETENTION_DAYS", 30, minimum=0, maximum=3650
            ),
            api_token=_env_str("PVAI_API_TOKEN", ""),
            api_rate_limit_per_s=_env_float(
                "PVAI_API_RATE_LIMIT", 20.0, minimum=0.0, maximum=10000.0
            ),
            api_rate_burst=_env_int("PVAI_API_RATE_BURST", 40, minimum=1, maximum=100000),
            ws_connect_rate_per_min=_env_float(
                "PVAI_WS_CONNECT_RATE", 10.0, minimum=0.0, maximum=10000.0
            ),
            ws_frame_rate_per_s=_env_float(
                "PVAI_WS_FRAME_RATE", 200.0, minimum=0.0, maximum=10000.0
            ),
            ws_enabled=bool(_env_optional_bool("PVAI_WS_ENABLED", False)),
            ws_token=_env_str("PVAI_WS_TOKEN", ""),
            ws_tokens=_env_str("PVAI_WS_TOKENS", ""),
            ws_max_connections=_env_int(
                "PVAI_WS_MAX_CONNECTIONS", 4, minimum=1, maximum=64
            ),
            ws_heartbeat_s=_env_float("PVAI_WS_HEARTBEAT", 15.0, minimum=0.0, maximum=600.0),
            ws_idle_timeout_s=_env_float(
                "PVAI_WS_IDLE_TIMEOUT", 60.0, minimum=0.0, maximum=3600.0
            ),
            ws_playback_pacing=bool(_env_optional_bool("PVAI_WS_PLAYBACK_PACING", True)),
            ws_playback_credit_ms=_env_float(
                "PVAI_WS_PLAYBACK_CREDIT_MS", 200.0, minimum=0.0, maximum=5000.0
            ),
            ws_allow_shared_tokens=bool(
                _env_optional_bool("PVAI_WS_ALLOW_SHARED_TOKENS", False)
            ),
            metrics_enabled=bool(_env_optional_bool("PVAI_METRICS_ENABLED", True)),
            llm_enable_thinking=bool(_env_optional_bool("PVAI_LLM_ENABLE_THINKING", False)),
            aec_enabled=bool(_env_optional_bool("PVAI_AEC_ENABLED", True)),
            aec_library=_env_str("PVAI_AEC_LIBRARY", ""),
            aec_filter_ms=_env_int("PVAI_AEC_FILTER_MS", 250, minimum=100, maximum=1000),
            aec_delay_ms=_env_int("PVAI_AEC_DELAY_MS", 40, minimum=0, maximum=500),
            log_level=_env_str("PVAI_LOG_LEVEL", "INFO").upper(),
        )
        issues = tuple(ENV_ISSUES)
        del ENV_ISSUES[:]
        if not issues:
            return settings
        return replace(settings, env_issues=issues)


def _print_launch(argv: list[str]) -> int:
    """``python -m server.config --launch`` — the llama-server command line.

    ``tools/start_llama.ps1`` calls this instead of repeating the flags, so the
    ``PVAI_N_CTX`` / ``PVAI_N_THREADS`` / ``PVAI_N_GPU_LAYERS`` settings in
    ``.env`` are the single source of truth for how the model server starts.
    """
    executable = "llama-server"
    json_output = False
    for argument in argv:
        if argument == "--launch-json":
            json_output = True
        elif argument in {"-h", "--help"}:
            print("usage: python -m server.config --launch [--exe PATH] [--json]")
            print("Prints the llama-server command line implied by PVAI_* settings.")
            return 0
        elif not argument.startswith("-"):
            executable = argument

    settings = Settings.from_env()
    command = settings.llama_server_command(executable)
    if json_output:
        import json

        print(
            json.dumps(
                {
                    "command": command,
                    "model_path": settings.model_path,
                    "n_ctx": settings.n_ctx,
                    "n_threads": settings.n_threads,
                    "n_gpu_layers": settings.n_gpu_layers,
                    "env_issues": [
                        {"variable": name, "reason": reason}
                        for name, reason in settings.env_issues
                    ],
                },
                ensure_ascii=False,
            )
        )
    else:
        print(" ".join(command))
    for name, reason in settings.env_issues:
        print(f"warning: {name}: {reason}", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover - thin CLI wrapper
    import sys

    raise SystemExit(_print_launch(sys.argv[1:]))
