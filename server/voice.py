"""Local, turn-based PC voice client. Optional audio imports stay lazy."""
from __future__ import annotations

import argparse
import importlib
import ipaddress
import json
import logging
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Any

import httpx

from .config import Settings
from .persian import normalize_for_eval
from .metrics import Metrics

logger = logging.getLogger("persian_voice_ai.asr")

SAMPLE_RATE = 16000
""""""  # the ASR/TTS contract of this project is PCM16 mono @ 16 kHz


class AsrPreempted(Exception):
    """A superseded partial ASR decode stopped between lazy segments."""


class VoiceError(RuntimeError):
    """User-facing audio failure."""


def local_api_url(settings: Settings) -> str:
    host = settings.api_host
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    if host != "localhost":
        try:
            address = ipaddress.ip_address(host)
        except ValueError as exc:
            raise VoiceError("آدرس API باید IP محلی باشد.") from exc
        if not (address.is_private or address.is_loopback):
            raise VoiceError("ارسال صدا و متن به آدرس عمومی مجاز نیست.")
    scheme = "https" if settings.ssl_certfile and settings.ssl_keyfile else "http"
    return f"{scheme}://{'[' + host + ']' if ':' in host else host}:{settings.api_port}"


def local_client_headers(settings: Settings, base_url: str | None = None) -> dict[str, str]:
    """Authenticate only the configured local API, never a custom remote URL."""
    if base_url is not None:
        from urllib.parse import urlsplit
        expected = urlsplit(local_api_url(settings))
        target = urlsplit(base_url)
        loopbacks = {"localhost", "127.0.0.1", "::1"}
        same_host = (target.hostname == expected.hostname or
                     target.hostname in loopbacks and expected.hostname in loopbacks)
        if not same_host or (target.scheme, target.port) != (expected.scheme, expected.port):
            return {}
    return {"Authorization": f"Bearer {settings.api_token}"} if settings.api_token else {}


def assert_local_api_authorized(response: httpx.Response) -> None:
    """Report missing internal credentials, not a generic model failure."""
    if getattr(response, "status_code", None) == 401:
        raise VoiceError("PVAI_API_TOKEN برای کلاینت داخلی تنظیم نشده؛ تنظیمات API و کلاینت را یکسان کنید.")


def dependency(name: str) -> Any:
    """Import an optional audio dependency, naming it in the error.

    The message used to be generic — "وابستگی صوتی آماده نیست" — so the user
    could not tell which of ``numpy``/``sounddevice``/``faster_whisper`` was
    missing, and had to guess from a stack trace.  The module name and the
    original error are now part of the message, and the original exception is
    kept as ``__cause__`` so ``traceback`` shows the real reason (a missing
    shared library reports as ``OSError``, not ``ImportError``).
    """
    try:
        return importlib.import_module(name)
    except (ImportError, OSError) as exc:
        detail = str(exc) or type(exc).__name__
        raise VoiceError(
            f"وابستگی صوتی «{name}» آماده نیست ({detail})؛ "
            "requirements-voice.txt را نصب کنید و راهنمای PC را ببینید."
        ) from exc


def normalized_phrase(phrase: str) -> str:
    """The canonical form used to look a phrase up in :data:`WHOLE_TRANSCRIPT_ONLY`."""
    return normalize_for_eval(phrase).strip(_PHRASE_TRIM)


#: Characters trimmed before comparing a transcript with a known phrase.
_PHRASE_TRIM = "\"'«»()[]{}.,،؛;:!?؟…-–—\u200c \t\n"


#: Short phrases that are boilerplate on silence but ordinary words inside a
#: real sentence ("به مادرم گفتم خداحافظ و رفتم").  These match only when they
#: are the *whole* transcript; every other phrase may also match as a prefix,
#: because hallucinated boilerplate carries extra words ("زیر‌نویس‌ها توسط تیم
#: ما تهیه شده است").
WHOLE_TRANSCRIPT_ONLY = frozenset({"خداحافظ", "تشکر", "بدرود", "خدا نگهدار"})


