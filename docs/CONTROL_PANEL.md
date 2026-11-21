# پنل مدیریت — قرارداد API و معماری

> این سند **قرارداد** است: مسیرها، شکل پاسخ و مرزهای ایمنی. پیاده‌سازی در
> `server/manage_*.py` و رابط کاربری در `web/control/` است.
> صفحهٔ میز تست یک‌نوبتی (`web/index.html`) دست‌نخورده می‌ماند؛ پنل مدیریت
> جداگانه و در همان اپ loopback سرو می‌شود.

## ۱. جای استقرار و مرز ایمنی

| موضوع | تصمیم |
|---|---|
| اپ میزبان | همان اپ `server/webui.py::create_app` (فقط loopback، middleware موجود) |
| مسیر UI | `GET /control` و `GET /control/` → `web/control/index.html` |
| فایل‌های ایستا | `GET /control/app.js` و `GET /control/styles.css` (و معادل `/control/assets/<file>`) با هدر `Cache-Control: no-cache` |
| تم و فونت مشترک | `GET /assets/theme.css` و `GET /assets/fonts/Vazirmatn-var.woff2` — همان شِل و فونتِ میز گفتگو (`/`)، میزبانی محلی و بدون CDN |
| مسیر API | `/api/manage/*` (زیر همان middleware؛ هیچ مسیر جدیدی بدون بررسی loopback نیست) |
| احراز هویت اضافه | لازم نیست؛ چون اپ فقط روی loopback سرو می‌شود و peer/origin بررسی می‌شود. اگر روزی bind غیرمحلی شد، پنل باید مثل `PVAI_API_TOKEN` رفتار کند |
| کلیدهای API ابری | فقط در `data/manage/providers.json` (gitignore)، هرگز در پاسخ API برنمی‌گردند |

**خط قرمز آفلاین:** مسیر پیش‌فرض کاملاً محلی می‌ماند. افزودن provider ابری یک
**اقدام صریح کاربر** است؛ پنل باید پیش از فعال‌سازی هشدار بدهد که متن (و در مسیر
صوتی، رونویسی) از این رایانه بیرون می‌رود. `offline_guarantee: false` در پاسخ
`/api/manage/status` همین را نشان می‌دهد.

## ۲. ماژول‌ها (مالکیت فایل)

| فایل | مسئول | محتوا |
|---|---|---|
| `server/manage_common.py` | lead | ویرایش اتمیک `.env`، ماسک راز، مسیرهای ایمن، کاتالوگ فیلدهای `Settings` |
| `server/manage_settings.py` | backend | `router` برای `/settings` |
| `server/manage_status.py` | backend | `router` برای `/status` و `/services/*` |
| `server/manage_models.py` | integrations | `router` برای `/models/*` و `/jobs/*` |
| `server/manage_providers.py` | integrations | `router` برای `/providers/*` |
| `server/manage_api.py` | lead | ترکیب routerها + نصب روی اپ |
| `web/control/*` | frontend | HTML/CSS/JS فارسی RTL؛ بدون CDN و فونت بیرونی |

هر ماژول API این شکل را دارد:

```python
from fastapi import APIRouter
router = APIRouter(prefix="/api/manage", tags=["manage"])
```

و `server/manage_api.py` آن‌ها را با `app.include_router(...)` نصب می‌کند. هیچ ماژولی
`app` را import نمی‌کند (بدون چرخه).

## ۳. تنظیمات

### `GET /api/manage/settings`

```json
{
  "groups": [
    {
      "id": "asr",
      "title_fa": "تبدیل گفتار به متن",
      "description_fa": "…",
      "fields": [
        {
          "key": "PVAI_ASR_BEAM_SIZE",
          "label_fa": "دقت جست‌وجو (beam)",
          "help_fa": "…",
          "type": "int",
          "value": 1,
          "default": 1,
          "minimum": 1,
          "maximum": 5,
          "choices": null,
          "widget": "number",
          "secret": false,
          "restart_required": true,
          "advanced": false
        }
      ]
    }
  ],
  "env_path": "D:\\PersianVoiceAI\\.env",
  "env_issues": [],
  "restart_required": ["PVAI_ASR_BEAM_SIZE"]
}
```

قواعد:

- `value` مقدار **مؤثر فعلی** است (از `Settings.from_env()`)، نه فقط آنچه در `.env` نوشته شده.
- فیلدهای `secret` (مثل توکن‌ها) همیشه `value: null` و `is_set: true|false` دارند.
- `widget` یکی از `text|textarea|number|switch|select|path|secret`.
- `restart_required` یعنی تغییرش تا ری‌استارت سرویس اثر نمی‌کند؛ پنل باید بج نشان دهد.

### `PUT /api/manage/settings`

```json
{ "values": { "PVAI_ASR_BEAM_SIZE": 3, "PVAI_ASR_HOTWORDS": "رله,سنسور" }, "apply": true }
```

پاسخ: `{ "changed": ["PVAI_ASR_BEAM_SIZE"], "restart_required": [...], "env_issues": [], "applied_live": [...] }`

قواعد سخت:

