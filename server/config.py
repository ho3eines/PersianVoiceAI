"""Runtime configuration for the Persian Voice AI local service (llama.cpp backend).

Every setting can be overridden with an environment variable so the same
code runs on a workstation, in tests, and later on a small local server.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MODEL_PATH = str(Path(__file__).resolve().parents[1] / "models" / "Qwen3-4B-Q4_K_M.gguf")
DEFAULT_LLAMA_SERVER_URL = "http://127.0.0.1:8080"
DEFAULT_API_HOST = "127.0.0.1"
DEFAULT_API_PORT = 8000

DEFAULT_SYSTEM_PROMPT = (
    "تو «Persian Voice AI» هستی؛ یک دستیار هوش مصنوعی محلی که روی رایانه کاربر اجرا می‌شود. "
    "همیشه به فارسی روان، طبیعی و درست پاسخ بده، مگر اینکه کاربر صریحاً زبان دیگری بخواهد. "
    "پاسخ را تا حد ممکن کوتاه، دقیق و مفید بده؛ مگر اینکه کاربر توضیح بیشتری بخواهد. "
    "از تعارف اضافه و تکرار پرسش پرهیز کن. "
    "اگر پاسخ چیزی را نمی‌دانی، صادقانه بگو که نمی‌دانی. "
    "برای پرسش‌های فنی، پاسخ را گام‌به‌گام و روشن بنویس."
)


def load_dotenv(path: Path | None = None) -> None:
    """Load a simple ``KEY=VALUE`` .env file without extra dependencies.

    Existing environment variables always win, so explicit shell settings
    override the file.
    """
    env_path = path or (Path.cwd() / ".env")
    if not env_path.is_file():
        return
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


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


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def _env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def _env_optional_bool(name: str, default: bool | None) -> bool | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    if normalized in {"none", "auto", "default"}:
        return None
    return default


VALID_TTS_VOICES = ("male", "female")


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
    n_threads: int = 0  # 0 = auto (llama-server side)
    n_gpu_layers: int = 0  # CPU-only on this laptop
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    temperature: float = 0.7
    max_tokens: int = 512
    request_timeout_s: float = 180.0
    api_host: str = DEFAULT_API_HOST
    api_port: int = DEFAULT_API_PORT
    log_level: str = "INFO"

    # ---- ASR (faster-whisper / CTranslate2, local files only) ----
    asr_model_path: str = "models/whisper-small"
    asr_device: str = "cpu"
    asr_compute_type: str = "int8"
    asr_threads: int = 2  # explicit budget: never leave LLM/TTS without cores
    asr_beam_size: int = 1
    asr_vad_filter: bool = False  # Whisper VAD is not our endpointing authority
    asr_partial_window_s: float = 6.0  # bounded overlapping window for partials
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
    history_retention_days: int = 0

    # ---- LAN voice transport (WebSocket, ESP32-S3 readiness) ----
    ws_enabled: bool = False  # opt-in: needs a device token and a trusted LAN
    ws_token: str = ""  # PVAI_WS_TOKEN; empty => every connection is refused

    # ---- observability ----
    metrics_enabled: bool = True
    llm_enable_thinking: bool = False

    # ---------------------------------------------------------------- helpers
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
        if self.ws_enabled and not self.ws_token:
            errors.append(
                "PVAI_WS_ENABLED=true بدون PVAI_WS_TOKEN مجاز نیست؛ "
                "API صوتی بی‌احراز هویت روی شبکه باز نمی‌شود."
            )
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

    @classmethod
    def from_env(cls) -> "Settings":
        """Build settings from ``PVAI_*`` environment variables."""
        load_dotenv()
        return cls(
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
            asr_model_path=_env_str("PVAI_ASR_MODEL_PATH", "models/whisper-small"),
            asr_device=_env_str("PVAI_ASR_DEVICE", "cpu").lower(),
            asr_compute_type=_env_str("PVAI_ASR_COMPUTE_TYPE", "int8"),
            asr_threads=_env_int("PVAI_ASR_THREADS", 2, minimum=1, maximum=32),
            asr_beam_size=_env_int("PVAI_ASR_BEAM_SIZE", 1, minimum=1, maximum=5),
            asr_vad_filter=bool(_env_optional_bool("PVAI_ASR_VAD_FILTER", False)),
            asr_partial_window_s=_env_float("PVAI_ASR_PARTIAL_WINDOW_S", 6.0, minimum=1.0, maximum=30.0),
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
                "PVAI_HISTORY_RETENTION_DAYS", 0, minimum=0, maximum=3650
            ),
            ws_enabled=bool(_env_optional_bool("PVAI_WS_ENABLED", False)),
            ws_token=_env_str("PVAI_WS_TOKEN", ""),
            metrics_enabled=bool(_env_optional_bool("PVAI_METRICS_ENABLED", True)),
            llm_enable_thinking=bool(_env_optional_bool("PVAI_LLM_ENABLE_THINKING", False)),
            aec_enabled=bool(_env_optional_bool("PVAI_AEC_ENABLED", True)),
            aec_library=_env_str("PVAI_AEC_LIBRARY", ""),
            aec_filter_ms=_env_int("PVAI_AEC_FILTER_MS", 250, minimum=100, maximum=1000),
            aec_delay_ms=_env_int("PVAI_AEC_DELAY_MS", 40, minimum=0, maximum=500),
            log_level=_env_str("PVAI_LOG_LEVEL", "INFO").upper(),
        )
