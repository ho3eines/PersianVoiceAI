# AGENTS.md — نقطهٔ ورود هر مدل/عامل به پروژهٔ Persian Voice AI

> این فایل «قرارداد کار» است. هر مدل، عامل کدنویس یا توسعه‌دهندهٔ انسانی که به این مخزن وصل می‌شود،
> باید پیش از هر تغییری این فایل را بخواند و سپس مستند کامل پروژه را در
> [`docs/SPEC.md`](docs/SPEC.md) ببیند. وضعیت واقعی کد در [`docs/STATUS.md`](docs/STATUS.md) است.

---

## ۱. پروژه در یک نگاه

دستیار صوتی فارسی **کاملاً آفلاین** در شبکهٔ داخلی (LAN):

- **ESP32-S3** = ترمینال صوتی (میکروفون I2S، پخش I2S، GPIO/رله/سنسور، Wi-Fi).
- **PC** = میزبان پردازش سنگین (FastAPI/WebSocket، VAD/AEC، ASR، LLM، TTS، Command Broker).
- **هیچ صدا یا داده‌ای به Cloud یا API اینترنتی فرستاده نمی‌شود.** این خط قرمز پروژه است.

### وضعیت فعلی (واقعیت کد، نه هدف)

| بخش | وضعیت |
|---|---|
| هستهٔ متنی محلی: FastAPI + llama.cpp (GGUF) + CLI فارسی | **پیاده‌شده و تست‌شده** (درس ۱) |
| بنچمارک فارسی (TTFT، tok/s، زمان کل) | اجراشده روی MiniCPM؛ جزئیات در STATUS |
| ESP32-S3 (I2S، پخش، RMS) | کد V1-A **در این مخزن نیست** (خارج از مخزن نگه داشته می‌شود) و روی سخت‌افزار واقعی build/تست نشده |
| صوت PC | ورودی پیوسته، VAD با هیسترزیس/پیش‌رول، صف final بدون‌افت، partial هم‌پوشان، async SSE، Piper تدریجی؛ تست خودکار سبز، تست واقعی لازم |
| AEC روی PC | SpeexDSP با مرجع duplex؛ تست مصنوعی موفق، تست دستگاه واقعی لازم |
| ماشین حالت و barge-in | صریح و تست‌شده (`tests/test_state_machine.py`, `tests/test_utterance_lifecycle.py`) |
| حافظهٔ مکالمه | محلی/JSONL، برش توکنی، پنجرهٔ RAM محدود، وضعیت interrupted |
| readiness / سنجه‌ها | `/ready` کامل + `server/metrics.py` با تست |
| بنچمارک و ارزیابی فارسی | `tools/benchmark_voice.py` و `benchmarks/persian_eval.py`؛ اعداد واقعی هدف NOT MEASURED |
| Wi-Fi/WebSocket صوتی ESP32 | transport سرور پیاده‌شده (`server/ws_voice.py`)؛ firmware پیاده‌نشده |
| IoT Command Broker، احراز هویت دستگاه، OTA | پیاده‌نشده |

جزئیات و فهرست فایل‌ها: [`docs/STATUS.md`](docs/STATUS.md).

---

## ۲. قوانین تغییر (non-negotiable)

1. **معماری تثبیت‌شده را بدون دستور صریح کاربر تغییر نده.** اگر تغییری لازم است، اول به‌صورت
   «گزینه + مزایا + معایب + اثر روی اجزا» مطرح کن، بعد اجرا.
2. **آفلاین بمان.** هیچ وابستگی‌ای که صدا/متن کاربر را به سرویس بیرونی بفرستد اضافه نکن
   (بدون مجوز صریح). llama.cpp محلی، ASR/TTS محلی.
3. **خروجی LLM هرگز مستقیم به GPIO/رله نمی‌رود.** مسیر اجباری:
   `ASR → LLM Intent → Structured Command → Command Broker → اعتبارسنجی/مجوز → دستگاه → بازخورد → پاسخ صوتی`.