- فقط کلیدهای شناخته‌شده (`PVAI_*` موجود در `Settings`) پذیرفته می‌شوند؛ کلید ناشناخته → `400`.
- نوشتن `.env` **اتمیک** است و کامنت‌ها/ترتیب/کلیدهای ناشناختهٔ موجود حفظ می‌شوند
  (`manage_common.update_env_file`).
- مقدار نامعتبر (خارج از بازه، عدد نامفهوم) → `422` با پیام فارسی، و **هیچ نوشتنی** انجام نمی‌شود.
- مقدار خالی برای رشته‌ها مجاز است (همان قرارداد `allow_empty`).

### `POST /api/manage/settings/reset`

`{ "keys": ["PVAI_ASR_BEAM_SIZE"] }` → حذف همان خط از `.env` (بازگشت به پیش‌فرض کد).

## ۴. وضعیت

### `GET /api/manage/status`

```json
{
  "server": { "ready": true, "uptime_s": 421.5, "python": "3.12.8", "host": "127.0.0.1", "port": 8100 },
  "offline_guarantee": true,
  "active_provider": "local",
  "components": {
    "llm":    { "state": "ready|loading|error|unconfigured", "detail": "…", "model": "Qwen3.5-4B-Q4_K_M" },
    "asr":    { "state": "ready", "detail": "…", "model": "whisper-small" },
    "tts":    { "state": "ready", "detail": "…", "model": "fa_IR.onnx" },
    "aec":    { "state": "ready", "detail": "…" },
    "audio":  { "state": "unknown", "detail": "…" }
  },
  "services": [
    {
      "id": "llama-server",
      "label_fa": "سرور مدل زبانی (llama.cpp)",
      "state": "running|stopped|starting|failed|unknown",
      "managed": true,
      "pid": 12345,
      "port": 8080,
      "started_at": 1730000000.0,
      "health": { "ok": true, "detail": "…" },
      "last_error": null
    }
  ],
  "restart_required": ["PVAI_ASR_BEAM_SIZE"]
}
```

### `POST /api/manage/services/{service_id}/{action}`

- `service_id` ∈ `llama-server` | `voice` | `api`
- `action` ∈ `start` | `stop` | `restart`

پاسخ: `{ "id": "...", "state": "...", "detail": "..." }`

قواعد سخت:

- **فقط فرایندهای فرزند خودِ همین اپ** متوقف می‌شوند (`Popen` ثبت‌شده). هیچ `kill`
  روی PID دلخواه، هیچ `taskkill /IM` و هیچ اجرای shell انجام نمی‌شود.
- سرویسی که این اپ مدیریتش نمی‌کند (`managed: false`) فقط گزارش می‌شود؛ تلاش برای
  stop → `409` با پیام فارسی.
- `start` وقتی سرویس بالا است → همان وضعیت فعلی برمی‌گردد (idempotent).
- سرویس `managed: false` که **بالا نیست** (مثلاً voice/api پیش از اولین اجرا) دکمهٔ «شروع از پنل» دارد؛
  از آن لحظه مدیریتش با پنل است. سرویس `managed: false` که **بالا است** فقط گزارش می‌شود و
  پیام فارسی توضیح می‌دهد برای کنترل کامل باید آن را ببندید.
- اجرای llama با `Settings.llama_server_command()` ساخته می‌شود (همان قرارداد مستند).
- سلامت سرویس `api` با `GET /health` سنجیده می‌شود، نه `/ready`: چک‌لیست کامل `/ready` چند ثانیه طول
  می‌کشد و پشت تایم‌اوت کوتاه پیمایش، سرویس سالم «در دسترس نیست» گزارش می‌شد. `/health` سبک است و
  توکن هم نمی‌خواهد.
- پیش از هر `start`/`restart` مقدارهای `.env` دوباره خوانده می‌شوند (`manage_api._reload_from_env`،
  با بازگرداندن `os.environ`). بدون این کار، فعال‌سازی مدل در UI نوشته می‌شد ولی llama-server با
  مدل قبلی بالا می‌آمد.

### `GET /api/manage/services/{service_id}/logs?tail=200`

`{ "lines": ["…"], "truncated": false, "path": "logs/…" }`

## ۵. مدل‌ها

### `GET /api/manage/models?kind=llm|asr|tts`

```json
{
  "kind": "asr",
  "models_dir": "D:\\PersianVoiceAI\\models",
  "active_id": "whisper-small",
  "installed": [
    { "id": "whisper-small", "name": "whisper-small", "path": "…", "kind": "asr",
      "size_bytes": 483546902, "valid": true, "active": true, "detail": "…" }
  ],
  "catalog": [
    { "id": "whisper-large-v3-turbo", "label_fa": "…", "kind": "asr",
      "approx_size": "1.5 GB", "installed": false, "needs": "…" }
  ]
}
```

