"""``/api/manage/models`` — install, inspect, activate and remove models.

Three independent kinds share one contract:

* ``llm`` — ``*.gguf`` weight files for llama.cpp,
* ``asr`` — CTranslate2 directories (``model.bin`` + ``config.json``) for Whisper,
* ``tts`` — Piper ``*.onnx`` voices (with their ``.json`` config).

Downloads run in a background thread so the browser can poll progress.  Only
the documented catalog or explicit HTTPS URLs supplied by the operator are
accepted; a catalog entry always carries the publisher's SHA-256 and the file
is rejected when the digest does not match.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .config import PROJECT_ROOT, Settings
from .manage_common import persist_env_updates

MODEL_KINDS = ("llm", "asr", "tts")

ACTIVE_KEYS = {
    "llm": "PVAI_MODEL_PATH",
    "asr": "PVAI_ASR_MODEL_PATH",
    "tts": "PVAI_TTS_MODEL_PATH",
}

ACTIVE_ATTRS = {
    "llm": "model_path",
    "asr": "asr_model_path",
    "tts": "tts_model_path",
}

MIRROR_BASE = "https://hf-mirror.com"
"""Where model files are fetched from.

Hugging Face itself is unreachable from the project's target network, and
``hf-mirror.com`` serves the same repositories byte-for-byte, which is why the
catalog pins the publisher SHA-256: the digest — not the host — is what makes a
weight file trustworthy.
"""

_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")


@dataclass(frozen=True)
class CatalogFile:
    name: str
    url: str
    sha256: str | None = None
    size: int | None = None


@dataclass(frozen=True)
class CatalogModel:
    id: str
    kind: str
    label_fa: str
    approx_size: str
    files: tuple[CatalogFile, ...]
    needs: str = ""
    source: str = ""
    installed_by_default: bool = False
    aliases: tuple[str, ...] = ()
    config_file: str | None = None
    """Repo-relative Piper config to write next to the ``.onnx`` after download.

    Some upstream repositories publish a VITS training config instead of the
    Piper ``.onnx.json``.  When set, this file is copied to
    ``<onnx>.json`` in the destination folder so the voice can be activated.
    """


def _mirror(
    repo: str,
    name: str,
    *,
    sha256: str | None = None,
    size: int | None = None,
    filename: str | None = None,
) -> CatalogFile:
    """A file on the mirror.

    ``name`` is the path inside the repository (it may contain ``/``), while
    ``filename`` is the flat name the file is saved as under ``models/<id>/``.
    """
    return CatalogFile(
        name=filename or name,
        url=f"{MIRROR_BASE}/{repo}/resolve/main/{name}",
        sha256=sha256,
        size=size,
    )


_WHISPER_TURBO = "deepdml/faster-whisper-large-v3-turbo-ct2"

_QWEN35 = "unsloth/Qwen3.5-4B-GGUF"
_QWEN3 = "Qwen/Qwen3-4B-GGUF"
_QWEN3_TINY = "Qwen/Qwen3-0.6B-GGUF"
_PIPER = "rhasspy/piper-voices"
_MANA = "MahtaFetrat/Mana-Persian-Piper"
_MANTATTS = "kiarashQ/fa-ir-tts-piper-ar-mantatts-v1"
_HAANIYE = "csukuangfj/vits-piper-fa-haaniye_low"

CATALOG: tuple[CatalogModel, ...] = (
    CatalogModel(
        id="Qwen3.5-4B-Q4_K_M",
        kind="llm",
        label_fa="Qwen3.5 4B (پیش‌فرض پروژه)",
        approx_size="۲.۶ گیگابایت",
        needs="مدل پیش‌فرض؛ روی CPU چهاررشته حدود ۶ توکن بر ثانیه. حالت تفکر باید خاموش بماند.",
        source=_QWEN35,
        installed_by_default=True,
        files=(
            _mirror(
                _QWEN35,
                "Qwen3.5-4B-Q4_K_M.gguf",
                sha256="00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4",
                size=2740937888,
            ),
        ),
    ),
    CatalogModel(
        id="Qwen3.5-4B-UD-IQ2_XXS",
        kind="llm",
        label_fa="Qwen3.5 4B فشرده (رایانهٔ کم‌حافظه)",
        approx_size="۱.۵ گیگابایت",
        needs="برای رایانه‌هایی با رم کم؛ کیفیت پایین‌تر از Q4 است.",
        source=_QWEN35,
        files=(
            _mirror(
                _QWEN35,
                "Qwen3.5-4B-UD-IQ2_XXS.gguf",
                sha256="4c1ba794e8d6098f4fb6482b4db6e880c80b5ee0b4c64d8668afaf9541163677",
                size=1520217248,
            ),
        ),
    ),
    CatalogModel(
        id="Qwen3-4B-Q4_K_M",
        kind="llm",
        label_fa="Qwen3 4B (عمومی)",
        approx_size="۲.۴ گیگابایت",
        needs="مدل عمومی سبک؛ اعداد فارسی آن در docs/BENCHMARKS.md آمده است.",
        source=_QWEN3,
        files=(
            _mirror(
                _QWEN3,
                "Qwen3-4B-Q4_K_M.gguf",
                sha256="7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5",
                size=2497280256,
            ),
        ),
    ),
    CatalogModel(
        id="Qwen3-0.6B-Q8_0",
        kind="llm",
        label_fa="Qwen3 0.6B (خیلی سبک)",
        approx_size="۶۴۰ مگابایت",
        needs="فقط برای آزمایش سریع زنجیره؛ دقت فارسی‌اش کم است.",
        source=_QWEN3_TINY,
        files=(
            _mirror(
                _QWEN3_TINY,
                "Qwen3-0.6B-Q8_0.gguf",
                sha256="9465e63a22add5354d9bb4b99e90117043c7124007664907259bd16d043bb031",
                size=639446688,
            ),
        ),
    ),
    CatalogModel(
        id="whisper-small",
        kind="asr",
        label_fa="Whisper Small (سبک و سریع)",
        approx_size="۴۸۳ مگابایت",
        needs="کم‌دقت‌ترین گزینه؛ روی گفتار واقعی ضعیف است ولی سریع جواب می‌دهد.",
        source="Systran/faster-whisper-small",
        installed_by_default=True,
        files=(
            _mirror("Systran/faster-whisper-small", "config.json"),
            _mirror("Systran/faster-whisper-small", "model.bin"),
            _mirror("Systran/faster-whisper-small", "tokenizer.json"),
            _mirror("Systran/faster-whisper-small", "vocabulary.txt"),
        ),
    ),
    CatalogModel(
        id="whisper-large-v3-turbo",
        kind="asr",
        label_fa="Whisper Large-v3 Turbo (دقیق‌ترین)",
        approx_size="۱.۵ گیگابایت",
        needs="حدود ۵ برابر کندتر از small روی CPU، ولی خطای واژه را نصف می‌کند.",
        source=_WHISPER_TURBO,
        files=(
            _mirror(_WHISPER_TURBO, "config.json"),
            _mirror(
                _WHISPER_TURBO,
                "model.bin",
                sha256="e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da",
                size=1617884929,
            ),
            _mirror(_WHISPER_TURBO, "preprocessor_config.json"),
            _mirror(_WHISPER_TURBO, "tokenizer.json"),
            _mirror(_WHISPER_TURBO, "vocabulary.json"),
        ),
    ),
    CatalogModel(
        id="ganji-adabi",
        kind="tts",
        label_fa="صدای فارسی گنجی ادبی (پیش‌فرض)",
        approx_size="۶۱ مگابایت",
        needs="همان صدای پیش‌فرض پروژه (models/piper/fa_IR.onnx)؛ برای فعال‌سازی فایل onnx انتخاب می‌شود.",
        source=_PIPER,
        installed_by_default=True,
        aliases=("fa_IR",),
        files=(
            _mirror(
                _PIPER,
                "fa/fa_IR/ganji_adabi/medium/fa_IR-ganji_adabi-medium.onnx",
                filename="fa_IR-ganji_adabi-medium.onnx",
                sha256="e9073b41ae65759dcf95778e569c8f3780406dac99549436f6ab8e7d2336ed72",
                size=63516050,
            ),
            _mirror(
                _PIPER,
                "fa/fa_IR/ganji_adabi/medium/fa_IR-ganji_adabi-medium.onnx.json",
                filename="fa_IR-ganji_adabi-medium.onnx.json",
                sha256="aa430ceebaa7c96d9cd6b1e73231a393901cabb23a1b7f53e8d85178a5ae70c9",
                size=4964,
            ),
        ),
    ),
    CatalogModel(
        id="amir",
        kind="tts",
        label_fa="صدای فارسی امیر",
        approx_size="۶۱ مگابایت",
        needs="صدای مردانهٔ medium از rhasspy/piper-voices.",
        source=_PIPER,
        files=(
            _mirror(
                _PIPER,
                "fa/fa_IR/amir/medium/fa_IR-amir-medium.onnx",
                filename="fa_IR-amir-medium.onnx",
                sha256="e8c2f4c8381bec787bec81cedf6d0b32ddeb5eaeb136cc1c554014614eefe443",
                size=63221984,
            ),
            _mirror(
                _PIPER,
                "fa/fa_IR/amir/medium/fa_IR-amir-medium.onnx.json",
                filename="fa_IR-amir-medium.onnx.json",
                sha256="fd8f253ea8b1233416d13fecc224ac72aede19e6eaf10a8ec9002dae46969130",
                size=6067,
            ),
        ),
    ),
    CatalogModel(
        id="gyro",
        kind="tts",
        label_fa="صدای فارسی ژیرو",
        approx_size="۶۱ مگابایت",
        needs="صدای مردانهٔ medium از rhasspy/piper-voices.",
        source=_PIPER,
        files=(
            _mirror(
                _PIPER,
                "fa/fa_IR/gyro/medium/fa_IR-gyro-medium.onnx",
                filename="fa_IR-gyro-medium.onnx",
                sha256="37dfae43c82ee38ca9e6aac4ffef76a74d6b282ccbc397b27761f35d355c99ba",
                size=63122309,
            ),
            _mirror(
                _PIPER,
                "fa/fa_IR/gyro/medium/fa_IR-gyro-medium.onnx.json",
                filename="fa_IR-gyro-medium.onnx.json",
                sha256="4cd0ca01824b460f490224e284f9b68ecf07f91f3c654ba3bce59d4eb7646082",
                size=7210,
            ),
        ),
    ),
    CatalogModel(
        id="mana-female",
        kind="tts",
        label_fa="صدای خانم فارسی — مانا (ایران، MIT)",
        approx_size="۶۱ مگابایت",
        needs="زنانه، فارسی ایران (Mana-TTS)، مجوز MIT؛ برای پاسخ‌های طبیعی‌تر پیشنهاد می‌شود.",
        source=_MANA,
        files=(
            _mirror(
                _MANA,
                "fa_IR-mana-medium.onnx",
                filename="fa_IR-mana-medium.onnx",
                sha256="e390c0e74ba71fd97c49ba662ee0c6e1724b462ba2d4561698af4f564840f126",
                size=63531379,
            ),
            _mirror(
                _MANA,
                "fa_IR-mana-medium.onnx.json",
                filename="fa_IR-mana-medium.onnx.json",
                sha256="d102276a05436613a05e1fbad8f125abcf8f56759aaf4fd36adf216653949997",
                size=7088,
            ),
        ),
    ),
    CatalogModel(
        id="magnoliasis-female",
        kind="tts",
        label_fa="صدای خانم فارسی — مگنولیا (مجوز نامشخص)",
        approx_size="۶۱ مگابایت",
        needs="زنانه، فارسی ایران؛ مجوز دیتاست نامشخص است و فقط برای استفادهٔ شخصی/آزمایشی پیشنهاد می‌شود.",
        source=_PIPER,
        files=(
            _mirror(
                _PIPER,
                "fa/fa_IR/magnoliasis/medium/fa_IR-magnoliasis-medium.onnx",
                filename="fa_IR-magnoliasis-medium.onnx",
                sha256="856025b3c16402e035daae3cd2a82ce36ad9c80f8a8f3d90fd101c6b6622cdec",
                size=63221984,
            ),
            _mirror(
                _PIPER,
                "fa/fa_IR/magnoliasis/medium/fa_IR-magnoliasis-medium.onnx.json",
                filename="fa_IR-magnoliasis-medium.onnx.json",
                sha256="d0590f5e99b982dedd1c3c9fb6f68224651feea7a7b735612ff383ecd8f3f897",
                size=6070,
            ),
        ),
    ),
    CatalogModel(
        id="mantatts-female",
        kind="tts",
        label_fa="صدای خانم فارسی — مانتاتس (ایران، Apache-2.0)",
        approx_size="۲۵۳ مگابایت",
        needs="زنانه، فارسی ایران (ManaTTS)، Apache-2.0؛ مدل بزرگ‌تر است و بارگذاری اولیهٔ آن بیشتر طول می‌کشد.",
        source=_MANTATTS,
        files=(
            _mirror(
                _MANTATTS,
                "fa_IR-mantatts-par.onnx",
                filename="fa_IR-mantatts-par.onnx",
                sha256="01e1749b6238de526a67cfd0b08af2a6131aff8eec831a14795344d8fbaf8df1",
                size=253646036,
            ),
            _mirror(
                _MANTATTS,
                "fa_IR-mantatts-par.onnx.json",
                filename="fa_IR-mantatts-par.onnx.json",
                sha256="87da9927492a12b91a28736415b3e8c592ef718e80cd7a2f3a8db411532814d6",
                size=10479299,
            ),
        ),
    ),
    CatalogModel(
        id="haaniye-female",
        kind="tts",
        label_fa="صدای خانم فارسی — هانیه (CC-0)",
        approx_size="۶۰ مگابایت",
        needs="زنانه، فارسی ایران (Haaniye، تبدیل‌شده از Mycroft mimic3)، CC-0؛ سبک و سریع برای CPU.",
        source=_HAANIYE,
        config_file="configs/voices/fa_IR-haaniye-medium.onnx.json",
        files=(
            _mirror(
                _HAANIYE,
                "fa-haaniye_low.onnx",
                filename="fa_IR-haaniye-medium.onnx",
                sha256="9551ec8d28eac19ca93eff73edc53f53b7cb9d55353f937f38aab1635a3e16a8",
                size=62783895,
            ),
        ),
    ),
)


@dataclass
class Job:
    id: str
    kind: str
    name: str
    total: int
    destination: Path
    state: str = "running"
    bytes: int = 0
    error: str | None = None
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    def snapshot(self) -> dict[str, Any]:
        elapsed = max(0.001, self.updated_at - self.started_at)
        return {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "state": self.state,
            "bytes": self.bytes,
            "total": self.total,
            "percent": round(min(100.0, (self.bytes / self.total * 100) if self.total else 0.0), 2),
            "speed_bps": int(self.bytes / elapsed) if self.bytes else 0,
            "error": self.error,
            "path": str(self.destination),
        }


class JobRegistry:
    """In-memory download jobs; bounded so a long session cannot leak."""

    def __init__(self, limit: int = 50) -> None:
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()
        self._limit = limit

    def add(self, job: Job) -> None:
        with self._lock:
            self._jobs[job.id] = job
            self._order.append(job.id)
            while len(self._order) > self._limit:
                self._jobs.pop(self._order.pop(0), None)

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Follow HTTPS redirects, refuse downgrades and non-HTTPS targets."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001 - stdlib signature
        parsed = urllib.parse.urlsplit(newurl)
        if parsed.scheme != "https" or not parsed.hostname:
            raise urllib.error.URLError("only HTTPS redirects are followed")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download_file(
    file: CatalogFile,
    destination: Path,
    job: Job,
    *,
    timeout: float = 60.0,
    chunk: int = 1 << 20,
) -> None:
    """Fetch one file to ``destination``; verify SHA-256 when the catalog pins it."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    opener = urllib.request.build_opener(_SafeRedirect)
    request = urllib.request.Request(
        file.url, headers={"User-Agent": "PersianVoiceAI-model-manager/1"}
    )
    digest = hashlib.sha256()
    written = 0
    with opener.open(request, timeout=timeout) as response:
        status = getattr(response, "status", 200)
        if status not in (200, 206):
            raise OSError(f"HTTP {status}")
        declared = response.headers.get("Content-Length")
        if file.size and declared and int(declared) != file.size:
            raise OSError("حجم فایل با مقدار ثبت‌شده در کاتالوگ مطابقت ندارد.")
        with temporary.open("wb") as handle:
            while True:
                if job._cancel.is_set():
                    raise OSError("download cancelled")
                block = response.read(chunk)
                if not block:
                    break
                handle.write(block)
                digest.update(block)
                written += len(block)
                job.bytes += len(block)
                job.updated_at = time.time()
            handle.flush()
    if file.sha256 and digest.hexdigest() != file.sha256:
        temporary.unlink(missing_ok=True)
        raise OSError("چکیدهٔ فایل با مقدار منتشرکننده مطابقت ندارد؛ دانلود کنار گذاشته شد.")
    if file.size and written != file.size:
        temporary.unlink(missing_ok=True)
        raise OSError("فایل ناقص دریافت شد.")
    temporary.replace(destination)


