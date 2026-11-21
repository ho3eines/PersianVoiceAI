"""Shared helpers for the management panel (``/api/manage/*``).

The panel edits ``.env`` atomically, masks secrets, and exposes a curated
catalog of the ``PVAI_*`` variables so the web UI can render Persian labels,
help text and widgets without duplicating knowledge from ``Settings``.

Nothing here talks to a model, a provider, or the network: it is pure
configuration plumbing plus small filesystem helpers.
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any, Iterable

from .config import Settings, require_local_http_url, strip_inline_comment

SECRET_KEYS = frozenset({"PVAI_API_TOKEN", "PVAI_WS_TOKEN"})
"""Variables whose value is never echoed back to the browser."""

READ_ONLY_KEYS = frozenset({"PVAI_WS_TOKENS"})
"""Per-device secrets: managed in ``.env`` by hand, never through the panel."""

ENV_TO_ATTR: dict[str, str] = {
    # Names whose ``.env`` spelling does not match the dataclass field.
    "PVAI_TIMEOUT": "request_timeout_s",
    "PVAI_HOST": "api_host",
    "PVAI_PORT": "api_port",
    "PVAI_VOICE_SILENCE_MS": "voice_vad_silence_ms",
    "PVAI_VOICE_ASR_INTERVAL": "voice_asr_interval_s",
    "PVAI_HISTORY_MAX_STORED": "history_max_stored_messages",
    "PVAI_ASR_COMPRESSION_RATIO": "asr_compression_ratio_threshold",
    "PVAI_API_RATE_LIMIT": "api_rate_limit_per_s",
    "PVAI_WS_CONNECT_RATE": "ws_connect_rate_per_min",
    "PVAI_WS_FRAME_RATE": "ws_frame_rate_per_s",
    "PVAI_WS_HEARTBEAT": "ws_heartbeat_s",
    "PVAI_WS_IDLE_TIMEOUT": "ws_idle_timeout_s",
}
"""``PVAI_*`` names mapped to their :class:`Settings` attribute.