4. **رمز و کلید در مخزن نباشد.** `.env`، مدل‌ها، داده‌های مکالمه در `.gitignore` هستند؛ آن‌ها را commit نکن.
5. **هر تغییر رفتار = تست.** تغییر در `server/` بدون تست در `tests/` کامل نیست.
6. **مستندات را هم‌گام نگه دار.** اگر وضعیت اجرایی تغییر کرد، `docs/STATUS.md` را به‌روز کن؛
   اگر تصمیم معماری عوض شد، `docs/SPEC.md` را با تأیید کاربر ویرایش کن.
7. **ادعای تست‌نشده = ممنوع.** «روی سخت‌افزار تست شد» فقط وقتی نوشته می‌شود که واقعاً اجرا شده باشد.

---

## ۳. نقشهٔ مخزن

```
PersianVoiceAI/
├─ AGENTS.md              ← همین فایل: قرارداد کار هر عامل
├─ README.md              ← معرفی پروژه + راه‌اندازی سریع درس ۱
├─ .env.example           ← قالب متغیرهای محیطی
├─ LICENSE                ← GPL-3.0-or-later (اجباری به‌خاطر piper-tts)
├─ THIRD_PARTY.md         ← مجوز وابستگی‌ها و هر مدل/صدا
├─ requirements.txt       ← فقط زمان اجرا
├─ requirements-dev.txt   ← pytest (فقط توسعه)
├─ requirements-voice.txt ← پشتهٔ صوتی اختیاری
├─ configs/               ← نمونهٔ پیکربندی داده و آموزش (JSON + YAML)
├─ docs/
│  ├─ PROJECT_GUIDE.md    ← راهنمای یکپارچه؛ تولید با tools/build_docs.py
│  ├─ DATASETS.md         ← دادهٔ سفارشی، پیکربندی JSON/YAML، قرارداد trainer خارجی
│  ├─ SPEC.md             ← مستند جامع v1.0 (مرجع تصمیم‌ها)
│  ├─ STATUS.md           ← وضعیت اجرایی، تست‌ها، نگاشت ۲۰ الزام به تست
│  ├─ ISSUE_REGISTER.md   ← دفترچهٔ ایرادها: ریشه، رفع، تست، وضعیت
│  ├─ STREAMING_ARCHITECTURE.md ← utterance/generation، صف‌ها، ماشین حالت، لغو
│  ├─ CONFIGURATION.md    ← مرجع کامل متغیرهای PVAI_*
│  ├─ AEC.md              ← SpeexDSP، تأخیر، چک‌لیست
│  ├─ PROTOCOL.md         ← قرارداد WebSocket ESP32-S3 + transport سرور
│  ├─ BENCHMARKS.md       ← روش‌شناسی و نتایج برچسب‌دار
│  ├─ TROUBLESHOOTING.md  ← پیام‌های خطا و راه‌حل
│  ├─ PC_VOICE.md         ← نصب و اجرای صوت PC
│  ├─ PLATFORMS.md        ← Windows/Linux، اسکریپت‌ها، تفاوت‌ها
│  ├─ AUDIT_REPORT.md     ← گزارش بازبینی (سند تاریخی)
│  └─ HARDWARE_VALIDATION.md ← چک‌لیست تست واقعی
├─ server/                ← سرویس محلی پایتون (FastAPI)
│  ├─ main.py             ← اپ FastAPI، /ready، /api/sessions، OpenAI-compatible
│  ├─ llm.py              ← کلاینت async llama.cpp + اندازه‌گیری زمان
│  ├─ config.py           ← تنظیمات از env / .env (PVAI_*) + اعتبارسنجی
│  ├─ voice.py            ← LocalAudio (Whisper) و CLI حالت turn-based
│  ├─ live_voice.py       ← Speaker (Piper)، جمله‌بندی، پخش لغوپذیر
│  ├─ streaming_voice.py  ← Pipeline: Utterance/AssistantTurn، AsrScheduler، ماشین حالت
│  ├─ vad.py              ← EnergyVAD: هیسترزیس، پیش‌رول، کف نویز تطبیقی
│  ├─ aec.py              ← SpeexDSP + DuplexAudio (مرجع پخش واقعی)
│  ├─ audio_io.py         ← انتخاب/اعتبارسنجی دستگاه، pump و recovery
│  ├─ conversation.py     ← Message/Conversation/ConversationStore (JSONL محلی)
│  ├─ sessions.py         ← CLI بازبینی/حذف نشست
│  ├─ metrics.py          ← سنجه‌های bounded
│  ├─ resources.py        ← CPU/RAM بدون وابستگی اجباری
│  ├─ readiness.py        ← چک‌لیست startup
│  ├─ persian.py          ← نرمال‌سازی فارسی + WER/CER
│  ├─ protocol.py         ← قرارداد فریم/پیام ESP32
│  ├─ ws_voice.py         ← transport WebSocket سرور برای دستگاه LAN
│  ├─ security.py         ← توکن REST/WS، rate limit، سقف اتصال
│  ├─ rag.py              ← RAG محلی (BM25، KnowledgeBase) برای پاسخ دانش‌محور
│  ├─ cli.py              ← کلاینت چت فارسی ترمینال
│  ├─ __main__.py         ← اجرای `python -m server`
│  └─ __init__.py         ← نسخهٔ سرویس
├─ tests/                 ← pytest؛ بدون نیاز به مدل (fake LLM/ASR/TTS/دستگاه)
├─ benchmarks/            ← پرامپت‌های متنی + persian_eval.py + persian_eval_set.json
├─ tools/                 ← اسکریپت‌های .ps1 و .sh + benchmark_voice.py + download_parallel.py
│                             + prepare_dataset.py / train_plan.py / training_config.py
├─ models/                ← مدل‌های محلی GGUF (خارج از git)
├─ data/                  ← مکالمه/بازخورد/دانش محلی (خارج از git)
└─ firmware/              ← (ساخته نشده) هدف: پروژهٔ ESP-IDF esp32s3_terminal
```