def _run_job(
    job: Job,
    files: list[CatalogFile],
    destination: Path,
    config_file: str | None = None,
) -> None:
    try:
        for file in files:
            if job._cancel.is_set():
                job.state = "cancelled"
                return
            _download_file(file, destination / file.name, job)
        if config_file:
            onnx = next(
                (file.name for file in files if file.name.lower().endswith(".onnx")),
                None,
            )
            if onnx:
                source = PROJECT_ROOT / config_file
                (destination / f"{onnx}.json").write_text(
                    source.read_text(encoding="utf-8"), encoding="utf-8"
                )
        job.state = "done"
    except Exception as exc:  # noqa: BLE001 - reported through the job record
        job.state = "failed"
        job.error = str(exc) or "خطای نامشخص در دریافت فایل"
    finally:
        job.updated_at = time.time()


def _index_models(models_dir: Path) -> dict[str, list[dict[str, Any]]]:
    """Scan ``models/`` once and bucket what is installed per kind."""
    found: dict[str, list[dict[str, Any]]] = {"llm": [], "asr": [], "tts": []}
    if not models_dir.is_dir():
        return found
    for directory in sorted(path for path in models_dir.rglob("*") if path.is_dir()):
        if (directory / "model.bin").is_file():
            size = sum(item.stat().st_size for item in directory.rglob("*") if item.is_file())
            valid = (directory / "config.json").is_file() and (
                (directory / "tokenizer.json").is_file() or (directory / "vocabulary.txt").is_file()
                or (directory / "vocabulary.json").is_file()
            )
            found["asr"].append({
                "id": directory.name,
                "name": directory.name,
                "path": str(directory.relative_to(models_dir)),
                "kind": "asr",
                "size_bytes": size,
                "valid": valid,
            })
    for path in sorted(models_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() == ".gguf":
            found["llm"].append({
                "id": path.stem,
                "name": path.name,
                "path": str(path.relative_to(models_dir)),
                "kind": "llm",
                "size_bytes": path.stat().st_size,
                "valid": True,
            })
        elif path.suffix.lower() == ".onnx":
            config = Path(str(path) + ".json")
            found["tts"].append({
                "id": path.stem,
                "name": path.name,
                "path": str(path.relative_to(models_dir)),
                "kind": "tts",
                "size_bytes": path.stat().st_size,
                "valid": config.is_file(),
            })
    return found


def installed_models(models_dir: Path, kind: str) -> list[dict[str, Any]]:
    """Public view of the installed models for one kind."""
    return _index_models(models_dir)[kind]


def catalog_installed(entry: CatalogModel, installed: list[dict[str, Any]]) -> bool:
    """Whether a catalog entry is already on disk.

    A downloaded voice keeps the publisher's file name, so the installed id is
    the file stem (``fa_IR-amir-medium``) rather than the catalog id (``amir``).
    The default Persian voice is stored under a shorter name (``fa_IR``), which
    is why entries may also declare explicit aliases.
    """
    names = {file.name for file in entry.files}
    stems = {Path(file.name).stem for file in entry.files}
    for item in installed:
        if item["id"] in (entry.id, *entry.aliases):
            return True
        if item.get("name") in names or item["id"] in stems:
            return True
    return False


def _active_id(kind: str, settings: Settings, models_dir: Path) -> str:
    raw = str(getattr(settings, ACTIVE_ATTRS[kind], "") or "").strip()
    if not raw:
        return ""
    installed = _index_models(models_dir)[kind]
    if not installed:
        return ""
    path = Path(raw)
    # The configured value may be relative to the project root (``models/x``)
    # or to the models directory itself; try both before falling back to name.
    candidates = [path] if path.is_absolute() else [PROJECT_ROOT / path, models_dir / path]
    models_root = models_dir.resolve()
    resolved_active: Path | None = None
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        try:
            resolved.relative_to(models_root)
        except ValueError:
            continue
        resolved_active = resolved
        break
    if resolved_active is not None:
        for item in installed:
            try:
                item_path = (models_dir / str(item["path"])).resolve()
            except OSError:
                continue
            if item_path == resolved_active:
                return str(item["id"])
    name = path.name if kind == "asr" else path.stem
    for item in installed:
        if item["id"] == name:
            return str(item["id"])
    return ""


class DownloadRequest(BaseModel):
    kind: str = "llm"
    catalog_id: str | None = None
    name: str | None = None
    files: list[dict[str, str]] = Field(default_factory=list)
    overwrite: bool = False


class ActivateRequest(BaseModel):
    kind: str
    id: str


def build_router(
    *,
    settings_provider: Callable[[], Settings],
    models_dir: Path,
    env_path: Path,
    jobs: JobRegistry | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/manage", tags=["manage"])
    registry = jobs or JobRegistry()

    def _catalog_entry(entry: CatalogModel, installed: bool) -> dict[str, Any]:
        return {
            "id": entry.id,
            "label_fa": entry.label_fa,
            "kind": entry.kind,
            "approx_size": entry.approx_size,
            "installed": installed,
            "needs": entry.needs,
            "source": entry.source,
            "files": [file.name for file in entry.files],
        }

    @router.get("/models")
    def list_models(kind: str = "llm") -> dict[str, Any]:
        if kind not in MODEL_KINDS:
            raise HTTPException(status_code=422, detail="نوع مدل باید llm یا asr یا tts باشد.")
        settings = settings_provider()
        installed = _index_models(models_dir)[kind]
        active = _active_id(kind, settings, models_dir)
        installed_ids = {item["id"] for item in installed}
        for item in installed:
            item["active"] = item["id"] == active
            item["detail"] = (
                "مدل فعال است."
                if item["active"]
                else ("فایل‌های مدل کامل‌اند." if item["valid"] else "فایل‌های مدل ناقص است.")
            )
        catalog = [
            _catalog_entry(entry, catalog_installed(entry, installed))
            for entry in CATALOG
            if entry.kind == kind
        ]
        return {
            "kind": kind,
            "models_dir": str(models_dir),
            "active_id": active,
            "installed": installed,
            "catalog": catalog,
            "kinds": [
                {"id": "llm", "label_fa": "مدل زبانی"},
                {"id": "asr", "label_fa": "تشخیص گفتار"},
                {"id": "tts", "label_fa": "تولید صدا"},
            ],
            "source_note_fa": f"فایل‌ها از {MIRROR_BASE} دریافت می‌شوند و چکیدهٔ SHA-256 بررسی می‌شود.",
        }

    @router.post("/models/download")
    def download_model(payload: DownloadRequest) -> dict[str, Any]:
        if payload.kind not in MODEL_KINDS:
            raise HTTPException(status_code=422, detail="نوع مدل نامعتبر است.")
        if payload.catalog_id:
            entry = next((item for item in CATALOG if item.id == payload.catalog_id), None)
            if entry is None:
                raise HTTPException(status_code=404, detail="این مدل در کاتالوگ نیست.")
            if entry.kind != payload.kind:
                raise HTTPException(status_code=422, detail="نوع مدل با کاتالوگ هم‌خوان نیست.")
            name = entry.id
            files = list(entry.files)
            total = sum(file.size or 0 for file in files)
            config_file = entry.config_file
        else:
            name = (payload.name or "").strip()
            if not _SAFE_NAME.fullmatch(name):
                raise HTTPException(
                    status_code=422,
                    detail="نام مدل باید فقط شامل حروف، عدد، نقطه، خط تیره و زیرخط باشد.",
                )
            if not payload.files:
                raise HTTPException(status_code=422, detail="حداقل یک نشانی فایل لازم است.")
            files = []
            for item in payload.files:
                url = validate_download_url(str(item.get("url", "")))
                file_name = Path(urllib.parse.urlsplit(url).path).name
                if not _SAFE_NAME.fullmatch(file_name):
                    raise HTTPException(status_code=422, detail=f"نام فایل نامعتبر است: {file_name}")
                files.append(CatalogFile(name=file_name, url=url, sha256=item.get("sha256") or None))
            total = 0
            config_file = None
        destination = (models_dir / name).resolve()
        try:
            destination.relative_to(models_dir.resolve())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="مسیر مقصد نامعتبر است.") from exc
        if destination.exists() and any(destination.iterdir()) and not payload.overwrite:
            raise HTTPException(
                status_code=409,
                detail=f"پوشهٔ «{name}» از قبل وجود دارد. برای جای‌گزینی، گزینهٔ بازنویسی را فعال کنید.",
            )
        job = Job(id=uuid.uuid4().hex[:12], kind=payload.kind, name=name, total=total, destination=destination)
        registry.add(job)
        thread = threading.Thread(
            target=_run_job, args=(job, files, destination, config_file), daemon=True
        )
        thread.start()
        return {"job_id": job.id, "name": name, "files": [file.name for file in files]}

    @router.get("/jobs/{job_id}")
    def job_status(job_id: str) -> dict[str, Any]:
        job = registry.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="کار دریافت پیدا نشد.")
        return job.snapshot()

    @router.post("/models/activate")
    def activate(payload: ActivateRequest) -> dict[str, Any]:
        if payload.kind not in MODEL_KINDS:
            raise HTTPException(status_code=422, detail="نوع مدل نامعتبر است.")
        installed = _index_models(models_dir)[payload.kind]
        item = next((entry for entry in installed if entry["id"] == payload.id), None)
        if item is None:
            raise HTTPException(status_code=404, detail="این مدل نصب نشده است.")
        if not item["valid"]:
            raise HTTPException(status_code=409, detail="فایل‌های این مدل کامل نیست و قابل فعال‌سازی نیست.")
        value = _activation_value(payload.kind, Path(item["path"]), models_dir)
        key = ACTIVE_KEYS[payload.kind]
        try:
            persist_env_updates(env_path, {key: value})
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        detail = "مدل فعال شد. برای اعمال، سرویس را دوباره راه‌اندازی کنید."
        return {"kind": payload.kind, "id": payload.id, "changed": [key],
                "restart_required": [key], "detail_fa": detail}

    @router.delete("/models/{kind}/{model_id}")
    def delete_model(kind: str, model_id: str) -> dict[str, Any]:
        if kind not in MODEL_KINDS:
            raise HTTPException(status_code=422, detail="نوع مدل نامعتبر است.")
        settings = settings_provider()
        if model_id == _active_id(kind, settings, models_dir):
            raise HTTPException(status_code=409, detail="مدل فعال را نمی‌توان حذف کرد؛ اول مدل دیگری را فعال کنید.")
        installed = _index_models(models_dir)[kind]
        item = next((entry for entry in installed if entry["id"] == model_id), None)
        if item is None:
            raise HTTPException(status_code=404, detail="این مدل پیدا نشد.")
        target = (models_dir / str(item["path"])).resolve()
        try:
            target.relative_to(models_dir.resolve())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="مسیر مدل بیرون از پوشهٔ models است.") from exc
        removed_files = 0
        if target.is_dir():
            for child in sorted(target.rglob("*"), reverse=True):
                if child.is_file():
                    child.unlink()
                    removed_files += 1
                elif child.is_dir():
                    child.rmdir()
            target.rmdir()
        elif target.is_file():
            target.unlink()
            removed_files = 1
        return {"removed": item["id"], "files": removed_files, "detail_fa": "مدل حذف شد."}

    return router


def validate_download_url(url: str) -> str:
    """Only plain HTTPS URLs without credentials or fragments are accepted."""
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise HTTPException(
            status_code=422,
            detail="فقط نشانی HTTPS بدون نام کاربری، رمز یا fragment پذیرفته می‌شود.",
        )
    return url


def _activation_value(kind: str, relative_path: Path, models_dir: Path) -> str:
    """Project-relative path for ``.env`` so ``Settings`` can resolve it.

    ``.env`` stores ``PVAI_*_MODEL_PATH`` relative to the project root
    (``models/...``), while :func:`_index_models` reports paths relative to the
    models directory.  Converting here keeps an activated model loadable on the
    next service start instead of writing a bare name that resolves to nothing.
    """
    target = (models_dir / relative_path).resolve()
    try:
        return target.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return target.as_posix()


def installed_model_ids(models_dir: Path, kind: str) -> list[str]:
    """Used by tests and the settings catalog to reference installed models."""
    return [item["id"] for item in _index_models(models_dir)[kind]]