def _is_hallucination(text: str, phrases: tuple[str, ...]) -> bool:
    """True when ``text`` is a known Whisper silence/noise artefact.

    Compared after :func:`server.persian.normalize_for_eval`, so Arabic letter
    forms, Persian/Arabic digits and decorated punctuation cannot hide a phrase
    from the filter.

    A rejection here throws away audio the user actually spoke, so the filter
    is deliberately conservative: only the phrases in
    :data:`WHOLE_TRANSCRIPT_ONLY` need an exact match, and everything else must
    appear as a *contiguous* fragment (which is how Whisper wraps boilerplate
    around the phrase it hallucinates).
    """
    if not phrases:
        return False
    candidate = normalize_for_eval(text).strip(_PHRASE_TRIM)
    if not candidate:
        return False
    for phrase in phrases:
        needle = normalize_for_eval(phrase).strip(_PHRASE_TRIM)
        if not needle:
            continue
        if candidate == needle:
            return True
        if normalized_phrase(phrase) in WHOLE_TRANSCRIPT_ONLY:
            continue
        if needle in candidate:
            return True
    return False


class LocalAudio:
    """Local Whisper (faster-whisper/CTranslate2) adapter, files only, no downloads."""

    def __init__(self, settings: Settings, metrics: Any = None) -> None:
        self.settings = settings
        self.metrics = metrics if metrics is not None else Metrics(enabled=False)
        self.model: Any = None
        self._lock = threading.Lock()
        # Decoding is *not* thread-safe in CTranslate2 and one model is now
        # shared by every LAN connection (MODEL_POOL), so two concurrent
        # transcriptions would race.  Serialise them on a separate lock: the
        # load lock above must stay free while a decode is running, otherwise a
        # second connection would block the first from even loading.
        self._decode_lock = threading.Lock()
        self.loaded_at: float | None = None
        self.last_options: dict[str, Any] = {}

    def validate(self) -> None:
        path = Path(self.settings.asr_model_path)
        if not path.is_dir() or not (path / "model.bin").is_file():
            raise VoiceError("مدل Whisper محلی موجود نیست؛ PVAI_ASR_MODEL_PATH را تنظیم کنید.")
        tts_path = Path(self.settings.tts_model_for(self.settings.tts_voice))
        if not tts_path.is_file():
            raise VoiceError(
                "مدل Piper موجود نیست؛ PVAI_TTS_MODEL_PATH یا مسیر صدای انتخاب‌شده را تنظیم کنید."
            )
        if not Path(str(tts_path) + ".json").is_file():
            raise VoiceError("فایل تنظیمات مدل Piper (.onnx.json) موجود نیست.")

    # ------------------------------------------------------------- model lifecycle
    def warmup(self) -> None:
        """Load the Whisper model once at startup instead of on the first utterance.

        The old pipeline created the model lazily inside the first ASR call, so
        the first user utterance paid the full model-load latency (seconds on a
        two-core laptop) and looked like "the assistant did not hear me".
        """
        np = dependency("numpy")
        silence = np.zeros(int(SAMPLE_RATE * 0.2), dtype=np.float32)
        started = time.perf_counter()
        self._transcribe(silence)
        if self.metrics is not None:
            self.metrics.observe("asr_model_load_ms", (time.perf_counter() - started) * 1000)

    def _ensure_model(self) -> Any:
        if self.model is not None:
            return self.model
        with self._lock:
            if self.model is None:
                whisper = dependency("faster_whisper")
                options = {
                    "device": self.settings.asr_device,
                    "compute_type": self.settings.asr_compute_type,
                    "local_files_only": True,
                    "cpu_threads": self.settings.asr_threads,
                }
                self.last_options = options
                try:
                    self.model = whisper.WhisperModel(self.settings.asr_model_path, **options)
                except Exception as exc:
                    raise VoiceError(
                        "بارگذاری مدل Whisper ناموفق بود؛ قالب CTranslate2 و "
                        "PVAI_ASR_DEVICE/PVAI_ASR_COMPUTE_TYPE را بررسی کنید."
                    ) from exc
                self.loaded_at = time.time()
            return self.model

    def record(self, destination: Path) -> None:
        sd = dependency("sounddevice")
        try:
            samples = sd.rec(int(self.settings.voice_record_seconds * 16000),
                             samplerate=16000, channels=1, dtype="int16", blocking=True)
            with wave.open(str(destination), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16000)
                output.writeframes(samples.tobytes())
        except KeyboardInterrupt:
            sd.stop()
            raise
        except Exception as exc:
            raise VoiceError("ضبط ناموفق بود؛ میکروفون و مجوز دسترسی را بررسی کنید.") from exc

    def transcribe(self, source: Path) -> str:
        return self._transcribe(str(source))

    def transcribe_pcm(self, pcm: bytes, token: threading.Event | None = None, job: Any = None) -> str:
        """Decode an in-memory PCM window; never write live microphone audio to disk.

        Partial windows are bounded by ``PVAI_ASR_PARTIAL_WINDOW_S`` in the
        pipeline; the final decode receives the complete utterance audio, so
        nothing is lost for accuracy while per-decode cost stays predictable.
        """
        np = dependency("numpy")
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        return self._transcribe(samples, token=token)

    def _transcribe_options(self) -> dict[str, Any]:
        """The ``transcribe`` keyword arguments, built from settings.

        ``language`` used to be hard-coded to ``"fa"``, which is right for this
        project but wrong for a Persian/English speaker who switches mid
        sentence, and impossible to override without editing code.
        ``PVAI_ASR_LANGUAGE=auto`` passes ``None`` to Whisper for detection.
        """
        options: dict[str, Any] = {
            "language": self.settings.asr_language if self.settings.asr_language not in ("auto", "") else None,
            "vad_filter": self.settings.asr_vad_filter,
            "beam_size": self.settings.asr_beam_size,
            "condition_on_previous_text": False,
            "no_speech_threshold": self.settings.asr_no_speech_threshold,
            "log_prob_threshold": self.settings.asr_log_prob_threshold,
            "compression_ratio_threshold": self.settings.asr_compression_ratio_threshold,
        }
        prompt = (self.settings.asr_initial_prompt or "").strip()
        if prompt:
            # Seeded text biases the *script* and the vocabulary: without it
            # Whisper sometimes answers a Persian clip in Arabic letters.
            options["initial_prompt"] = prompt
        return options

    def _accepted_segments(self, segments: Iterable[Any], token: threading.Event | None = None) -> list[Any]:
        """Drop the segments that are Whisper talking to itself.

        On silence or noise Whisper does not return nothing: it returns a
        plausible sentence ("زیرنویسها توسط تیم ما تهیه شده است", "ممنون از
        تماشای شما").  A non-empty transcript starts a whole LLM turn, so a
        hallucination becomes a confident answer to a question nobody asked.
        The two uncertainty scores must agree; other rejection signals are independent:

        * ``no_speech_prob``  — high *together with* low avg_logprob;
        * ``avg_logprob``     — low *together with* high no_speech_prob;
        * ``compression_ratio`` — the classic repetition loop;
        * a phrase list, matched after Persian normalisation.
        """
        threshold = self.settings.asr_max_no_speech_prob
        log_prob_min = self.settings.asr_log_prob_threshold
        ratio_max = self.settings.asr_compression_ratio_threshold
        phrases = self._hallucination_phrases()
        accepted: list[Any] = []
        for segment in segments:
            if token is not None and token.is_set():
                raise AsrPreempted()
            text = (getattr(segment, "text", "") or "").strip()
            if not text:
                continue
            no_speech = getattr(segment, "no_speech_prob", None)
            log_prob = getattr(segment, "avg_logprob", None)
            ratio = getattr(segment, "compression_ratio", None)
            reason = None
            if (no_speech is not None and no_speech >= threshold
                    and log_prob is not None and log_prob < log_prob_min):
                reason = "no_speech_and_low_logprob"
            elif ratio is not None and ratio > ratio_max:
                reason = "compression_ratio"
            elif _is_hallucination(text, phrases):
                reason = "phrase"
            if reason is not None:
                self.metrics.increment("asr_segments_rejected")
                logger.info("asr segment rejected (%s): %r", reason, text[:80])
                continue
            accepted.append(segment)
        return accepted

    def _hallucination_phrases(self) -> tuple[str, ...]:
        raw = self.settings.asr_hallucination_phrases or ""
        return tuple(
            part.strip()
            for part in raw.split(",")
            if part.strip()
        )

    def _transcribe(self, source: Any, token: threading.Event | None = None) -> str:
        try:
            if token is not None and token.is_set():
                raise AsrPreempted()
            model = self._ensure_model()
            with self._decode_lock:
                if token is not None and token.is_set():
                    raise AsrPreempted()
                segments, _ = model.transcribe(source, **self._transcribe_options())
                # The generator must be consumed *inside* the lock: faster-whisper
                # decodes lazily as it is iterated.  Filtering happens while
                # consuming, so a hallucinated segment never reaches the caller.
                accepted = self._accepted_segments(segments, token=token)
                return " ".join(segment.text.strip() for segment in accepted).strip()
        except (VoiceError, AsrPreempted):
            raise
        except Exception as exc:
            raise VoiceError("تشخیص گفتار ناموفق بود؛ مدل محلی Whisper را بررسی کنید.") from exc

    def speak(self, text: str, destination: Path) -> None:
        try:
            subprocess.run(
                [sys.executable, "-m", "piper", "--model",
                 self.settings.tts_model_for(self.settings.tts_voice),
                 "--output_file", str(destination)],
                input=text, text=True, encoding="utf-8", capture_output=True,
                timeout=self.settings.request_timeout_s, check=True,
            )
            sd = dependency("sounddevice")
            sf = dependency("soundfile")
            samples, rate = sf.read(str(destination), dtype="float32")
            sd.play(samples, rate, blocking=True)
        except KeyboardInterrupt:
            dependency("sounddevice").stop()
            raise
        except Exception as exc:
            raise VoiceError("تولید یا پخش صدا ناموفق بود؛ مدل Piper و بلندگو را بررسی کنید.") from exc