پوشه‌های `server/audio`، `vad`، `aec`، `asr`، `tts`، `conversation`، `playback`، `iot`، `devices`
در ساختار هدف وجود دارند ولی **هنوز ساخته نشده‌اند**. آن‌ها را فقط هنگام پیاده‌سازی همان قابلیت اضافه کن.

---

## ۴. اجرا و بررسی

هر اسکریپت `.ps1` یک معادل `.sh` دارد؛ جزئیات در [`docs/PLATFORMS.md`](docs/PLATFORMS.md).

**Windows / PowerShell**

```powershell
# یک‌بار: محیط
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env

# سرویس (مدل GGUF از قبل در models/ کپی شده)
.\tools\start_server.ps1          # یا: python -m uvicorn server.main:app --port 8000

# چت فارسی
.\tools\chat.ps1
.\tools\chat.ps1 --once "سلام! خودت را معرفی کن"

# تست‌ها (بدون مدل) — باید همیشه سبز باشد: نتیجهٔ جاری را با pytest ببینید
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest

# آمادگی سرویس و بنچمارک زنجیرهٔ صوت (برچسب MEASURED/SIMULATED/NOT MEASURED)
curl http://127.0.0.1:8000/ready
.\.venv\Scripts\python.exe tools\benchmark_voice.py --json-out benchmarks\results\voice-pc.json

# سنجش کیفیت/سرعت پاسخ
.\tools\benchmark.ps1
# ابزار قدیمی در docs/legacy بایگانی شده است
```

**Linux / macOS / bash**

```bash
# یک‌بار: محیط
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements-dev.txt
cp .env.example .env

./tools/start_server.sh
./tools/chat.sh --once "سلام! خودت را معرفی کن"
./.venv/bin/python -m pytest
./tools/benchmark.sh
```