کاتالوگ برای هر سه نوع `llm`، `asr` و `tts` حداقل یک گزینه دارد (نوعِ خالی در UI به‌صورت
«گزینه‌ای گزارش نشد» دیده می‌شد). هر فایل وزن (`llm`/`tts`) با `sha256` و `size` منتشرکننده پین
شده است؛ ناهمخوانی چکیده یا حجم → دانلود کنار گذاشته می‌شود و کار `failed` گزارش می‌شود.
فایل‌های صدا با نام تخت در `models/<id>/` ذخیره می‌شوند (`<voice>.onnx` + `<voice>.onnx.json`)
و فعال‌سازی، مسیر همان فایل onnx را در `PVAI_TTS_MODEL_PATH` می‌نویسد — نه نام خالی صدا.

### `POST /api/manage/models/download`

```json
{ "kind": "llm", "catalog_id": "Qwen3-4B-Q4_K_M" }
```
یا برای نشانی دلخواه کاربر:

```json
{ "kind": "llm", "name": "my-model", "files": [{ "url": "https://…/x.gguf", "name": "x.gguf" }] }
```

پاسخ: `{ "job_id": "…" }`. کاتالوگ داخلی **فقط** نشانی‌های ثابت و مستند را می‌پذیرد
(هیچ URL از query اجرا نمی‌شود ❌). دانلود در `models/<name>/` و در صورت وجود پوشه،
`409` مگر `"overwrite": true`.

### `GET /api/manage/jobs/{job_id}`

`{ "id": "…", "state": "running|done|failed|cancelled", "bytes": 10, "total": 100, "percent": 10.0, "speed_bps": 1, "error": null, "path": "modules/…" }`

### `POST /api/manage/models/activate`

`{ "kind": "asr", "id": "whisper-small" }` → مسیر مربوطه را در `.env` می‌نویسد و
`restart_required` را برمی‌گرداند. برای `llm` مدل به llama-server فعال داده می‌شود و
نیاز به restart سرویس llama دارد.

### `DELETE /api/manage/models/{kind}/{id}`

فقط زیر `models/` و فقط اگر مدل فعال نیست (`409`). هیچ مسیری بیرون `models/` حذف نمی‌شود.

## ۶. Providerها (مدل‌های ارتباطی)

### `GET /api/manage/providers`

```json
{
  "active": "local",
  "providers": [
    { "id": "local", "kind": "llama_server", "label_fa": "مدل محلی (llama.cpp)",
      "base_url": "http://127.0.0.1:8080", "model": "Qwen3.5-4B-Q4_K_M",
      "api_key_set": false, "enabled": true, "offline": true,
      "last_test": { "ok": true, "latency_ms": 42, "detail": "…" } },
    { "id": "openai-main", "kind": "openai_compat", "label_fa": "OpenAI سازگار",
      "base_url": "https://api.example.com/v1", "model": "gpt-4o-mini",
      "api_key_set": true, "enabled": false, "offline": false,
      "last_test": null }
  ]
}
```

`kind` ∈ `llama_server` | `openai_compat` | `anthropic`.

### `PUT /api/manage/providers/{id}`

```json
{ "kind": "openai_compat", "label_fa": "…", "base_url": "https://…/v1",
  "model": "…", "api_key": "sk-…", "enabled": true }
```

- `api_key` خالی یا غایب = **همان کلید قبلی حفظ می‌شود**؛ هرگز کلید موجود در پاسخ نمی‌آید.
- `base_url` با `llama_server` باید محلی باشد (`require_local_http_url`). برای
  providerهای ابری اجازهٔ نشانی بیرونی داده می‌شود، ولی پاسخ `"offline": false` دارد.
- ذخیره اتمیک در `data/manage/providers.json` با ماسک‌شدن در همهٔ پاسخ‌ها.

### `POST /api/manage/providers/{id}/test`

`{ "ok": true, "latency_ms": 120, "detail": "…" }` — فقط یک درخواست کوچک
`/models` (یا معادل) و **بدون** ارسال متن کاربر.

### `POST /api/manage/providers/active`

`{ "id": "local" }` → provider فعال را عوض می‌کند. اگر `offline` نباشد، پاسخ شامل
`"warning_fa": "از این پس متن از رایانهٔ شما خارج می‌شود."` است.

## ۷. راهنما

راهنما داخل خود UI است (`web/control/help.js` یا بخش `<template>`)، بدون درخواست
شبکه و بدون فونت/CDN بیرونی. هر فیلد تنظیمات `help_fa` دارد؛ هر بخش یک دکمهٔ
«راهنمای این صفحه» باز می‌کند.

## ۸. آزمون و اثبات

| مورد | ابزار |
|---|---|
| قرارداد API | `tests/test_manage_settings.py`, `tests/test_manage_status.py`, `tests/test_manage_models.py`, `tests/test_manage_providers.py` |
| مرز ایمنی (loopback، مسیر پیمایش، کلید مخفی) | تست‌های همان فایل‌ها با `TestClient` محلی |
| UI | `tests/test_control_panel.py`: صفحه سرو می‌شود، هیچ منبع بیرونی (`http(s)://`) در HTML/JS نیست، شکل API مورد انتظار در JS هست |
| همه چیز سبز | `python -m pytest` |

اگر چیزی در UI فعال نیست ولی API دارد، دکمه باید غیرفعال با توضیح باشد؛ ادعای
قابلیت بدون API **ممنوع** است.