class VoiceSession:
    def __init__(self, client: Any, audio: Any) -> None:
        self.client = client
        self.audio = audio
        self.history: list[dict[str, str]] = []

    def turn(self, source: Path, output: Path) -> dict[str, Any]:
        started = time.perf_counter()
        text = self.audio.transcribe(source)
        asr_ms = round((time.perf_counter() - started) * 1000, 2)
        if not text:
            raise VoiceError("گفتاری تشخیص داده نشد؛ دوباره تلاش کنید.")
        messages = self.history[-10:] + [{"role": "user", "content": text}]
        try:
            response = self.client.post("/v1/chat/completions", json={"messages": messages, "stream": False})
            assert_local_api_authorized(response)
            response.raise_for_status()
            data = response.json()
            reply = data["choices"][0]["message"]["content"]
            if not isinstance(reply, str) or not reply.strip():
                raise ValueError("Empty reply")
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            raise VoiceError("پاسخ معتبر از مدل دریافت نشد؛ API و مدل فارسی را بررسی کنید.") from exc
        self.history = (messages + [{"role": "assistant", "content": reply}])[-12:]
        print(f"شما: {text}\nدستیار: {reply}")
        playback = time.perf_counter()
        self.audio.speak(reply, output)
        return {"asr_ms": asr_ms, "llm": data.get("timings", {}),
                "tts_and_playback_ms": round((time.perf_counter() - playback) * 1000, 2),
                "total_ms": round((time.perf_counter() - started) * 1000, 2)}