(`PVAI_PYTHON=/path/to/python` اگر venv جای دیگری است.)

**تعریف «انجام‌شده» برای هر تغییر:** `pytest` سبز + در صورت تغییر رفتار واقعی، یک اجرای دستی
مستندشده + به‌روزرسانی `docs/STATUS.md`.

---

## ۵. قراردادهای کد

- **پایتون ۳.۱۲**، تایپ‌هینت کامل (`from __future__ import annotations`)، بدون وابستگی سنگین جدید
  بدون دلیل. `async` برای I/O مدل و شبکه.
- **زبان:** پاسخ‌ها و متن کاربر‌پسند **فارسی**؛ نام متغیر/تابع/فایل و پیام‌های لاگ فنی **انگلیسی**.
- **پیکربندی:** فقط از طریق `Settings.from_env()` و متغیرهای `PVAI_*`. مقدار پیش‌فرض
  داخل کد، override از `.env` یا محیط.
- **خطاها:** خطاهای دامنه‌ای (`ModelNotLoaded`, `GenerationError`, `LlamaCppError`) به کد HTTP
  نگاشت می‌شوند (503/502) و پیام فارسی قابل‌فهم دارند. هیچ استثنای خام به کلاینت نشت نکند.
- **API:** سازگاری با OpenAI در `/v1/chat/completions` حفظ شود؛ کلاینت‌های آینده (ESP32، وب) از همین
  مسیر استفاده می‌کنند. افزودن فیلد با `extra="ignore"` باشد تا کلاینت‌های قدیمی نشکنند.
- **تست:** مدل با fake جایگزین می‌شود؛ تست‌ها نباید به llama.cpp یا شبکه وابسته باشند.
- **زمان‌سنجی:** هر مسیر پاسخ‌دهی باید TTFT/زمان کل/tok/s را برگرداند؛ این داده‌ها ورودی تصمیم‌های
  ASR/TTS/Barge-in در آینده‌اند.

---

## ۶. قرارداد صوت و شبکه (هدف معماری)

- ورودی: PCM، mono، **16 kHz، 16-bit** → ۳۲ KB/s.
- خروجی پیشنهادی: PCM، mono، **24 kHz، 16-bit** (وابسته به TTS نهایی).
- فریم ۲۰–۴۰ ms → ۶۴۰ یا ۱۲۸۰ بایت.
- انتقال: **WebSocket، فریم باینری**؛ BLE فقط Pairing/تنظیمات/کنترل سبک (Bluetooth Classic پشتیبانی نمی‌شود).
- پیام‌های کنترلی: `HELLO`, `DEVICE_INFO`, `SESSION_START`, `INPUT_END`, `PLAY_START`, `PLAY_END`,
  `PLAY_STOP`, `SET_VOLUME`, `SET_VOICE`, `PING/PONG`, `ERROR`.
- شناسه‌ها: `device_id` (هر دستگاه)، `session_id` (هر مکالمه)، `stream_id` (هر جریان) جداگانه.

---

## ۷. نقشهٔ راه (خلاصه)

`V1-A` میکروفون/آمپلی‌فایر/RMS ← `V1.1` Wi-Fi + WebSocket PCM ← `V1.2` ASR فارسی ←
`V1.3` LLM + مدیریت مکالمه ← `V1.4` TTS فارسی (مرد/زن، Streaming) ← `V1.5` VAD/AEC/Barge-in ←
`V1.6` GPIO/رله/سنسور ← `V2` چنددستگاهی، امنیت کامل، محصول تجاری.

جزئیات، معیارهای انتخاب مدل‌ها و ریسک‌ها: [`docs/SPEC.md`](docs/SPEC.md).

---

## ۸. نکات سخت‌افزاری که نباید اشتباه شود