Everything else follows the ``PVAI_FOO_BAR`` -> ``foo_bar`` rule.
"""


def attr_for(key: str) -> str:
    """Attribute name for ``PVAI_FOO`` (``foo``), honouring the alias table."""
    return ENV_TO_ATTR.get(key, key.removeprefix("PVAI_").lower())


def known_keys() -> set[str]:
    """Every ``PVAI_*`` variable the panel is allowed to read or write."""
    return {
        key
        for key in _CATALOG_KEYS
        if key not in READ_ONLY_KEYS
    }


SECRET_PLACEHOLDER = "••••••••"
"""Sentinel sent by the UI to mean "keep the stored secret"."""


def mask_secret(value: str | None) -> str:
    """Placeholder shown in the UI for a stored secret."""
    return SECRET_PLACEHOLDER if value else ""


def is_secret_set(settings: Settings | None, key: str) -> bool:
    """Whether a secret variable has a non-empty effective value."""
    if settings is None:
        return False
    return bool(str(getattr(settings, attr_for(key), "") or ""))


def effective_value(settings: Settings, key: str) -> Any:
    """Effective value of ``key`` for a running :class:`Settings` snapshot."""
    return getattr(settings, attr_for(key), None)


def _value_to_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value)


def normalize_text(value: str) -> str:
    """Lower-case and trim so ``True`` and ``true`` compare equal."""
    return value.strip().strip("\"'").lower()


_TRUE_WORDS = frozenset({"true", "1", "yes", "on"})
_FALSE_WORDS = frozenset({"false", "0", "no", "off"})


def values_equal(file_value: str, effective: Any) -> bool:
    """Whether a text read from ``.env`` means the same as an effective value.

    ``.env`` stores ``PVAI_TIMEOUT=180`` while ``Settings`` holds ``180.0``, so
    a plain string comparison reported phantom "restart required" entries.
    """
    if isinstance(effective, bool):
        return normalize_text(file_value) in (_TRUE_WORDS if effective else _FALSE_WORDS)
    text = _value_to_text(effective)
    if normalize_text(file_value) == normalize_text(text):
        return True
    try:
        return float(normalize_text(file_value)) == float(normalize_text(text))
    except ValueError:
        return False


def read_env_file(path: Path) -> dict[str, str]:
    """Read raw ``KEY=VALUE`` assignments from ``.env``.

    Comments, blank lines and unknown keys are ignored.  This mirrors
    :func:`server.config.load_dotenv` so the panel reports exactly what the
    service will read on its next start.
    """
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return values
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            continue
        value = strip_inline_comment(value).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


def _safe_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, str)):
        text = str(value)
    else:
        raise ValueError("مقدار باید متن، عدد یا بولین باشد.")
    if any(char in text for char in "\r\n\x00"):
        raise ValueError("مقدار نمیتواند خط جدید یا نویسهٔ کنترلی داشته باشد.")
    return text


def _persisted_form(value: Any) -> str:
    text = _safe_text(value)
    if any(char in text for char in '\\"\''):
        raise ValueError("نویسهٔ کوتیشن یا بکاسلش در مقدار پشتیبانی نمیشود.")
    if any(char in text for char in " #"):
        return f'"{text}"'
    return text


def _atomic_write(path: Path, lines: Iterable[str], *, newline: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            body = list(lines)
            stream.write(newline.join(body))
            if body:
                stream.write(newline)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def persist_env_updates(path: Path, updates: dict[str, Any]) -> list[str]:
    """Write ``updates`` into ``.env`` atomically, preserving every other line.

    Comments, ordering and untouched assignments survive; only the edited keys
    change.  Inline comments after an edited value are kept.
    """
    if not updates:
        return []
    allowed = known_keys()
    normalized: dict[str, str] = {}
    for key, raw in updates.items():
        if key not in allowed:
            raise ValueError(f"تنظیم ناشناخته: {key}")
        normalized[key] = _persisted_form(raw)

    try:
        original = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        original = ""
    newline = "\r\n" if "\r\n" in original else "\n"
    out: list[str] = []
    seen: set[str] = set()
    for line in original.splitlines():
        match = re.match(r"^(\s*)(PVAI_[A-Z0-9_]+)(\s*=)(.*)$", line)
        if not match or match.group(2) not in normalized:
            out.append(line)
            continue
        key = match.group(2)
        remainder = match.group(4)
        comment = ""
        stripped = strip_inline_comment(remainder)
        if stripped and stripped != remainder.rstrip():
            comment = " " + remainder[remainder.index(stripped) + len(stripped):].strip()
            if comment.strip():
                comment = " " + comment.strip()
        out.append(f"{match.group(1)}{key}{match.group(3)}{normalized[key]}{comment}")
        seen.add(key)
    for key, value in sorted(normalized.items()):
        if key not in seen:
            out.append(f"{key}={value}")
    _atomic_write(path, out, newline=newline)
    return sorted(normalized)


def remove_env_keys(path: Path, keys: Iterable[str]) -> list[str]:
    """Delete selected assignments so the code default applies again."""
    targets = {key for key in keys if key in known_keys()}
    if not targets:
        return []
    try:
        original = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return []
    newline = "\r\n" if "\r\n" in original else "\n"
    out: list[str] = []
    removed: list[str] = []
    for line in original.splitlines():
        match = re.match(r"^\s*(PVAI_[A-Z0-9_]+)\s*=", line)
        if match and match.group(1) in targets:
            removed.append(match.group(1))
            continue
        out.append(line)
    if removed:
        _atomic_write(path, out, newline=newline)
    return sorted(set(removed))


def validate_updates(updates: dict[str, Any]) -> None:
    """Reject unsafe or meaningless values before anything touches the disk."""
    if not isinstance(updates, dict) or not updates:
        raise ValueError("حداقل یک تنظیم باید ارسال شود.")
    allowed = known_keys()
    for key, raw in updates.items():
        value = _safe_text(raw)
        if key not in allowed:
            raise ValueError(f"تنظیم ناشناخته: {key}")
        lowered = value.lower()
        if key == "PVAI_LLAMA_SERVER_URL":
            require_local_http_url(value, variable=key)
        if key == "PVAI_HOST" and value not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("پنل مدیریت فقط میتواند PVAI_HOST را روی loopback بگذارد.")
        if key in SECRET_KEYS and value and len(value) < 16:
            raise ValueError(f"{key} باید حداقل ۱۶ نویسه باشد.")
        if key == "PVAI_AUDIO_SAMPLE_RATE" and value != "16000":
            raise ValueError("نرخ نمونهبرداری صوت باید ۱۶۰۰۰ هرتز بماند.")
        if key == "PVAI_AUDIO_BLOCK_MS" and value not in {"10", "20", "40"}:
            raise ValueError("اندازهٔ بلوک صوت باید ۱۰، ۲۰ یا ۴۰ میلیثانیه باشد.")
        if key == "PVAI_TTS_VOICE" and lowered not in {"male", "female", ""}:
            raise ValueError("صدای TTS باید male یا female باشد.")
        if key == "PVAI_ASR_DEVICE" and lowered not in {"auto", "cpu", "cuda", "mps"}:
            raise ValueError("دستگاه ASR پشتیبانی نمیشود.")
        if key == "PVAI_LOG_LEVEL" and lowered not in {"debug", "info", "warning", "error", "critical"}:
            raise ValueError("سطح لاگ باید یکی از debug/info/warning/error/critical باشد.")


def pending_restart(settings: Settings, env_path: Path) -> list[str]:
    """Keys whose ``.env`` value differs from the value this process loaded.

    The panel cannot hot-swap a running ``Settings`` snapshot, so after a save
    the UI shows which changes need a service restart to take effect.
    """
    file_values = read_env_file(env_path)
    pending: list[str] = []
    for key, raw in file_values.items():
        if key not in _CATALOG_KEYS:
            continue
        if not values_equal(raw, effective_value(settings, key)):
            pending.append(key)
    return sorted(pending)


def env_issues(settings: Settings) -> list[str]:
    """Human-readable configuration problems recorded while loading ``.env``."""
    issues: list[str] = []
    for issue in getattr(settings, "env_issues", []) or []:
        if isinstance(issue, (tuple, list)) and len(issue) == 2:
            issues.append(f"{issue[0]}: {issue[1]}")
        else:
            issues.append(str(issue))
    return issues


class Field:
    """One editable setting, as the UI needs to see it."""

    def __init__(
        self,
        key: str,
        label: str,
        help_text: str,
        *,
        widget: str = "text",
        value_type: str | None = None,
        minimum: float | None = None,
        maximum: float | None = None,
        choices: list[str] | None = None,
        advanced: bool = False,
        restart_required: bool = True,
    ) -> None:
        self.key = key
        self.label = label
        self.help_text = help_text
        self.widget = widget
        self.value_type = value_type
        self.minimum = minimum
        self.maximum = maximum
        self.choices = choices
        self.advanced = advanced
        self.restart_required = restart_required

    @property
    def type(self) -> str:
        if self.value_type is not None:
            return self.value_type
        if self.widget == "switch":
            return "bool"
        if self.widget == "number":
            # The shipped default decides: 0.7 is a float field, 4 is an int one.
            return "float" if isinstance(_DEFAULTS.get(self.key), float) else "int"
        return "str"

    def as_dict(self, settings: Settings, file_values: dict[str, str]) -> dict[str, Any]:
        secret = self.key in SECRET_KEYS
        raw = file_values.get(self.key)
        stored = _stored_value(self, raw)
        if secret:
            return {
                "key": self.key,
                "label_fa": self.label,
                "help_fa": self.help_text,
                "type": "str",
                "value": None,
                "default": None,
                "is_set": is_secret_set(settings, self.key),
                "minimum": None,
                "maximum": None,
                "choices": None,
                "widget": "secret",
                "secret": True,
                "restart_required": self.restart_required,
                "advanced": self.advanced,
                "stored_in_file": raw is not None,
            }
        effective = effective_value(settings, self.key)
        return {
            "key": self.key,
            "label_fa": self.label,
            "help_fa": self.help_text,
            "type": self.type,
            "value": _json_value(self, effective),
            "default": _json_value(self, _default_value(self)),
            "minimum": self.minimum,
            "maximum": self.maximum,
            "choices": self.choices,
            "widget": self.widget,
            "secret": False,
            "restart_required": self.restart_required,
            "advanced": self.advanced,
            "stored_in_file": raw is not None,
            "stored_value": stored,
        }


def _default_value(field: Field) -> Any:
    return _DEFAULTS.get(field.key)


def _json_value(field: Field, value: Any) -> Any:
    if field.widget == "switch":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
    if value is None:
        return ""
    if field.widget == "number":
        try:
            number = float(value)
        except (TypeError, ValueError):
            return ""
        return int(number) if number.is_integer() else number
    return str(value)


def _stored_value(field: Field, raw: str | None) -> str:
    if raw is None:
        return ""
    return raw


def settings_groups(settings: Settings, env_path: Path) -> list[dict[str, Any]]:
    """Build the ``groups`` payload for ``GET /api/manage/settings``."""
    file_values = read_env_file(env_path)
    return [
        {
            "id": group,
            "title_fa": meta["title"],
            "description_fa": meta["description"],
            "fields": [field.as_dict(settings, file_values) for field in meta["fields"]],
        }
        for group, meta in GROUPS.items()
        if meta["fields"]
    ]


def _defaults() -> dict[str, Any]:
    try:
        return {
            key: effective_value(Settings(), key)
            for key in _CATALOG_KEYS
        }
    except Exception:  # noqa: BLE001 - defaults are cosmetic; never break the panel
        return {}


def _f(key: str, label: str, help_text: str, **kwargs: Any) -> Field:
    return Field(key, label, help_text, **kwargs)


_GROUPS_SPEC: dict[str, dict[str, Any]] = {
    "llm": {
        "title": "مدل زبانی",
        "description": "انتخاب مدل GGUF محلی، آدرس llama-server و پارامترهای تولید پاسخ.",
        "fields": [
            _f("PVAI_MODEL_PATH", "مسیر مدل زبانی", "فایل GGUF مدل. مسیر نسبی از ریشهٔ پروژه حساب می‌شود، مثل models/qwen3.5-4b/Qwen3.5-4B-Q4_K_M.gguf.", widget="path"),
            _f("PVAI_LLAMA_SERVER_URL", "آدرس llama-server", "آدرس محلی سرویس llama.cpp. پیش‌فرض http://127.0.0.1:8080 است و فقط loopback پذیرفته می‌شود تا دادهٔ شما از دستگاه خارج نشود."),
            _f("PVAI_LLAMA_SERVER_EXE", "برنامهٔ llama-server", "مسیر کامل یا نام اجراپذیر llama-server که پنل برای «شروع» استفاده می‌کند. اگر فقط نام بنویسید، در PATH جست‌وجو می‌شود؛ برای این دستگاه مسیر کامل بدهید، مثل D:\\Lama.cpp\\bin\\llama-server.exe.", widget="path"),
            _f("PVAI_N_CTX", "اندازهٔ پنجرهٔ متن", "بیشترین تعداد توکنی که مدل هم‌زمان می‌بیند. بزرگ‌تر یعنی حافظهٔ بیشتر و پردازش کندتر.", widget="number", minimum=256, maximum=131072),
            _f("PVAI_N_THREADS", "تعداد رشتهٔ پردازش", "صفر یعنی llama-server خودش تصمیم بگیرد. برای این لپ‌تاپ عدد ۴ (معادل دو هستهٔ فیزیکی) اندازه‌گیری شده است.", widget="number", minimum=0, maximum=64),
            _f("PVAI_N_GPU_LAYERS", "لایه‌های روی GPU", "صفر یعنی فقط CPU. روی این دستگاه GPU کندتر از CPU اندازه‌گیری شده، پس پیش‌فرض را تغییر ندهید.", widget="number", minimum=0, maximum=200, advanced=True),
            _f("PVAI_TEMPERATURE", "خلاقیت پاسخ", "مقدار پایین یعنی پاسخ قابل‌پیش‌بینی‌تر و مقدار بالا یعنی متنوع‌تر. بازهٔ رایج ۰.۲ تا ۱.۰ است.", widget="number", minimum=0.0, maximum=2.0),
            _f("PVAI_MAX_TOKENS", "سقف طول پاسخ", "بیشترین تعداد توکن در هر پاسخ. خالی یا صفر یعنی بی‌نهایت تا پر شدن پنجرهٔ متن.", widget="number", minimum=0, maximum=32768),
            _f("PVAI_TIMEOUT", "مهلت پاسخ مدل (ثانیه)", "اگر مدل تا این زمان پاسخ را کامل نکند، درخواست با خطا برمی‌گردد. روی CPU این مقدار را بالا نگه دارید.", widget="number", minimum=5, maximum=1800),
            _f("PVAI_LLM_ENABLE_THINKING", "حالت تفکر مدل", "برای مدل‌های استدلالی (مانند Qwen3) اگر روشن باشد، مدل پیش از پاسخ فکر می‌کند؛ نتیجه دقیق‌تر ولی کندتر است.", widget="switch"),
            _f("PVAI_SYSTEM_PROMPT", "دستور سیستمی", "شخصیت و قواعد پاسخ‌دهی دستیار. خالی یعنی متن پیش‌فرض کد استفاده شود.", widget="textarea"),
        ],
    },
    "asr": {
        "title": "تبدیل گفتار به متن",
        "description": "مدل Whisper، دقت جست‌وجو و فیلترهای جلوگیری از توهم مدل.",
        "fields": [
            _f("PVAI_ASR_MODEL_PATH", "مسیر مدل گفتار", "پوشهٔ مدل CTranslate2 (مثل models/whisper-small یا models/whisper-large-v3-turbo) یا نام مدل.", widget="path"),
            _f("PVAI_ASR_DEVICE", "دستگاه اجرا", "cpu امن‌ترین گزینه روی این لپ‌تاپ است. auto اگر CUDA نبود، خودش به CPU برمی‌گردد.", widget="select", choices=["cpu", "auto", "cuda", "mps"]),
            _f("PVAI_ASR_COMPUTE_TYPE", "دقت محاسبه", "int8 سریع‌ترین و کم‌حافظه‌ترین است؛ float32 دقیق‌تر ولی حدوداً دو برابر کندتر.", widget="select", choices=["int8", "int8_float16", "float16", "float32"]),
            _f("PVAI_ASR_THREADS", "تعداد رشتهٔ گفتار", "پردازش گفتار با مدل زبانی بر سر CPU رقابت می‌کند؛ دو رشته تعادل پیش‌فرض است.", widget="number", minimum=1, maximum=32),
            _f("PVAI_ASR_BEAM_SIZE", "دقت جست‌وجو (beam)", "عدد ۵ معمولاً دقیق‌تر است ولی کندتر. با مدل‌های بزرگ‌تر اثر بیشتری دارد؛ با whisper-small تقریباً بی‌اثر بود.", widget="number", minimum=1, maximum=5),
            _f("PVAI_ASR_LANGUAGE", "زبان", "fa برای فارسی. مقدار auto اجازه می‌دهد Whisper زبان را خودش تشخیص دهد.", widget="select", choices=["fa", "auto"]),
            _f("PVAI_ASR_INITIAL_PROMPT", "متن راهنما", "یک جملهٔ نمونه که املا و سبک نوشتن را هدایت می‌کند. در آزمون ما روی صدای مصنوعی نتیجه را بدتر کرد، پس با احتیاط تغییر دهید.", widget="textarea"),
            _f("PVAI_ASR_HOTWORDS", "واژه‌های کلیدی", "واژه‌هایی که می‌خواهید مدل بیشتر بشناسد، جدا شده با ویرگول. خالی یعنی غیرفعال."),
            _f("PVAI_ASR_VAD_FILTER", "فیلتر سکوت Whisper", "سکوت و بی‌صداهای داخلی را حذف می‌کند. تشخیص پایان گفتار پروژه مستقل از این گزینه است.", widget="switch"),
            _f("PVAI_ASR_WARMUP", "گرم‌کردن مدل", "مدل را هنگام بالا آمدن سرویس یک‌بار اجرا می‌کند تا اولین پاسخ واقعی سریع‌تر شود.", widget="switch"),
            _f("PVAI_ASR_NO_SPEECH_THRESHOLD", "آستانهٔ بی‌گفتاری", "احتمالی که بالاتر از آن، قطعهٔ صوتی «گفتار نیست» تلقی می‌شود.", widget="number", minimum=0.0, maximum=1.0, advanced=True),
            _f("PVAI_ASR_LOG_PROB_THRESHOLD", "آستانهٔ اطمینان", "اگر میانگین لاگ‌احتمال پایین‌تر از این مقدار باشد، رونویسی نامطمئن شمرده می‌شود.", widget="number", minimum=-5.0, maximum=0.0, advanced=True),
            _f("PVAI_ASR_MAX_NO_SPEECH_PROB", "سخت‌گیری روی نویز", "قطعه‌هایی با احتمال بی‌گفتاری بالاتر از این مقدار کنار گذاشته می‌شوند تا متن ساختگی تولید نشود.", widget="number", minimum=0.0, maximum=1.0, advanced=True),
            _f("PVAI_ASR_HALLUCINATION_PHRASES", "عبارت‌های ساختگی", "عبارت‌هایی که Whisper روی سکوت می‌سازد و باید حذف شوند، جدا شده با ویرگول.", widget="textarea", advanced=True),
            _f("PVAI_ASR_PARTIAL_WINDOW_S", "پنجرهٔ رونویسی موقت", "طول پنجرهٔ هم‌پوشان برای نمایش متن در حال گفتار. باید دست‌کم دو برابر PVAI_VOICE_ASR_INTERVAL باشد.", widget="number", minimum=1.0, maximum=30.0, advanced=True),
            _f("PVAI_ASR_MAX_FINAL_JOBS", "سقف کارهای نهایی", "آستانهٔ هشدار برای صف رونویسی نهایی؛ هیچ رونویسی نهایی حذف نمی‌شود.", widget="number", minimum=1, maximum=256, advanced=True),
        ],
    },
    "tts": {
        "title": "تولید صدا",
        "description": "انتخاب صدا و مدل Piper برای پاسخ صوتی.",
        "fields": [
            _f("PVAI_TTS_VOICE", "جنسیت صدا", "صدای زن یا مرد. باید با مدل انتخاب‌شده هم‌خوان باشد.", widget="select", choices=["female", "male"]),
            _f("PVAI_TTS_MODEL_PATH", "مدل صدای فعال", "فایل ONNX مدل Piper که همین حالا استفاده می‌شود.", widget="path"),
            _f("PVAI_TTS_MODEL_FEMALE", "مدل صدای زن", "مسیر اختیاری مدل صدای زن. خالی یعنی از مدل فعال استفاده شود.", widget="path"),
            _f("PVAI_TTS_MODEL_MALE", "مدل صدای مرد", "مسیر اختیاری مدل صدای مرد. خالی یعنی از مدل فعال استفاده شود.", widget="path"),
        ],
    },
    "audio": {
        "title": "دستگاه صوتی",
        "description": "ورودی و خروجی صدا و بازیابی خطای جریان صوتی.",
        "fields": [
            _f("PVAI_AUDIO_INPUT_DEVICE", "میکروفون", "نام یا شمارهٔ دستگاه ورودی. خالی یعنی دستگاه پیش‌فرض سیستم. فهرست دستگاه‌ها را با tools/mic_probe.py --list ببینید."),
            _f("PVAI_AUDIO_OUTPUT_DEVICE", "بلندگو", "نام یا شمارهٔ دستگاه خروجی. خالی یعنی دستگاه پیش‌فرض سیستم."),
            _f("PVAI_AUDIO_SAMPLE_RATE", "نرخ نمونه‌برداری", "قرارداد پروژه ۱۶۰۰۰ هرتز است و تغییر آن پشتیبانی نمی‌شود.", widget="number", minimum=16000, maximum=16000),
            _f("PVAI_AUDIO_BLOCK_MS", "اندازهٔ بلوک", "۲۰ میلی‌ثانیه قرارداد انتقال است؛ ۱۰ و ۴۰ فقط برای آزمایش‌های خاص.", widget="select", choices=["10", "20", "40"], advanced=True),
            _f("PVAI_AUDIO_STALL_S", "مهلت توقف جریان", "اگر جریان صوتی این مدت داده ندهد، به‌عنوان قطعی تلقی و دوباره باز می‌شود.", widget="number", minimum=0.2, maximum=30.0, advanced=True),
            _f("PVAI_AUDIO_REOPEN_ATTEMPTS", "تلاش برای بازکردن مجدد", "تعداد تلاش‌ها برای بازیابی دستگاه پس از خطا.", widget="number", minimum=1, maximum=20, advanced=True),
            _f("PVAI_AUDIO_REOPEN_STABLE_FRAMES", "فریم‌های پایدار", "چند فریم باید بی‌خطا برسد تا جریان بازگشته سالم شمرده شود.", widget="number", minimum=1, maximum=1000, advanced=True),
        ],
    },
    "vad": {
        "title": "تشخیص گفتار و نوبت",
        "description": "حساسیت تشخیص شروع/پایان گفتار و رفتار قطع پاسخ (barge-in).",
        "fields": [
            _f("PVAI_VOICE_VAD_THRESHOLD", "آستانهٔ صدا", "سطح انرژی لازم برای شروع گفتار. مقدار کمتر یعنی حساس‌تر و زودتر شروع می‌کند.", widget="number", minimum=0.001, maximum=0.5),
            _f("PVAI_VOICE_VAD_OFFSET_RATIO", "نسبت پایان گفتار", "کسری از آستانه که زیر آن گفتار تمام‌شده تلقی می‌شود. هیسترزیس از لرزش جلوگیری می‌کند.", widget="number", minimum=0.1, maximum=1.0),
            _f("PVAI_VOICE_VAD_ONSET_MS", "مدت تأیید شروع", "چند میلی‌ثانیه صدا لازم است تا واقعاً شروع گفتار شمرده شود.", widget="number", minimum=10, maximum=1000),
            _f("PVAI_VOICE_SILENCE_MS", "سکوت پایان نوبت", "چند میلی‌ثانیه سکوت یعنی کاربر صحبتش تمام شده است.", widget="number", minimum=100, maximum=5000),
            _f("PVAI_VOICE_VAD_PRE_ROLL_MS", "پیش‌رول", "صوت پیش از لحظهٔ تشخیص هم نگه داشته می‌شود تا ابتدای کلمه بریده نشود.", widget="number", minimum=0, maximum=2000),
            _f("PVAI_VOICE_VAD_MIN_SPEECH_MS", "کوتاه‌ترین گفتار", "قطعه‌های کوتاه‌تر از این زمان (سرفه، کلیک) نادیده گرفته می‌شوند.", widget="number", minimum=0, maximum=5000),
            _f("PVAI_VOICE_VAD_MAX_SEGMENT_S", "بلندترین قطعه", "اگر گفتار از این طول بگذرد، قطعه به‌زور بسته می‌شود تا حافظه پر نشود.", widget="number", minimum=1.0, maximum=120.0),
            _f("PVAI_VOICE_VAD_NOISE_ADAPT", "تطبیق با نویز", "کف نویز اتاق را یاد می‌گیرد و آستانه را خودکار تنظیم می‌کند.", widget="switch"),
            _f("PVAI_VOICE_VAD_NOISE_MULTIPLIER", "ضریب نویز", "آستانه چند برابر کف نویز باشد. بزرگ‌تر یعنی سخت‌گیرتر در محیط پرصدا.", widget="number", minimum=1.0, maximum=10.0, advanced=True),
            _f("PVAI_VOICE_BARGE_IN_MS", "مدت قطع پاسخ", "اگر کاربر این مدت صحبت کند، پاسخ دستیار متوقف می‌شود.", widget="number", minimum=0, maximum=2000),
            _f("PVAI_VOICE_BARGE_IN_MARGIN", "حاشیهٔ قطع پاسخ", "چند برابر آستانهٔ عادی لازم است تا قطع پاسخ فعال شود و بوق اضافه صدایش را قطع نکند.", widget="number", minimum=1.0, maximum=10.0, advanced=True),
            _f("PVAI_VOICE_ASR_INTERVAL", "فاصلهٔ رونویسی موقت", "هر چند ثانیه متن در حال گفتار به‌روز شود.", widget="number", minimum=0.3, maximum=5.0, advanced=True),
            _f("PVAI_VOICE_SPEECH_QUEUE", "ظرفیت صف گفتار", "تعداد قطعه‌های صوتی که پیش از پردازش در صف می‌مانند.", widget="number", minimum=1, maximum=64, advanced=True),
            _f("PVAI_VOICE_RECORD_SECONDS", "طول ضبط حالت قدیمی", "فقط در حالت --turn-based استفاده می‌شود.", widget="number", minimum=1, maximum=60, advanced=True),
        ],
    },
    "aec": {
        "title": "حذف پژواک (AEC)",
        "description": "تنظیمات SpeexDSP برای جلوگیری از شنیدن صدای خودِ بلندگو.",
        "fields": [
            _f("PVAI_AEC_ENABLED", "فعال بودن AEC", "اگر هدفون استفاده می‌کنید می‌توانید خاموش کنید، ولی با بلندگو باید روشن بماند.", widget="switch"),
            _f("PVAI_AEC_LIBRARY", "مسیر کتابخانهٔ AEC", "روی ویندوز مسیر DLL اسپیکس‌دی‌اس‌پی. خالی یعنی جست‌وجوی سیستمی.", widget="path"),
            _f("PVAI_AEC_FILTER_MS", "طول فیلتر", "طول فیلتر تطبیقی پژواک. بزرگ‌تر یعنی تحمل تأخیر بیشتر و هزینهٔ محاسباتی بالاتر.", widget="number", minimum=10, maximum=1000, advanced=True),
            _f("PVAI_AEC_DELAY_MS", "تأخیر مرجع", "تأخیر واقعی مسیر پخش تا میکروفون؛ باید روی سخت‌افزار واقعی کالیبره شود.", widget="number", minimum=0, maximum=2000, advanced=True),
        ],
    },
    "storage": {
        "title": "حافظهٔ مکالمه",
        "description": "نگه‌داری متن گفتگو روی دیسک محلی و سیاست پنجرهٔ حافظه.",
        "fields": [
            _f("PVAI_DATA_DIR", "پوشهٔ داده", "محل نگه‌داری مکالمه‌ها و بازخورد. متن محلی می‌ماند؛ صوت در این پروژه ذخیره نمی‌شود.", widget="path"),
            _f("PVAI_HISTORY_PERSIST", "ذخیره روی دیسک", "اگر خاموش باشد حافظه فقط در RAM می‌ماند و با بستن برنامه پاک می‌شود.", widget="switch"),
            _f("PVAI_HISTORY_RESUME", "ادامهٔ آخرین نشست", "با روشن بودن، هر اجرا ادامهٔ نشست قبلی را می‌گیرد. برای پاسخ سریع‌تر و شروع تمیز، خاموش کنید.", widget="switch"),
            _f("PVAI_HISTORY_MAX_MESSAGES", "پیام‌های پنجرهٔ فعال", "چند پیام آخر به مدل داده شود.", widget="number", minimum=0, maximum=200),
            _f("PVAI_HISTORY_TOKEN_BUDGET", "بودجهٔ توکن حافظه", "سقف توکنی که صرف تاریخچه می‌شود تا پنجرهٔ متن مدل پر نشود.", widget="number", minimum=0, maximum=131072),
            _f("PVAI_HISTORY_MAX_STORED", "سقف پیام‌های ذخیره‌شده", "بیشترین تعداد پیامی که در فایل هر نشست نگه داشته می‌شود.", widget="number", minimum=1, maximum=100000),
            _f("PVAI_HISTORY_INCLUDE_INTERRUPTED", "ثبت پاسخ‌های نیمه‌کاره", "اگر روشن باشد، پاسخ‌هایی که با barge-in قطع شده‌اند هم در تاریخچه می‌آیند.", widget="switch", advanced=True),
            _f("PVAI_HISTORY_RETENTION_DAYS", "نگه‌داری (روز)", "نشست‌های قدیمی‌تر از این تعداد روز پاک می‌شوند. صفر یعنی هرگز پاک نکن.", widget="number", minimum=0, maximum=3650),
            _f("PVAI_VOICE_SESSION_ID", "شناسهٔ نشست ثابت", "اگر نام ثابتی بدهید، هر اجرا همان نشست را ادامه می‌دهد. خالی یعنی آخرین نشست. فقط حروف و عدد و _ و -."),
            _f("PVAI_METRICS_ENABLED", "سنجه‌ها", "فعال بودن مسیر /metrics برای پایش محلی.", widget="switch"),
        ],
    },
    "network": {
        "title": "شبکه و امنیت",
        "description": "آدرس شنود، توکن‌ها و محدودیت نرخ. پنل مدیریت فقط روی loopback کار می‌کند.",
        "fields": [
            _f("PVAI_HOST", "آدرس شنود API", "روی loopback بماند. پنل مدیریت اجازهٔ تغییر آن به آدرس عمومی را نمی‌دهد.", widget="select", choices=["127.0.0.1", "localhost", "::1"]),
            _f("PVAI_PORT", "درگاه API", "درگاه سرویس اصلی (پیش‌فرض ۸۰۰۰).", widget="number", minimum=1, maximum=65535),
            _f("PVAI_API_TOKEN", "توکن REST", "اگر تنظیم شود، همهٔ درخواست‌های REST باید توکن داشته باشند. مقدار ذخیره‌شده هرگز نمایش داده نمی‌شود.", widget="secret"),
            _f("PVAI_API_RATE_LIMIT", "سقف نرخ درخواست", "بیشترین درخواست در ثانیه از هر مشتری.", widget="number", minimum=1, maximum=10000, advanced=True),
            _f("PVAI_API_RATE_BURST", "انفجار مجاز", "تعداد درخواست‌های ناگهانی که در یک لحظه پذیرفته می‌شود.", widget="number", minimum=1, maximum=100000, advanced=True),
            _f("PVAI_WS_ENABLED", "انتقال صوتی WebSocket", "درگاه گفتار ESP32-S3. بدون توکن، هر اتصال رد می‌شود.", widget="switch"),
            _f("PVAI_WS_TOKEN", "توکن WebSocket", "توکن مشترک دستگاه‌ها. مقدار ذخیره‌شده هرگز نمایش داده نمی‌شود.", widget="secret", advanced=True),
            _f("PVAI_WS_MAX_CONNECTIONS", "حداکثر اتصال", "سقف اتصال هم‌زمان دستگاه‌ها.", widget="number", minimum=1, maximum=256, advanced=True),
            _f("PVAI_WS_HEARTBEAT", "ضربان (ثانیه)", "فاصلهٔ پیام‌های ضربان برای تشخیص قطعی اتصال.", widget="number", minimum=1, maximum=3600, advanced=True),
            _f("PVAI_WS_IDLE_TIMEOUT", "مهلت بی‌کاری", "اتصال بی‌استفاده پس از این مدت بسته می‌شود.", widget="number", minimum=1, maximum=86400, advanced=True),
            _f("PVAI_WS_PLAYBACK_PACING", "کنترل سرعت پخش", "جلوگیری از سرریز صف پخش در دستگاه.", widget="switch", advanced=True),
            _f("PVAI_SSL_CERTFILE", "مسیر گواهی TLS", "برای wss/https. خالی یعنی بدون رمزنگاری.", widget="path", advanced=True),
            _f("PVAI_SSL_KEYFILE", "مسیر کلید TLS", "کلید خصوصی متناظر با گواهی.", widget="path", advanced=True),
            _f("PVAI_LOG_LEVEL", "سطح لاگ", "میزان جزئیات لاگ سرور.", widget="select", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], advanced=True),
        ],
    },
}

_CATALOG_KEYS: frozenset[str] = frozenset(
    field.key
    for meta in _GROUPS_SPEC.values()
    for field in meta["fields"]
)

GROUPS: dict[str, dict[str, Any]] = _GROUPS_SPEC

_DEFAULTS: dict[str, Any] = _defaults()