def main() -> int:
    parser = argparse.ArgumentParser(description="دستیار صوتی آفلاین PC")
    parser.add_argument("--turn-based", action="store_true", help="ضبط ثابت قدیمی با Enter")
    parser.add_argument("--list-devices", action="store_true", help="فهرست دستگاه‌های صوتی")
    args = parser.parse_args()
    try:
        settings = Settings.from_env()
        if args.list_devices:
            print(dependency("sounddevice").query_devices())
            return 0
        if not args.turn_based:
            from .live_voice import run
            run(settings)
            return 0
        audio = LocalAudio(settings)
        audio.validate()
        with httpx.Client(base_url=local_api_url(settings), headers=local_client_headers(settings), timeout=settings.request_timeout_s,
                          trust_env=False, follow_redirects=False) as client:
            session = VoiceSession(client, audio)
            print("Enter: ضبط نوبتی | /پاک: حذف تاریخچه | /خروج: خروج | Ctrl+C: توقف")
            while True:
                command = input("آماده‌اید؟ ").strip()
                if command in {"/خروج", "/exit"}:
                    break
                if command in {"/پاک", "/clear"}:
                    session.history.clear()
                    continue
                if command:
                    continue
                try:
                    with tempfile.TemporaryDirectory(prefix="pvai-") as directory:
                        source, output = Path(directory) / "input.wav", Path(directory) / "reply.wav"
                        print(f"صحبت کنید؛ ضبط {settings.voice_record_seconds:g} ثانیه است…")
                        audio.record(source)
                        print(json.dumps(session.turn(source, output), ensure_ascii=False))
                except VoiceError as exc:
                    print(str(exc), file=sys.stderr)
    except VoiceError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nپایان نشست.")
    except Exception:
        print("راه‌اندازی صوت ناموفق بود؛ دستگاه صوتی و راهنمای PC را بررسی کنید.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    # Keep VoiceError identity shared with live_voice when invoked via -m.
    from .voice import main as entrypoint
    raise SystemExit(entrypoint())
