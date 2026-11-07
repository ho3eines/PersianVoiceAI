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


@dataclass(frozen=True, slots=True)
class Settings:
    """Immutable service settings."""

    model_path: str = DEFAULT_MODEL_PATH
    llama_server_url: str = DEFAULT_LLAMA_SERVER_URL
    n_ctx: int = 4096
    n_threads: int = 0  # 0 = auto
    n_gpu_layers: int = 0  # CPU-only on this laptop
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    temperature: float = 0.7
    max_tokens: int = 512
    request_timeout_s: float = 180.0
    api_host: str = DEFAULT_API_HOST
    api_port: int = DEFAULT_API_PORT
    log_level: str = "INFO"
    asr_model_path: str = "models/whisper-small"
    tts_model_path: str = "models/piper/fa_IR.onnx"
    llm_enable_thinking: bool = False
    aec_enabled: bool = True
    aec_library: str = ""
    aec_filter_ms: int = 250
    aec_delay_ms: int = 40
    voice_asr_interval_s: float = 0.8
    voice_vad_threshold: float = 0.015
    voice_record_seconds: float = 5.0

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
            tts_model_path=_env_str("PVAI_TTS_MODEL_PATH", "models/piper/fa_IR.onnx"),
            llm_enable_thinking=bool(_env_optional_bool("PVAI_LLM_ENABLE_THINKING", False)),
            aec_enabled=bool(_env_optional_bool("PVAI_AEC_ENABLED", True)),
            aec_library=_env_str("PVAI_AEC_LIBRARY", ""),
            aec_filter_ms=_env_int("PVAI_AEC_FILTER_MS", 250, minimum=100, maximum=1000),
            aec_delay_ms=_env_int("PVAI_AEC_DELAY_MS", 40, minimum=0, maximum=500),
            voice_asr_interval_s=_env_float("PVAI_VOICE_ASR_INTERVAL", 0.8, minimum=0.3, maximum=5.0),
            voice_vad_threshold=_env_float("PVAI_VOICE_VAD_THRESHOLD", 0.015, minimum=0.001, maximum=0.5),
            voice_record_seconds=_env_float("PVAI_VOICE_RECORD_SECONDS", 5.0, minimum=1.0, maximum=30.0),
            log_level=_env_str("PVAI_LOG_LEVEL", "INFO").upper(),
        )