- بلندگو **فقط بین SPK+ و SPK-**؛ هیچ سر بلندگو به GND وصل نشود (خروجی MAX98357A از نوع BTL است).
- زمین مشترک بین آمپلی‌فایر، برد و منبع ۵ ولت.
- پیش از روشن‌کردن، پین‌آوت برد واقعی (ESP32-S3 N16R8) بررسی شود؛ پین‌های پیشنهادی:
  INMP441 → `SCK=4, WS=5, SD=6`، MAX98357A → `BCLK=7, LRC=15, DIN=16`.
- در تست اول میکروفون از بلندگو فاصله داشته باشد تا فیدبک/نویز تست را خراب نکند.
## معماری PC Streaming (جایگزین مسیر قبلی)

با درخواست صریح کاربر معماری async actor-based شد. مرجع: `docs/STREAMING_ARCHITECTURE.md`.
Partial ASR نباید فرمان اجرا کند؛ generation قدیمی حق انتشار/پخش/ثبت تاریخچه ندارد.
Whisper افزایشی با snapshot را با مدل ذاتاً streaming یکسان توصیف نکنید.

AEC مرجع `docs/AEC.md`: مرجع فقط از PCM ارسال‌شده در callback duplex گرفته شود؛
نبود کتابخانهٔ native نباید باعث bypass خاموش شود. AEC قبل از VAD/ASR اعمال می‌شود.


---

## نکات تازهٔ بازبینی (الزامی برای هر عامل بعدی)

1. **برچسب‌گذاری اندازه‌گیری:** هر عدد در مستندات باید `MEASURED` / `SIMULATED` /
   `NOT MEASURED` داشته باشد. `SIMULATED` را هرگز «کارایی» ننامید. مرجع: `docs/BENCHMARKS.md`.
2. **مالکیت صف‌ها:** `AsrScheduler.finals` بدون‌افت است؛ هیچ لغو دستیار اجازهٔ دست‌زدن به آن را ندارد.
   فقط shutdown می‌تواند کنارش بگذارد و باید اعلام کند. تست: `tests/test_asr_scheduler.py`.
3. **`AsrScheduler.submit_*` باید روی thread حلقهٔ asyncio صدا زده شود.** اگر روزی از thread دیگر
   صدا زدید، `loop.call_soon_threadsafe` لازم است؛ در غیر این صورت lost wakeup برمی‌گردد.
4. **هیچ ادعای «تست سخت‌افزاری» بدون ثبت در `docs/HARDWARE_VALIDATION.md`.** الگوی ثبت در
   بخش ۸ همان سند است.
5. **متن در برابر صوت:** متن مکالمه با `PVAI_HISTORY_PERSIST` روی دیسک محلی می‌ماند؛ صوت نه.
   اگر قابلیت جدیدی متن را جایی خارج از `PVAI_DATA_DIR` می‌نویسد، همان‌جا مستند و قابل حذف کنید.
6. **firmware ESP32 هنوز وجود ندارد** (`/api/protocol` → `transport_implemented: true`,
   `firmware_implemented: false`). تا پیاده‌سازی
   transport، در README/STATUS «پیاده‌شده» نوشته نشود.


## هم‌گام‌سازی مستندات

نقطهٔ ورود یکپارچه: [docs/PROJECT_GUIDE.md](docs/PROJECT_GUIDE.md).
این فایل از راهنماهای موجود تولید می‌شود؛ پس از ویرایش منبع،
`python tools/build_docs.py` و `python tools/build_docs.py --check` را اجرا کنید.
ابزار `tools/prepare_dataset.py` (ورودی JSON/JSONL/CSV یا Hugging Face `save_to_disk`)
و `tools/train_plan.py` فقط اعتبارسنجی پیکربندی و داده‌اند؛ **trainer نداریم**. هر
hyper-parameter جدید (يا مسیر) باید در `configs/*.example.*` و `docs/DATASETS.md`
مستند شود و از `tools/training_config.py` رد شود، نه داخل اسکریپت hardcode.
