# راهنمای یکپارچهٔ Persian Voice AI

> این فایل خروجی خودکار `tools/build_docs.py` است؛ مستقیم ویرایش نکنید.
> اسناد منبع را اصلاح و سپس `python tools/build_docs.py` را اجرا کنید.

مرجع واحد مطالعهٔ پروژه: راه‌اندازی، وضعیت فعلی، تنظیمات، معماری، داده،
امنیت و تست. SPEC شامل اهداف آینده است؛ گزارش‌های audit و issue سابقه‌اند،
نه اثبات قابلیت فعلی. در تعارض تاریخی، کد و STATUS فعلی ملاک‌اند.

## فهرست بخش‌ها
- [بخش 1: README.md](#section-1)
- [بخش 2: docs/STATUS.md](#section-2)
- [بخش 3: docs/PC_VOICE.md](#section-3)
- [بخش 4: docs/CONFIGURATION.md](#section-4)
- [بخش 5: docs/STREAMING_ARCHITECTURE.md](#section-5)
- [بخش 6: docs/AEC.md](#section-6)
- [بخش 7: docs/PROTOCOL.md](#section-7)
- [بخش 8: docs/DATASETS.md](#section-8)
- [بخش 9: docs/PLATFORMS.md](#section-9)
- [بخش 10: docs/TROUBLESHOOTING.md](#section-10)
- [بخش 11: docs/BENCHMARKS.md](#section-11)
- [بخش 12: docs/HARDWARE_VALIDATION.md](#section-12)
- [بخش 13: THIRD_PARTY.md](#section-13)
- [بخش 14: AGENTS.md](#section-14)
- [بخش 15: docs/SPEC.md](#section-15)
- [بخش 16: docs/ISSUE_REGISTER.md](#section-16)
- [بخش 17: docs/AUDIT_REPORT.md](#section-17)

---

<a id="section-1"></a>

## بخش 1 — README.md

# Persian Voice AI

دستیار فارسی آفلاین با پردازش روی PC و هدف توسعهٔ ترمینال ESP32-S3 در LAN.
هیچ API ابری برای متن یا صوت استفاده نمی‌شود.

**[راهنمای یکپارچهٔ کامل پروژه](PROJECT_GUIDE.md)** — همهٔ راهنماها در یک فایل،
با فهرست بخش‌ها و تفکیک وضعیت فعلی از سوابق تاریخی. برای به‌روزرسانی پس از تغییر
اسناد: `python tools/build_docs.py`؛ کنترل هم‌خوانی: `python tools/build_docs.py --check`.

## مسیر فعلی PC

```text
میکروفون پیوسته (20ms) → AEC SpeexDSP → VAD با هیسترزیس و پیش‌رول
   → partial هم‌پوشان (۳s پیش‌فرض) + صف final بدون‌افت  →  Whisper محلی
   → AssistantTurn روی llama-server (async SSE)  →  جمله‌بندی تدریجی
   → Piper محلی (زن/مرد) → پخش 20ms در duplex
```

**حالت پیش‌فرض زنده است و Enter لازم ندارد.** شروع صحبت جدید پاسخ دستیار را قطع می‌کند
(barge-in) اما **هیچ‌وقت نتیجهٔ final ASR قبلی را از بین نمی‌برد** — این تفکیک، هستهٔ
`Utterance` / `AssistantTurn` / `PartialASR` / `FinalASR` است.
ASR حین صحبت با پنجره‌های هم‌پوشان اجرا می‌شود؛ این adapter افزایشی روی Whisper است،
نه مدل ذاتاً streaming. سرعت واقعی روی هدف هنوز اندازه‌گیری نشده است (NOT MEASURED).

## شروع

**Windows (PowerShell):**

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-voice.txt
Copy-Item .env.example .env
```

**Linux / macOS (bash):**

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements-voice.txt
cp .env.example .env
```

`requirements-voice.txt` فقط وابستگی‌های زمان اجراست. برای اجرای تست‌ها
`requirements-dev.txt` را هم نصب کنید (pytest فقط همان‌جاست و در deployment لازم نیست).
تست‌های تبدیل فایل صوتی همچنین به numpy، scipy، soundfile و librosa از پشتهٔ صوتی نیاز دارند.

کتابخانهٔ native **SpeexDSP** را نیز طبق [راهنمای AEC](AEC.md) نصب کنید؛ فقط pip کافی نیست.
سپس مدل‌های **Qwen3-4B-Q4_K_M GGUF**، **Whisper Small CTranslate2** و **Piper فارسی** را
محلی تهیه و مسیرشان را در `.env` تنظیم کنید. مدل‌ها دانلود خودکار نمی‌شوند.
اگر `.env` موجود است، فقط تنظیمات آن را ویرایش کنید.

هر اسکریپت نسخهٔ bash هم دارد؛ هر جا `.ps1` است، `.sh` متناظر در همان مسیر است.

```powershell
# Windows / PowerShell
# ترمینال ۱؛ پرچم‌ها از .env خوانده می‌شوند (PVAI_N_CTX / PVAI_N_THREADS / PVAI_N_GPU_LAYERS)
.\tools\start_llama.ps1 -Exe D:\Lama.cpp\bin\llama-server.exe
# ترمینال ۲
.\tools\start_server.ps1
# ترمینال ۳
.\.venv\Scripts\python.exe -m server.voice
```

```bash
# Linux / macOS
./tools/start_llama.sh --exe /opt/llama.cpp/llama-server   # یا llama-server از PATH
./tools/start_server.sh
./.venv/bin/python -m server.voice
```

> `tools/start_llama.ps1` به‌جای تکرار دستی پرچم‌ها، `python -m server.config --launch` را
> صدا می‌زند؛ پس `PVAI_N_THREADS` و `PVAI_N_GPU_LAYERS` واقعاً اعمال می‌شوند.
> اجرای دستی هم ممکن است، ولی باید با `.env` هم‌خوان باشد: پیش‌فرض کد `PVAI_N_THREADS=0`
> است (حذف `-t` و انتخاب توسط llama-server)؛ `.env.example` مقدار پیشنهادی `4` دارد.
> در اجرای دستی پرچم خودِ llama-server برنده می‌شود.

[راهنمای کامل PC، مدل‌ها، تنظیم حساسیت و محدودیت‌ها](PC_VOICE.md) ·
[مرجع همهٔ متغیرهای `.env`](CONFIGURATION.md)

## وضعیت صادقانه

- تست خودکار: **تعداد تست‌ها پویاست؛ `python -m pytest` را اجرا کنید** (بدون مدل/میکروفون/شبکه؛ علت skipها را با `python -m pytest -rs` ببینید).
  این تست‌ها منطق و کنترل جریان را ثابت می‌کنند، نه کیفیت صدا.
- مدل‌های واقعی (Whisper/Piper/Qwen) در مخزن نیستند؛ در sandbox نصب هم نبودند، پس
  ASR/TTS/LLM واقعی **NOT MEASURED** است.
- AEC با اکو/دوصدایی مصنوعی تست شده (کاهش ۵۷٫۸dB در سیگنال ساختگی)؛ اتاق و دستگاه واقعی
  تأیید نشده‌اند. بدون هدفون، کالیبراسیون `PVAI_AEC_DELAY_MS` لازم است.
- تاریخچهٔ مکالمه محلی و ماندگار است (`data/conversations/*.jsonl`)؛ پنجرهٔ RAM محدود است.
- پروتکل WebSocket صوتی ESP32-S3 و **transport سمت سرور** پیاده شده‌اند (`WS /ws/voice`، خاموش به‌صورت
  پیش‌فرض، با توکن per-device اجباری، سقف اتصال، pacing پخش و heartbeat)؛ **firmware و تست LAN واقعی
  انجام نشده‌اند** — همهٔ تست‌ها با کلاینت درون‌فرایندی و fake هستند.
- REST با `PVAI_API_TOKEN` محافظت می‌شود و روی نشانی غیر-loopback بدون توکن بالا نمی‌آید.
- اجرای CPU پیش‌فرض است؛ بنچمارک قدیمی GPU روی MiniCPM به Qwen قابل تعمیم نیست.

## API و چت متنی

| مسیر | کار |
|---|---|
| `GET /health` | فقط `{"status": "ok"}`؛ تنها مسیر **بدون** توکن و عمداً بدون جزئیات |
| `GET /ready` | چک‌لیست کامل: تنظیمات، مدل ASR/TTS، وابستگی‌ها، AEC، ذخیره‌سازی، backend (503 اگر ناقص) |
| `GET /api/protocol` | قرارداد انتقال ESP32 (`transport_implemented: true`، `firmware_implemented: false`) |
| `WS /ws/voice` | صدای دستگاه LAN (opt-in با `PVAI_WS_ENABLED` و `PVAI_WS_TOKENS`) |
| `GET /v1/models` | مدل‌های بارشده در llama-server |
| `POST /v1/chat/completions` | چت JSON یا SSE با `stream=true` |
| `POST /api/chat` | پاسخ سادهٔ یک‌پیامی با `session_id` اختیاری |
| `GET/DELETE /api/sessions[/{id}]` | بازبینی و حذف نشست‌های محلی |

اگر `PVAI_API_TOKEN` تنظیم شده باشد، مسیرهای HTTP جز `/health` و درخواست‌های OPTIONS توکن می‌خواهند: `Authorization: Bearer $PVAI_API_TOKEN`
(یا `X-API-Key`). با bind فقط loopback و توکن خالی، احراز هویت HTTP غیرفعال است.
WebSocket از توکن مستقل دستگاه استفاده می‌کند. روی `PVAI_HOST=0.0.0.0` توکن **الزامی** است وگرنه برنامه بالا نمی‌آید.

چت متنی: `python -m server.cli` یا `tools/chat.ps1`.
مستندات API: `http://127.0.0.1:8000/docs`.
FastAPI از طریق **async HTTP به llama-server** متصل می‌شود؛ `llama-cpp-python` لازم نیست.

## تست و بنچمارک

```powershell
# تست واحد/یکپارچه — بدون مدل و بدون سخت‌افزار
.\.venv\Scripts\python.exe -m pytest -q

# بنچمارک کل زنجیرهٔ صوت (هر ردیف برچسب MEASURED / SIMULATED / NOT MEASURED دارد)
.\.venv\Scripts\python.exe tools\benchmark_voice.py --json-out benchmarks\results\voice-pc.json

# ارزیابی فارسی WER/CER (۴۰ جمله، سیاست نرمال‌سازی مستند)
.\.venv\Scripts\python.exe benchmarks\persian_eval.py --list
.\.venv\Scripts\python.exe benchmarks\persian_eval.py --audio-dir recordings\fa --strict

# بنچمارک متنی قدیمی
.\tools\benchmark.ps1
./tools/benchmark.sh        # معادل bash
```

اعداد sandbox و روش‌شناسی در [BENCHMARKS](BENCHMARKS.md) است؛ اعداد i7-6500U تا اجرای
واقعی `NOT MEASURED` نوشته می‌شوند. دادهٔ خصوصی و وزن‌ها وارد Git نشوند.
وزن و ضبط خصوصی در مخزن نیست؛ مجموعهٔ متن ارزیابی و نتایج نمونه موجودند؛ مجوز کد GPL-3.0-or-later و مجوز هر مدل/صدا در
[THIRD_PARTY.md](../THIRD_PARTY.md) آمده است.

## مستندات

- [AGENTS.md](../AGENTS.md): قواعد توسعه و نقشهٔ مخزن
- [SPEC](SPEC.md): معماری و تصمیم‌ها
- [STATUS](STATUS.md): وضعیت واقعی پیاده‌سازی + نگاشت ۲۰ الزام به تست
- [ISSUE_REGISTER](ISSUE_REGISTER.md): دفترچهٔ ایرادها با ریشه، رفع و تست
- [PC_VOICE](PC_VOICE.md): نصب و اجرای صوت PC
- [STREAMING_ARCHITECTURE](STREAMING_ARCHITECTURE.md): utterance/generation، صف‌ها، ماشین حالت، لغو
- [CONFIGURATION](CONFIGURATION.md): مرجع کامل `PVAI_*`
- [AEC](AEC.md): SpeexDSP، تأخیر، چک‌لیست بلندگو/میکروفون
- [PROTOCOL](PROTOCOL.md): قرارداد WebSocket صوتی ESP32-S3 + transport سرور (firmware نشده)
- [BENCHMARKS](BENCHMARKS.md): روش‌شناسی و نتایج برچسب‌دار
- [TROUBLESHOOTING](TROUBLESHOOTING.md): پیام‌ها و راه‌حل
- [HARDWARE_VALIDATION](HARDWARE_VALIDATION.md): چک‌لیست تست واقعی
- [PLATFORMS](PLATFORMS.md): Windows/Linux، اسکریپت‌ها، تفاوت‌ها
- [THIRD_PARTY](../THIRD_PARTY.md): مجوز کد، وابستگی‌ها و هر مدل/صدا

## مجوز

کد این مخزن **GPL-3.0-or-later** است ([LICENSE](../LICENSE)). دلیل این انتخاب `piper-tts`
است که خود GPL-3.0-or-later است و in-process وارد می‌شود؛ مجوز MIT یا انحصاری برای این
مخزن با آن ناسازگار می‌شود. جزئیات و گزینه‌ها در [THIRD_PARTY.md](../THIRD_PARTY.md).

وزن مدل‌ها در مخزن نیستند و مجوز هرکدام جداست: Qwen3-4B با Apache-2.0، وزن‌های
Whisper با MIT، و صدای Piper فارسی بسته به فایل صدا متفاوت است — پیش از عرضه،
`MODEL_CARD` همان صدا را بخوانید.

### دادهٔ سفارشی و آماده‌سازی آموزش

مخزن فعلی inference/evaluation است و trainer ندارد. برای قرارداد JSON/JSONL/CSV،
اعتبارسنجی صوت محلی و روش آماده‌سازی splitهای سفارشی به
[راهنمای داده‌ها](DATASETS.md) و `python tools/prepare_dataset.py --help` مراجعه کنید.

---

<a id="section-2"></a>

## بخش 2 — docs/STATUS.md

# وضعیت اجرایی پروژه — PersianVoiceAI Conversation Engine

آخرین به‌روزرسانی: ۹ نوامبر ۲۰۲۶ — رفع باگ‌های گزارش‌شده، بازبینی مدل‌فری و مستندسازی؛
مرحلهٔ ۱ تا ۶ پیشین: رفع P0، بهبود خط لولهٔ صوتی، ماندگاری مکالمه،
ابزار بنچمارک/ارزیابی و مستندسازی. بازبینی پیشین روی commit `1db2581` انجام شده بود؛ این متن با کد working tree فعلی هم‌گام شده است؛
commit `9654a68` و شاخهٔ `arena/31347dde-persianvoiceai` فقط سابقهٔ مراحل پیشین‌اند.

این سند **وضعیت واقعی کد** است و سه سطح را از هم جدا می‌کند:

- **پیاده‌شده + تست خودکار** — با fake یا داده‌ی ساختگی در `pytest` سبز است.
- **پیاده‌شده، تست واقعی لازم** — منطق کامل است ولی بدون مدل/میکروفون/بلندگو تأیید نشده.
- **پیاده‌نشده** — نباید به‌عنوان قابلیت موجود معرفی شود.

---

## ۱. جدول وضعیت

| بخش | وضعیت | مرجع |
|---|---|---|
| هستهٔ متنی: FastAPI + llama.cpp + CLI فارسی | پیاده‌شده + تست (fake مدل) | `server/main.py`, `server/llm.py` |
| API سازگار OpenAI، JSON و SSE، لغو استریم | پیاده‌شده + تست | `tests/test_api.py` |
| ورودی میکروفون پیوستهٔ ۲۰ms، انتخاب دستگاه، recovery | پیاده‌شده + تست fake؛ دستگاه واقعی **تست نشده** | `server/audio_io.py` |
| watchdog ضد‌گیر و سهمیهٔ بازگشایی محدود (بدون حلقهٔ بی‌پایان) | پیاده‌شده + تست fake | `tests/test_audio_io.py` |
| ساعت یکسان (`monotonic`) برای watchdog و مهر فریم | پیاده‌شده + تست (اختلاف مبدأ شبیه‌سازی‌شده) | `server/aec.py`, `server/audio_io.py` |
| ریست VAD/pipeline و AEC روی هر بازگشایی stream | پیاده‌شده + تست | `reset_stream_state` + `Pipeline.reset` |
| VAD انرژی با هیسترزیس، پیش‌رول، کف نویز تطبیقی | پیاده‌شده + تست؛ صدای واقعی **تست نشده** | `server/vad.py` |
| ASR افزایشی: صف final بدون‌افت + partial قابل‌جایگزینی | پیاده‌شده + تست کامل P0 | `server/streaming_voice.py` |
| تأخیر final پشت partial: سنجهٔ تفکیک‌شده + لغو partial در حال decode | پیاده‌شده + تست SIMULATED؛ عدد دستگاه **NOT MEASURED** | `tests/test_asr_preemption.py` |
| utterance ردشدهٔ VAD: رویداد صریح، حالت درست، پاسخ نوبت قبلی حفظ می‌شود | پیاده‌شده + تست | `tests/test_short_noise_utterance.py` |
| شکست TTS/LLM/context: `failed` + خروج از `THINKING`/`SPEAKING` | پیاده‌شده + تست | `tests/test_turn_failures.py` |
| جدا شدن طول عمر utterance و generation دستیار | پیاده‌شده + تست (۶ سناریوی الزامی) | `tests/test_utterance_lifecycle.py` |
| ماشین حالت صریح `idle/listening/transcribing/thinking/speaking` | پیاده‌شده + تست جدول گذار | `tests/test_state_machine.py` |
| barge-in: توقف LLM/TTS/پخش، بدون آسیب به صف final | پیاده‌شده + تست fake؛ تأخیر دستگاه **تست نشده** | `tests/test_tts.py`, `tests/test_long_run.py` |
| TTS Piper: انتخاب مرد/زن، جمله‌بندی، لغو، نرمال‌سازی نرخ | پیاده‌شده + تست؛ وزن فارسی **تست نشده** | `server/live_voice.py` |
| AEC SpeexDSP duplex پیش از VAD/ASR | پیاده‌شده + تست مصنوعی؛ اتاق واقعی **تست نشده** | `server/aec.py`, `docs/AEC.md` |
| تاریخچه: نشست، وضعیت interrupted، برش توکنی، JSONL محلی | پیاده‌شده + تست | `server/conversation.py`, `server/sessions.py` |
| readiness و health (`/ready`, `/health`) | پیاده‌شده + تست | `server/readiness.py`, `tests/test_api.py` |
| سنجه‌های کامل خط لوله (شامل jitter callback، صف، CPU/RAM در اجرای زنده) | پیاده‌شده + تست | `server/metrics.py`, `server/audio_io.py` |
| بنچمارک کل زنجیرهٔ صوت | ابزار آماده (`tools/benchmark_voice.py`)؛ اعداد واقعی هدف **NOT MEASURED** | `docs/BENCHMARKS.md` |
| ارزیابی فارسی WER/CER | مجموعهٔ ۴۰ جمله + ۶ کلیپ `no_speech` + ابزار؛ اعداد واقعی **NOT MEASURED** | `benchmarks/persian_eval.py` |
| نرخ پذیرش کاذب روی سکوت/نویز | سنجه و ورودی‌ها آماده؛ عدد واقعی **NOT MEASURED** (بدون wav) | `benchmarks/persian_eval.py`, `server/persian.py` |
| پروتکل و **transport سمت سرور** WebSocket صوتی (`WS /ws/voice`) | پیاده‌شده + تست (fake)؛ توکن per-device، سقف اتصال، pacing، heartbeat، فریم ۲۰ms اجباری | `server/protocol.py`, `server/ws_voice.py`, `tests/test_ws_voice.py` |
| امنیت REST (`PVAI_API_TOKEN`، rate limit، `/health` حداقلی) | پیاده‌شده + تست | `server/security.py`, `server/main.py`, `tests/test_ws_security.py` |
| firmware ESP32-S3، OTA، تست LAN واقعی | **پیاده‌نشده / NOT MEASURED** | — |
| Command Broker / IoT | **پیاده‌نشده** | — |

## ۲. وضعیت تست

```text
$ python -m pytest
نتیجهٔ جاری را با اجرای python -m pytest ببینید (تعداد ثابت نیست)
```

- ۳۸ تست امنیتی در `tests/test_ws_security.py` و ۱۸۹ تست آماده‌سازی متن TTS در
  `tests/test_tts_text_preparation.py`: هر تست یک نقص گزارش‌شده را می‌پوشاند و
  روی کد پیش از رفع شکست می‌خورد (برای بارگذاری مدل، pacing، صف حیاتی و نرخ فریم دستی بررسی شد).
  (تصحیح: پیش‌تر «۳۶ امنیتی» و «متن/ASR» نوشته شده بود؛ فیلتر توهم ASR در
  `tests/test_voice.py` تست می‌شود، نه در آن فایل.)

- تست‌های native SpeexDSP در sandbox بدون کتابخانهٔ ساخته‌شده صریحاً skip می‌شوند؛ اگر
  `libspeexdsp` نصب باشد اجرا می‌شوند (در اجرای قبلی همین محیط: ۵۴ تست موفق با کتابخانه).
- ۱۴ تست تطابق مستندات در `tests/test_docs_consistency.py` برای متغیرهای محیطی، مسیرها، ارجاع تست‌ها، نقشهٔ مخزن و لینک‌ها افزوده شد.
- تست پایه پیش از این مرحله: گزارش تاریخی (تعداد تست‌ها تغییر کرده است). یعنی ۵۵۳ تست جدید یا گسترش‌یافته
  آخرین افزوده: ۱۴ تست transport وب‌سوکت + ۱ تست قطع کلاینت SSE + ۲ تست پیکربندی/readiness +
  ۲۷ تست موتور مکالمه و مسیر صوتی + ۲۳ تست مرحلهٔ resume (ادامهٔ نشست از مسیر
  `create_conversation`/`Pipeline`/`WS`، سخت‌گیری شناسهٔ نشست، شمارش سبک فهرست نشست‌ها و
  سیاست نگه‌داری) + ۲۸ تست پیکربندی `.env` (کامنت هم‌خط، مقدار رد/کلمپ‌شده، محل `.env` و
  هم‌خوانی پرچم‌های llama-server).
- در اجرای فعلی با numpy/scipy/librosa/soundfile نصب‌شده، دو تست native SpeexDSP
  به‌علت نبود کتابخانه skip شدند. تعداد وابسته به محیط است؛ `python -m pytest -rs`
  مرجع تعداد و علت جاری است. اجرای Windows/macOS در این نشست انجام نشده است.
- **همهٔ تست‌ها fake/ساختگی‌اند**: بدون مدل Whisper/Piper/Qwen، بدون میکروفون و بلندگو، بدون شبکهٔ
  واقعی. این تست‌ها «صحت منطق و کنترل جریان» را ثابت می‌کنند، نه کیفیت صوتی.

نگاشت تست‌ها به ۲۰ الزام گزارش بازبینی:

| # | الزام | تست |
|---|---|---|
| ۱ | final ASR با شروع گفتار جدید زنده می‌ماند | `test_utterance_lifecycle.py::test_final_asr_in_flight_survives_new_speech_onset` |
| ۱ب | utterance ردشده (نویز کوتاه) پاسخ نوبت قبلی را حذف نمی‌کند و state را رها می‌کند | `test_short_noise_utterance.py` (۳ سناریو) |
| ۱پ | شکست TTS/LLM/context نوبت را `failed` می‌کند و از `THINKING`/`SPEAKING` خارج می‌شود | `test_turn_failures.py` |
| ۱ت | final پشت partial در حال decode معطل نمی‌ماند و تأخیرش اندازه‌گیری می‌شود | `test_asr_preemption.py` |
| ۲ | چند utterance پشت‌سرهم حفظ می‌شوند | `…::test_multiple_consecutive_utterances_are_all_preserved` |
| ۳ | جایگزینی partial، final را از بین نمی‌برد | `…::test_partial_replacement_never_loses_the_final` |
| ۴ | گفتار بلند (>15s) همهٔ بخش‌ها را حفظ می‌کند | `…::test_long_speech_beyond_segment_limit_keeps_every_segment` + `test_vad.py::test_long_speech_is_segmented_without_losing_audio` |
| ۵ | barge-in تولید LLM را لغو می‌کند | `test_streaming_voice.py::test_async_sse_stream_cancelled_without_waiting_for_next_token` |
| ۶ | barge-in پخش TTS را متوقف می‌کند | `test_tts.py::test_playback_stops_within_one_block_and_aborts_the_stream` |
| ۶ب | اکوی پخش، VAD را مکرر فعال نمی‌کند | `test_bargein.py::test_moderate_playback_echo_does_not_retrigger_vad_but_real_speech_does` |
| ۷ | هیچ صدای کهنه‌ای بعد از قطع پخش نمی‌شود | `test_bargein.py::test_sentences_queued_behind_the_spoken_one_never_play_after_barge_in` + `test_streaming_voice.py::test_incremental_playback_before_remaining_tokens` |
| ۸ | پیام کاربر تکراری ثبت نمی‌شود | `test_utterance_lifecycle.py::test_duplicate_final_result_is_not_committed_twice` |
| ۹ | پاسخ دستیار تکراری ثبت نمی‌شود | `test_conversation.py::test_assistant_turn_is_committed_once` |
| ۱۰ | وضعیت صریح interrupted/failed | `test_conversation.py::test_interrupted_reply_has_explicit_status_and_is_excluded_by_default` |
| ۱۱ | نبود AEC = رفتار ایمن و پیام صریح | `test_aec.py::test_missing_library_is_explicit_not_silent_bypass` |
| ۱۲ | حالت AEC خاموش | `test_aec.py::test_aec_config` |
| ۱۳ | قطع دستگاه | `test_audio_io.py::test_pump_detects_a_stalled_device` |
| ۱۴ | وصل دوباره/بازگشایی دستگاه | `test_audio_io.py::test_stream_forever_reopens_after_portaudio_failure_then_gives_up` |
| ۱۵ | قطع llama-server و بازیابی نوبت بعد | `test_api.py::test_backend_unavailable_returns_503`, `test_stream_backend_failure_is_sse_error`, `test_streaming_voice.py::test_llm_failure_releases_the_state_and_the_next_turn_recovers` |
| ۱۶ | خطای init مدل به‌صورت پیام | `test_voice.py::test_module_startup_missing_model_has_no_traceback`, `test_tts.py::test_missing_piper_model_is_a_user_facing_error` |
| ۱۷ | ماندگاری context پس از restart | `test_conversation.py::test_session_is_persisted_and_reloaded`, `test_create_conversation_resumes_an_explicit_session`, `test_resume_defaults_to_the_last_session_for_the_local_loop`, `test_api.py::test_chat_with_session_id_persists_and_reuses_context`, `test_streaming_voice.py::test_resumed_pipeline_continues_ids_and_keeps_the_first_new_utterance`, `test_ws_voice.py::test_device_resumes_a_session_with_its_earlier_context`, `test_resumed_device_session_context_reaches_the_model` |
| ۱۸ | برش توکنی | `test_conversation.py::test_context_is_truncated_by_tokens_not_only_message_count` |
| ۱۹ | کار آفلاین بدون API خارجی | `test_voice.py::test_whisper_never_downloads`, `test_public_api_rejected` |
| ۲۰ | نبود صف/حافظهٔ نامحدود | `test_long_run.py` (۲ تست) + `test_conversation.py` (۲ تست bounded-RAM) |
| ۲۱ | پیکربندی `.env` واقعاً اعمال می‌شود (کامنت هم‌خط، مقدار رد/کلمپ‌شده، محل فایل) | `test_env_config.py::test_copying_env_example_produces_the_values_it_documents`, `test_invalid_number_is_reported_not_silently_defaulted`, `test_ready_endpoint_surfaces_rejected_values`, `test_dotenv_is_found_from_any_working_directory`, `test_the_file_flags_and_the_documented_manual_command_agree` |

## ۳. اعداد اندازه‌گیری‌شده در sandbox

| معیار | مقدار | معنا |
|---|---|---|
| طول عمر خط لوله در حالت سکوت (jitter p95) | ۰٫۳۹ ms | فقط pacing هماهنگ‌کننده؛ **jitter واقعی callback دستگاه نیست** |
| RAM پروسه در حالت idle | ۲۹٫۵ MB | بدون مدل لود شده |
| CPU حالت idle | ~۰٪ | بدون مدل لود شده |
| VAD false positive روی سکوت | ۰ | باورپذیر برای آستانهٔ انرژی |
| سنجه‌های زنده: jitter/صف/CPU/RAM | ثبت می‌شوند | `audio_io.pump` هر ۱ ثانیه (`test_pump_publishes_system_and_queue_metrics`) |

هیچ عدد ASR/LLM/TTS روی این sandbox تولید نشد: مدل‌ها نصب نیستند و llama-server اجرا نبود.
جدول کامل با برچسب `MEASURED`/`SIMULATED`/`NOT MEASURED` در [BENCHMARKS.md](BENCHMARKS.md).

## ۴. کارهایی که فقط روی سخت‌افزار هدف معنا دارند (NOT TESTED)

- کیفیت و سرعت Whisper فارسی با وزن واقعی روی i7-6500U؛ RTF و تأخیر final.
- کیفیت تلفظ Piper فارسی (اعداد، واژهٔ انگلیسی داخل جمله، نیم‌فاصله) با گوش انسان.
- حذف اکوی اتاق واقعی و کالیبراسیون `PVAI_AEC_DELAY_MS`؛ رفتار double-talk در بلندگو.
- jitter واقعی callback و dropout در همزمانی ASR+LLM+TTS.
- انتخاب صدای مرد/زن با فایل‌های واقعی `.onnx` و مجوز آن‌ها.
- قطع/وصل فیزیکی میکروفون و بلندگو در Windows 11.

چک‌لیست اجرای این موارد: [HARDWARE_VALIDATION.md](HARDWARE_VALIDATION.md).

## ۵. گزارش کامل بازبینی

خروجی A تا F (دفترچهٔ ایراد، فایل‌ها، تست، کارایی، موارد باز، چک‌لیست پذیرش) در
[AUDIT_REPORT.md](AUDIT_REPORT.md) است.

## ۶. اولویت بعدی

> transport سرور WebSocket **انجام شده** است (بند ۴ پیشین، ردیف جدول بالا، و
> `tests/test_ws_voice.py`). آنچه باقی مانده firmware دستگاه و اجرای واقعی روی LAN است،
> نه سرور. این بند قبلاً «کار بعدی» شمرده می‌شد و با وضعیت واقعی نمی‌خواند.

1. تهیهٔ وزن‌ها و اجرای `tools/benchmark_voice.py` روی هدف؛ ثبت اعداد در BENCHMARKS.
2. اجرای چک‌لیست سخت‌افزاری، مخصوصاً barge-in و بدون هدفون.
3. کلکسیون صدای ارزیابی (۴۰ جمله) و اجرای `benchmarks/persian_eval.py --audio-dir` برای WER واقعی.
4. firmware ESP32-S3 که با `WS /ws/voice` سخن بگوید، و تست LAN واقعی با دستگاه
   (سرور سمت خودش آماده و تست‌شده است).
5. ضبط واقعی سکوت/نویز و اجرای `benchmarks/persian_eval.py --no-speech-dir` برای
   «نرخ پذیرش کاذب»؛ تا آن زمان عدد آن `NOT MEASURED` است.

## بازبینی نوامبر ۲۰۲۶ (این شاخه)

- تست‌های بازتولیدکننده با ASGITransport، WebSocket درون‌فرایندی، مولد ساختگی کند Whisper،
  و ۴۷ نمونهٔ TTS اجرا شدند؛ `python -m pytest -q` ملاک نتیجهٔ جاری است، نه عدد قدیمی این سند.
- هدر Bearer برای کلاینت داخلی، محدودیت handshake مستقل از slot مدل، اصلاح overflow/pacing،
  لغو partial بین segmentهای Whisper و پنجرهٔ پیش‌فرض ۳ ثانیه، زبان auto، فیلتر ترکیبی،
  پیوند utterance و تلفظ اعداد/تاریخ/شماره پیاده‌سازی و با fake تأیید شدند.
- ماندگاری پیش‌فرض ۳۰ روز و purge روزانهٔ API؛ نشست‌های حلقهٔ محلی پیشوند `pc-` دارند
  و تنها از همان پیشوند برای resume بدون شناسه استفاده می‌کنند. شمارش فهرست نشست‌ها
  با cache اندازه/mtime فایل سریع‌تر شده؛ برخورد حروف بزرگ/کوچک قبلاً رد می‌شد.
- مالکیت انحصاری JSONL در مسیر نویسندهٔ local pipeline، دستگاه WS و `/api/chat`
  با قفل سیستم‌عامل روی lockfile دارای PID تضمین می‌شود؛ رقیب با `session_in_use`
  (REST 409) رد می‌شود. تست thread/process و API هم‌زمان با fake اجرا شد؛
  رفتار Windows و قطع ناگهانی فرایند روی سخت‌افزار هدف **NOT MEASURED** است.
  پیشوند `pc-` جلوی resume تصادفی نشست دستگاه را می‌گیرد؛ نشست‌های محلی قدیمی
  بدون این پیشوند خودکار resume نمی‌شوند و باید صریحاً مهاجرت/انتخاب شوند.

### NOT MEASURED (نیازمند مدل/دستگاه واقعی)

- کیفیت فارسی Whisper: WER/CER، false-reject گفتار، false-accept سکوت/نویز؛ آستانه‌های
  no_speech/logprob هنوز با فایل wav همسان کالیبره نشده‌اند.
- کیفیت پاسخ فارسی Qwen با وزن/سرور واقعی و تلفظ فارسی Piper با صدای واقعی.
- AEC و double-talk در اتاق واقعی و تأخیر قابل شنیدن دستگاه.
- بار CPU روی لپ‌تاپ دوهسته‌ای هدف و `asr_final_wait_behind_partial_ms` با مدل واقعی.
  آزمون مقایسه‌ای با همان backend ساختگیِ کند کاهش انتظار final را تأیید می‌کند،
  ولی فقط **SIMULATED** است، نه اندازه‌گیری سخت‌افزاری.
- firmware ESP32-S3، برقراری LAN/wss واقعی، jitter شبکه و VAD عصبی (پیاده‌نشده).


## بازبینی ایمنی inference — ۹ نوامبر ۲۰۲۶

- تشخیص CUDA با CTranslate2 برای `PVAI_ASR_DEVICE=auto` یا `cuda`؛ در نبود
  CUDA یا شکست بارگذاری GPU، fallback صریح با لاگ به CPU/int8. مقدار پیش‌فرض CPU
  حفظ شده است. MPS در CTranslate2 پشتیبانی نمی‌شود و به CPU برمی‌گردد.
- فایل ورودی ASR با librosa محلی به mono/16kHz/float32 تبدیل می‌شود؛ مسیر نامعتبر،
  فایل خراب/خالی و NaN/Inf با `AudioValidationError` رد می‌شوند. PCM زنده همچنان
  قرارداد PCM16 mono/16kHz دارد؛ دادهٔ خالی یا نمونهٔ ناقص رد می‌شود.
- `ModelLoadError` برای وزن‌های Whisper/Piper؛ بارگذاری Piper یک مسیر مشترک دارد.
  اسکریپت‌های llama پیش از اجرای واقعی نبود فایل وزن (حتی مسیر مطلق) را رد می‌کنند؛
  dry-run همچنان بدون وزن قابل استفاده است.
- generator تنبل ASR در success/error/preemption بسته می‌شود؛ مدل مشترک بین
  اتصال‌ها حفظ می‌شود و cache سراسری GPU در هر utterance تخلیه نمی‌شود.
- تست‌های مدل‌فری و تبدیل WAV مصنوعی: `tests/test_inference_safety.py`؛
  GPU، MPS، وزن‌های واقعی و اجرای PowerShell روی دستگاه واقعی **NOT MEASURED**.

## بازبینی MLOps و داده — ۹ نوامبر ۲۰۲۶

- trainer یا fine-tuning در مخزن موجود نیست؛ هایپرپارامتر آموزش بلااستفاده اضافه نشد.
- `tools/prepare_dataset.py`: CLI آفلاین با مسیرهای قابل تنظیم، ورودی JSON/JSONL/CSV،
  اعتبارسنجی schema/شناسه و recording تکراری/PCM16 mono WAV و export مسیر نسبی.
- loader ارزیابی: رد نوع نامعتبر، شناسهٔ ناامن برای مسیر صوت، no_speech با متن،
  hypothesis تکراری یا غیررشته‌ای؛ خطای ورودی CLI بدون traceback.
- راهنمای `docs/DATASETS.md` شامل جداسازی split، حریم خصوصی، قرارداد trainer خارجی
  و export محلی Hugging Face است. پشتیبانی آموزش یا دریافت dataset از Hub اضافه نشده.
- تست‌های ساختگی: `tests/test_dataset_preparation.py`؛ کیفیت دادهٔ واقعی و آموزش
  **NOT MEASURED**.

## یکپارچه‌سازی مستندات — ۹ نوامبر ۲۰۲۶

`docs/PROJECT_GUIDE.md` نسخهٔ تک‌فایلی همهٔ راهنماهای فعال و پیوست‌های تاریخی است؛
با `tools/build_docs.py` از منبع تولید می‌شود و تست مانع کهنه‌شدن آن می‌شود.
ناسازگاری‌های پیش‌فرض partial (۳ ثانیه)، endpoint (۶۰۰ms)، thread مدل متنی
(کد: صفر/خودکار، نمونهٔ env: چهار)، نگه‌داری تاریخچه (۳۰ روز)، شرط توکن روی loopback،
HTTP 503 مدل بارنشده و ادعای اجرای تست Windows/macOS اصلاح شدند.
جدول مجوز با librosa هم‌گام شد؛ هیچ تأیید سخت‌افزاری تازه‌ای ادعا نشده است.

---

<a id="section-3"></a>

## بخش 3 — docs/PC_VOICE.md

# دستیار صوتی PC — شنیدن پیوسته و پاسخ استریم

## مسیر فعلی — معماری جدید

[قرارداد معماری streaming](STREAMING_ARCHITECTURE.md) مرجع رفتار اجرایی است.
مسیر قبلی «منتظر پایان گفتار بمان و سپس ASR را شروع کن» جایگزین شده است:

`PCM زنده → snapshot هم‌پوشان → ASR partial/final → async SSE → صف جمله → Piper PCM`

بدون Enter صحبت کنید. تقریباً هر ۰٫۸ ثانیه از بخش گفتار در حال دریافت یک snapshot برای Whisper
فرستاده می‌شود و `transcript_partial` در همان حین نمایش داده می‌شود. بعد از ۶۰۰ms سکوت (پیش‌فرض قابل تنظیم)،
متن final تولید و درخواست مدل شروع می‌شود. Whisper ذاتاً streaming نیست؛ این یک adapter افزایشی با
بازشناسی تکراری و توافق دو decode است. فاصلهٔ ۰٫۸ ثانیه زمان‌بندی است، نه تضمین آماده‌شدن نتیجه.
هر بخش حداکثر ۱۵ ثانیه است؛ اگر گفتار ادامه یابد بخش بعدی ساخته می‌شود و **هیچ بخشی دور ریخته
نمی‌شود**. متن partial قابل‌جایگزینی است، اما صف final هرگز drop نمی‌کند.
مرجع دقیق: [STREAMING_ARCHITECTURE.md](STREAMING_ARCHITECTURE.md).

توکن‌ها با async SSE دریافت می‌شوند و جمله‌های آماده بدون انتظار کل پاسخ پخش می‌شوند.
شروع صحبت جدید task شبکه و token پخش قبلی را لغو می‌کند؛ نتیجهٔ inference قدیمی کنار گذاشته می‌شود.
سنتز native در حال اجرا ممکن است تا بازگشت chunk قابل توقف نباشد.

**AEC اضافه شده:** SpeexDSP با مرجع واقعی پخش در stream دوطرفه، پیش از VAD/ASR قرار دارد.
نصب native، تنظیم تأخیر و تست دستگاه واقعی طبق [AEC.md](AEC.md) لازم است. اگر AEC را خاموش می‌کنید هدفون استفاده کنید.
حتی با AEC، echo باقی‌مانده و نویز ممکن است VAD را فعال کنند.
لایو بودن این مسیر به معنی full-duplex مقاوم به اکو یا تضمین تأخیر روی CPU ضعیف نیست.

## نصب روی PC ویندوزی

Python 3.12، دستگاه صوتی duplex و میکروفون لازم است؛ هدفون برای تست پایه مفید است:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-voice.txt
Copy-Item .env.example .env
```

اگر `.env` از قبل دارید آن را بازنویسی نکنید؛ تنظیمات زیر را در همان فایل اصلاح کنید.
نصب وابستگی و تهیهٔ مدل‌ها مرحلهٔ آنلاین اولیه است؛ اجرای برنامه API ابری ندارد.

**وابستگی جداگانه:** SpeexDSP DLL/SO باید طبق [راهنمای حذف اکو](AEC.md) نصب شود؛ pip آن را تأمین نمی‌کند.
نبود آن با AEC فعال، خطای صریح ایجاد می‌کند.

### فایل‌های مدل (خارج از Git)

1. **LLM پیش‌فرض جدید:** `models/Qwen3-4B-Q4_K_M.gguf`.
   فایل را از توزیع معتبر GGUF خانوادهٔ Qwen تهیه کنید و مجوز و نام واقعی فایل را بررسی کنید.
   این جایگزینی تنظیمات است؛ وزن مدل خودکار دانلود نشده و کیفیت/سرعتش روی PC هنوز سنجیده نشده.
   MiniCPM قبلی فقط سابقهٔ بنچمارک زیرساخت است و دیگر انتخاب پیش‌فرض نیست.
2. **ASR:** پوشهٔ کامل Whisper Small چندزبانه با قالب CTranslate2/faster-whisper در
   `models/whisper-small/`، شامل `model.bin`، config، tokenizer و vocabulary.
   مدل اصلی PyTorch قابل جایگزینی مستقیم نیست. دانلود خودکار با `local_files_only=True` مسدود است.
3. **TTS:** وزن فارسی سازگار با Piper، `models/piper/fa_IR.onnx` به همراه
   `models/piper/fa_IR.onnx.json`. این نام قراردادی است؛ مسیر فایل واقعی را تنظیم کنید.
   مجوز وزن/گوینده و کیفیت تلفظ را قبل از استفاده بررسی کنید؛ انتخاب تجاری نهایی نشده است.

```dotenv
PVAI_MODEL_PATH=models/Qwen3-4B-Q4_K_M.gguf
PVAI_LLAMA_SERVER_URL=http://127.0.0.1:8080
PVAI_LLM_ENABLE_THINKING=false
PVAI_ASR_MODEL_PATH=models/whisper-small
PVAI_ASR_THREADS=2
PVAI_TTS_VOICE=female   # male | female
PVAI_TTS_MODEL_FEMALE=models/piper/fa_IR_female.onnx
PVAI_TTS_MODEL_MALE=models/piper/fa_IR_male.onnx
PVAI_AUDIO_INPUT_DEVICE=Microphone
PVAI_AUDIO_OUTPUT_DEVICE=Speakers
# توضیح را در خط جدا بنویسید: کامنت هم‌خط حالا حذف می‌شود،
# ولی نام دستگاه با فاصله داخلی («Realtek Microphone») قربانی می‌شد
PVAI_VOICE_VAD_THRESHOLD=0.015
PVAI_VOICE_ASR_INTERVAL=0.8
PVAI_HISTORY_PERSIST=true
```

مرجع کامل متغیرها و اثر هر کدام: [CONFIGURATION.md](CONFIGURATION.md).

Thinking برای Qwen در `chat_template_kwargs` غیرفعال است؛ llama-server باید این گزینه را
پشتیبانی کند. فیلد `reasoning_content` دیگر به‌عنوان جواب یا صدا استفاده نمی‌شود.

## اجرا — سه ترمینال

```powershell
# ۱: مسیر باینری را مطابق نصب خود تغییر دهید؛ پرچم‌ها از .env می‌آیند
.\tools\start_llama.ps1 -Exe D:\Lama.cpp\bin\llama-server.exe
# ۲: API
.\tools\start_server.ps1
# ۳: کلاینت صوتی زنده (SpeexDSP نصب و مسیر DLL/SO تنظیم شده باشد)
.\.venv\Scripts\python.exe -m server.voice
```

برای خروج Ctrl+C بزنید. **تاریخچهٔ مکالمه با خروج پاک نمی‌شود و اجرای بعدی همان نشست را ادامه
می‌دهد**: با `PVAI_HISTORY_PERSIST=true` در `data/conversations/<session_id>.jsonl` می‌ماند و
`PVAI_HISTORY_RESUME=on` (پیش‌فرض) هنگام شروع، آخرین نشست محلی دارای پیشوند `pc-` را می‌خواند؛ پیام‌ها در startup چاپ
می‌شوند (`ادامهٔ نشست «…» با N پیام قبلی`). برای شروع تازه `PVAI_HISTORY_RESUME=off` بگذارید یا
`python -m server.sessions delete-all` را اجرا کنید. با `PVAI_VOICE_SESSION_ID` می‌توانید یک
شناسهٔ ثابت انتخاب کنید. بازبینی/حذف: `python -m server.sessions list|show|delete|delete-all`.
پنجرهٔ RAM با `PVAI_HISTORY_MAX_STORED` محدود است، فایل دیسک کامل می‌ماند؛ برای جلوگیری از
نگه‌داشتن بی‌پایان متن گفتار، `PVAI_HISTORY_RETENTION_DAYS` (پیش‌فرض `30` روز؛ `0` = همه) و
`python -m server.sessions purge` را ببینید.

برای فهرست دستگاه‌ها: `python -m server.voice --list-devices`. انتخاب صریح ورودی/خروجی با
`PVAI_AUDIO_INPUT_DEVICE` / `PVAI_AUDIO_OUTPUT_DEVICE` (نام یا index) انجام می‌شود؛ پیام startup
نام واقعی دستگاه و نرخ را چاپ می‌کند.
حالت قدیمی فقط برای عیب‌یابی: `python -m server.voice --turn-based`.
در آن حالت `PVAI_VOICE_RECORD_SECONDS`، `/پاک` و `/خروج` همچنان کار می‌کنند.

### تنظیم حساسیت

| نشانه | کلید |
|---|---|
| صدای آرام دیده نمی‌شود | `PVAI_VOICE_VAD_THRESHOLD` ↓ |
| نویز/پنکه utterance می‌سازد | `PVAI_VOICE_VAD_THRESHOLD` ↑ و `PVAI_VOICE_VAD_NOISE_MULTIPLIER` ↑ |
| جمله وسط مکث می‌شکند | `PVAI_VOICE_SILENCE_MS` ↑ |
| حرف اول کلمه بریده می‌شود | `PVAI_VOICE_VAD_PRE_ROLL_MS` ↑ |
| دستیار دیر قطع می‌شود | `PVAI_VOICE_BARGE_IN_MS` ↓ |
| دستیار با کوچک‌ترین صدا قطع می‌شود | `PVAI_VOICE_BARGE_IN_MARGIN` ↑ / کالیبراسیون AEC |

رویدادهای `speech_start`/`speech_end` مقدار `rms`، `noise_floor` و `boundary` را چاپ می‌کنند؛
کالیبراسیون را با همین اعداد انجام دهید. این تنظیم جای مدل VAD عصبی یا کالیبراسیون AEC نیست.
اگر هیچ راه‌حلی جواب نداد: [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

## امنیت و داده

- متن فقط به API محلی/خصوصی می‌رود؛ proxy محیط و redirect کلاینت غیرفعال است.
- llama-server را محلی نگه دارید و REST را فقط با `PVAI_API_TOKEN` در LAN در دسترس قرار دهید؛ سرویس روی نشانی غیر-loopback بدون توکن بالا نمی‌آید.
- در مسیر زنده **صوت** فقط در RAM است و فایل صوتی ساخته نمی‌شود؛ فقط حالت `--turn-based`
  فایل WAV موقت می‌سازد. اما **متن مکالمه** با `PVAI_HISTORY_PERSIST=true` به‌صورت JSONL محلی
  ذخیره می‌شود (قابل خاموش‌کردن و حذف). هیچ‌چیز به بیرون فرستاده نمی‌شود.
- صف میکروفون و صف جمله‌ها محدودند؛ overflow ورودی نوبت جاری را حذف می‌کند.
- ASR و synthesis در حال اجرای native ممکن است فوراً متوقف نشوند؛ خروج، لغو منطقی است و
  تضمین hard realtime نیست. Ctrl+C برنامه را خاتمه می‌دهد؛ مدل‌های سنگین را روی PC واقعی بسنجید.

## اعتبارسنجی لازم روی PC واقعی

1. سکوت/نویز: پاسخ ساختگی تولید نشود.
2. جملهٔ فارسی طولانی: پیش از پایان صحبت `transcript_partial` و بعد از مکث `transcript_final` دیده شود.
3. پاسخ طولانی: صدای جملهٔ اول قبل از تکمیل متن کل پاسخ شنیده شود.
4. هنگام پاسخ صحبت کنید: پخش قبلی قطع شود؛ درخواست جدید جایگزین آن شود.
5. قطع backend و مدل خراب: خطای فارسی، بدون crash نشست پردازش.
6. پس از نصب، اینترنت را قطع کنید و همان تست‌ها را تکرار کنید.
7. سنجه‌ها: `first_token.endpoint_ms`، `tts_start`، `first_audible` (اولین بلوک تحویل‌شده به
   دستگاه؛ تأخیر DAC/بلندگو را نمی‌سنجد)، `barge_in_latency_ms`، `audio_callback_jitter_ms`.
   برای اعداد کامل: `python tools/benchmark_voice.py` (روش در [BENCHMARKS.md](BENCHMARKS.md)).
8. چک‌لیست کامل و قابل ثبت: [HARDWARE_VALIDATION.md](HARDWARE_VALIDATION.md).

تست خودکار از fake استفاده می‌کند. میکروفون واقعی، وزن‌های Whisper/Piper/Qwen و عملکرد
روی Windows هدف در sandbox تأیید نشده‌اند. Linux ممکن است PortAudio سیستمی لازم داشته باشد.

## ایمنی inference و ورودی فایل

- پیش‌فرض ASR همچنان CPU/int8 است. `PVAI_ASR_DEVICE=auto` وجود CUDA را با
  CTranslate2 تشخیص می‌دهد؛ `cuda` هم در نبود دستگاه یا شکست بارگذاری مدل GPU با
  هشدار به CPU/int8 برمی‌گردد. CTranslate2 از MPS پشتیبانی نمی‌کند؛ `mps` نیز به CPU برمی‌گردد.
  این رفتار فقط ASR است؛ offload مدل GGUF همچنان با تنظیمات مستقل llama-server کنترل می‌شود.
- `LocalAudio.transcribe(path)` فایل را با librosa محلی به mono/16kHz/float32 تبدیل
  می‌کند؛ مسیر اشتباه، صوت خالی/خراب و NaN/Inf با `AudioValidationError` رد می‌شوند.
- مسیر PCM زنده metadata نرخ/کانال ندارد و قرارداد PCM16 mono/16kHz را فرض می‌کند؛
  فقط ورودی خالی یا نمونهٔ ناقص را رد می‌کند. ابزار ارزیابی WAV عمداً نرخ/کانال/عرض
  نمونه را سخت‌گیرانه بررسی می‌کند و برخلاف ورودی فایل عمومی، تبدیل خودکار ندارد.
- وزن ناقص یا ناسازگار با `ModelLoadError` گزارش می‌شود؛ هر دو استثنا زیرکلاس
  `VoiceError` هستند. generator تنبل ASR هنگام پایان، خطا یا لغو بسته می‌شود؛
  لغو بین segmentهاست و قطع فوری فراخوانی native را تضمین نمی‌کند.

---

<a id="section-4"></a>

## بخش 4 — docs/CONFIGURATION.md

# مرجع پیکربندی — متغیرهای `PVAI_*`

این سند **قرارداد واقعی `server/config.py`** است: هر متغیری که در کد خوانده می‌شود اینجا فهرست شده،
با پیش‌فرض واقعی، بازهٔ مجاز و اثری که روی خط لوله دارد. مقدار پیش‌فرض داخل کد است؛ `.env` یا
متغیر محیطی آن را override می‌کند و متغیر محیطی همیشه بر `.env` اولویت دارد.

قواعد کلی:

- نام متغیرها انگلیسی و با پیشوند `PVAI_` است؛ مقدار متنی می‌تواند فارسی باشد (`PVAI_SYSTEM_PROMPT`).
- **کامنت هم‌خط حذف می‌شود**: در `PVAI_N_THREADS=2   # توضیح` مقدار `2` است، نه رشتهٔ کامل.
  `#` فقط وقتی کامنت است که ابتدای مقدار باشد یا قبلش فاصله باشد، و هرگز داخل کوتیشن نیست؛
  پس `PVAI_SYSTEM_PROMPT="یک # دو"` دست‌نخورده می‌ماند. نوشتن توضیح در خط جدا همیشه امن‌تر است
  (نام دستگاه با فاصله مثل `Realtek Microphone` اگر بعدش ` # ...` بیاید بریده می‌شود).
- عدد خارج از بازهٔ مجاز **به نزدیک‌ترین حد مجاز** بریده می‌شود، نه اینکه برنامه را متوقف کند.
- **هیچ مقدار نامعتبری بی‌صدا نادیده گرفته نمی‌شود**: مقدار غیرعددی، بولی نامفهوم یا کلمپ‌شده
  با `WARNING` (شامل نام متغیر) لاگ می‌شود، در `Settings.env_issues` می‌نشیند و از راه
  `Settings.config_errors()` در `GET /ready` و پیام startup دیده می‌شود.
- مقادیر بولی: `1/true/yes/on` و `0/false/no/off`؛ `none/auto/default` برای متغیرهای سه‌حالته مقدار «خودکار».
- خطاهای ساختاری (مثل صدای نامعتبر یا نرخ نمونه‌برداری نادرست) با `Settings.config_errors()` برگردانده
  می‌شوند و در `GET /ready` دیده می‌شوند؛ مسیر زنده در این حالت اجرا نمی‌شود.
- **محل `.env`**: اول `.env` در پوشهٔ جاری، بعد `.env` در ریشهٔ پروژه جست‌وجو می‌شود (اولی که
  وجود داشته باشد برنده است). قبلاً فقط پوشهٔ جاری بود، پس اجرا از مسیر دیگر `.env` را
  **بی‌صدا** نادیده می‌گرفت.
- هیچ متغیری صدا یا متن را به اینترنت نمی‌فرستد. آدرس backend در `PVAI_LLAMA_SERVER_URL` تعیین می‌شود؛ کلاینت صوتی
  API محلی را از `PVAI_HOST`/`PVAI_PORT` می‌سازد و نشانی عمومی را رد می‌کند.
  ابزار دانلود وزن جدا از مسیر inference است و فقط با اقدام صریح اپراتور اجرا می‌شود.

---

## ۱. مدل متنی و llama-server

| متغیر | پیش‌فرض | معنا |
|---|---|---|
| `PVAI_MODEL_PATH` | `models/Qwen3-4B-Q4_K_M.gguf` | مسیر GGUF برای llama-server (خود سرویس آن را لود می‌کند؛ فایل باید محلی باشد) |
| `PVAI_LLAMA_SERVER_URL` | `http://127.0.0.1:8080` | آدرس محلی llama-server؛ باید private/loopback باشد |
| `PVAI_N_CTX` | `4096` | پنجرهٔ context (۵۱۲–۳۲۷۶۸)؛ به `llama-server -c` داده می‌شود |
| `PVAI_N_THREADS` | `0` | thread سمت llama-server؛ `0` = خودکار (پرچم `-t` ارسال نمی‌شود). مستقل از threadهای Whisper است؛ `.env.example` مقدار پیشنهادی `4` را تنظیم می‌کند، نه پیش‌فرض `0` |
| `PVAI_N_GPU_LAYERS` | `0` | تعداد لایه روی GPU (`-ngl`)؛ روی GeForce 940MX (۲GB) پیش‌فرض CPU حفظ می‌شود |
| `PVAI_SYSTEM_PROMPT` | متن فارسی پیش‌فرض | system prompt؛ در **هر دو** مسیر متنی و صوتی به مدل می‌رود |
| `PVAI_TEMPERATURE` | `0.7` | دمای نمونه‌گیری (۰–۲) |
| `PVAI_MAX_TOKENS` | `512` | سقف توکن پاسخ |
| `PVAI_TIMEOUT` | `180` | مهلت درخواست به llama-server (ثانیه) |
| `PVAI_LLM_ENABLE_THINKING` | `false` | `chat_template_kwargs` برای Qwen؛ `false` تا `reasoning_content` به صدا تبدیل نشود |

این چهار متغیر (`PVAI_MODEL_PATH`, `PVAI_N_CTX`, `PVAI_N_THREADS`, `PVAI_N_GPU_LAYERS`) توسط
کلاینت HTTP خوانده **نمی‌شوند**؛ llama-server مسیر مدل و thread را خودش تعیین می‌کند. برای اینکه
این تنظیمات واقعاً اثر کنند، اسکریپت راه‌انداز آن‌ها را به پرچم تبدیل می‌کند:

```powershell
.\tools\start_llama.ps1 -Exe D:\Lama.cpp\bin\llama-server.exe   # اجرا
.\tools\start_llama.ps1 -DryRun                                  # فقط نمایش دستور
.venv\Scripts\python.exe -m server.config --launch                # دستور خام
```

> اگر llama-server را دستی اجرا می‌کنید، پرچم‌هایتان باید با `.env` هم‌خوان باشد؛ در غیر این صورت
> **llama-server برنده می‌شود** و تغییر `.env` اثری ندارد. (پیش‌فرض کد `PVAI_N_THREADS=0` است؛ مقدار پیشنهادی `.env.example` برابر `4` است.
> اندازه‌گیری قدیمی MiniCPM در SPEC تضمین عملکرد Qwen نیست.)

## ۲. سرویس API

| متغیر | پیش‌فرض | معنا |
|---|---|---|
| `PVAI_HOST` | `127.0.0.1` | آدرس bind. `0.0.0.0` فقط با توکن REST و در شبکهٔ خصوصی |
| `PVAI_PORT` | `8000` | پورت API |
| `PVAI_LOG_LEVEL` | `INFO` | سطح لاگ |

## ۳. ASR (faster-whisper / CTranslate2)

| متغیر | پیش‌فرض | معنا |
|---|---|---|
| `PVAI_ASR_MODEL_PATH` | `models/whisper-small` | پوشهٔ مدل CTranslate2 شامل `model.bin`؛ دانلود خودکار مسدود است (`local_files_only=True`) |
| `PVAI_ASR_DEVICE` | `cpu` | `cpu`، `auto`، `cuda` یا `mps`؛ تشخیص CUDA با CTranslate2. نبود CUDA یا شکست بارگذاری GPU به CPU/int8 با لاگ برمی‌گردد؛ MPS پشتیبانی نمی‌شود و به CPU/int8 برمی‌گردد. CUDA واقعی روی 940MX تأیید نشده |
| `PVAI_ASR_COMPUTE_TYPE` | `int8` | `int8` روی CPU؛ `float16` فقط با GPU معتبر |
| `PVAI_ASR_THREADS` | `2` | بودجهٔ thread مخصوص Whisper، جدا از `PVAI_N_THREADS` (هدف: ۲ هستهٔ فیزیکی) |
| `PVAI_ASR_BEAM_SIZE` | `1` | beam search؛ ۱ سریع‌ترین، بیشتر = دقیق‌تر و کندتر |
| `PVAI_ASR_VAD_FILTER` | `false` | VAD داخلی Whisper؛ مرجع پایان‌گفتار ما نیست (VAD خودمان تصمیم می‌گیرد) |
| `PVAI_ASR_LANGUAGE` | `fa` | زبان Whisper. پیش‌تر در کد **سخت‌کد** بود. `auto` = انتخاب خود Whisper (برای گویندهٔ دوزبانه) |
| `PVAI_ASR_INITIAL_PROMPT` | خالی | متن آغازین؛ املا/واژگان را جهت می‌دهد (بدون آن Whisper گاهی پاسخ فارسی را با حروف عربی می‌نویسد) |
| `PVAI_ASR_NO_SPEECH_THRESHOLD` | `0.6` | آستانهٔ `no_speech_prob` خود Whisper |
| `PVAI_ASR_LOG_PROB_THRESHOLD` | `-1.0` | اگر `avg_logprob` سگمنت از این کمتر باشد، دور ریخته می‌شود |
| `PVAI_ASR_COMPRESSION_RATIO` | `2.4` | بالاتر از این = حلقهٔ تکرار، دور ریخته می‌شود |
| `PVAI_ASR_MAX_NO_SPEECH_PROB` | `0.75` | اگر `no_speech_prob` سگمنت از این بیشتر باشد، **کل سگمنت** حذف می‌شود |
| `PVAI_ASR_HALLUCINATION_PHRASES` | فهرست فارسی/انگلیسی | عبارت‌های توهمی روی سکوت/نویز، جداشده با ویرگول. خالی = فیلتر خاموش (توصیه نمی‌شود) |
| `PVAI_ASR_PARTIAL_WINDOW_S` | `3.0` | طول پنجرهٔ هم‌پوشان برای partial؛ cost هر decode را ثابت نگه می‌دارد. باید ≥ ۲× `PVAI_VOICE_ASR_INTERVAL` باشد |
| `PVAI_ASR_MAX_FINAL_JOBS` | `16` | آستانهٔ هشدار عمق صف final؛ **هیچ final حذف نمی‌شود** |
| `PVAI_ASR_WARMUP` | `true` | لود و warmup مدل در startup به‌جای اولین utterance |

### فیلتر توهم Whisper

Whisper روی سکوت و نویز «هیچ» برنمی‌گرداند؛ یک جملهٔ ساختگی برمی‌گرداند
(«زیرنویس‌ها توسط تیم ما تهیه شده است»، «ممنون از تماشای شما»). چون متن ناتهی یک نوبت
کامل LLM را شروع می‌کند، یک توهم یعنی پاسخ مطمئن به پرسشی که کسی نپرسیده.

چهار سیگنال مستقل، هر سگمنت را رد می‌کند: `no_speech_prob`، `avg_logprob`،
`compression_ratio` و فهرست عبارت‌ها. فیلتر عمداً محافظه‌کار است: فقط عبارت‌های کوتاه
(`WHOLE_TRANSCRIPT_ONLY` در `server/voice.py`) باید **کل متن** باشند، وگرنه
«به مادرم گفتم خداحافظ و رفتم» هم دور ریخته می‌شد — بدتر از رد نکردن یک توهم.
شمارندهٔ `asr_segments_rejected` در متریک‌ها ثبت می‌شود.

`benchmarks/persian_eval.py` نرخ **پذیرش کاذب** را جدا گزارش می‌کند: ورودی‌های
`kind=no_speech` در مجموعه (سکوت/نویز/تلویزیون) مرجع متنی ندارند و WER نمی‌توانند
بسنجند. با `--no-speech-dir DIR` روی wavهای واقعی هم اندازه‌گیری می‌شود؛ بدون فایل صوتی
گزارش صریحاً **NOT MEASURED** است، نه صفر.
- firmware، رجیستری دستگاه و OTA وجود ندارند؛ `docs/PROTOCOL.md` این تفکیک را صریح می‌گوید.

## ۴. TTS (Piper)

| متغیر | پیش‌فرض | معنا |
|---|---|---|
| `PVAI_TTS_MODEL_PATH` | `models/piper/fa_IR.onnx` | مدل پیش‌فرض/عمومی Piper |
| `PVAI_TTS_MODEL_FEMALE` | خالی | مسیر مدل صدای زن؛ اگر خالی باشد `PVAI_TTS_MODEL_PATH` استفاده می‌شود |
| `PVAI_TTS_MODEL_MALE` | خالی | مسیر مدل صدای مرد؛ همان قاعده |
| `PVAI_TTS_VOICE` | `female` | `male` یا `female`. مقدار دیگر خطای پیکربندی است |

هر مدل `.onnx` **باید** فایل کنارش `.onnx.json` داشته باشد؛ وگرنه startup/readiness پیام فارسی می‌دهد.

### آماده‌سازی متن پیش از سنتز (`server.persian.prepare_for_tts`)

پیش‌تر `server/persian.py` توابع `normalize` و `spoken_numbers` را داشت ولی **هیچ ماژولی
آن را import نمی‌کرد**؛ خروجی خام LLM به Piper می‌رفت. Piper هرچه بگیرد می‌خواند: `**` را
صدا می‌کند، هر شمارهٔ فهرست را با «نقطه» تمام می‌کند و روی ایموجی مکث می‌کند. بدتر اینکه
جمله‌بندی روی نقطهٔ `1.` می‌شکست:
`"**مهم:** سه مورد:\n1. اول\n2. دوم"` → `['**مهم:** سه مورد:\n1.', 'اول\n2.', 'دوم']`.

اکنون هر جمله پیش از رسیدن به Piper از این مرحله می‌گذرد:

- حذف markdown (پررنگ/مورب/عنوان/نقل‌قول/خط افقی)، بلوک کد، لینک (برچسب می‌ماند، URL حذف)،
  تگ HTML، ایموجی و ستارهٔ سرگردان؛
- نشانهٔ فهرست (`1.`, `-`, `•`) حذف می‌شود، نه اینکه جمله را بشکند؛
- ارقام فارسی/عربی/انگلیسی به **حروف** تبدیل می‌شوند (`1234` → «یک هزار و دویست و سی و چهار»،
  `۳.۵` → «سه ممیز پنج»)، همچنین ساعت (`10:30` → «ده و سی دقیقه»)، درصد («بیست و پنج درصد»)،
  ارز («پنج دلار») و نسخه (`2.1.0` → «دو نقطه یک نقطه صفر»)؛
- نیم‌فاصله و **همزهٔ اضافه** (`U+0654`) حفظ می‌شوند: حذف دومی «جملهٔ» را به «جمله» تبدیل می‌کند
  و تلفظ را عوض می‌کند؛ اعراب (فتحه/کسره/…) حذف می‌شود چون Piper آن‌ها را واکهٔ جدا می‌خواند؛
- خروجی idempotent است، چون ممکن است متنی از قبل آماده‌شده دوباره پردازش شود.

جمله‌بندی (`server.live_voice.sentences`) هم اصلاح شد: نقطه‌ای که به شمارهٔ فهرست یا اعشار
تعلق دارد مرز جمله نیست، و در حالت streaming یک قطعهٔ پایانی (`2.`) تا رسیدن ادامهٔ متن نگه
داشته می‌شود. متن **خام** همچنان در رونوشت و در پیام دستگاه می‌ماند؛ فقط صدای Piper تغییر می‌کند.

system prompt پیش‌فرض هم صریح می‌گوید پاسخ با صدا خوانده می‌شود: بدون markdown، فهرست،
جدول، لینک و ایموجی، و ارقام با حروف. این لایهٔ دوم است؛ لایهٔ اول همان `prepare_for_tts` است
که حتی اگر مدل رعایت نکند، صدا را درست می‌کند.

## ۵. VAD و پایان‌گفتار

| متغیر | پیش‌فرض | معنا |
|---|---|---|
| `PVAI_VOICE_VAD_THRESHOLD` | `0.015` | آستانهٔ RMS شروع گفتار (۰٫۰۰۱–۰٫۵) |
| `PVAI_VOICE_VAD_OFFSET_RATIO` | `0.6` | هیسترزیس: آستانهٔ ادامهٔ گفتار = آستانهٔ شروع × این نسبت (۰٫۰۵–۱) |
| `PVAI_VOICE_VAD_ONSET_MS` | `60` | چند میلی‌ثانیه صدای بلند برای اعلام onset |
| `PVAI_VOICE_SILENCE_MS` | `600` | سکوت لازم برای endpoint |
| `PVAI_VOICE_VAD_PRE_ROLL_MS` | `200` | پیش‌رول نگه‌داشته‌شده تا حروف اولیه («س»، «ش»، «خ») بریده نشوند |
| `PVAI_VOICE_VAD_MIN_SPEECH_MS` | `160` | حداقل گفتار برای پذیرش یک utterance (سرفه رد می‌شود) |
| `PVAI_VOICE_VAD_MAX_SEGMENT_S` | `15.0` | سقف طول یک بخش؛ گفتار بلندتر تقسیم می‌شود و **همهٔ بخش‌ها** حفظ می‌شوند |
| `PVAI_VOICE_VAD_NOISE_ADAPT` | `true` | تطبیق کف نویز (پنکه/کولر) |
| `PVAI_VOICE_VAD_NOISE_MULTIPLIER` | `2.5` | ضریب کف نویز برای آستانهٔ مؤثر |
| `PVAI_VOICE_BARGE_IN_MS` | `200` | چند میلی‌ثانیه گفتار برای قطع پاسخ دستیار |
| `PVAI_VOICE_BARGE_IN_MARGIN` | `1.5` | افزایش موقت آستانهٔ VAD هنگام پخش (کاهش false barge-in؛ جایگزین AEC نیست) |

## ۶. زمان‌بندی خط لوله

| متغیر | پیش‌فرض | معنا |
|---|---|---|
| `PVAI_VOICE_ASR_INTERVAL` | `0.8` | فاصلهٔ ارسال partial (۰٫۳–۵ ثانیه) |
| `PVAI_VOICE_SPEECH_QUEUE` | `8` | صف محدود جمله‌ها؛ پر شدنش به SSE backpressure می‌دهد |
| `PVAI_VOICE_RECORD_SECONDS` | `5` | فقط حالت عیب‌یابی `--turn-based` |

## ۷. دستگاه و انتقال صدا

| متغیر | پیش‌فرض | معنا |
|---|---|---|
| `PVAI_AUDIO_INPUT_DEVICE` | خالی | نام (زیررشته) یا index میکروفون؛ خالی = پیش‌فرض سیستم. نام مبهم خطا می‌دهد |
| `PVAI_AUDIO_OUTPUT_DEVICE` | خالی | همان قاعده برای خروجی |
| `PVAI_AUDIO_SAMPLE_RATE` | `16000` | **باید ۱۶۰۰۰ باشد**؛ قرارداد PCM16 mono 16k برای VAD/ASR/AEC. مقدار دیگر خطای پیکربندی است |
| `PVAI_AUDIO_BLOCK_MS` | `20` | طول بلوک (۱۰/۲۰/۴۰) |
| `PVAI_AUDIO_STALL_S` | `2.0` | اگر بیش از این مدت فریمی نرسد، دستگاه قطع‌شده فرض می‌شود. مبدأ شمارش، آخرین فریم است و اگر هیچ فریمی نرسیده باشد، لحظهٔ باز شدن stream |
| `PVAI_AUDIO_REOPEN_ATTEMPTS` | `3` | تعداد تلاش بازگشایی stream پیش از خطای نهایی |
| `PVAI_AUDIO_REOPEN_STABLE_FRAMES` | `50` | حداقل فریم واقعی که یک stream بازگشایی‌شده باید تحویل دهد تا «سالم» شمرده شود و سهمیهٔ تلاش‌ها ریست شود (۱ ثانیه با بلوک ۲۰ms). کمتر از این یعنی دستگاه باز می‌شود ولی صدا نمی‌دهد (mute/exclusive/Bluetooth) و باید سهمیه را مصرف کند |

## ۸. AEC (SpeexDSP)

| متغیر | پیش‌فرض | معنا |
|---|---|---|
| `PVAI_AEC_ENABLED` | `true` | با `false` صریح، bypass کامل (برای تست با هدفون). نبود کتابخانه با AEC فعال = خطای صریح، نه bypass خاموش |
| `PVAI_AEC_LIBRARY` | خالی | مسیر مطلق DLL/SO؛ خالی = جست‌وجوی سیستمی |
| `PVAI_AEC_FILTER_MS` | `250` | طول فیلتر تطبیقی (۱۰۰–۱۰۰۰ ms) |
| `PVAI_AEC_DELAY_MS` | `40` | تأخیر مرجع پخش تا میکروفون؛ **باید روی دستگاه واقعی کالیبره شود** |

AEC فقط با ۱۶kHz و بلوک ۲۰ms معنا دارد؛ `GET /ready` این را بررسی می‌کند.

## ۹. حافظهٔ مکالمه (فقط محلی)

| متغیر | پیش‌فرض | معنا |
|---|---|---|
| `PVAI_HISTORY_PERSIST` | `true` | ذخیرهٔ JSONL محلی زیر `PVAI_DATA_DIR/conversations` |
| `PVAI_DATA_DIR` | `data` | ریشهٔ دادهٔ محلی (در `.gitignore`) |
| `PVAI_HISTORY_MAX_MESSAGES` | `24` | سقف تعداد پیام در context (پس از برش توکنی) |
| `PVAI_HISTORY_TOKEN_BUDGET` | `1536` | بودجهٔ تخمینی توکن context (حداقل ۱۲۸) |
| `PVAI_HISTORY_MAX_STORED` | `500` | پنجرهٔ RAM؛ فایل روی دیسک کامل می‌ماند (جلوگیری از رشد نامحدود حافظه) |
| `PVAI_HISTORY_INCLUDE_INTERRUPTED` | `false` | آیا پاسخ قطع‌شده در context مدل بیاید (برای بازبینی همیشه ذخیره می‌شود) |
| `PVAI_HISTORY_RESUME` | `on` | `on`: مسیر زندهٔ محلی در هر اجرا نشستِ قبلی را ادامه می‌دهد (الزام ۱۷). `off`: هر اجرا نشست تازه؛ `session_id` صریح همیشه resume می‌شود |
| `PVAI_VOICE_SESSION_ID` | (خالی) | اگر تنظیم شود، نشستِ ثابتی است که مسیر زندهٔ محلی همیشه در آن می‌نویسد (فقط `[A-Za-z0-9_-]`) |
| `PVAI_HISTORY_RETENTION_DAYS` | `30` | `0` = نگه‌داشتن همه. با مقدار مثبت، نشست‌هایی که این تعداد روز دست‌نخورده مانده‌اند در startup و هر ۲۴ ساعت و با `python -m server.sessions purge` پاک می‌شوند |

## ۱۰. انتقال صوتی WebSocket (LAN)

| متغیر | پیش‌فرض | معنا |
|---|---|---|
| `PVAI_WS_ENABLED` | `false` | ثبت `WS /ws/voice` برای دستگاه‌های LAN. خاموش = هیچ پورت/مسیر صوتی اضافه‌ای باز نمی‌شود |
| `PVAI_WS_TOKENS` | خالی | توکن per-device به شکل `device_id:token,device_id:token`. **ترجیح داده می‌شود**: بستن یک دستگاه بقیه را قطع نمی‌کند. جفت بدون `:` یا توکن تکراری خطای پیکربندی است |
| `PVAI_WS_TOKEN` | خالی | توکن مشترک همهٔ دستگاه‌ها؛ فقط با `PVAI_WS_ALLOW_SHARED_TOKENS=true` پذیرفته می‌شود. با `PVAI_WS_ENABLED=true` یکی از `PVAI_WS_TOKENS`/`PVAI_WS_TOKEN` الزامی است |
| `PVAI_WS_ALLOW_SHARED_TOKENS` | `false` | پذیرش `PVAI_WS_TOKEN` مشترک. با یک توکن برای همه، بستن دسترسی یک دستگاه یعنی بستن همه |
| `PVAI_WS_MAX_CONNECTIONS` | `4` | سقف اتصال هم‌زمان؛ اتصال اضافی `error` کد `too_many_connections` می‌گیرد |
| `PVAI_WS_CONNECT_RATE` | `10` | سقف سراسری اتصال جدید در دقیقه در فرایند سرور |
| `PVAI_WS_FRAME_RATE` | `200` | سقف فریم capture در ثانیه برای هر اتصال (نرخ اسمی ۵۰ = ۲۰ms؛ ۲۰۰ یعنی ۴ برابر). `0` = بی‌سقف |
| `PVAI_WS_HEARTBEAT` | `15` | فاصلهٔ `ping` سرور (ثانیه). `0` = خاموش |
| `PVAI_WS_IDLE_TIMEOUT` | `60` | اگر دستگاه این مدت هیچ پیامی نفرستد اتصال بسته می‌شود. `0` = خاموش |
| `PVAI_WS_PLAYBACK_PACING` | `true` | ارسال فریم پخش با نرخ واقعی. `false` فقط برای اشکال‌زدایی: دستگاه تا ۵ ثانیه بافر می‌کند و بعد از barge-in صدای لغوشده پخش می‌شود |
| `PVAI_WS_PLAYBACK_CREDIT_MS` | `200` | اعتبار اولیهٔ بافر دستگاه (میلی‌ثانیه) تا بین برست‌های سنتز underrun نشود |

- پورت همان `PVAI_PORT` برنامه است؛ مسیر جدا `WS /ws/voice`.
- `/ready` (پشت توکن REST) کلید `ws_voice` را برمی‌گرداند: خاموش / روشن بدون توکن (ناآماده) / آماده.

## ۱۱. دسترسی REST

| متغیر | پیش‌فرض | معنا |
|---|---|---|
| `PVAI_API_TOKEN` | خالی | توکن Bearer برای همهٔ مسیرهای REST جز `/health`. **اجباری** وقتی `PVAI_HOST` غیر-loopback است، وگرنه برنامه در startup خطا می‌دهد |
| `PVAI_API_RATE_LIMIT` | `20` | درخواست REST در ثانیه به‌ازای هر کلاینت (توکن یا نشانی) |
| `PVAI_API_RATE_BURST` | `40` | اندازهٔ برست مجاز پیش از ۴۲۹ |

- وقتی توکن REST تنظیم شده، `/health` از احراز هویت معاف است (OPTIONS نیز معاف است) و فقط `{"status": "ok"}` برمی‌گرداند: بدون نام مدل، بدون مسیر داده.
  جزئیات در `/ready` است؛ با توکن خالی و bind فقط loopback، HTTP بدون احراز هویت کار می‌کند.
- پیش از این `GET /api/sessions/{id}` متن کامل گفتگو را بی‌احراز هویت برمی‌گرداند؛ با
  `PVAI_HOST=0.0.0.0` (که برای دستگاه LAN لازم است) هر کسی در شبکه آن را می‌دید.
- توکن روی شبکهٔ بدون TLS به‌صورت plaintext می‌رود؛ برای شبکهٔ غیرمورد‌اعتماد reverse proxy با
  `https://`/`wss://` بگذارید (بخش ۷ در `docs/PROTOCOL.md`).
- تمایز مهم: `Settings.config_errors()` **دروازهٔ سخت** است (`run_live` را متوقف می‌کند و
  `/ready` را ناآماده می‌کند). `Settings.config_warnings()` ریسک‌های آگاهانه را برمی‌گرداند —
  مثل `ws://` بدون TLS در LAN یا نبودن `PVAI_API_TOKEN` روی loopback — که در لاگ startup و در
  `checks.configuration.warnings` در `/ready` دیده می‌شوند و جلوی اجرا را نمی‌گیرند.

## ۱۲. مشاهده‌پذیری

| متغیر | پیش‌فرض | معنا |
|---|---|---|
| `PVAI_METRICS_ENABLED` | `true` | فعال‌سازی سنجه‌ها (`server/metrics.py`)؛ خاموش = ثبت سنجه‌ها غیرفعال؛ هزینهٔ فراخوانی‌های کد لزوماً صفر نیست |

فهرست نام سنجه‌ها در `server/metrics.py::METRIC_NAMES` است و تست
`tests/test_metrics.py::test_documented_metric_names_cover_the_pipeline` هم‌خوانی آن با کد را بررسی می‌کند.

---

## نمونهٔ `.env` حداقلی برای هدف (i7-6500U)

```dotenv
PVAI_MODEL_PATH=models/Qwen3-4B-Q4_K_M.gguf
PVAI_N_THREADS=2
PVAI_ASR_THREADS=2
PVAI_ASR_PARTIAL_WINDOW_S=3
PVAI_TTS_VOICE=female
PVAI_TTS_MODEL_FEMALE=models/piper/fa_IR_female.onnx
PVAI_TTS_MODEL_MALE=models/piper/fa_IR_male.onnx
PVAI_AUDIO_INPUT_DEVICE=Microphone
PVAI_AUDIO_OUTPUT_DEVICE=Speakers
PVAI_AEC_ENABLED=true
PVAI_VOICE_VAD_THRESHOLD=0.015
PVAI_HISTORY_PERSIST=true
```

پس از تغییر، `GET /ready` را ببینید: هر متغیر نادرست به‌صورت خطای فارسی با نام همان بخش گزارش می‌شود.


### TLS مستقیم روی API/WS

| متغیر | پیش‌فرض | کاربرد |
|---|---|---|
| `PVAI_SSL_CERTFILE` | خالی | مسیر گواهی TLS برای uvicorn (همراه KEYFILE) |
| `PVAI_SSL_KEYFILE` | خالی | کلید خصوصی TLS؛ در git نگذارید |

هر دو را با هم تنظیم کنید؛ در LAN آدرس `https://` و `wss://` می‌شود.
`PVAI_API_TOKEN` و توکن per-device همچنان اجباری هستند؛ TLS جای احراز هویت نیست.

---

<a id="section-5"></a>

## بخش 5 — docs/STREAMING_ARCHITECTURE.md

# معماری موتور مکالمهٔ PC — utterance، generation، صف‌ها و لغو

این سند رفتار **پیاده‌شده** در `server/streaming_voice.py` را توصیف می‌کند. هر ادعای اینجا با
نام تست مشخص پشتیبانی می‌شود؛ اگر چیزی تست نشده، همان‌جا نوشته شده است.

## ۱. مسئله‌ای که حل شد

پیاده‌سازی قبلی یک شمارندهٔ `generation` و یک صف یک‌تایی (`snapshots`) برای همهٔ کارها داشت.
نتیجه: با شروع گفتار جدید، `interrupt()` صف را خالی می‌کرد و `recognize()` هم نتیجهٔ final را
در صورت تغییر `generation` دور می‌ریخت. یعنی جملهٔ A می‌توانست بی‌صدا از مکالمه حذف شود:

```text
کاربر A را می‌گوید → سکوت → final(A) به صف میرود → Whisper در حال decode
کاربر B را شروع می‌کند → onset → interrupt() → generation+1
decode تمام میشود → «generation قدیمی» → final(A) دور ریخته میشود ← باگ P0-01
```

سه مسیر مستقل این از دست رفتن را می‌ساختند: خالی‌کردن صف در `interrupt()`، بررسی `generation`
پس از decode، و mailbox مشترک partial/final. هر سه در طراحی جدید حذف شده‌اند.

## ۲. چهار مفهوم مستقل

| مفهوم | مالک | عمر | لغو می‌شود؟ |
|---|---|---|---|
| `Utterance` | VAD + Pipeline | onset تا commit متن | خیر (شناسهٔ یکتا و افزایشی) |
| `FinalASR` (صف FIFO) | `AsrScheduler.finals` | submit تا decode و ثبت | **فقط** در shutdown، با اعلام |
| `PartialASR` (mailbox یک‌جا) | `AsrScheduler.partial` | تا جایگزینی یا رسیدن final | بله، بدون اثر |
| `AssistantTurn` | `Pipeline.pending_turn` | تا commit (complete/interrupted/failed) | بله، مستقل از ASR |

قاعدهٔ طلایی: **لغو پاسخ دستیار هیچ‌وقت به صف final دست نمی‌زند.** barge-in فقط
`AssistantTurn`، task پاسخ و صف پخش را متوقف می‌کند.

### شناسه‌ها

- `utterance_id`: ۱، ۲، ۳…؛ هرگز تکرار نمی‌شود. کلید dedupe در `Conversation.add_user`.
- `turn_id`: هر تلاش پاسخ؛ کلید dedupe در `Conversation.finish_assistant`.
- `generation`: شمارندهٔ barge-in برای بی‌اعتبار کردن taskهای async قدیمی (شبکه/TTS)،
  و **ملاک اعتبار ASR نیست**.

## ۳. خط لوله

```text
PortAudio duplex callback  (PCM16 mono / 16 kHz / 20 ms)
   │  capture+reference تازه، شماره‌گذاری‌شده
   ▼
Queue محدود  ──►  AEC (SpeexDSP)  ──►  EnergyVAD  ──►  Pipeline.feed()
                                                       │
                        ┌──────────────────────────────┼──────────────────────────────┐
                        ▼                              ▼                              ▼
                  onset: Utterance(id)         partial هر 0.8s               endpoint/max_segment
                  barge-in فقط اگر            پنجرهٔ هم‌پوشان ۳s             audio کامل بخش
                  THINKING/SPEAKING           → mailbox یک‌جا               → صف FIFO final
                        │                              │                              │
                        └──────────────► AsrScheduler ◄┘                              │
                                             │                                        │
                                     یک actor Whisper (thread)                        │
                                             │                                        │
                              partial → رویداد transcript_partial                      │
                              final   → transcript_final → Conversation.add_user        │
                                             │                                        │
                                    AssistantTurn (SSE به llama-server) ◄──────────────┘
                                             │
                                    جمله‌های آماده → صف محدود (۸) → actor پخش
                                             │
                                     Piper → mono 16k → duplex → بلندگو
```

هر پایین‌دست **یک مالک** دارد: یک actor برای Whisper، یک actor برای پخش. این کار هم‌زمانی
دو decode یا دو سنتز را حذف می‌کند و روی CPU دوهسته‌ای قابل پیش‌بینی است.

## ۴. ASR: چرا partial «ارزان» شد

پیاده‌سازی قدیم هر ۰٫۸ ثانیه **کل بخش در حال رشد** را به Whisper می‌داد؛ هزینه تا سقف ۱۵ ثانیه
تقریباً درجه‌دوم رشد می‌کرد. اکنون:

- **final**: کل PCM بخش (بدون برش، فقط حذف سکوت پایانی اضافی) — دقت حفظ می‌شود.
- **partial**: پنجرهٔ هم‌پوشان با سقف `PVAI_ASR_PARTIAL_WINDOW_S` (پیش‌فرض ۳ ثانیه)؛
  هزینهٔ هر decode تقریباً ثابت است.
- ترتیب اولویت: صف final **همیشه** قبل از partial سرو می‌شود؛ partial معلق با رسیدن final
  همان utterance منسوخ می‌شود.
- `agreed_prefix` فقط وقتی معنا دارد که پنجره جابه‌جا نشده باشد؛ در غیر این صورت رشتهٔ خالی
  گزارش می‌شود تا «پیشوند پایدار» دروغ نباشد.
- سنجه‌ها: `asr_ms`, `asr_rtf`, `asr_final_latency_ms`, `asr_partial_latency_ms`,
  `asr_queue_depth`, `asr_dropped_partial_count`, `asr_final_job_count`, `asr_errors`.
- **تأخیر final پشت partial (اندازه‌گیری + رفع):** یک actor تنها یعنی partialی که در حال
  decode است، final را تا پایان خودش معطل می‌کند. `AsrJob.started_at` لحظهٔ تحویل به decoder را
  نگه می‌دارد و سه سنجه این تأخیر را تفکیک می‌کنند:
  `asr_final_dispatch_wait_ms` (کل انتظار در صف)، `asr_final_wait_behind_partial_ms`
  (بخشی که صرف partial در حال اجرا شد) و `asr_final_decode_ms` (خودِ decode نهایی).
  به‌محض صف‌شدن final، `AsrScheduler.preempt()` توکن partial در حال اجرا را set می‌کند؛
  backendی که `transcribe_pcm(pcm, token, job)` را می‌پذیرد فوراً متوقف می‌شود
  (`asr_partial_preempted` / `asr_partial_cancelled` / `asr_partial_wasted_ms`)، و backend
  قدیمی `transcribe_pcm(pcm)` فقط نتیجه‌اش دور ریخته می‌شود ولی همان تأخیر در سنجه ثبت می‌شود.
  هر partial منسوخ (final همان utterance یا جدیدتر در صف است) در `submit_partial` رد می‌شود.
  تست: `tests/test_asr_preemption.py` (SIMULATED؛ با ۲ ثانیه partial، پاسخ از ۱۹۶۵ms به ۳ms
  رسید — عدد دستگاه واقعی **NOT MEASURED**).
- **backpressure**: صف final بی‌کران نیست در معنا که هیچ‌وقت drop نمی‌کند؛ فقط از
  `PVAI_ASR_MAX_FINAL_JOBS` عبور می‌کند و شمارندهٔ `backlog_warnings` بالا می‌رود. این انتخاب
  آگاهانه است: گم‌کردن متن کاربر بدتر از تأخیر است.

اصلاح مهم مرحلهٔ اخیر: در `AsrScheduler.next()` پرچم بیدارباش **بعد از** بررسی صف پاک می‌شد؛
یک final که در همان پنجره submit می‌شد می‌توانست تا submit بعدی خواب بماند (lost wakeup).
اکنون ابتدا `clear()` و سپس بررسی مجدد انجام می‌شود.
تست: `tests/test_asr_scheduler.py::test_submit_landing_between_check_and_wait_is_not_lost`.

## ۵. ماشین حالت

| حالت | معنا |
|---|---|
| `IDLE` | نه گفتاری، نه پاسخی |
| `LISTENING` | utterance باز است |
| `TRANSCRIBING` | final صف شده و منتظر decode |
| `THINKING` | پاسخ در حال دریافت از مدل |
| `SPEAKING` | سنتز/پخش |

گذار مجاز در `ALLOWED_TRANSITIONS`. هر گذار غیرمجاز **گزارش** می‌شود (`state_violation`) و
شمارندهٔ `states.violations` بالا می‌رود؛ هرگز باعث crash نمی‌شود. تست:
`tests/test_state_machine.py` (همهٔ ۲۵ جفت حالت + جریان واقعی یک نوبت + barge-in).

اصلاح مرحلهٔ اخیر (رها شدن state پس از شکست): دو مسیر شکست هیچ نشانگر commitی در صف پخش
نمی‌گذاشتند و state را در حالت قبلی رها می‌کردند، بنابراین گفتار بعدی کاربر «barge-in» علیه
نوبتی حساب می‌شد که دیگر وجود نداشت:

- TTS: مسیر exception فقط نوبت را `failed` ثبت می‌کرد و در `SPEAKING` می‌ماند
  (`pending_turn` هم پاک نمی‌شد). همچنین توکن سراسری `self.cancel` را set می‌کرد بدون جایگزینی،
  پس هر پاسخ بعدی در اولین خط قطع می‌شد.
- LLM/context: `respond` با لیست خالی پیام‌ها `return` می‌کرد؛ نه commit، نه `response_end`،
  نه خروج از `THINKING` — نوبت تا ابد معلق.

اکنون هر دو مسیر از `_fail_turn` می‌گذرند: commit با وضعیت `failed` (و دلیل صریح
`llm_error`/`history_error`/`empty_context`/`tts_error`)، پاک‌کردن نشانه‌های همان نوبت،
جایگزینی `self.cancel` اگر set شده باشد، و گذار به `LISTENING`/`IDLE` وقتی صف پخش خالی و
`pending_turn is None` است. تست: `tests/test_turn_failures.py`.

در همین مرحله: utterance کوتاه‌تر از `PVAI_VOICE_VAD_MIN_SPEECH_MS` که VAD رد می‌کند اکنون
رویداد `utterance_rejected` دارد؛ `_latest_utterance_id` فقط با final معتبر جلو می‌رود و
«نوبت جدیدی که جواب نگرفت» پاسخ نوبت قبلی را حذف نمی‌کند. تست: `tests/test_short_noise_utterance.py`.

اصلاح مرحلهٔ اخیر: پس از پایان پخش، حالت در `SPEAKING` گیر می‌کرد چون نشانگر commit
(`SpeechItem` با متن خالی) آخرین آیتم صف است و شرط `reply.done()` در آن لحظه هنوز false بود.
اکنون هنگام مصرف نشانگر، اگر صف خالی و `pending_turn` پاک شده باشد، گذار به
`LISTENING`/`IDLE` انجام می‌شود. تست: `test_pipeline_walks_the_documented_states_for_one_exchange`.

## ۶. barge-in و لغو

| وضعیت هنگام شروع گفتار کاربر | رفتار |
|---|---|
| `IDLE` | utterance جدید؛ هیچ لغوی |
| `LISTENING` | ادامهٔ همان utterance |
| `TRANSCRIBING` | utterance جدید ساخته می‌شود؛ **صف final دست‌نخورده**؛ پاسخ قدیمی اگر فعال باشد لغو می‌شود |
| `THINKING` | barge-in پس از `PVAI_VOICE_BARGE_IN_MS` گفتار: cancel task پاسخ، افزایش generation، پاک‌کردن صف جمله |
| `SPEAKING` | همان + set کردن token پخش؛ Speaker بین دو بلوک ۲۰ms متوقف می‌شود |

قواعد تکمیلی:

- پاسخ قطع‌شده با وضعیت `interrupted` ثبت می‌شود و فقط **آنچه شنیده شد** در تاریخچه می‌رود:
  اگر جمله‌ای کامل شده باشد همان‌ها (`turn.spoken`)، اگر فقط جملهٔ اول آغاز شده باشد همان جمله
  (`turn.current`)، و اگر هنوز هیچ صدایی نبود متن تولیدشده فقط برای بازبینی ثبت می‌شود و به‌طور
  پیش‌فرض وارد context مدل نمی‌شود. جمله‌های صف‌شدهٔ پخش‌نشده هرگز سنتز/پخش نمی‌شوند.
  تست: `tests/test_bargein.py`.
- `Stale audio` ممکن نیست: هر آیتم صف، `token` و `generation` خود را حمل می‌کند و actor پخش
  قبل از هر قطعه آن را بررسی می‌کند.
- توقف قطعی inference native در حال اجرا **تضمین نمی‌شود** (thread درجا kill نمی‌شود)؛
  نتیجهٔ دیررس با `generation`/`turn.token` دور ریخته می‌شود. رفع کامل = process isolation
  (مورد باز OPEN-01).

## ۷. صف‌ها و کرانه‌ها

| صف | کرانه | سیاست |
|---|---|---|
| capture (DuplexAudio) | ۱۰۰ فریم ≈ ۲s | overflow → `Pipeline.overflow()` نوبت جاری را حذف می‌کند، finalها می‌مانند |
| `AsrScheduler.finals` | بدون drop | FIFO؛ عمق فقط هشدار می‌دهد |
| `AsrScheduler.partial` | ۱ | latest-only |
| `Pipeline.speech` | `PVAI_VOICE_SPEECH_QUEUE` (۸) | پرشدن → backpressure روی SSE |
| `Conversation.messages` | `PVAI_HISTORY_MAX_STORED` (۵۰۰) | پنجرهٔ RAM؛ فایل JSONL کامل می‌ماند |
| `_finalized_ids` | ۵۱۲ | dedupe نتیجهٔ final تکراری |
| `Metrics` | ثابت | count/last/min/max/mean بدون نگه‌داشتن نمونه‌ها |

تست بار: `tests/test_long_run.py` با ۱۲۰ نوبت متوالی و بررسی همهٔ کرانه‌ها در هر نوبت.

## ۸. رویدادها (JSON lines روی stdout)

`speech_start`, `speech_end`, `transcript_partial`, `transcript_final`, `user_message`,
`first_token`, `response_delta`, `tts_enqueue`, `tts_start`, `tts_end`, `first_audible`,
`response_end`, `interrupted`, `response_skipped`, `state`, `state_violation`, `error`, `ready`,
`llm_timings`, `metrics` (در صورت فعال بودن).

هر رویداد `generation` و `state` دارد. `transcript_partial` هرگز فرمان اجرا نمی‌کند.

## ۹. معیار پذیرش (قابل اجرا)

1. `pytest -q` سبز (بدون مدل و بدون سخت‌افزار).
2. `python tools/benchmark_voice.py` بدون استثنا اجرا شود؛ ردیف‌های واقعی `MEASURED` و
   ردیف‌های بی‌مدل `SIMULATED`/`NOT MEASURED` باشند.
3. در اجرای واقعی (چک‌لیست سخت‌افزار): final(A) بعد از شروع B در تاریخچه بماند؛
   پیام تکراری ظاهر نشود؛ صدای کهنه پخش نشود؛ `barge_in_latency_ms` ثبت شود.

---

<a id="section-6"></a>

## بخش 6 — docs/AEC.md

# حذف اکو در PC — SpeexDSP

## پیاده‌سازی

AEC به‌صورت پیش‌فرض در مسیر زنده فعال است. میکروفون هنگام پخش خاموش نمی‌شود:

`Piper → mono/resample 16k → صف پخش → callback دوطرفه → بلندگو`

`میکروفون + PCM واقعاً ارسال‌شده به خروجی → SpeexDSP AEC → VAD → ASR → barge-in`

مرجع از نمونه‌های تولیدشدهٔ مدل به‌صورت حدسی گرفته نمی‌شود؛ همان فریم‌های انتخاب‌شده برای
`outdata` در یک `sounddevice.RawStream` دوطرفه ثبت می‌شوند. هنگام سکوت یا لغو، مرجع هم سکوت است.
فریم‌ها PCM16، تک‌کاناله، 16kHz، ۲۰ms هستند. خروجی Piper با polyphase resampling به این قالب می‌رود.
انتخاب نرخ خروجی PC به 16k برای هم‌قالب‌بودن AEC است؛ پیشنهاد 24k برای ESP32/TTS نهایی تغییر نکرده است.

SpeexDSP یک فیلتر تطبیقی محلی است؛ mute حین پاسخ یا صرفاً تغییر آستانهٔ VAD جایگزین آن نشده.
پردازش DSP خارج از callback انجام می‌شود. callback فقط صف محدود ورودی و خروجی را مدیریت می‌کند.
خطای underflow/overflow نوبت را لغو و state فیلتر و صف مرجع تأخیر را reset می‌کند.

## نصب وابستگی native

`pip install -r requirements-voice.txt` شامل NumPy/SciPy است، اما **DLL یا SO مربوط به SpeexDSP را نصب نمی‌کند**.
برای نصب نرم‌افزار به مجوز SpeexDSP و در صورت توزیع باینری به همراه برنامه، الزامات NOTICE آن توجه کنید.
منبع رسمی: https://github.com/xiph/speexdsp (مجوز در فایل COPYING).

### Windows x64

کتابخانهٔ shared از SpeexDSP را از منبع معتبر تهیه/با ابزار build خودتان بسازید؛
درخت upstream دارای راهنمای Windows است. DLL باید نمادهای زیر را export کند:

- `speex_echo_state_init`, `speex_echo_state_destroy`, `speex_echo_state_reset`
- `speex_echo_ctl`, `speex_echo_cancellation`

در `.env` مسیر مطلق DLL را وارد کنید (نام فایل ممکن است بسته به build متفاوت باشد):

```dotenv
PVAI_AEC_ENABLED=true
PVAI_AEC_LIBRARY=C:/AudioLibs/speexdsp/libspeexdsp.dll
PVAI_AEC_FILTER_MS=250
PVAI_AEC_DELAY_MS=40
```

DLL و وابستگی‌های runtime آن باید با معماری Python (معمولاً x64) سازگار باشند.
ما DLL ویندوز را در مخزن قرار نداده‌ایم و آن را روی Windows هدف آزمایش نکرده‌ایم.

### Linux

کتابخانهٔ runtime سیستم `libspeexdsp` را با مدیر بستهٔ توزیع نصب کنید (مثلاً در Debian/Ubuntu بستهٔ
`libspeexdsp1`). با `PVAI_AEC_LIBRARY=` خالی، `ctypes.util.find_library` آن را پیدا می‌کند؛
یا مسیر مطلق `.so` را تنظیم کنید. نصب خودکار یا دانلود از داخل برنامه انجام نمی‌شود.

**کتابخانهٔ مفقود/خراب باعث خطای صریح فارسی می‌شود؛ برنامه بی‌صدا به حالت بدون AEC برنمی‌گردد.**
برای عیب‌یابی و استفاده از هدفون می‌توانید صریحاً خاموش کنید:

```dotenv
PVAI_AEC_ENABLED=false
```

AEC برای مسیر پیش‌فرض زنده است؛ حالت قدیمی `--turn-based` از این مسیر دوطرفه استفاده نمی‌کند.

## تنظیم دستگاه و تأخیر

- ورودی و خروجی پیش‌فرض سیستم باید قابلیت mono 16k duplex داشته باشند. اگر دستگاه پشتیبانی نمی‌کند،
  برنامه خطا می‌دهد؛ نرخ نمونه‌برداری را با دورزدن AEC تغییر ندهید.
- `PVAI_AEC_DELAY_MS`: تأخیر اضافی مرجع پیش از فیلتر، پیش‌فرض ۴۰ms؛ به مضرب پایین‌تر ۲۰ms گرد می‌شود.
  بسته به latency دستگاه با ۰، ۲۰، ۴۰، ۶۰ و ... آزمایش کنید. تأخیر زیادی که مرجع را از اکو عقب‌تر ببرد نتیجه را خراب می‌کند.
- `PVAI_AEC_FILTER_MS`: طول tail فیلتر، پیش‌فرض ۲۵۰ms؛ افزایش تا ۱۰۰۰ms مصرف CPU را بیشتر می‌کند.
- بعد از تغییر فاصلهٔ میکروفون/بلندگو، فیلتر باید دوباره با مسیر صوت سازگار شود؛ حذف اولیه کامل نیست.
- تقویت خیلی زیاد، clipping، بلوتوث با latency متغیر، clock drift و پردازش صوتی درایور می‌توانند کیفیت را کم کنند.
  ابتدا با دستگاه سیمی و ولوم متوسط تست کنید. پردازش خودکار صوت سیستم ممکن است با AEC نرم‌افزار تداخل کند.
- مرجع فقط پخش همین برنامه است؛ صدای موسیقی/مرورگر/برنامه‌های دیگر حذف نمی‌شود.
- یک stream دوطرفه صف‌ها را هماهنگ می‌کند، ولی ساعت دو سخت‌افزار مستقل یا DAC واقعی را یکسان نمی‌کند.
- این نسخه تخمین خودکار delay، nonlinear residual suppression و VAD تخصصی اضافه نمی‌کند.
  وجود AEC تضمین نمی‌کند energy VAD هیچ‌گاه با echo باقی‌مانده فعال نشود.

## تست خودکار و واقعی

تست معمولی بدون کتابخانهٔ native هم قابل اجراست؛ تست native اختیاری است:

```powershell
$env:PVAI_TEST_AEC_LIBRARY = "C:/AudioLibs/speexdsp/libspeexdsp.dll"
.\.venv\Scripts\python.exe -m pytest -q -s tests/test_aec.py
```

در sandbox لینوکسی، بخش AEC کتابخانه از upstream commit
`7a158783df74efe7c2d1c6ee8363c1e695c71226` با GCC و floating-point/smallft، بیرون مخزن ساخته شد.
تست synthetic (نه اتاق واقعی): نویز مرجع، اکو با تأخیر ۴۰ms و gain=0.55، ۱۲ ثانیه.
در دو ثانیهٔ پایانی **۵۷٫۸۲ dB** کاهش توان اکو اندازه‌گیری شد.
تست double-talk مصنوعی با سیگنال مستقل دوفرکانسی نیز correlation حدود **۰٫۹۱۸** و
RMS retention حدود **۰٫۹۹۳** داشت؛ این تست معادل کیفیت گفتار واقعی نیست.

چک‌لیست تست سخت‌افزار (هنوز اجرا نشده):

1. فقط صدای دستیار پخش شود؛ کاربر ساکت بماند. false `speech_start` و قطع ناخواسته را بشمارید.
2. حین پخش صحبت کنید؛ صدای کاربر نباید mute شود و پاسخ قبلی باید قطع شود.
3. با تغییر ولوم/فاصله، هم echo-only و هم double-talk را تکرار کنید.
4. تأخیر و طول فیلتر را تنظیم و نتایج را همراه مشخصات دستگاه ثبت کنید.
5. underflow/overflow، لغو پاسخ و قطع دستگاه را بررسی کنید؛ مرجع قدیمی نباید به نوبت بعدی نشت کند.

تا عبور از این تست‌ها، «AEC پیاده شده» درست است ولی «مکالمهٔ بدون هدفون روی PC کاربر تأیید شده» درست نیست.

---

<a id="section-7"></a>

## بخش 7 — docs/PROTOCOL.md

# پروتکل صوتی ESP32-S3 ↔ PC (transport سرور پیاده‌شده — firmware نشده)

> **وضعیت صادقانه:** قرارداد انتقال در `server/protocol.py` (تست: `tests/test_protocol.py`) و
> **transport سمت سرور** در `server/ws_voice.py` (تست: `tests/test_ws_voice.py`) پیاده شده است.
> `GET /api/protocol` این وضعیت را با `"implemented": true`, `"transport_implemented": true` و
> `"firmware_implemented": false` برمی‌گرداند.
> **هیچ firmware، رجیستری دستگاه یا OTA در این مخزن وجود ندارد** و هیچ تست سخت‌افزاری/شبکهٔ واقعی
> اجرا نشده است (به `docs/HARDWARE_VALIDATION.md` نگاه کنید). اولویت PC-first است: تا وقتی موتور
> مکالمهٔ PC تست پذیرش را پاس نکرده، firmware شروع نمی‌شود.

## ۱. اصول

- انتقال: **WebSocket روی LAN خصوصی**؛ فریم‌های باینری برای صدا و متن JSON برای کنترل.
- فقط IPv4/IPv6 خصوصی یا loopback؛ هیچ relay ابری، هیچ STUN/TURN عمومی.
- صدا **PCM16 تک‌کاناله 16kHz** (32 KB/s) در هر دو جهت. خروجی TTS روی PC به 16kHz نرمال می‌شود.
- بلوک مرجع ۲۰ms = ۳۲۰ نمونه = ۶۴۰ بایت.
- هر دستگاه `device_id`، هر مکالمه `session_id`، هر پاسخ دستیار `turn_id` و هر گفتار کاربر
  `utterance_id` جدا دارد. این چهار شناسه با هم اشتباه نمی‌شوند.
- احراز هویت با توکن مستقل دستگاه (`token`) در پیام `hello`؛ توکن مشترک فقط با opt-in صریح؛ مقایسه با `hmac.compare_digest`
  (`server.protocol.check_token`). قبل از `hello` هیچ پیام دیگری پذیرفته نمی‌شود.

## ۲. فریم صوتی باینری

هدر ۱۰ بایتی big-endian:

| فیلد | نوع | معنا |
|---|---|---|
| `version` | uint8 | نسخهٔ پروتکل (`PROTOCOL_VERSION`) |
| `kind` | uint8 | `1` = capture (دستگاه→سرور)، `2` = playback (سرور→دستگاه) |
| `sequence` | uint16 | شمارهٔ ترتیب، افزایشی و مستقل برای هر جهت |
| `samples` | uint16 | تعداد نمونه؛ `1..MAX_AUDIO_SAMPLES` |
| `timestamp_ms` | uint32 | زمان تولید نسبت به شروع session |

سپس `samples * 2` بایت PCM16 little-endian. پیاده‌سازی: `encode_audio` / `decode_audio`.
هر نقض قرارداد (`kind` ناشناخته، طول نامنطبق، نمونهٔ فرد، نسخهٔ دیگر) `ProtocolError` می‌دهد؛
گزارش آن به دستگاه با پیام `error` انجام می‌شود، نه با بستن بی‌صدا.

## ۳. پیام‌های کنترلی (JSON)

فرمت: `{"type": "...", "seq": n, ...}`. فیلدهای الزامی در `server/protocol.py::REQUIRED_FIELDS`
تعریف شده‌اند و تست `test_every_message_type_has_a_required_field_rule` کامل بودن آن را بررسی می‌کند.

| نوع | جهت | فیلدهای الزامی | کار |
|---|---|---|---|
| `hello` | دستگاه→سرور | `device_id`, `token` | احراز هویت و شناسایی دستگاه |
| `hello_ok` | سرور→دستگاه | `session_id` | پذیرش، اعلام قالب صدا و نسخه |
| `session_start` | دستگاه→سرور | `session_id` | آغاز مکالمهٔ جدید (پاک‌شدن context قبلی) |
| `session_ack` | سرور→دستگاه | `session_id` | تأیید آغاز نشست |
| `speech_start` | دستگاه→سرور | `utterance_id` | VAD سمت دستگاه: شروع گفتار |
| `speech_end` | دستگاه→سرور | `utterance_id`, `reason` | پایان گفتار (`endpoint`/`max_segment`/`forced`/`overflow`/`device`) |
| `interrupt` | دستگاه→سرور | `reason` | barge-in (`user_speech`/`manual`/`timeout`/`shutdown`/`superseded`) |
| `play_start` | سرور→دستگاه | `turn_id`, `sample_rate` | آغاز ارسال صدای پاسخ |
| `play_end` | سرور→دستگاه | `turn_id`, `played_ms` | پایان پخش واقعی روی دستگاه |
| `play_stop` | سرور→دستگاه | `turn_id`, `reason` | قطع پخش (`barge_in`/`new_turn`/`shutdown`/`error`) |
| `set_voice` | هر دو | `voice` (`male`/`female`) | انتخاب صدا؛ سرور و دستگاه هم‌گام می‌شوند |
| `asr_partial` | سرور→دستگاه | `utterance_id`, `text` | متن نهایی‌نشده برای نمایش/ردیابی |
| `asr_final` | سرور→دستگاه | `utterance_id`, `text` | متن نهایی همان utterance |
| `ping` / `pong` | هر دو | `nonce` | اندازه‌گیری RTT و تشخیص قطعی |
| `error` | هر دو | `code`, `detail` | خطای قابل‌فهم (فارسی برای انسان، `code` انگلیسی) |

### قواعد رفتاری

1. **`asr_partial` هرگز فرمان نیست.** فقط `asr_final` به LLM می‌رود و می‌تواند به `Command Broker` منتهی شود.
2. `speech_start` سمت سرور، پاسخ در حال پخش را قطع می‌کند (barge-in) اما **ریال‌های ASR نهایی قبلی را
   هرگز باطل نمی‌کند** — همان قاعدهٔ `docs/STREAMING_ARCHITECTURE.md`.
3. `play_stop` باید در دستگاه هم اجرا شود: بافر پخش پاک و صدای قدیمی خاموش؛ بایت‌های در راه نباید
   بعد از `play_stop` پخش شوند (شمارهٔ `sequence` برای تشخیص).
4. `ping` بدون `pong` در چند دوره = قطع اتصال؛ دستگاه باید reconnect کند و `session_id` تازه بگیرد.
5. خطای احراز هویت → `error` با کد `auth_failed` و بستن اتصال.
6. NaN/`inf` در صدا پذیرفته نمی‌شود؛ PCM16 دقیقاً ۲ بایت هر نمونه.

## ۴. جریان نمونه (یک نوبت کامل)

```text
device            server(PC)                llama-server      piper
  | hello(token) -->|
  |<- hello_ok ------|
  | session_start ->|
  |<- session_ack ---|
  | capture seq=1 -->|  (20ms blocks, continuous)
  | speech_start --->|  VAD onset
  | capture ... ---->|  partial ASR windows every 0.8s
  |<- asr_partial ---|
  | speech_end ----->|  endpoint
  |<- asr_final -----|
  |                  |--- POST /v1/chat/completions (SSE) -->|
  |                  |<-- sentence streams ------------------|
  |<- play_start ----|
  |<- playback seq=1-|  PCM 16k
  |  (user speaks)   |
  | interrupt ------>|  barge-in: cancel generation + queued speech
  |<- play_stop -----|
  | capture ... ---->|  utterance جدید؛ context مکالمه حفظ می‌شود
```

## ۵. بازپخش، reconnect و شناسه‌ها

- `sequence` هر جهت مستقل و ۱۶ بیتی است؛ سرریز → `sequence = 0` و ادامه.
- پس از reconnect، دستگاه `hello` می‌فرستد؛ اگر `session_id` قبلی را بخواهد، با `session_start`
  همان شناسه را اعلام می‌کند و سرور context محلی را از `data/conversations/<session_id>.jsonl`
  بازیابی می‌کند (فقط اگر `PVAI_HISTORY_PERSIST=true`).
- سرور در `hello_ok` نسخهٔ پروتکل و قالب صدا را اعلام می‌کند؛ دستگاه ناسازگار باید صریحاً
  `error` بدهد، نه اینکه با نرخ اشتباه ادامه دهد.

## ۶. transport سمت سرور (`WS /ws/voice`)

پیاده‌سازی: `server/ws_voice.py`؛ فقط وقتی `PVAI_WS_ENABLED=true` باشد در برنامه ثبت می‌شود.

- **احراز هویت اجباری است.** اگر هیچ توکنی تنظیم نشده باشد، هر اتصال با `error` کد
  `auth_not_configured` و بستن `1008` رد می‌شود؛ API صوتی بی‌احراز هویت باز نمی‌شود.
  `PVAI_WS_ENABLED=true` بدون توکن خطای پیکربندی است (`Settings.config_errors`).
- **توکن per-device.** `PVAI_WS_TOKENS="esp32-a:token-a,esp32-b:token-b"` هر دستگاه را به توکن
  خودش گره می‌زند؛ بستن دسترسی یک دستگاه یعنی حذف همان جفت، بدون تعویض توکن بقیه. اگر توکن با
  `device_id` دیگری در `hello` بیاید `error` کد `device_mismatch` است، و توکن ناشناخته
  `auth_failed`. توکن مشترک `PVAI_WS_TOKEN` هنوز کار می‌کند ولی باید صریحاً
  `PVAI_WS_ALLOW_SHARED_TOKENS=true` بگیرد: با یک توکن برای همه، بستن یک دستگاه یعنی بستن همه،
  و لاگ نمی‌تواند بگوید کدام دستگاه بوده.
- نخستین پیام باید `hello` باشد؛ پیام دیگر → `error` کد `handshake_required`. مهلت `hello` ده ثانیه
  است (`handshake_timeout`). پیام خطا **قبل از** بستن اتصال فرستاده می‌شود تا دستگاه دلیل را لاگ کند.
- هر اتصال **Pipeline و Conversation مستقل** دارد؛ ASR، TTS و صف پخش بین دستگاه‌ها مشترک نیست.
- capture باینری به همان `Pipeline` مسیر میکروفن داده می‌شود؛ event‌های آن یک‌به‌یک به پیام‌های
  `speech_start`/`speech_end`/`asr_partial`/`asr_final`/`play_start`/`play_end`/`play_stop` تبدیل می‌شوند.
- **فریم ورودی دقیقاً ۲۰ms.** capture باینری باید مضربی از ۳۲۰ نمونه باشد؛ بلوک‌های بزرگ‌تر
  به همان ۳۲۰ نمونه‌ای تقسیم می‌شوند و اندازهٔ نامعتبر `error` کد `protocol_error` می‌گیرد.
  دلیل: endpointing ناشی از VAD فریم‌ها را ۲۰ms فرض می‌کند، پس یک فریم ۱۰۰ms زمان سکوت را
  ۵ برابر اشتباه می‌شمارد.
- `seq` در capture بررسی می‌شود: پرش و تکرار در `ws_capture_gap_frames` و
  `ws_capture_out_of_order` ثبت می‌شود (WiFi بسته گم می‌کند؛ پیش‌تر هیچ اثری نمی‌ماند).
  نرخ فریم هم سقف دارد (`PVAI_WS_FRAME_RATE`، پیش‌فرض ۲۰۰/s = ۴ برابر نرخ اسمی): دستگاه معیوب
  نمی‌تواند CPU را با صدایی که خط لوله دور می‌ریزد اشغال کند. برست کوتاه (توقف WiFi و بعد
  تحویل دسته‌ای) جریمه نمی‌شود.
- پخش: هر بلوک ۲۰ms در یک فریم باینری `kind=2` با `sequence` صعودی ۰..۶۵۵۳۵. صف پخش محدود است
  (۲۵۰ فریم ≈ ۵ ثانیه)؛ دستگاه کند باعث backpressure روی سنتز می‌شود، نه رشد حافظه.
- **پخش pacing دارد.** فریم‌ها با نرخ واقعی (هر ۲۰ms) فرستاده می‌شوند، نه با سرعت سنتز.
  پیش از این سرور تا ۵ ثانیه جلوتر از دستگاه می‌رفت؛ دستگاه آن را بافر می‌کرد و پس از barge-in
  همان بافر تا رسیدن `play_stop` پخش می‌شد. اعتبار اولیهٔ بافر
  `PVAI_WS_PLAYBACK_CREDIT_MS` (پیش‌فرض ۲۰۰ms) است تا بین برست‌های سنتز underrun نشود؛
  با `PVAI_WS_PLAYBACK_PACING=false` می‌توان خاموشش کرد (فقط برای اشکال‌زدایی).
  از این رو `played_ms` یعنی «صدایی که واقعاً به دستگاه رسید»، نه «صدایی که در صف بود».
- `play_start` و `play_end` هر دو مرز **نوبت** هستند، نه جمله: `tts_start` برای هر جمله می‌آید،
  ولی سرور یک `play_start` در آغاز نوبت و یک `play_end` پس از ارسال همهٔ فریم‌های آن می‌فرستد
  (نوبت چندجمله‌ای دقیقاً یک جفت دارد).
- **پیام‌های حیاتی هرگز دور ریخته نمی‌شوند.** صف کنترل کران‌دار است (۶۴)؛ پیش‌تر هر چه جا
  نمی‌شد حذف می‌شد — از جمله `play_stop`. اکنون فقط `asr_partial` قابل‌حذف است (شمارندهٔ
  `ws_control_dropped`، چون partial تازه‌تر جایش را می‌گیرد)؛ `play_stop`/`play_end`/`asr_final`/
  `error` در صف سرریز می‌مانند و **پیش از** بقیهٔ صف فرستاده می‌شوند. شمارندهٔ `rejected` فقط
  برای رد handshake/ظرفیت است.
- **heartbeat و idle timeout.** سرور هر `PVAI_WS_HEARTBEAT` (پیش‌فرض ۱۵s) یک `ping` می‌فرستد و
  اتصالی که `PVAI_WS_IDLE_TIMEOUT` (پیش‌فرض ۶۰s) هیچ پیامی نفرستد بسته می‌شود؛ پیش از این
  دستگاه مرده فقط با timeout چنددقیقه‌ای TCP آزاد می‌شد و مدل‌ها اشغال می‌ماند. دستگاه باید
  به `ping` با `pong` پاسخ دهد.
- اتصال همزمان سقف دارد (`PVAI_WS_MAX_CONNECTIONS`، پیش‌فرض ۴) و نرخ اتصال هم
  (`PVAI_WS_CONNECT_RATE`). مدل‌ها (Whisper و Piper) **یک‌بار** در کل فرایند بار می‌شوند و بین
  اتصال‌ها مشترک‌اند؛ بارگذاری در `asyncio.to_thread` انجام می‌شود تا loop و REST بلاک نشود.
  چون هیچ‌کدام thread-safe نیستند، decode در `LocalAudio._decode_lock` و هر گام مدل Piper در
  قفل همان صدا (`ModelPool.piper_lock`) سریال می‌شود — پخش دو دستگاه هم‌زمان هم‌پوشانی دارد،
  ولی مدل مشترک هم‌زمان پیش برده نمی‌شود.
- یک `session_id` در هر لحظه یک نویسنده دارد؛ دستگاه دوم `error` کد `session_in_use` می‌گیرد
  (دو نویسنده روی یک JSONL رکوردها را درهم می‌کردند).
- `interrupt` از دستگاه: `Pipeline.barge_in` اجرا می‌شود، صف پخش همان لحظه خالی می‌شود و سپس
  `play_stop` می‌رود؛ هیچ فریمی از نوبت لغوشده بعد از `play_stop` ارسال نمی‌شود.
- `speech_start`/`speech_end` از دستگاه فقط **اطلاعی** هستند؛ endpointing مال سرور است.
- `set_voice` صدای جمله‌های بعدی را عوض می‌کند؛ اگر مدل آن صدا نباشد `error` کد `voice_unavailable`.
- متریک‌ها: `ws_connections`, `ws_frames_in`, `ws_frames_out`, `ws_auth_failures`,
  `ws_control_dropped`, `ws_capture_gap_frames`, `ws_capture_out_of_order`,
  `ws_capture_bad_size`, `ws_capture_rate_limited`, `ws_voice_rejected`, `ws_heartbeats`,
  `ws_idle_timeouts`. سمت REST: `api_auth_failures`, `api_rate_limited`.
- `/ready` (پشت توکن) کلید `ws_voice` را برمی‌گرداند (خاموش/بدون‌توکن/آماده).

## ۷. امنیت LAN

- **هر مسیر REST توکن می‌خواهد.** `PVAI_API_TOKEN` به‌صورت
  `Authorization: Bearer <token>` (یا `X-API-Key`) روی همهٔ مسیرها جز `/health` بررسی می‌شود.
  پیش از این `GET /api/sessions/{id}` متن کامل گفتگو را بی‌احراز هویت برمی‌گرداند و
  `/v1/chat/completions` هم آزاد بود — با `PVAI_HOST=0.0.0.0`، که برای دستگاه LAN لازم است،
  هر کسی در شبکه این‌ها را می‌دید. `/health` فقط `{"status": "ok"}` است: بدون نام مدل، بدون
  مسیر داده. جزئیات پشت توکن در `/ready` است.
- **اتصال روی نشانی غیر-loopback بدون توکن بالا نمی‌آید.** `PVAI_HOST=0.0.0.0` یا `::` بدون
  `PVAI_API_TOKEN` خطای پیکربندی است، پس حالت «امن نیست ولی کار می‌کند» وجود ندارد.
  روی `127.0.0.1` توکن اختیاری است.
- نرخ درخواست REST محدود است (`PVAI_API_RATE_LIMIT`/`PVAI_API_RATE_BURST`).
- توکن در `.env` نگه داشته می‌شود؛ در مخزن commit نشود (`.gitignore`).
- **TLS لازم است اگر شبکه مورد اعتماد نیست.** توکن روی WS و REST به‌صورت plaintext روی LAN
  می‌رود؛ در شبکهٔ مشترک (WiFi عمومی، خوابگاه، مهمان) هر کسی می‌تواند آن را بخواند.
  جلوی سرور یک reverse proxy با `wss://`/`https://` بگذارید (Caddy یا nginx) و دستگاه را به
  آن وصل کنید. سرور خودش TLS terminate نمی‌کند.
- این وضعیت **هشدار** است نه خطا: در لاگ startup و در `/ready` (کلید
  `checks.configuration.warnings`) دیده می‌شود. `config_errors` عمداً چیزی نمی‌گوید، چون آن
  یکی سخت‌گیر است (`run_live` را متوقف می‌کند و `/ready` را ناآماده می‌کند) و نباید راه‌اندازی
  خانگی مستند را غیرممکن کند.
- توکن per-device است (بخش ۶): هر توکن فقط با یک `device_id` کار می‌کند، پس لو رفتن توکن
  یک دستگاه بقیه را باز نمی‌کند و بستن آن دستگاه بقیه را قطع نمی‌کند.

## ۸. آنچه هنوز لازم است (به ترتیب)

1. firmware ESP-IDF: I2S capture/playback، VAD سبک سمت دستگاه، reconnect (شروع نشده).
2. رجیستری دستگاه، توکن per-device، rate limit و لاگ اتصال — **پیاده شد** (بخش ۶ و ۷)؛
   تنها حلقهٔ باقی‌مانده `pong` در firmware است.
3. OTA و مدیریت نسخهٔ firmware (شروع نشده).
4. سنجش RTT/LAN jitter واقعی با `ping/pong` روی سخت‌افزار — **NOT MEASURED**.

### HTTPS / WSS در LAN

با `PVAI_SSL_CERTFILE` و `PVAI_SSL_KEYFILE` روی uvicorn، دستگاه به
`wss://host:port/ws/voice` وصل می‌شود. اگر گواهی را در reverse proxy پایان
می‌دهید، API را فقط روی `127.0.0.1:8000` نگه دارید و توکن REST و دستگاه را
حفظ کنید. نمونهٔ Caddy (دامنه و گواهی معتبر خودتان):

```caddy
voice.example.internal {
    reverse_proxy 127.0.0.1:8000
}
```

برای nginx نیز `proxy_pass http://127.0.0.1:8000`،
`proxy_set_header Upgrade $http_upgrade` و `proxy_set_header Connection "upgrade"`
در location `/ws/voice` ضروری است. اتصال دستگاه `wss://` باشد؛ در شبکهٔ
غیرمطمئن `ws://` توکن hello و صوت را آشکار می‌کند. گواهی خودامضا نیاز به
نصب CA روی firmware دارد؛ بدون آن اعتبارسنجی TLS را خاموش نکنید.

---

<a id="section-8"></a>

## بخش 8 — docs/DATASETS.md

# آماده‌سازی دادهٔ سفارشی (آفلاین)

## دامنهٔ فعلی

این مخزن **حلقهٔ آموزش یا fine-tuning ندارد**؛ Whisper/CTranslate2، Piper و
llama.cpp برای inference محلی استفاده می‌شوند. `benchmarks/persian_eval.py`
برای ارزیابی است و `tools/prepare_dataset.py` فقط اعتبارسنجی و خروجی manifest
انجام می‌دهد. batch size، learning rate و epochs در کد فعلی مصرف‌کننده ندارند؛
آن‌ها باید در argparse یا فایل پیکربندی trainer آینده تعریف شوند، نه در inference.
فایل CTranslate2 `model.bin` جایگزین checkpoint قابل آموزش Whisper نیست.

## ۱. جمع‌آوری و جداسازی

- فقط صوت دارای رضایت و مجوز مناسب استفاده کنید؛ اطلاعات شخصی را حذف کنید.
- دادهٔ خصوصی را بیرون مخزن نگه دارید. صوت و manifest را commit نکنید.
- train/validation/test را **پیش از آموزش** بر اساس گوینده و جلسهٔ ضبط جدا کنید؛
  قطعه‌های یک ضبط نباید در چند split باشند. validator هر manifest را مستقل بررسی
  می‌کند و عدم هم‌پوشانی بین splitها را تضمین نمی‌کند.
- مجموعهٔ `benchmarks/persian_eval_set.json` برای ارزیابی است؛ آن را وارد train نکنید.
- متن باید رونویسی واقعی همان قطعه باشد. نرمال‌سازی WER در
  `server.persian.normalize_for_eval` سیاست ارزیابی است، نه الزام تغییر متن آموزش.

## ۲. قرارداد manifest

هر split یک فایل UTF-8 با پسوند `.json`، `.jsonl` یا `.csv` دارد.
JSON یک آرایهٔ غیرخالی از objectهاست؛ JSONL هر خط یک object؛ CSV هدر الزامی دارد.
فیلدهای الزامی و رشته‌ای غیرخالی:

| فیلد | معنی |
|---|---|
| `id` | شناسهٔ یکتا در manifest |
| `audio` | مسیر محلی فایل WAV |
| `text` | رونویسی مرجع |

نمونهٔ JSONL (`train.jsonl`):

```json
{"id":"speaker01-001","audio":"audio/speaker01-001.wav","text":"سلام حال شما چطور است؟"}
```

نمونهٔ CSV:

```csv
id,audio,text
speaker01-001,audio/speaker01-001.wav,سلام حال شما چطور است؟
```

فیلد CSV دارای کاما باید داخل کوتیشن باشد. فیلدهای اضافی در export حفظ نمی‌شوند؛
نسخهٔ اصلی manifest و metadata گوینده/مجوز را جداگانه نگه دارید.

## ۳. مسیرها و اعتبارسنجی

مسیرهای نسبی `audio` به **پوشهٔ manifest** وابسته‌اند، نه پوشهٔ اجرای فرمان.
`--audio-root` این مبنا را تغییر می‌دهد؛ مقدار نسبی آن نسبت به پوشهٔ اجرای فرمان است.
مسیر مطلق، `~` و متغیر محیطی مانند `${CORPUS_ROOT}` نیز پشتیبانی می‌شوند.
خروجی مسیر صوت را نسبت به پوشهٔ manifest خروجی ذخیره می‌کند؛ برای انتقال، ساختار
نسبی پوشه‌های صوت و manifest را با هم حفظ کنید. متغیر محیطی تعریف‌نشده در نهایت
به‌صورت مسیر ناموجود رد می‌شود. هیچ دانلود یا تماس شبکه‌ای انجام نمی‌شود.

```bash
python tools/prepare_dataset.py --manifest /path/to/corpus/train.jsonl
python tools/prepare_dataset.py --manifest /path/to/corpus/train.csv \
  --audio-root /path/to/recordings --sample-rate 16000 \
  --out /path/to/corpus/validated/train.jsonl
```

همهٔ پارامترهای preprocessing از argparse می‌آیند:
`--manifest` (اجباری)، `--audio-root` (پیش‌فرض پوشهٔ manifest)،
`--sample-rate` (پیش‌فرض 16000)، `--out` (اختیاری).

validator با کتابخانهٔ استاندارد پایتون فایل را واقعاً باز می‌کند و WAV PCM16،
mono، نرخ انتخاب‌شده، غیرخالی بودن و کامل بودن فریم‌ها را بررسی می‌کند.
شناسه یا recording تکراری، نوع فیلد نامعتبر، JSON خراب و صوت ناخوانا با پیام شامل
مسیر/شمارهٔ رکورد و exit code 2 رد می‌شوند. فایل ورودی بازنویسی نمی‌شود.
این ابزار صوت را تغییر نمی‌دهد؛ تبدیل را آگاهانه روی یک کپی انجام دهید، مثلاً با
FFmpeg نصب‌شدهٔ محلی:

```bash
ffmpeg -i original.wav -ar 16000 -ac 1 -c:a pcm_s16le normalized.wav
```

## ۴. استفاده با trainer خارجی یا Hugging Face

این ابزار `DatasetDict`، مسیر `save_to_disk` یا نام dataset روی Hub را نمی‌خواند.
برای دادهٔ Hugging Face، هر split را **محلی** به قرارداد JSONL بالا export کنید؛
ستون audio باید مسیر فایل باشد، نه object، آرایهٔ نمونه‌ها یا URL.
اگر داده فقط آرایه دارد، ابتدا آن را با نرخ صحیح به WAV محلی ذخیره کنید.
شناسهٔ پایدار به هر ردیف بدهید و splitها را جدا اعتبارسنجی کنید.
پس از validation، trainer خارجی باید manifest را بخواند و مسیرهای نسبی audio را
نسبت به خود manifest resolve کند. هایپرپارامترها، seed، نسخهٔ مدل/داده و وابستگی‌ها
را در پیکربندی همان trainer ثبت کنید. این مخزن اجرای آموزش را ارائه نمی‌دهد.

## ۵. ارزیابی پس از آموزش

فرمت ارزیابی با فرمت train متفاوت است: JSON دارای کلید `sentences`، با `id` و
`text` و فیلدهای اختیاری `category` و `kind`؛ شناسه فقط حروف لاتین، رقم، `_` و `-`
دارد. `kind=no_speech` باید متن خالی داشته باشد. برای حالت صوت فایل
`<id>.wav` با PCM16 mono 16kHz بسازید.

```bash
python benchmarks/persian_eval.py --set /path/to/eval.json --list
python benchmarks/persian_eval.py --set /path/to/eval.json \
  --hypotheses /path/to/transcripts.json --strict
```

hypotheses یک object از `id: text` یا لیست objectهای `id` و `text` است؛ شناسهٔ
تکراری در لیست و مقدار غیررشته‌ای رد می‌شود. معیارها روی دادهٔ واقعی گزارش شوند؛
validation manifest به‌تنهایی تأیید کیفیت برچسب‌ها یا نتیجهٔ آموزش نیست.

---

<a id="section-9"></a>

## بخش 9 — docs/PLATFORMS.md

# Platform support

The Python code is portable: it is developed and tested on Linux with no
platform-specific imports in its core. Windows (the original target), Linux and
macOS are intended platforms; this session only exercised the Linux sandbox. The gap was never the code — it was the tooling, which shipped only
PowerShell. That is now fixed, with two deliberate exceptions listed below.

## What runs where

| Area | Windows | Linux / macOS | Notes |
|---|---|---|---|
| Server, REST, WebSocket | yes | yes | `python -m server` / `uvicorn server.main:app` |
| Chat client | yes | yes | `python -m server.cli` |
| Test suite | yes | yes | run `python -m pytest -rs`; Windows/macOS execution not verified here |
| llama-server launcher | `tools/start_llama.ps1` | `tools/start_llama.sh` | both read `python -m server.config --launch` |
| Server launcher | `tools/start_server.ps1` | `tools/start_server.sh` | |
| Chat launcher | `tools/chat.ps1` | `tools/chat.sh` | |
| Chat benchmark | `tools/benchmark.ps1` | `tools/benchmark.sh` | |
| Large-file download | `tools/download_parallel.py` | `tools/download_parallel.py` | one Python implementation, both platforms |
| SpeexDSP AEC | needs a built DLL | needs a built `.so` / `.dylib` | see `docs/AEC.md`; not a pip install |
| Audio I/O | WASAPI via `sounddevice` | ALSA/PulseAudio via `sounddevice` | device names differ; check `/ready` |

Every `.ps1` in `tools/` has a `.sh` twin, and
`tests/test_tools_scripts.py` fails if a new unmatched one appears.

## Choosing the interpreter

The shell scripts look for `.venv` next to the repository. For bash scripts, set `PVAI_PYTHON` to
point somewhere else (PowerShell scripts use `.venv\Scripts\python.exe`):

```bash
PVAI_PYTHON=/opt/pvai/bin/python ./tools/start_server.sh
```

## Legacy (not on supported launch path)

Old Ollama setup, lesson-1 verification and its historic report are archived under
`docs/legacy/`. Use the current llama.cpp launchers and benchmark instead.

## Path and environment differences

- Paths in `.env` may be absolute or relative to the repository root;
  `server/config.py` resolves them, so `/opt/models/x.gguf` and `models/x.gguf`
  both work.
- `llama-server` is `llama-server` on both platforms; pass `--exe` / `-Exe` when
  it is not on `PATH`.
- The `.env` loader skips a leading UTF-8 BOM, because Windows editors add one.

## What is *not* verified

Real audio, real models and real microphones have not been exercised on either
platform in this environment — see `docs/HARDWARE_VALIDATION.md`. "Runs on
Linux" here means the test suite passes and the scripts parse and execute, not
that a Persian conversation has been held on a Linux desktop.

---

<a id="section-10"></a>

## بخش 10 — docs/TROUBLESHOOTING.md

# عیب‌یابی — پیام‌ها، علت و راه‌حل

پیام‌های این سند همان متن‌هایی هستند که کد تولید می‌کند (`grep` در `server/` قابل تأیید است).
هیچ خطای دامنه‌ای به‌صورت traceback خام به کاربر نمی‌رسد؛ اگر traceback دیدید، آن یک باگ است.

## ۰. اولین قدم همیشه

```powershell
curl http://127.0.0.1:8000/ready
curl http://127.0.0.1:8000/health
```

`/ready` برای هر بخش (configuration، asr_model، tts_model، dependencies، aec، storage،
llm_backend) وضعیت `ok` و پیام فارسی می‌دهد و کل پاسخ `503` می‌شود اگر چیزی ناقص باشد.
`/health` فقط liveness برنامه (`{"status":"ok"}`) است و اتصال backend را بررسی نمی‌کند.
اگر توکن REST تنظیم شده، برای `/ready` هدر Authorization لازم است.

---

## ۱. مدل‌ها و فایل‌ها

| پیام | علت | راه‌حل |
|---|---|---|
| «مدل Whisper محلی موجود نیست؛ PVAI_ASR_MODEL_PATH را تنظیم کنید.» | مسیر پوشه یا `model.bin` نیست | پوشهٔ CTranslate2 کامل (نه فایل PyTorch) را در مسیر بگذارید |
| «فایل‌های مدل Whisper ناقص است: model.bin» | فقط بخشی از مدل کپی شده | `config.json`, `tokenizer.json`, `vocabulary.txt` و وزن‌ها را کامل کنید |
| «مدل Piper موجود نیست؛ …» | `.onnx` نیست | `PVAI_TTS_MODEL_PATH` یا `PVAI_TTS_MODEL_MALE/FEMALE` را درست کنید |
| «فایل تنظیمات مدل Piper (.onnx.json) موجود نیست» | فایل کنار مدل کپی نشده | هر دو فایل `.onnx` و `.onnx.json` لازم است |
| «بارگذاری مدل Piper ناموفق بود؛ …onnxruntime…» | نسخهٔ piper/onnxruntime ناسازگار | `requirements-voice.txt` را در همان venv نصب کنید |
| «بارگذاری مدل Whisper ناموفق بود؛ … PVAI_ASR_DEVICE/PVAI_ASR_COMPUTE_TYPE» | `cuda` بدون GPU/کتابخانه یا `float16` روی CPU | `PVAI_ASR_DEVICE=cpu`, `PVAI_ASR_COMPUTE_TYPE=int8` |

## ۲. صدا و دستگاه

| پیام | علت | راه‌حل |
|---|---|---|
| «هیچ دستگاه صوتی به سیستم متصل نیست.» | PortAudio دستگاهی نمی‌بیند | درایور/هدفون را بررسی کنید؛ `python -m server.voice --list-devices` |
| «شمارهٔ دستگاه … نامعتبر است» | index بیرون از فهرست | فهرست را دوباره بگیرید (شماره‌ها با اتصال دستگاه تغییر می‌کنند) |
| «دستگاه … با نام «…» پیدا نشد یا کانال لازم را ندارد» | نام اشتباه یا دستگاه فقط ورودی/خروجی است | نام دقیق از فهرست؛ یا index |
| «نام دستگاه … مبهم است (…)» | چند دستگاه با همان زیررشته | از index دقیق استفاده کنید |
| «دستگاه … نرخ 16000Hz تک‌کاناله را نمی‌پذیرد» | میکروفون فقط 44.1/48k را می‌پذیرد | در Windows از "Advanced" دستگاه نرخ 16k را فعال کنید یا دستگاه دیگری انتخاب کنید (بازنمونه‌برداری خودکار ورودی زندهٔ دستگاه پیاده نشده؛ تبدیل خودکار فقط در ورودی فایل ASR است) |
| «جریان صوتی بیش از N ثانیه فریم نداد؛ دستگاه قطع شده است.» | USB/هدست جدا شد یا درایور هنگ کرد | دستگاه را وصل کنید؛ برنامه تا `PVAI_AUDIO_REOPEN_ATTEMPTS` بازگشایی می‌کند و بعد خطای روشن می‌دهد |
| «ورودی صوت عقب افتاد؛ نوبت جاری حذف شد.» | صف capture پر شد (CPU یا دیسک کند) | بار سیستم را کم کنید؛ `PVAI_AUDIO_BLOCK_MS` و threadها را بررسی کنید. **finalهای قبلی حذف نمی‌شوند** |

## ۳. AEC

| پیام | علت | راه‌حل |
|---|---|---|
| «کتابخانهٔ SpeexDSP پیدا نشد؛ PVAI_AEC_LIBRARY را تنظیم کنید یا AEC را صریحاً خاموش کنید.» | DLL/SO نصب نیست | طبق [AEC.md](AEC.md) نصب کنید یا `PVAI_AEC_ENABLED=false` (با هدفون) |
| «نمادهای لازم در کتابخانهٔ SpeexDSP نیست: …» | کتابخانهٔ اشتباه (نسخهٔ ۳۲بیتی/بدون echo) | DLL ۶۴بیتی صحیح |
| «AEC فقط با 16kHz و فریم 20ms کار می‌کند» | تنظیمات صوتی تغییر کرده | `PVAI_AUDIO_SAMPLE_RATE=16000` و `PVAI_AUDIO_BLOCK_MS=20` |
| AEC فعال است ولی دستیار خودش را قطع می‌کند | تأخیر مرجع نامناسب یا اکوی زیاد | `PVAI_AEC_DELAY_MS` را ۲۰ms‌به‌۲۰ms جابه‌جا و تست کنید؛ `PVAI_VOICE_BARGE_IN_MARGIN` را بالا ببرید؛ در نهایت هدفون |

هشدار: موفقیت تست مصنوعی به معنی حذف اکوی اتاق واقعی نیست. تا کالیبراسیون، بدون هدفون
ممکن است barge-in نادرست رخ دهد.

## ۴. گفتار تشخیص داده نمی‌شود یا زود تمام می‌شود

| نشانه | تنظیم |
|---|---|
| صدای آرام تشخیص داده نمی‌شود | `PVAI_VOICE_VAD_THRESHOLD` را کم کنید (مثلاً ۰٫۰۱ → ۰٫۰۰۵) |
| نویز پنکه utterance می‌سازد | آستانه را زیاد کنید؛ `PVAI_VOICE_VAD_NOISE_MULTIPLIER` را بالا ببرید؛ `PVAI_VOICE_VAD_MIN_SPEECH_MS` را بیشتر کنید |
| جمله وسط مکث کوتاه قطع می‌شود | `PVAI_VOICE_SILENCE_MS` را زیاد کنید (مثلاً ۸۰۰) |
| حرف اول کلمه بریده می‌شود | `PVAI_VOICE_VAD_PRE_ROLL_MS` را زیاد کنید (تا ۴۰۰) |
| پایان جمله دیر تشخیص داده می‌شود | `PVAI_VOICE_SILENCE_MS` را کم کنید |
| گفتار بلند چند تکه می‌شود | طبیعی است (سقف `PVAI_VOICE_VAD_MAX_SEGMENT_S`)؛ همهٔ تکه‌ها حفظ می‌شوند و در تاریخچه به ترتیب می‌آیند |
| هیچ `transcript_partial` نمی‌بینم | `PVAI_VOICE_ASR_INTERVAL` را کم کنید؛ اگر CPU عقب است interval را **زیاد** کنید (Whisper کندتر از real-time است) |

رویدادهای `speech_start`/`speech_end` شامل `rms`، `noise_floor` و `boundary` هستند؛ برای
کالیبراسیون همین اعداد را در سکوت و در گفتار مقایسه کنید.

## ۵. پاسخ‌دهی و barge-in

| نشانه | علت | راه‌حل |
|---|---|---|
| «پاسخ استریم ناموفق بود؛ اتصال مدل و llama-server را بررسی کنید.» | llama-server قطع/خطا | `tools/start_server.ps1`، `/health` |
| «استریم پاسخ ناقص بود.» | اتصال SSE وسط کار بسته شد | سرور و مدل را بررسی کنید؛ پاسخ ناقص در تاریخچه ثبت نمی‌شود |
| «تولید یا پخش صدا ناموفق بود؛ مدل Piper و دستگاه خروجی را بررسی کنید.» | مدل/دستگاه خروجی | `/ready` و فهرست دستگاه‌ها |
| «تشخیص گفتار ناموفق بود؛ مدل محلی Whisper را بررسی کنید.» | خطای decode | مدل و RAM را بررسی کنید؛ utterance بعدی ادامه می‌دهد |
| دستیار حرف کاربر را قطع نمی‌کند | آستانه یا AEC | `PVAI_VOICE_BARGE_IN_MS` را کم کنید (مثلاً ۱۲۰) و margin را پایین بیاورید |
| دستیار با کوچک‌ترین صدا قطع می‌شود | echo/noise | margin را بالا ببرید، AEC را کالیبره کنید |
| اولین utterance کند است | warmup خاموش | `PVAI_ASR_WARMUP=true` (پیش‌فرض) تا مدل در startup لود شود |

## ۶. API و نشست‌ها

| کد | معنا | راه‌حل |
|---|---|---|
| `503` با `backend unavailable` | llama-server در دسترس نیست | سرویس مدل را شروع کنید |
| `503` `model_not_loaded` | مدل در llama-server لود نشده | `-m` را بررسی کنید |
| `502` `generation error` | خطای تولید | لاگ llama-server |
| `400` «شناسهٔ نشست نامعتبر است.» | `session_id` کاراکتر غیرمجاز دارد | فقط حروف **انگلیسی**/رقم/`-`/`_`، حداکثر ۶۴ کاراکتر، و نه نام دستگاه رزروشدهٔ ویندوز (`CON`, `NUL`, `COM1`…) |
| `404` «نشست پیدا نشد.» | نشست ذخیره نشده | `PVAI_HISTORY_PERSIST=true` و `/api/sessions` |

## ۶.۱. تنظیمات `.env` بی‌اثر به نظر می‌رسد

| نشانه | علت | راه‌حل |
|---|---|---|
| مقدار `.env` عوض می‌شود ولی رفتار سرویس تغییر نمی‌کند | llama-server دستی با پرچم‌های خودش اجرا شده؛ `PVAI_N_THREADS`/`PVAI_N_CTX`/`PVAI_N_GPU_LAYERS` را کلاینت HTTP نمی‌خواند | با `tools/start_llama.ps1` اجرا کنید یا `python -m server.config --launch` را ببینید |
| `GET /ready` در بخش `configuration` خطای `PVAI_...` دارد | مقدار غیرعددی/نامفهوم بود و به پیش‌فرض برگشت (WARNING در لاگ) | مقدار آن متغیر را در `.env` اصلاح کنید |
| «مقدار کلمپ شد» در لاگ | عدد خارج از بازهٔ مجاز بود | بازهٔ مجاز را در `docs/CONFIGURATION.md` ببینید |
| `.env` ساخته شده ولی خوانده نمی‌شود | فایل در پوشهٔ جاری و ریشهٔ پروژه نیست | `.env` را در ریشهٔ پروژه بگذارید؛ `PVAI_DATA_DIR`/مسیرها نسبت به همان‌جا خوانده می‌شوند |

> کامنت هم‌خط حذف می‌شود، ولی توضیح را در خط جدا بنویسید: `PVAI_AUDIO_INPUT_DEVICE=Realtek Microphone`
> با یک ` # ...` در ادامه، نام دستگاه را می‌بُرد.

## ۷. عیب‌یابی داده و حافظه

```powershell
# فهرست و حذف نشست‌های محلی
.\.venv\Scripts\python.exe -m server.sessions list
.\.venv\Scripts\python.exe -m server.sessions show <session_id>
.\.venv\Scripts\python.exe -m server.sessions delete <session_id>
.\.venv\Scripts\python.exe -m server.sessions delete-all
.\.venv\Scripts\python.exe -m server.sessions purge --days 30
```

- داده در `PVAI_DATA_DIR/conversations/*.jsonl` است (در `.gitignore`).
- `PVAI_HISTORY_PERSIST=false` هیچ فایلی نمی‌سازد (تست: `test_persistence_can_be_disabled`).
- پاسخ‌های قطع‌شده با `status=interrupted` ذخیره می‌شوند و به‌طور پیش‌فرض وارد context مدل نمی‌شوند.
- **شروع تازه می‌خواهید؟** `PVAI_HISTORY_RESUME=off` بگذارید (هر اجرا نشست تازه) یا
  `python -m server.sessions delete-all`. برعکس، اگر بعد از restart مکالمه ادامه نیافت،
  `PVAI_HISTORY_PERSIST=true` و `PVAI_HISTORY_RESUME=on` را بررسی کنید؛ پیام startup
  «ادامهٔ نشست … با N پیام قبلی» نشان می‌دهد resume انجام شده است.
- **`sessions purge` می‌گوید «مدت نگه‌داری تعیین نشده است»؟** `PVAI_HISTORY_RETENTION_DAYS`
  را تنظیم کنید یا `--days N` بدهید (پیش‌فرض کد `30` روز است؛ مقدار `0` پاک‌سازی را غیرفعال می‌کند).

## ۸. اگر مشکل پیدا نشد

1. `PVAI_LOG_LEVEL=DEBUG`.
2. رویدادها JSON line هستند؛ `speech_start`/`speech_end`/`transcript_final`/`interrupted`/`error`
   را ذخیره کنید (بدون متن خصوصی اگر می‌خواهید گزارش بدهید).
3. `python tools/benchmark_voice.py --scenarios idle` را اجرا کنید تا مشخص شود مسئله در
   دستگاه/هماهنگ‌کننده است یا در مدل‌ها.
4. اگر پاسخ اشتباه است ولی جریان درست: مسئله کیفیت مدل است، نه خط لوله — به `docs/BENCHMARKS.md`
   بخش ارزیابی فارسی مراجعه کنید.

---

<a id="section-11"></a>

## بخش 11 — docs/BENCHMARKS.md

# روش‌شناسی بنچمارک و نتایج

## ۱. چرا برچسب‌گذاری اجباری است

هر عدد در این پروژه یکی از سه برچسب زیر را دارد و **هیچ عددی بدون برچسب نقل نمی‌شود**:

| برچسب | معنا |
|---|---|
| `MEASURED` | همین حالا روی همین ماشین با کامپوننت واقعی اندازه‌گیری شده |
| `SIMULATED` | کامپوننت واقعی (وزن مدل/سرور) موجود نبوده؛ تأخیر ساختگی و فقط اثبات اجرای مسیر |
| `NOT MEASURED` | برای اندازه‌گیری نیاز به سخت‌افزار/وزن/اتصال است و انجام نشده |

`SIMULATED` هرگز نباید به‌عنوان «کارایی» نقل شود. جدول target (i7-6500U) تا اجرای واقعی
`NOT MEASURED` است.

## ۲. اجرا

```powershell
# همهٔ سناریوها (آنچه در دسترس باشد واقعی، بقیه برچسب‌گذاری می‌شوند)
.\.venv\Scripts\python.exe tools\benchmark_voice.py

# فقط idle و یک نوبت کامل
.\.venv\Scripts\python.exe tools\benchmark_voice.py --scenarios idle,combined

# ذخیرهٔ گزارش
.\.venv\Scripts\python.exe tools\benchmark_voice.py --json-out benchmarks\results\voice-pc.json
```

هفت گروه اندازه‌گیری که بازبینی الزام کرده است، به‌همراه دو سناریوی تکمیلی:

| گروه/سناریو | چه چیزی اندازه می‌گیرد | پیش‌نیاز واقعی |
|---|---|---|
| `idle` | pacing هماهنگ‌کننده (jitter p95/max)، CPU، RAM، VAD false positive | — (همیشه MEASURED) |
| `asr` (ASR only) | `asr_ms` و `asr_rtf` روی بافر ۱/۳/۶ ثانیه | مدل Whisper محلی |
| `llm` (LLM only) | `llm_ttft_ms`، `llm_tokens_per_second`، زمان کل استریم | llama-server |
| `tts` (TTS only) | `tts_first_chunk_ms`، `tts_first_frame_ms`، مدت پخش | مدل Piper + `.onnx.json` |
| `asr_llm` (ASR+LLM) | `asr_ms` + `llm_ttft_ms` + `turn_total_ms` بدون TTS | Whisper + llama-server |
| `asr_llm_tts` (ASR+LLM+TTS) | یک نوبت کامل گفتاری: VAD → final ASR → SSE → جمله → TTS → commit | هر سه |
| `duplex` (مکالمهٔ کامل دوطرفه) | چند نوبت پشت‌سرهم، یک قطع، `turn_latency_mean/max_ms`، پیام‌های ثبت‌شده | هر سه |
| `bargein` | `barge_in_latency_ms`، شمارش پاسخ کهنه | همان |
| `combined` | نام تاریخی همان `asr_llm_tts` | هر سه |

سناریوهای چندنوبتی (`duplex`) با `--seconds` تعداد نوبت را هم تعیین می‌کنند (حداقل ۲).

### قواعد اندازه‌گیری

1. هر سناریو را **حداقل ۵ بار** اجرا و **میانه** را گزارش کنید؛ اولین اجرا warmup است.
2. همهٔ برنامه‌های سنگین دیگر (مرورگر، CPU-boost) را ببندید؛ روی باتری اجرا نکنید.
3. برای CPU/RAM سنجه‌های `server/resources.py` استفاده می‌شود (psutil اگر نصب باشد، وگرنه
   `/proc` در Linux و `psapi` در Windows). نبود ابزار = `NOT MEASURED`، نه صفر.
4. `asr_rtf < 1` یعنی decode سریع‌تر از زمان واقعی؛ روی i7-6500U با whisper-small/int8
   انتظار می‌رود نزدیک ۱ یا بیشتر باشد، اما **تا اندازه‌گیری، ادعا نمی‌شود**.
5. `first_audible_ms` از پایان utterance تا اولین بلوک تحویل‌شده به دستگاه است؛
   تأخیر DAC/بلندگو از پایتون قابل اندازه‌گیری نیست و باید با گوش/اسکوپ سنجیده شود.
6. `barge_in_latency_ms` زمان منطقی teardown است (cancel task + set token + پاک‌کردن صف).
   قطع واقعی صدا یک بلوک ۲۰ms بعد از آن رخ می‌دهد؛ تست سخت‌افزاری جدا لازم است.

## ۳. نتایج sandbox (۸ نوامبر ۲۰۲۶)

محیط: Linux sandbox، Python 3.11، **بدون** مدل Whisper/Piper، **بدون** llama-server، بدون دستگاه صوتی.
دستور: `tools/benchmark_voice.py --quick --json-out benchmarks/results/voice-sandbox-20261108.json`

| سناریو | معیار | مقدار | واحد | برچسب |
|---|---|---|---|---|
| idle | `audio_loop_jitter_p95_ms` | ۰٫۳۹ | ms | MEASURED |
| idle | `audio_loop_jitter_max_ms` | ۰٫۴۲ | ms | MEASURED |
| idle | `cpu_usage_percent` | ~۱ | % | MEASURED (بدون مدل) |
| idle | `ram_usage_mb` | ~۳۰ | MB | MEASURED |
| idle | `vad_false_positives` | ۰ | count | MEASURED |
| asr | `asr_ms`, `asr_rtf` | — | — | NOT MEASURED (مدل Whisper موجود نیست) |
| llm | `llm_ttft_ms`, `llm_tokens_per_second` | — | — | NOT MEASURED (llama-server در دسترس نبود) |
| tts | `tts_first_chunk_ms` | — | — | NOT MEASURED (مدل Piper موجود نیست) |
| asr_llm | `asr_ms` + `llm_ttft_ms` + `turn_total_ms` | ~۲۰۵ | ms | SIMULATED (ASR/LLM ساختگی) |
| asr_llm | `turn_completed` | ۱ | flag | MEASURED |
| asr_llm_tts | `first_audible_ms` / `turn_total_ms` | ~۲۵۳ / ~۶۶۴ | ms | SIMULATED |
| asr_llm_tts | `vad_start_ms` / `vad_end_ms` | ۶۰ / ۶۰۰ | ms | MEASURED (هم‌خوان با تنظیمات) |
| duplex | `turn_latency_mean_ms` (۲ نوبت) | ~۲۰۶ | ms | SIMULATED |
| duplex | `interrupted_turns` | ۱ | count | MEASURED |
| duplex | `stale_completions_after_interrupt` | ۰ | count | MEASURED |
| duplex | `messages_recorded` | ۴ | count | MEASURED (هیچ utterance گم نشد) |
| bargein | `barge_in_latency_ms` | <۱ | ms | SIMULATED (کامپوننت‌ها ساختگی) |
| bargein | `stale_completions_after_interrupt` | ۰ | count | MEASURED |

**تفسیر:** این جدول فقط ثابت می‌کند مسیر کامل بدون استثنا اجرا می‌شود و کرانه‌ها نگه داشته می‌شوند.
هیچ نتیجه‌ای دربارهٔ کارایی روی i7-6500U از آن گرفته نمی‌شود.

## ۴. نتایج target hardware

| معیار | i7-6500U (Windows 11) |
|---|---|
| `asr_ms` / `asr_rtf` (whisper-small int8, 6s window) | NOT MEASURED |
| `asr_final_latency_ms` (utterance 3s) | NOT MEASURED |
| `llm_ttft_ms` (Qwen3-4B-Q4_K_M) | NOT MEASURED |
| `llm_tokens_per_second` | NOT MEASURED |
| `tts_first_chunk_ms` (Piper fa) | NOT MEASURED |
| `first_audible_ms` | NOT MEASURED |
| `barge_in_latency_ms` (real device) | NOT MEASURED |
| `audio_callback_jitter_ms` | NOT MEASURED |
| CPU/RAM در ASR+LLM+TTS هم‌زمان | NOT MEASURED |

پس از اجرا، این جدول را با همان برچسب‌ها پر کنید و فایل JSON را در `benchmarks/results/` نگه دارید.

## ۵. بنچمارک متنی (سابقه)

`benchmarks/bench_chat.py` و `tools/benchmark.ps1` فقط مسیر متنی را می‌سنجند. نتیجهٔ تاریخی
درس ۱ روی MiniCPM5-1B-Q8_0 (i7-6500U): TTFT ≈ ۱٫۴۵s، کل ≈ ۱۱٫۴۵s، ≈ ۱۹٫۹ tok/s.
این اعداد **به Qwen3-4B و به مسیر صوتی تعمیم داده نمی‌شوند** و Qwen با وزن واقعی هنوز سنجیده نشده است.

## ۶. ارزیابی فارسی (WER/CER)

```powershell
# فهرست مجموعه
.\.venv\Scripts\python.exe benchmarks\persian_eval.py --list

# ارزیابی transcript‌های موجود (پس از تست واقعی)
.\.venv\Scripts\python.exe benchmarks\persian_eval.py --hypotheses my_transcripts.json --json-out eval.json

# ارزیابی مستقیم از فایل‌های WAV (فقط با مدل واقعی)
.\.venv\Scripts\python.exe benchmarks\persian_eval.py --audio-dir recordings\fa --strict
```

سیاست نرمال‌سازی (`server.persian.normalize_for_eval`): NFC، `ي/ك→ی/ک`، حذف اعراب و کشیده،
یکسان‌سازی ارقام به ASCII، نیم‌فاصله → فاصله، حذف علائم، جمع‌کردن فاصله‌ها.
دو نکتهٔ مهم:

- **تفاوت فقط در قالب** (حروف عربی، ارقام شرقی، نیم‌فاصله به‌صورت فاصله) خطا شمرده نمی‌شود.
- **حذف نیم‌فاصله** (`میروم` به‌جای `میروم`) یک خطای مرز واژه شمرده می‌شود؛ این عمدی است و
  دستهٔ `zwnj` مجموعه دقیقاً همین را می‌سنجد. اعداد به‌صورت رقم و به‌صورت واژه معادل نیستند
  (`24` با `بیست و چهار` یکسان شمرده نمی‌شود) — این محدودیت سیاست است، نه اشتباه ابزار.

نتیجهٔ فعلی روی دادهٔ واقعی: **NOT MEASURED** (بدون ضبط و وزن مدل).

## کالیبراسیون فیلتر توهم Whisper (NOT MEASURED)

`benchmarks/persian_eval.py --audio-dir DIR` روی همان مجموعهٔ wav گفتار واقعی
`speech_rejections.false_reject_rate` را (تعداد گفتارهای ثبت‌شده که transcript تهی
داشتند / تعداد گفتارها) کنار WER/CER می‌دهد. سکوت/نویز با `kind=no_speech`
و فرضیه‌های همان شناسه‌ها `no_speech.false_accept_rate` را می‌دهد؛ ورودی غایب
در مخرج هیچ‌کدام شمرده نمی‌شود و در `missing_ids` گزارش می‌شود.

آستانه‌های فعلی `PVAI_ASR_MAX_NO_SPEECH_PROB=0.75` و
`PVAI_ASR_LOG_PROB_THRESHOLD=-1.0` فقط اگر **هر دو** عبور کنند segment را حذف
می‌کنند؛ compression_ratio و عبارات ثابت مستقل‌اند. برای تنظیم آستانه‌ها
گفتار/سکوت را با همان میکروفون، محل، وزن Whisper و تنظیمات decode جمع کنید؛
روی یک بخش آستانه را انتخاب و روی بخش نگه‌داشته‌شده هر دو نرخ را گزارش کنید.
هیچ فایل wav/مدل واقعی برای این بازبینی فراهم نبود: هر دو نرخ و آستانهٔ بهینه
**NOT MEASURED** هستند؛ تست‌های stub معیار کیفیت فارسی نیستند.

---

<a id="section-12"></a>

## بخش 12 — docs/HARDWARE_VALIDATION.md

# چک‌لیست اعتبارسنجی روی سخت‌افزار واقعی

> هیچ مورد این سند در sandbox اجرا نشده است. تا زمانی که هر ردیف با تاریخ و نتیجه پر نشود،
> وضعیت آن **NOT TESTED** است. تست‌های خودکار `pytest` با fake اجرا می‌شوند و جای این سند را نمی‌گیرند.

ماشین هدف: Intel i7-6500U (۲ هسته/۴ رشته)، ۱۶GB RAM، GeForce 940MX (۲GB، پیش‌فرض استفاده نمی‌شود)،
Windows 11 64-bit، SSD+HDD.

## ۰. آماده‌سازی

- [ ] Python 3.12 در venv، `pip install -r requirements-voice.txt -r requirements-dev.txt`
      (voice = زمان اجرا، dev = pytest برای اجرای تست‌ها)
- [ ] SpeexDSP DLL ۶۴بیتی طبق [AEC.md](AEC.md) (یا تصمیم صریح برای تست با هدفون)
- [ ] وزن‌ها: `models/Qwen3-4B-Q4_K_M.gguf`، `models/whisper-small/`، `models/piper/fa_*`
- [ ] `.env` طبق [CONFIGURATION.md](CONFIGURATION.md)
- [ ] llama-server روی `127.0.0.1:8080` بالا
- [ ] `GET /ready` → `"status": "ready"` (اگر 503 است، اول همان را رفع کنید)

## ۱. دستگاه و کلاک

- [ ] `python -m server.voice --list-devices` دستگاه‌ها را نشان می‌دهد
- [ ] انتخاب ورودی/خروجی با نام یا index کار می‌کند (پیام «دستگاه صوتی آماده است» با نام درست)
- [ ] `tools/benchmark_voice.py --scenarios idle --seconds 30` بدون خطا؛ jitter را ثبت کنید
- [ ] قطع فیزیکی میکروفون در حین اجرا: پس از حداکثر `PVAI_AUDIO_REOPEN_ATTEMPTS` پیام
      «جریان صوتی بیش از … فریم نداد» و خروج تمیز (بدون traceback)
- [ ] وصل دوباره و اجرای مجدد: برنامه دوباره صدا می‌شنود

## ۲. VAD و تشخیص گفتار

| آزمایش | انتظار | نتیجه |
|---|---|---|
| ۱۰ ثانیه سکوت | هیچ `speech_start` | |
| پنکه/کولر روشن | بدون utterance، فقط `noise_floor` بالاتر | |
| جملهٔ آرام فارسی | `speech_start` با `rms` پایین، متن کامل | |
| «سلام» (حرف «س») | ابتدای کلمه بریده نشود | |
| جمله با مکث کوتاه وسط | یک utterance، نه دو | |
| ۲۰ ثانیه صحبت پیوسته | دو بخش (سقف ۱۵s)، هر دو متن در تاریخچه | |

## ۳. ASR و دقت فارسی

- [ ] `tools/benchmark_voice.py --scenarios asr` اعداد `asr_ms`/`asr_rtf` را MEASURED می‌دهد
- [ ] ضبط ۴۰ جملهٔ `benchmarks/persian_eval_set.json` و اجرای
      `benchmarks/persian_eval.py --audio-dir recordings\fa --strict`
- [ ] WER/CER را در `docs/BENCHMARKS.md` ثبت کنید (هیچ عددی قبل از این مرحله ادعا نمی‌شود)
- [ ] متن فارسی: نیم‌فاصله، ارقام، واژهٔ انگلیسی داخل جمله، نام خاص

## ۴. LLM

- [ ] `tools/benchmark_voice.py --scenarios llm` → TTFT و tok/s (MEASURED)
- [ ] جملهٔ اول پاسخ قبل از پایان متن کامل شنیده می‌شود
- [ ] قطع llama-server وسط پاسخ → پیام فارسی روی ترمینال، نشست زنده می‌ماند
- [ ] `reasoning_content` به صدا تبدیل نمی‌شود (`PVAI_LLM_ENABLE_THINKING=false`)

## ۵. TTS

- [ ] صدای زن و مرد جدا: `PVAI_TTS_VOICE=male/female` و مسیرهای `.onnx`
- [ ] `tools/benchmark_voice.py --scenarios tts` → `tts_first_chunk_ms` (MEASURED)
- [ ] سرعت و زیروبمی طبیعی است (نه سریع/نازک) — این همان باگ نرخ ۲۲٫۰۵k در مسیر بدون duplex است
- [ ] اعداد و تاریخ به‌صورت قابل‌فهم خوانده می‌شوند؛ کلمات انگلیسی داخل جمله فارسی
- [ ] قطع دستگاه خروجی وسط پخش → پیام روشن، بدون crash

## ۶. barge-in و AEC

- [ ] با هدفون و `PVAI_AEC_ENABLED=false`: حین پخش صحبت کنید → صدا در <۳۰۰ms قطع شود
- [ ] با بلندگو و AEC فعال: هیچ قطع ناخواسته در سکوت (false barge-in) رخ ندهد
- [ ] کالیبراسیون `PVAI_AEC_DELAY_MS`: مقادیر ۰/۲۰/۴۰/۶۰/۸۰ را امتحان و بهترین را ثبت کنید
- [ ] double-talk: کاربر و دستیار همزمان → صدای کاربر ادامه پیدا کند، پخش قطع شود
- [ ] `tools/benchmark_voice.py --scenarios bargein` → `stale_completions_after_interrupt=0`
- [ ] هیچ صدای کهنه‌ای بعد از قطع پخش شنیده نشود

## ۷. مکالمهٔ پیوسته (سناریوی پذیرش نهایی)

اجرا با اینترنت **قطع**:

1. [ ] برنامه offline شروع می‌شود (`/ready` سبز)
2. [ ] فارسی صحبت می‌کنید؛ Enter لازم نیست
3. [ ] گفتار خودکار تشخیص داده می‌شود
4. [ ] کل utterance رونویسی می‌شود (شامل کلمهٔ آخر)
5. [ ] LLM شروع به تولید می‌کند
6. [ ] TTS قبل از پایان متن کامل صحبت می‌کند
7. [ ] وسط صحبت دستیار حرف می‌زنید
8. [ ] دستیار سریع متوقف می‌شود
9. [ ] utterance جدید درست تشخیص داده می‌شود
10. [ ] context مکالمه درست است (دستیار جملهٔ قبلی را به یاد دارد)
11. [ ] **هیچ جملهٔ قبلی گم نشده** (تاریخچه را با `python -m server.sessions show <id>` ببینید)
12. [ ] صدای کهنه پخش نمی‌شود
13. [ ] ۲۰ نوبت پشت‌سرهم بدون crash و بدون افت حافظه (RAM را قبل/بعد ثبت کنید)
14. [ ] کل مسیر آفلاین می‌ماند (هیچ درخواست بیرونی)

## ۸. ثبت نتیجه

پس از اجرا، این بلوک را در `docs/STATUS.md` به‌روز کنید:

```text
تاریخ:
دستگاه/ویندوز:
مدل‌ها:
pytest: … passed, … skipped
benchmark (MEASURED): asr_ms=…  asr_rtf=…  llm_ttft_ms=…  tok/s=…  tts_first_chunk_ms=…
                      first_audible_ms=…  barge_in_latency_ms=…  cpu=…%  ram=…MB
WER/CER فارسی: …
مشکلات باقی‌مانده:
```

بدون این بلوک، وضعیت پروژه همچنان «تست خودکار سبز، سخت‌افزار NOT TESTED» است.

---

<a id="section-13"></a>

## بخش 13 — THIRD_PARTY.md

# Third-party components and model licences

This project is distributed under **GPL-3.0-or-later** (`LICENSE`). That choice is
not arbitrary: `piper-tts` is GPL-3.0-or-later and is imported **in-process** by
`server/live_voice.py`, so a permissive or proprietary licence for this
repository would be incompatible with it. See *Why GPL* below.

No model weights are stored in this repository (`models/` is git-ignored). Every
model is downloaded by the operator, and the operator is bound by the licence of
whatever they download. The table records the default and the licence verified
for it, plus the trap cases.

## Python dependencies

Verified from PyPI package metadata and the upstream repositories.

| Package | Version | Licence | Notes |
|---|---|---|---|
| `fastapi` | 0.162.0 | MIT | |
| `uvicorn[standard]` | 0.54.0 | BSD-3-Clause | `[standard]` pulls `uvloop` (MIT), `httptools` (MIT), `websockets` (BSD-3-Clause), `watchfiles` (MIT), `python-dotenv` (BSD-3-Clause), `PyYAML` (MIT) |
| `httpx` | 0.28.1 | BSD-3-Clause | runtime, used by `server/cli.py` |
| `pytest` | 9.2.0 | MIT | **dev only** (`requirements-dev.txt`) |
| `faster-whisper` | >=1.1,<2 | MIT | optional voice stack |
| `piper-tts` | >=1.3,<2 | **GPL-3.0-or-later** | optional voice stack; the reason for this project's licence |
| `sounddevice` | >=0.5,<1 | MIT | optional voice stack |
| `soundfile` | >=0.13,<1 | BSD-3-Clause | optional voice stack |
| `numpy` | >=1.26,<3 | BSD-3-Clause and others | optional voice stack |
| `scipy` | >=1.11,<2 | BSD-3-Clause | optional voice stack |
| `librosa` | >=0.10.2,<1 | ISC | optional voice stack; local file decoding/downmix/resampling |

`faster-whisper` in turn depends on `ctranslate2` (MIT) and `onnxruntime` (MIT),
which `piper-tts` also uses.

SpeexDSP (used by `server/aec.py` when built) is BSD-3-Clause; its `COPYING` and
`NOTICE` obligations are described in `docs/AEC.md` and must be honoured when
shipping a binary that links it.

## Model weights

| Model | Default path | Licence | SHA-256 of exact weight | Notes |
|---|---|---|---|---|
| Qwen3-4B (GGUF) | `models/Qwen3-4B-Q4_K_M.gguf` | Apache-2.0 | NOT VERIFIED (revision-dependent) | Apache-2.0 is GPLv3-compatible; safe to ship alongside this code. Quantised re-uploads inherit the base licence. |
| Whisper `small` weights | `models/whisper-small` | MIT | NOT VERIFIED (conversion-dependent) | OpenAI releases both code and weights under MIT; `faster-whisper` conversions keep it. |
| Piper Persian voice | `models/piper/fa_IR.onnx` | **varies per voice — check** | NOT VERIFIED (voice-dependent) | The voice file is a separate work from the Piper engine. See below. |

### The Piper voice licence trap

The engine's GPL licence says nothing about the **voice** you load into it. Each
Piper voice carries the licence of the recordings it was trained on, and several
Persian voices are not licensed for commercial use.

- The `fa_IR` models published in `rhasspy/piper-voices` (for example
  `fa_IR/amir/medium`) are documented on their model card as fine-tuned on a
  dataset released under **CC0**, which would permit commercial use — but the
  card is the authoritative statement, not this document.
- Third-party Persian Piper voices exist (for example `MahtaFetrat/Mana-Persian-Piper`,
  MIT, fine-tuned on the Mana-TTS dataset) and carry their own terms.
- Some vendored Persian voices are explicitly distributed for
  **personal, non-commercial use only**.

Vendors disagree on this, so **do not treat any summary — including this one — as
the licence**. Before shipping a voice, download it and read the `MODEL_CARD`
and any `LICENSE` that ships next to the `.onnx` file. Record the voice, its
source URL and its licence in your deployment notes.

`docs/SPEC.md` §۷ already lists "commercial use licence" as a selection criterion
for the ASR/TTS models and warns that the ParSiGo dataset licence must be
re-verified at the source. That warning still stands.

### Dataset licences

Fine-tuning data is not distributed here. If you fine-tune or redistribute a
model, the dataset licence travels with the derived weights: Mana-TTS and
datacula's Persian TTS databases each state their own terms on their project
pages.

## Why GPL

`server/live_voice.py` runs `dependency("piper.voice").PiperVoice.load(...)`
inside this process, and `server/voice.py` can also launch `python -m piper` as a
subprocess. Both are uses that the FSF and the Piper maintainers treat as
forming a combined work, so distributing this program together with
`piper-tts` obliges the combined distribution to be GPL-3.0-or-later.

If a permissive or closed-source licence is ever required, the options are:

1. Drop `piper-tts` and speak through a different engine, or
2. Keep the voice stack strictly separate and communicate with it only over a
   process boundary that the project documents as a separate program
   (an arms-length interface, which needs its own legal review), or
3. Pin the MIT-era `rhasspy/piper`, which was archived in October 2025 and no
   longer receives fixes — a deliberate trade of maintenance for licence.

Option 1 is the only one that does not depend on a legal judgement call.

## Regenerating the checks

Versions above were read from the installed environment and PyPI metadata:

```bash
python - <<'PY'
import json, urllib.request
for name in ("piper-tts", "faster-whisper", "fastapi", "uvicorn", "httpx", "pytest"):
    info = json.load(urllib.request.urlopen(f"https://pypi.org/pypi/{name}/json"))["info"]
    classifiers = [c for c in info.get("classifiers") or [] if c.startswith("License")]
    print(f"{name:16} {info.get('license_expression') or classifiers}")
PY
```

Model licences must be read from each model's card on its hosting page; they are
not machine-readable in a way that can be trusted here.

## Checksums for externally downloaded weights

No fixed model SHA-256 is asserted by this repository: a GGUF quantisation,
Whisper conversion or Piper voice revision changes its digest. Hashes are
**NOT VERIFIED** without an exact publisher URL/revision. Obtain the published
SHA-256 for the exact file from the model publisher (or a separately trusted
signed manifest), verify its licence/model card above, then pass that digest via
`tools/download_parallel.py --url https://... --output models/... --sha256 <64-hex>`.
The downloader checks bytes **before** replacing the destination. A checksum
copied from the same untrusted download endpoint does not authenticate a model.
`model.bin`, `.gguf` and `.onnx` require `--sha256`; TLS is required except
loopback-only test sources. Do not claim a model's licence from its extension.

---

<a id="section-14"></a>

## بخش 14 — AGENTS.md

# AGENTS.md — نقطهٔ ورود هر مدل/عامل به پروژهٔ Persian Voice AI

> این فایل «قرارداد کار» است. هر مدل، عامل کدنویس یا توسعه‌دهندهٔ انسانی که به این مخزن وصل می‌شود،
> باید پیش از هر تغییری این فایل را بخواند و سپس مستند کامل پروژه را در
> [`docs/SPEC.md`](SPEC.md) ببیند. وضعیت واقعی کد در [`docs/STATUS.md`](STATUS.md) است.

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

جزئیات و فهرست فایل‌ها: [`docs/STATUS.md`](STATUS.md).

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
├─ docs/
│  ├─ PROJECT_GUIDE.md    ← راهنمای یکپارچه؛ تولید با tools/build_docs.py
│  ├─ DATASETS.md         ← دادهٔ سفارشی و اعتبارسنجی manifest (بدون trainer)
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
│  ├─ cli.py              ← کلاینت چت فارسی ترمینال
│  ├─ __main__.py         ← اجرای `python -m server`
│  └─ __init__.py         ← نسخهٔ سرویس
├─ tests/                 ← pytest؛ بدون نیاز به مدل (fake LLM/ASR/TTS/دستگاه)
├─ benchmarks/            ← پرامپت‌های متنی + persian_eval.py + persian_eval_set.json
├─ tools/                 ← اسکریپت‌های .ps1 و .sh + benchmark_voice.py + download_parallel.py
├─ models/                ← مدل‌های محلی GGUF (خارج از git)
├─ data/                  ← مکالمه/بازخورد/دانش محلی (خارج از git)
└─ firmware/              ← (ساخته نشده) هدف: پروژهٔ ESP-IDF esp32s3_terminal
```

پوشه‌های `server/audio`، `vad`، `aec`، `asr`، `tts`، `conversation`، `playback`، `iot`، `devices`
در ساختار هدف وجود دارند ولی **هنوز ساخته نشده‌اند**. آن‌ها را فقط هنگام پیاده‌سازی همان قابلیت اضافه کن.

---

## ۴. اجرا و بررسی

هر اسکریپت `.ps1` یک معادل `.sh` دارد؛ جزئیات در [`docs/PLATFORMS.md`](PLATFORMS.md).

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

جزئیات، معیارهای انتخاب مدل‌ها و ریسک‌ها: [`docs/SPEC.md`](SPEC.md).

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

نقطهٔ ورود یکپارچه: [docs/PROJECT_GUIDE.md](PROJECT_GUIDE.md).
این فایل از راهنماهای موجود تولید می‌شود؛ پس از ویرایش منبع،
`python tools/build_docs.py` و `python tools/build_docs.py --check` را اجرا کنید.
ابزار `tools/prepare_dataset.py` فقط اعتبارسنجی محلی JSON/JSONL/CSV است؛ trainer نداریم.

---

<a id="section-15"></a>

## بخش 15 — docs/SPEC.md

# مستند جامع پروژه Persian Voice AI

**نسخهٔ سند ۱.۰ — مبنای ادامه توسعه.** (این شماره نسخهٔ همین سند است، نه نسخهٔ نرم‌افزار؛
نسخهٔ سرویس `server/__init__.py::__version__` است؛ `/health` فقط وضعیت liveness را برمی‌گرداند، نه نسخه.)

این سند شامل تصمیم‌های تثبیت‌شده و اهداف آیندهٔ پروژه است؛ بخش ESP32، IoT و خروجی پیشنهادی 24kHz
به معنی پیاده‌سازی firmware نیست. انتقال فعلی PC/WebSocket در هر دو جهت 16kHz است.
این سند مرجع تصمیم‌های تثبیت‌شدهٔ پروژه است. تغییر هر بند آن نیازمند دستور صریح کاربر است.
برای وضعیت واقعی پیاده‌سازی به [`STATUS.md`](STATUS.md) و برای قرارداد کار عوامل به
[`../AGENTS.md`](../AGENTS.md) مراجعه کنید.

---

## فهرست

1. [هدف پروژه](SPEC.md#۱-هدف-پروژه)
2. [تصمیم‌های تثبیت‌شده](SPEC.md#۲-تصمیمهای-تثبیتشده)
3. [معماری کلان](SPEC.md#۳-معماری-کلان)
4. [سخت‌افزار نمونهٔ اولیه](SPEC.md#۴-سختافزار-نمونهٔ-اولیه)
5. [Firmware مرحلهٔ اول](SPEC.md#۵-firmware-مرحلهٔ-اول)
6. [قالب صوت و شبکه](SPEC.md#۶-قالب-صوت-و-شبکه)
7. [پردازش گفتار و ASR](SPEC.md#۷-پردازش-گفتار-و-asr)
8. [مدل زبانی (LLM)](SPEC.md#۸-مدل-زبانی-llm)
9. [تبدیل متن به گفتار (TTS)](SPEC.md#۹-تبدیل-متن-به-گفتار-tts)
10. [مکالمهٔ دوطرفه و Barge-in](SPEC.md#۱۰-مکالمهٔ-دوطرفه-و-barge-in)
11. [ساختار پروژه](SPEC.md#۱۱-ساختار-پروژه)
12. [خانهٔ هوشمند](SPEC.md#۱۲-خانهٔ-هوشمند)
13. [امنیت و حریم خصوصی](SPEC.md#۱۳-امنیت-و-حریم-خصوصی)
14. [نقشهٔ راه توسعه](SPEC.md#۱۴-نقشهٔ-راه-توسعه)
15. [ریسک‌ها و موارد نیازمند بررسی](SPEC.md#۱۵-ریسکها-و-موارد-نیازمند-بررسی)
16. [منابع فنی](SPEC.md#۱۶-منابع-فنی)
17. [وضعیت فعلی و گام بعدی](SPEC.md#۱۷-وضعیت-فعلی-و-گام-بعدی)

---

## ۱. هدف پروژه

ساخت یک دستیار صوتی فارسی **کاملاً آفلاین** در شبکهٔ داخلی (LAN) با:

- مکالمهٔ طبیعی و دوطرفه،
- صدای مرد و زن،
- امکان قطع صحبت دستیار توسط کاربر (**Barge-in**)،
- قابلیت توسعه برای کنترل خانهٔ هوشمند،
- مسیر تبدیل‌شدن به محصول تجاری.

## ۲. تصمیم‌های تثبیت‌شده

- پردازش هوش مصنوعی کاملاً آفلاین؛ **بدون ارسال صدا یا داده به Cloud/API اینترنتی**.
- **ESP32-S3** به‌عنوان ترمینال صوتی و کنترل تجهیزات.
- **PC موجود** به‌عنوان میزبان پردازش سنگین.
- ارتباط صوتی و کنترلی روی **Wi-Fi/LAN**؛ BLE فقط برای Pairing، تنظیمات و کنترل سبک.
- Backend با **Python + FastAPI** و ارتباط **WebSocket**.
- پشتیبانی هدف از **چند ESP32 متصل به یک PC**.
- معماری فعلی تا زمانی که دستور صریح داده نشود تغییر نمی‌کند.

## ۳. معماری کلان

### ESP32-S3

- دریافت صدای میکروفون از طریق **I2S**
- پخش صدای خروجی از طریق **I2S**
- ارسال و دریافت PCM روی Wi-Fi
- کنترل GPIO، رله‌ها و سنسورها
- مدیریت شناسهٔ دستگاه و اتصال امن

### PC

- **FastAPI** و WebSocket Gateway
- مدیریت جریان صوت و بافرها
- **VAD**، **AEC** و Noise Suppression
- **ASR** برای تبدیل گفتار به متن
- **Conversation Manager** برای مدیریت مکالمه
- **LLM** برای فهم درخواست و تولید پاسخ/فرمان
- **TTS** برای تولید صدای فارسی
- **Playback Manager** برای کنترل پخش و توقف
- **IoT Command Broker** برای فرمان‌های خانهٔ هوشمند

## ۴. سخت‌افزار نمونهٔ اولیه

| قطعه | مشخصات |
|---|---|
| برد | ESP32-S3 **N16R8** — ۱۶MB Flash و ۸MB PSRAM (مشخصات ماژول واقعی باید کنترل شود) |
| میکروفون | **INMP441** (دیجیتال I2S) |
| آمپلی‌فایر | **MAX98357A** |
| بلندگو | ۴ اهم، ۳ وات |
| تغذیه | ۵ ولت پایدار برای آمپلی‌فایر + زمین مشترک |

### سیم‌کشی پیشنهادی

**INMP441**

| پین ماژول | اتصال |
|---|---|
| SCK | GPIO4 |
| WS | GPIO5 |
| SD | GPIO6 |
| L/R | GND |
| VDD | 3.3V |
| GND | GND |

**MAX98357A**

| پین ماژول | اتصال |
|---|---|
| BCLK | GPIO7 |
| LRC | GPIO15 |
| DIN | GPIO16 |
| VIN | 5V |
| GND | زمین مشترک |
| SD/EN | مطابق ماژول فعال شود |

> ⚠️ **هشدارهای سخت‌افزاری**
> - بلندگو فقط بین **SPK+** و **SPK-** متصل شود؛ هیچ‌کدام از سرهای بلندگو به GND وصل نشود،
>   چون خروجی آمپلی‌فایر از نوع **BTL** است.
> - پیش از روشن‌کردن، پین‌آوت برد واقعی بررسی شود.
> - در تست اول، میکروفون از بلندگو فاصله داشته باشد.

## ۵. Firmware مرحلهٔ اول

- محیط توسعه: **ESP-IDF 5.4.4**، زبان **C**، هدف **ESP32-S3**.
- مرحلهٔ **V1-A** شامل:
  - راه‌اندازی I2S RX برای INMP441
  - راه‌اندازی I2S TX برای MAX98357A روی **دو پورت مستقل**
  - نرخ نمونه‌برداری **16 kHz**
  - پخش صدای تست **440Hz به مدت 500ms**
  - نمایش **RMS** ورودی میکروفون در Serial Monitor
  - مانیتورینگ زندهٔ میکروفون در ابتدا **غیرفعال** است تا از Feedback جلوگیری شود

دستورات نمونه در PowerShell:

```powershell
cd C:\Dev\PersianVoiceAI\PersianVoiceAI_V1_Stage1
idf.py set-target esp32s3
idf.py build
idf.py -p COM5 flash monitor
```

> `COM5` باید با پورت واقعی جایگزین شود. کد هنوز روی سخت‌افزار واقعی build و تست نشده و
> عملکرد آن تأییدشده محسوب نمی‌شود. سازگاری APIهای I2S با ESP-IDF 5.4.4 باید در build واقعی بررسی شود.

## ۶. قالب صوت و شبکه

| مورد | مقدار |
|---|---|
| ورودی اولیه | PCM، تک‌کاناله، 16 kHz، 16-bit |
| خروجی اولیهٔ پیشنهادی | PCM، تک‌کاناله، 24 kHz، 16-bit (وابسته به TTS منتخب و تست سخت‌افزار) |
| فریم صوتی | ۲۰ تا ۴۰ میلی‌ثانیه |
| حجم خام | حدود **32 KB/s** (16 kHz mono 16-bit) |
| اندازهٔ فریم | 20ms = **640 بایت**، 40ms = **1280 بایت** |
| انتقال | **WebSocket** با فریم‌های باینری |
| BLE برای استریم صوت | انتخاب نشده؛ ESP32-S3 از Bluetooth Classic پشتیبانی نمی‌کند |

### پیام‌های کنترلی پیشنهادی

`HELLO`، `DEVICE_INFO`، `SESSION_START`، `INPUT_END`، `PLAY_START`، `PLAY_END`،
`PLAY_STOP`، `SET_VOLUME`، `SET_VOICE`، `PING/PONG`، `ERROR`.

هر دستگاه `device_id`، هر مکالمه `session_id` و هر جریان `stream_id` مستقل خواهد داشت.

## ۷. پردازش گفتار و ASR

- گزینهٔ شروع برای بنچمارک: **faster-whisper** با مدل **Whisper Small**.
- انتخاب نهایی باید با تست فارسی محاوره‌ای، نویز محیطی، سرعت پاسخ و مصرف منابع انجام شود.

### معیارهای انتخاب

- دقت تشخیص فارسی و محاوره
- **WER**
- تأخیر و پشتیبانی از پردازش جریانی (streaming)
- مصرف RAM/VRAM
- عملکرد در نویز
- مجوز استفادهٔ تجاری

### مجموعهٔ تست

جملهٔ کوتاه و بلند، فارسی محاوره‌ای، اعداد و تاریخ، واژه‌های انگلیسی، گفتار سریع و آرام،
و صدای محیط.

## ۸. مدل زبانی (LLM)

- **بک‌اند اجرایی:** llama-server از llama.cpp به‌صورت فرایند محلی مستقل؛ FastAPI از طریق async HTTP به آن متصل است. `llama-cpp-python` استفاده نمی‌شود.
- **فرمت مدل:** GGUF؛ بدون Ollama یا سرویس ابری.
- **پیش‌فرض جدید PC:** `Qwen3-4B-Q4_K_M.gguf` به‌جای MiniCPM5-1B؛ فایل وزن و بنچمارک Qwen هنوز در این محیط فراهم نیست.
- Thinking با `PVAI_LLM_ENABLE_THINKING=false` و `chat_template_kwargs` خاموش است؛ reasoning_content جواب قابل پخش محسوب نمی‌شود.
- MiniCPM در بنچمارک قبلی پوشش فارسی مناسب نشان نداده؛ ادعای قبلی «پوشش خوب فارسی» معتبر نیست.

### اجرای سخت‌افزاری (CPU/GPU)

- **پیش‌فرض: CPU.** بیلد `D:\Lama.cpp\bin` فقط CPU است و `--list-devices` در آن `(none)` برمی‌گرداند.
- **بیلد Vulkan (آزمایشی):** `D:\Lama.cpp\vulkan-b12186\bin` از انتشار رسمی `b12186`. کارت با
  `--list-devices` شناسایی می‌شود: `Vulkan0: NVIDIA GeForce 940MX (2215 MiB, 1913 MiB free)`.
- **اندازه‌گیری روی MiniCPM5-1B-Q8_0** (i7-6500U، ۴ رشته، `llama-bench -p 512 -n 128`):

  | حالت | pp512 | tg128 |
  |---|---|---|
  | CPU (`-ngl 0`) | ۸۰.۶۸ t/s | ۱۷.۴۹ t/s |
  | GPU (`-ngl 99`، Vulkan0) | ۵۹.۹۱ t/s | ۱۲.۳۲ t/s |

- **نتیجه:** GPU در این لپ‌تاپ کار می‌کند ولی کندتر از CPU است. علت: 940MX از نسل Maxwell است،
  `fp16` ندارد و فقط ۲ گیگابایت VRAM دارد. تا وقتی اندازه‌گیری خلافش را نشان ندهد، اجرا روی CPU می‌ماند
  و بیلد Vulkan فقط برای آزمایش در دسترس است.
- **CUDA:** بیلدهای رسمی CUDA 13.5 نسل Maxwell را پشتیبانی نمی‌کنند و CUDA Toolkit روی این سیستم
  نصب نیست؛ مسیر GPU در این سیستم Vulkan است.

### وظایف

- فهم فارسی و مکالمهٔ طبیعی
- حفظ زمینهٔ مکالمه
- تشخیص Intent
- تولید پاسخ و فرمان ساختاریافته
- خلاصه‌سازی تاریخچهٔ طولانی

> **قاعدهٔ امنیتی:** خروجی LLM نباید مستقیماً به GPIO متصل شود. تمام فرمان‌ها باید از
> **Command Broker** عبور کنند و schema validation، بررسی مجوز و کنترل ایمنی داشته باشند.

## ۹. تبدیل متن به گفتار (TTS)

- گزینه‌های بررسی اولیه: **ParSiGo**، **Piper**، **Nava FA** و سایر مدل‌های فارسی.
- **انتخاب نهایی انجام نشده است.**

### معیار انتخاب

- طبیعی‌بودن تلفظ فارسی
- صدای مرد و زن
- قابلیت Streaming و شروع پخش پیش از پایان تولید کل پاسخ
- تأخیر، مصرف منابع و اجرای آفلاین
- مجوز کد، وزن مدل، دیتاست و صدای گوینده برای استفادهٔ تجاری

> ⚠️ مجوز **ParSiGo** که قبلاً **CC-BY-NC-4.0** مطرح شده بود باید مجدداً از منبع رسمی بررسی شود؛
> شرط **NC** برای استفادهٔ تجاری محدودیت ایجاد می‌کند.

### نمونهٔ اجرایی PC

با درخواست کاربر، نمونهٔ PC از ابتدا با ورودی پیوسته و پاسخ استریم توسعه یافته است:
`server/streaming_voice.py` برای هماهنگی async و actorهای ASR/LLM/TTS؛
`server/live_voice.py` ابزار endpointing، جمله‌بندی و پخش chunkهای Piper.
Piper و Whisper انتخاب نمونهٔ فعلی PC هستند، نه انتخاب نهایی تجاری یا سرور/ESP32.
راهنمای اجرایی و محدودیت‌ها در [PC_VOICE.md](PC_VOICE.md) است.

## ۱۰. مکالمهٔ دوطرفه و Barge-in

برای مکالمهٔ هم‌زمان واقعی، اجزای زیر لازم‌اند:

- **AEC** برای حذف صدای بلندگوی خود دستگاه از ورودی میکروفون
- **VAD** برای تشخیص شروع و پایان گفتار
- **Noise Suppression**
- **ASR** و **TTS** جریانی
- **Playback Cancellation** و پاک‌سازی بافر
- **State Manager** برای مدیریت وضعیت مکالمه

### رفتار مورد انتظار

هنگام شروع صحبت کاربر، سیستم باید گفتار را تشخیص دهد، پخش فعلی را با `PLAY_STOP` متوقف کند،
بافر صوت قدیمی را پاک کند و درخواست جدید را پردازش کند.

### وضعیت PC و مرز ادعا

نسخهٔ PC اکنون میکروفون را پیوسته می‌خواند؛ Whisper از snapshotهای هم‌پوشان حین گفتار
متن partial تولید می‌کند و با ۶۰۰ms سکوت (پیش‌فرض قابل تنظیم) متن final به LLM می‌رود.
LLM از SSE خوانده می‌شود و Piper جمله‌های آماده را هم‌زمان با ادامهٔ دریافت توکن‌ها پخش می‌کند.
شروع صدای جدید پاسخ قبلی را لغو می‌کند. AEC روی PC با SpeexDSP اضافه شده است:
پخش Piper به PCM16/16k تبدیل و ورودی/خروجی در یک RawStream دوطرفه جفت می‌شوند.
مرجع واقعی خروجی همراه ورودی، پیش از VAD/ASR پردازش می‌شود. جزئیات نصب و محدودیت‌ها در [AEC.md](AEC.md).
خروجی 16k در این مسیر PC برای AEC است؛ قرارداد خروجی پیشنهادی ESP32 بدون تغییر است.
این adapter افزایشی است، نه ASR ذاتاً streaming؛ full-duplex مقاوم به اکو روی سخت‌افزار واقعی تأیید نشده.
تغییر معماری با درخواست صریح کاربر انجام شد؛ قرارداد جدید در [STREAMING_ARCHITECTURE.md](STREAMING_ARCHITECTURE.md) است.

### پیشنهاد اجرایی

در مسیر PC، AEC و لغو پاسخ اضافه شده‌اند ولی تست واقعی دستگاه و تنظیم تأخیر هنوز لازم است.
برای مسیر ESP32 ابتدا اجزا جداگانه پایدار و سپس AEC/Barge-in یکپارچه شوند.
**Push-to-Talk** یا مکالمهٔ نوبتی می‌تواند مرحلهٔ واسط تست باشد.

## ۱۱. ساختار پروژه

```
PersianVoiceAI/
  firmware/
    esp32s3_terminal/
      main/
      components/
      CMakeLists.txt
      sdkconfig.defaults
  server/
    app/
    api/
    audio/
    vad/
    aec/
    asr/
    llm/
    tts/
    conversation/
    devices/
    playback/
    iot/
  tests/
  requirements.txt
  requirements-dev.txt
  requirements-voice.txt
  LICENSE
  THIRD_PARTY.md
  models/
  config/
  tools/
  docs/
```

> این ساختار، ساختار **هدف نسخهٔ کامل** است و به معنی پیاده‌سازی‌شدهٔ همهٔ اجزا نیست.
> وضعیت واقعی: [`STATUS.md`](STATUS.md).

## ۱۲. خانهٔ هوشمند

مسیر فرمان:

```
ASR → LLM Intent → Structured Command → Command Broker
    → اعتبارسنجی و مجوز → ESP32/IoT → دریافت بازخورد واقعی → پاسخ صوتی
```

فرمان‌های حساس مانند قفل در یا تجهیزات پرخطر باید **تأیید صریح کاربر** را لازم داشته باشند.

## ۱۳. امنیت و حریم خصوصی

- احراز هویت مستقل برای هر دستگاه
- توکن مستقل و قابل لغو
- احراز هویت WebSocket و اعتبارسنجی پیام‌ها
- محدودسازی دسترسی API
- OTA امن در محصول نهایی
- ذخیره‌سازی صوت خام به‌صورت اختیاری و محدود
- امکان حذف تاریخچهٔ مکالمه
- عدم ارسال صدا به اینترنت
- نشانگر فعال‌بودن میکروفون و بررسی امکان قطع فیزیکی آن

## ۱۴. نقشهٔ راه توسعه

| مرحله | محدوده |
|---|---|
| **V1-A** | تست میکروفون، آمپلی‌فایر، ضبط/پخش و RMS |
| **V1.1** | اتصال Wi-Fi و WebSocket و انتقال PCM دوطرفه |
| **V1.2** | اتصال ASR و ارزیابی تشخیص فارسی |
| **V1.3** | اتصال LLM و مدیریت مکالمه |
| **V1.4** | اتصال TTS فارسی مرد/زن و خروجی Streaming |
| **V1.5** | پیاده‌سازی AEC، VAD و Barge-in |
| **V1.6** | کنترل GPIO، رله‌ها و سنسورها |
| **V2** | چنددستگاهی، امنیت کامل، مدیریت دستگاه‌ها، طراحی بدنه و آماده‌سازی محصول تجاری |

## ۱۵. ریسک‌ها و موارد نیازمند بررسی

- کیفیت تک‌میکروفون در فاصله و نویز زیاد ممکن است کافی نباشد.
- AEC و حذف صدای بلندگو برای مکالمهٔ طبیعی حیاتی است.
- انتخاب TTS فارسی با صدای مرد و زن و مجوز تجاری هنوز باز است.
- انتخاب مدل ASR و LLM باید با سخت‌افزار PC و بنچمارک واقعی نهایی شود.
- سازگاری APIهای I2S با ESP-IDF 5.4.4 باید در build واقعی بررسی شود.
- همهٔ اجزا باید ابتدا جداگانه تست و سپس یکپارچه شوند.

## ۱۶. منابع فنی

- ESP-IDF — <https://dl.espressif.com/dl/esp-idf/>
- راهنمای Windows برای ESP32-S3 — <https://docs.espressif.com/projects/esp-idf/en/v5.4.2/esp32s3/get-started/windows-setup.html>
- faster-whisper — <https://github.com/SYSTRAN/faster-whisper>
- Qwen — <https://huggingface.co/Qwen>
- llama.cpp — <https://github.com/ggml-org/llama.cpp>
- llama-cpp-python — <https://github.com/abetlen/llama-cpp-python>
- مخزن TTS فارسی مطرح‌شده — <https://github.com/nimaone/persian_tts>
- ESP-SR — <https://github.com/espressif/esp-sr>
- ESP-ADF — <https://github.com/espressif/esp-adf>

## ۱۷. تکمیل قرارداد موتور مکالمه (نسخهٔ ۱.۱ — ۸ نوامبر ۲۰۲۶)

این بخش، **تصمیم‌های تازه‌ای** است که در بازبینی موتور مکالمهٔ PC گرفته و پیاده شد؛ جای تصمیم‌های
قبلی را نمی‌گیرد و فقط آن‌ها را دقیق‌تر می‌کند.

1. **چهار مفهوم جدا:** `UserUtterance`، `AssistantTurn`، `PartialASR`، `FinalASR`.
   لغو پاسخ دستیار (barge-in) اجازهٔ دست‌زدن به صف final ASR را ندارد. صف final بدون‌افت است؛
   فقط shutdown می‌تواند آن را کنار بگذارد و باید اعلام کند. مرجع:
   [`STREAMING_ARCHITECTURE.md`](STREAMING_ARCHITECTURE.md).
2. **ماشین حالت صریح** `idle/listening/transcribing/thinking/speaking` با جدول گذار قابل‌تست؛
   گذار غیرمجاز گزارش می‌شود و برنامه را نمی‌کشد. تست: `tests/test_state_machine.py`.
3. **VAD انرژی با هیسترزیس، پیش‌رول و کف نویز تطبیقی** به‌عنوان پیش‌فرض؛ جایگزینی با VAD عصبی
   فقط پس از اندازه‌گیری روی هدف و با معیار WER، نه بر اساس حدس.
4. **AEC محلی SpeexDSP** پیش از VAD/ASR؛ نبود کتابخانهٔ native با AEC فعال = خطای صریح،
   نه bypass خاموش. کالیبراسیون تأخیر روی دستگاه واقعی الزامی است.
5. **مکالمهٔ محلی و ماندگار:** `session_id`/`conversation_id`، وضعیت `complete|interrupted|failed`،
   برش توکنی، ذخیرهٔ JSONL محلی، قابل خاموش‌کردن و حذف. تبدیل مکالمه به حافظهٔ بلندمدت شخصی
   **تصمیم نگرفته و پیاده نشده** است. جداسازی صریح: `Conversation` (پنجرهٔ کوتاه‌مدت که وارد
   context مدل می‌شود) از `ConversationStore` (بایگانی JSONL محلی که **هرگز** خودکار بازیابی و
   به مدل داده نمی‌شود) جدا است؛ بایگانی ≠ حافظهٔ بلندمدت. دسترسی به آن فقط با API/CLI بازبینی
   یا `session_id` صریح است.
6. **اندازه‌گیری بدون ادعا:** هر عدد `MEASURED`/`SIMULATED`/`NOT MEASURED` برچسب دارد؛ روش و
   نتایج در [`BENCHMARKS.md`](BENCHMARKS.md).
7. **PC-first:** firmware ESP32 تا پاس‌شدن تست پذیرش PC شروع نمی‌شود. قرارداد انتقال و
   **transport سمت سرور** در [`PROTOCOL.md`](PROTOCOL.md) مستند شده‌اند؛ `/api/protocol` مقدار
   `transport_implemented: true` و `firmware_implemented: false` برمی‌گرداند.
8. **warmup فقط برای مدلی که درون پروسه است:** Whisper با `PVAI_ASR_WARMUP` گرم می‌شود و
   Piper هنگام ساخت `Speaker` لود می‌شود؛ LLM در llama-server جدا زندگی می‌کند، پس warmup
   جداگانه در این پروسه معنا ندارد و وضعیت آن با `model_available` در `/ready` گزارش می‌شود.

## ۱۸. وضعیت فعلی و گام بعدی

- تصمیم‌های معماری کلان و قطعات نمونهٔ اولیه مشخص شده‌اند.
- Firmware مرحلهٔ اول آمادهٔ بررسی است، اما هنوز روی برد واقعی کاربر آزمایش نشده است.
- گام بعدی: تهیه/تأیید برد و قطعات، بررسی پین‌آوت، build و تست مستقل میکروفون و بلندگو،
  و سپس ورود به مرحلهٔ شبکه.
- برای انتخاب دقیق مدل‌های ASR/LLM/TTS و تنظیمات اجرایی، مشخصات CPU، RAM، GPU و VRAM
  کامپیوتر میزبان باید هنگام شروع بنچمارک ثبت شود.

وضعیت واقعی کد و تست‌های اجراشده: [`STATUS.md`](STATUS.md).

> **اصل مهم پروژه:** معماری و تصمیم‌های تثبیت‌شده بدون دستور صریح تغییر نکنند؛ هر تغییر پیشنهادی
> ابتدا به‌صورت گزینه و با بیان مزایا، معایب و اثر آن بر اجزای موجود مطرح شود.

---

<a id="section-16"></a>

## بخش 16 — docs/ISSUE_REGISTER.md

# دفترچهٔ ایرادها — PersianVoiceAI Conversation Engine

> **دفترچهٔ تاریخی رفع ایرادها:** مقادیر و تعداد تست هر مرحله مربوط به همان مرحله‌اند،
> نه پیش‌فرض جاری. برای وضعیت فعلی [STATUS](STATUS.md)، [CONFIGURATION](CONFIGURATION.md)
> و [راهنمای یکپارچه](PROJECT_GUIDE.md) را ببینید.


این سند نتیجهٔ بازبینی واقعی کد (نه فرض) است و وضعیت هر ایراد را با تست متناظر نشان می‌دهد.
وضعیت‌ها: `رفع‌شده + تست`، `رفع‌شده + تست ناقص`، `باز`، `نیازمند سخت‌افزار`.

مبنا: commit `9654a68` (پایه)، شاخهٔ `arena/31347dde-persianvoiceai`.
تست پایه پیش از تغییر: مرحلهٔ تاریخی (تست‌های native AEC skip می‌شوند).
تست پس از مرحلهٔ تکمیلی: مرحلهٔ تکمیلی تاریخی (۲ skip فقط native AEC در sandbox بدون کتابخانه).

> **این عدد تاریخی است** و به لحظهٔ همان مرحله مربوط می‌شود. شمار تست‌های جاری در
> `docs/STATUS.md` است (اکنون `python -m pytest`). برای دیدن عدد امروز،
> `python -m pytest` را اجرا کنید — عدد داخل سند به‌صورت خودکار به‌روز نمی‌شود.

---

## خلاصهٔ شدت

| شدت | تعداد | موضوع |
|---|---|---|
| P0 | 3 | گم‌شدن نتیجهٔ final ASR؛ هزینهٔ بازشناسی تکراری؛ رقابت منابع CPU |
| P1 | 9 | VAD/پیش‌رول، لغو و ماشین حالت، system prompt، تاریخچه، AEC، دستگاه صوت، TTS، API/readiness، مشاهده‌پذیری |
| P2 | 6 | معیارها، بنچمارک، پنجرهٔ partial، پیام‌های خطا، هم‌ترازی صف‌ها |
| P3 | 3 | نام‌گذاری، مستندات ناهمگام، ورودی‌های کوچک |
| P1 (مرحلهٔ ۲) | 4 | lost-wakeup صف final، گیرکردن حالت در SPEAKING، نرخ اشتباه TTS بدون duplex، baseline غلط CPU |
| P2 (مرحلهٔ ۲) | 2 | نبود اعتبارسنجی نرخ نمونه‌برداری، رشد نامحدود RAM مکالمه |

---

## P0

### P0-01 — نتیجهٔ final ASR پس از شروع گفتار جدید گم می‌شود

- **شدت:** P0
- **فایل:** `server/streaming_voice.py`
- **توابع:** `Pipeline.interrupt`، `Pipeline.recognize`، `Pipeline.offer`، `Pipeline.feed`
- **ریشهٔ ایراد (سه مسیر مستقل):**
  1. `interrupt()` با هر `onset` صدا را پاک می‌کند: `while not self.snapshots.empty(): get_nowait()`.
     اگر final در صف مانده باشد (ASR هنوز آن را برنداشته)، همان‌جا حذف می‌شود.
  2. `recognize()` بعد از decode در thread، `item.generation != self.generation` را چک می‌کند.
     با شروع جملهٔ بعد، `generation` عوض شده و **final معتبر دور ریخته می‌شود**؛ این همان
     سناریوی «جملهٔ A ناپدید می‌شود» است.
  3. `offer()` یک mailbox یک‌تایی مشترک برای partial و final است: partial جملهٔ بعد می‌تواند
     final معلق جملهٔ قبل را بازنویسی کند.
  4. تقسیم بخش در سقف ۱۵ ثانیه بدون onset بعدی، `voiced` را صفر می‌کند و onset بعدی (۳ فریم بعد)
     `interrupt()` را روی final بخش قبلی اجرا می‌کند؛ گفتار طولانی بخش‌های خود را از دست می‌دهد.
- **رفع:** جداسازی کامل `UserUtterance` (شناسهٔ یکتا و افزایشی)، `AssistantGeneration`
  (شمارندهٔ مستقل barge-in)، `PartialASR` (mailbox یک‌جای قابل‌جایگزینی) و `FinalASR`
  (صف FIFO بدون‌افت). لغو پاسخ فقط task پاسخ و پخش را لغو می‌کند و به صف final دست نمی‌زند.
  dedupe با `utterance_id`؛ ثبت کاربر یک‌بار انجام می‌شود.
- **تست:** `tests/test_utterance_lifecycle.py` (۶ سناریوی الزامی) + به‌روزرسانی تست‌های قبلی.
- **وضعیت:** رفع‌شده + تست

### P0-02 — بازشناسی تکراری و پرهزینهٔ ASR

- **شدت:** P0
- **فایل:** `server/streaming_voice.py`, `server/voice.py`
- **ریشهٔ ایراد:** هر ۰٫۸ ثانیه `b"".join(self.detector.frames)` یعنی کل بخش در حال رشد به Whisper
  داده می‌شد؛ هزینهٔ کل تا سقف ۱۵ ثانیه حدوداً درجهٔ دوم رشد می‌کند. یک actor سریالی هم
  یعنی final پشت partial‌های طولانی می‌ماند (head-of-line blocking). هیچ شمارنده/سنجه‌ای نبود.
- **رفع:** پنجرهٔ partial محدود و هم‌پوشان (`PVAI_ASR_PARTIAL_WINDOW_S`, پیش‌فرض ۶ ثانیه)؛
  audio کامل برای final حفظ می‌شود؛ صف final با اولویت مطلق و بدون‌افت؛ حذف partial منسوخ
  هنگام رسیدن final؛ محدودسازی فراخوانی هم‌زمان Whisper به یک actor؛ سنجه‌های
  `asr_ms`, `asr_rtf`, `asr_final_latency_ms`, `asr_partial_latency_ms`, `asr_queue_depth`,
  `asr_dropped_partial_count`, `asr_final_job_count`.
- **تست:** `tests/test_asr_scheduler.py`, `tests/test_metrics.py`
- **وضعیت:** رفع‌شده + تست (کیفیت/سرعت واقعی وزن‌ها سنجیده نشده: **NOT MEASURED**)

### P0-03 — رقابت منابع روی CPU دوهسته‌ای

- **شدت:** P0
- **فایل:** `server/voice.py`, `server/config.py`, `server/streaming_voice.py`
- **ریشهٔ ایراد:** `cpu_threads=self.settings.n_threads or 4`؛ با `PVAI_N_THREADS=0` یعنی ۴ thread
  برای Whisper روی ماشینی با ۲ هستهٔ فیزیکی، هم‌زمان با LLM و Piper. هیچ بودجهٔ thread،
  هیچ اندازه‌گیری بار و هیچ backpressure قابل‌مشاهده‌ای وجود نداشت.
- **رفع:** `PVAI_ASR_THREADS` جدا از `PVAI_N_THREADS` با پیش‌فرض ۲؛ تست بار و سنجهٔ
  CPU/RAM (`server/resources.py`, `tools/benchmark_voice.py`)؛ صف‌های محدود با شمارش عمق؛
  AEC خارج از callback؛ اندازه‌گیری jitter callback.
- **تست:** `tests/test_resources.py`, `tests/test_audio_io.py`
- **وضعیت:** رفع‌شده + تست؛ **اعداد واقعی i7-6500U اندازه‌گیری نشده: NOT MEASURED**

---

## P1

### P1-01 — system prompt در مسیر صوتی ارسال نمی‌شد

- **فایل:** `server/streaming_voice.py` → `respond`
- **ریشه:** payload فقط `messages` تاریخچه + پیام کاربر بود؛ `Settings.system_prompt` هرگز
  در مسیر زنده به مدل نمی‌رسید (فقط مسیر متنی از `LlamaServerClient` آن را تزریق می‌کرد).
- **رفع:** `messages = [system] + context` در مسیر صوتی؛ قابل تنظیم با `PVAI_SYSTEM_PROMPT`.
- **تست:** `tests/test_utterance_lifecycle.py::test_system_prompt_is_sent_to_the_model`
- **وضعیت:** رفع‌شده + تست

### P1-02 — نبود ماشین حالت و یکسان‌بودن لغو در پنج وضعیت

- **فایل:** `server/streaming_voice.py`
- **ریشه:** `feed()` با هر onset بی‌قید `interrupt()` را صدا می‌زد: هنگام بی‌کاری، هنگام ASR،
  هنگام تولید LLM، هنگام سنتز TTS و هنگام پخش. نتیجه: لغو کارهای بی‌ربط و از دست رفتن final.
- **رفع:** `VoiceState` صریح (`IDLE`, `LISTENING`, `TRANSCRIBING`, `THINKING`, `SPEAKING`) با
  جدول گذار قابل‌تست؛ barge-in فقط وقتی فعالیت دستیار وجود دارد؛ گفتار هنگام ASR فقط
  utterance جدید می‌سازد.
- **تست:** `tests/test_state_machine.py`, `tests/test_utterance_lifecycle.py`
- **وضعیت:** رفع‌شده + تست

### P1-03 — VAD انرژی با آستانهٔ ثابت، بدون هیسترزیس و پیش‌رول

- **فایل:** `server/live_voice.py` → `Endpoint`
- **ریشه:** آستانهٔ واحد، `voiced >= 3` سخت‌کد، حداقل ۸ فریم، بدون پیش‌رول واقعی
  (حداکثر ۲ فریم قبل از onset)، بدون تطبیق نویز، سکوت ۵۰۰ms ثابت؛ در سقف ۱۵ ثانیه فریم‌های
  ابتدای بخش بعد دور ریخته می‌شد.
- **رفع:** `server/vad.py` با `EnergyVAD`: هیسترزیس onset/offset، پیش‌رول حلقه‌ای، حداقل
  گفتار قابل تنظیم، کف نویز تطبیقی، پارامترهای کامل از `.env`؛ کلاس `Endpoint` سازگار
  با API قبلی باقی مانده است.
- **تست:** `tests/test_vad.py`
- **وضعیت:** رفع‌شده + تست (صوت واقعی میکروفون **NOT TESTED**)

### P1-04 — TTS تک‌صدا و بدون سنجهٔ تأخیر

- **فایل:** `server/live_voice.py`, `server/config.py`
- **ریشه:** تنها `Settings.tts_model_path`؛ بدون انتخاب مرد/زن؛ `tts_start` زمان اولین صدای
  واقعی نبود؛ خطاها به پیام عمومی تبدیل می‌شدند؛ پایان پخش با «تحویل به صف» اشتباه می‌شد.
- **رفع:** `PVAI_TTS_VOICE=male|female` + مسیر جدا برای هر صدا، اعتبارسنجی فایل
  `.onnx`/`.onnx.json` در startup، callbackهای اولین chunk و اولین فریم و مدت پخش، خطای
  قابل‌فهم برای دستگاه/مدل.
- **تست:** `tests/test_tts.py`
- **وضعیت:** رفع‌شده + تست؛ کیفیت تلفظ فارسی با وزن واقعی **NOT MEASURED**

### P1-05 — تاریخچهٔ مکالمه ناقص و ناسازگار با مستندات

- **فایل:** `server/streaming_voice.py`, `server/voice.py`
- **ریشه:** فقط RAM، برش بر اساس تعداد پیام (`[-10:]` هنگام درخواست و `[-12:]` هنگام ثبت)،
  پیام کاربر اگر پاسخ شکست می‌خورد هرگز ثبت نمی‌شد، پاسخ قطع‌شده بدون وضعیت دور ریخته
  می‌شد، session/conversation id وجود نداشت، امکان بازبینی/حذف نبود.
- **رفع:** `server/conversation.py` با `Message` (وضعیت `complete|interrupted|failed`)،
  `Conversation` (dedupe بر اساس utterance، برش token-aware)، `ConversationStore`
  (JSONL محلی، قابل خاموش‌کردن، list/delete) و CLI `python -m server.sessions`.
- **تست:** `tests/test_conversation.py`
- **وضعیت:** رفع‌شده + تست

### P1-06 — API بدون readiness و بدون جداسازی نشست

- **فایل:** `server/main.py`
- **رفع:** `/ready` با وضعیت مدل/TTS/ASR/AEC/وابستگی‌ها، `/health` توسعه‌یافته،
  `/api/sessions` (list/show/delete) و پارامتر `session_id` در `/api/chat`.
- **تست:** `tests/test_api.py`
- **وضعیت:** رفع‌شده + تست

### P1-07 — مدیریت دستگاه صوتی

- **فایل:** `server/streaming_voice.py` (`run_live`)
- **ریشه:** فقط دستگاه پیش‌فرض سیستم، فقط 16k، بدون `check_input_settings`، بدون تشخیص
  قطع دستگاه؛ خطای callback فقط `overflow` می‌شد و حلقه بی‌پایان خطا تولید می‌کرد.
- **رفع:** `server/audio_io.py`: انتخاب ورودی/خروجی با نام یا index، اعتبارسنجی نرخ/کانال،
  پیام خطای فارسی با فهرست دستگاه‌ها، watchdog قطع جریان و بازگشایی محدود.
- **تست:** `tests/test_audio_io.py`
- **وضعیت:** رفع‌شده + تست مصنوعی؛ قطع/وصل دستگاه واقعی **NOT TESTED**

### P1-08 — AEC: تأخیر، فریم نامعتبر و نبود وضعیت در startup

- **فایل:** `server/aec.py`, `server/streaming_voice.py`
- **ریشه:** `process()` برای هر فریم غیر ۶۴۰ بایت `ValueError` می‌دهد؛ اگر دستگاه blocksize را
  رعایت نکند، لاگ با خطای تکراری پر می‌شود به‌جای پیام عملی. وضعیت AEC در readiness نبود،
  تأخیر پیش‌فرض بدون هشدار استفاده می‌شد و زمان پردازش سنجیده نمی‌شد.
- **رفع:** `aec_status()` برای readiness/startup، اعتبارسنجی طول فریم با پیام قابل‌فهم،
  سنجهٔ `aec_process_ms`، `PVAI_AEC_DELAY_MS` مستند، حفظ حالت صریح‌خطا (بدون bypass خاموش).
- **تست:** `tests/test_aec.py`
- **وضعیت:** رفع‌شده + تست مصنوعی؛ حذف اکوی اتاق واقعی **NOT TESTED**

### P1-09 — مشاهده‌پذیری

- **رفع:** `server/metrics.py` (شمارنده/gauge/خلاصهٔ هیستوگرام بدون رشد نامحدود) + رویداد
  `metrics` در خط لوله + `asr_*`, `llm_*`, `tts_*`, `barge_in_*`, `audio_callback_jitter_ms`,
  `cpu_usage_percent`, `ram_usage_mb`, `queue_depth`.
- **تست:** `tests/test_metrics.py`
- **وضعیت:** رفع‌شده + تست

---

## P2 / P3

| شناسه | شدت | موضوع | وضعیت |
|---|---|---|---|
| P2-01 | P2 | نبود هیچ سنجه‌ای در مسیر صوت | رفع‌شده + تست |
| P2-02 | P2 | نبود بنچمارک زنجیرهٔ صوت | رفع‌شده (`tools/benchmark_voice.py`، ۶ سناریو، برچسب MEASURED/SIMULATED/NOT MEASURED، تست `test_benchmark_tools.py`)؛ اعداد واقعی هدف NOT MEASURED |
| P2-03 | P2 | `agreement()` بدون لنگر پنجره | رفع‌شده: پیشوند فقط وقتی پنجره ثابت است |
| P2-04 | P2 | `pending_history` ویژگی پویا | رفع‌شده (`AssistantTurn` صریح) |
| P2-05 | P2 | `vad_filter=True` اجباری Whisper | رفع‌شده و قابل تنظیم |
| P2-06 | P2 | ناسازگاری `[-10:]`/`[-12:]` | رفع‌شده: `PVAI_HISTORY_MAX_MESSAGES` + بودجهٔ token |
| P3-01 | P3 | نام `Snapshot`/انتساب‌های پویا | رفع‌شده |
| P3-02 | P3 | مستندات ادعای «شش نوبت» و «۵۴ تست» بدون به‌روزرسانی | رفع‌شده در `docs/STATUS.md` |
| P3-03 | P3 | نبود نرمال‌سازی فارسی و سنجهٔ WER/CER | رفع‌شده (`server/persian.py`, `benchmarks/persian_eval.py`, مجموعهٔ ۴۰ جملهٔ `benchmarks/persian_eval_set.json`)؛ اعداد واقعی NOT MEASURED |

---

## موارد باز (بدون رفع در این مرحله)

| شناسه | شدت | موضوع | دلیل |
|---|---|---|---|
| OPEN-01 | P1 | توقف قطعی inference در حال اجرا (thread درجا قابل kill نیست) | نیازمند process isolation؛ خارج از دامنهٔ این مرحله |
| OPEN-02a | — | transport سمت سرور WebSocket صوتی + احراز هویت دستگاه | **رفع‌شده** (`server/ws_voice.py`، ۱۴ تست در `tests/test_ws_voice.py`، متریک‌های `ws_*`، کلید `ws_voice` در `/ready`) |
| OPEN-02b | P1 | firmware ESP32-S3، رجیستری دستگاه و OTA | هیچ کد سخت‌افزاری در مخزن نیست؛ تا پاس‌شدن پذیرش PC شروع نمی‌شود |
| OPEN-03 | P1 | صحت اکوی اتاق واقعی و تأخیر دستگاه | نیازمند سخت‌افزار |
| OPEN-04 | P2 | VAD عصبی محلی (Silero/WebRTC) | وابستگی جدید بدون سنجش؛ حالت انرژی با هیسترزیس فعلی حفظ شد |
| OPEN-05 | P2 | Command Broker / IoT و OTA | خارج از دامنهٔ موتور مکالمه |


---

## مرحلهٔ تکمیلی — ایرادهای تازه‌یافته (۸ نوامبر ۲۰۲۶)

این موارد در بازبینی همان کدِ رفع‌شده پیدا شدند، نه در پایه. همه با تست بازگشتی پوشش داده شده‌اند.

### P1-10 — lost wakeup در `AsrScheduler.next()`: final می‌توانست برای همیشه در صف بخوابد

- **فایل:** `server/streaming_voice.py` → `AsrScheduler.next`
- **ریشه:** ترتیب `بررسی صف‌ها → self._wakeup.clear() → await wait()`. اگر `submit_final` دقیقاً
  در همین پنجره اجرا می‌شد، پرچم را set می‌کرد و actor بلافاصله پاکش می‌کرد؛ بعد تا submit بعدی
  (که ممکن بود هرگز نیاید) خواب می‌ماند. همان علامتِ «جمله ناپدید شد»، با علت متفاوت.
- **رفع:** ابتدا `clear()` و سپس بررسی مجدد صف (`if self.finals or partial: continue`).
- **تست:** `tests/test_asr_scheduler.py::test_submit_landing_between_check_and_wait_is_not_lost`
  (با پیاده‌سازی قدیم deterministic timeout می‌دهد — تأیید شده) و `test_next_waits_then_returns_a_later_final`.
- **وضعیت:** رفع‌شده + تست

### P1-11 — حالت در `SPEAKING` گیر می‌کرد و هر گفتار بعدی «barge-in» تلقی می‌شد

- **فایل:** `server/streaming_voice.py` → `Pipeline.play`
- **ریشه:** نشانگر commit (آیتم با متن خالی) آخرین قلم صف پخش است. شرط گذار به
  `LISTENING/IDLE` در شاخهٔ جملهٔ واقعی `reply.done()` را می‌خواست که در آن لحظه هنوز false بود،
  و شاخهٔ نشانگر هیچ گذاری نداشت. نتیجه: `state` تا ابد `SPEAKING`.
- **اثر:** `_begin_utterance` از حالت SPEAKING به LISTENING نمی‌رود، `_maybe_barge_in` هر onset را
  barge-in می‌بیند، و وضعیت گزارش‌شده به کاربر/دستگاه دروغ است.
- **رفع:** در مصرف نشانگر، اگر صف خالی و `pending_turn is None` باشد گذار به LISTENING/IDLE.
- **تست:** `tests/test_state_machine.py::test_pipeline_walks_the_documented_states_for_one_exchange`.
- **وضعیت:** رفع‌شده + تست

### P1-12 — پخش TTS بدون duplex با نرخ اشتباه (۲۲٫۰۵k داخل stream 16k)

- **فایل:** `server/live_voice.py` → `Speaker._chunk_pcm`, `_open_output`
- **ریشه:** مسیر بدون duplex بایت‌های بومی chunk را می‌نوشت در حالی که stream با
  `PVAI_AUDIO_SAMPLE_RATE` باز می‌شد؛ وزن‌های فارسی Piper معمولاً ۲۲٫۰۵kHz هستند، یعنی پخش
  ~۲۷٪ سریع و با زیروبمی غلط. مسیر duplex (AEC) درست بود، پس باگ فقط در حالت تک‌مسیره دیده می‌شد.
- **رفع:** `_to_mono_rate(chunk, target)`؛ وقتی نرخ/کانال با stream یکی است بایت خام (بدون numpy)،
  وگرنه resample به نرخ stream.
- **تست:** `tests/test_tts.py::test_speaker_only_path_resamples_piper_rate_to_the_output_stream`،
  `test_native_rate_chunk_is_written_verbatim_without_resampling`.
- **وضعیت:** رفع‌شده + تست (کیفیت شنیداری با گوش: NOT TESTED)

### P1-13 — baseline غلط سنجهٔ CPU در Linux (اعداد میلیون‌درصدی)

- **فایل:** `server/resources.py` → `_LinuxSampler.__init__`
- **ریشه:** `_last_cpu = 0.0` به‌جای tick فعلی؛ اولین نمونه، کل CPU زمان‌عمر پروسه را بر چند
  میکروثانیه تقسیم می‌کرد. در اولین اجرای بنچمارک `cpu_usage_percent = 1,956,947.20` ثبت شد —
  یعنی سنجه‌ای که برای تصمیم‌گیری بود، بی‌معنا. (پیدا شدنش مدیون همین ابزار بنچمارک است.)
- **رفع:** prime کردن baseline با `_total_ticks()`؛ بنچمارک هم یک sampler پایدار برای نمونهٔ قبل/بعد
  نگه می‌دارد.
- **تست:** `tests/test_resources.py::test_fresh_sampler_first_reading_is_plausible`.
- **وضعیت:** رفع‌شده + تست

### P2-07 — نبود اعتبارسنجی نرخ نمونه‌برداری در مسیر زنده

- **فایل:** `server/config.py` → `config_errors`
- **ریشه:** `PVAI_AUDIO_SAMPLE_RATE=48000` پذیرفته می‌شد؛ VAD/Whisper قرارداد 16k دارند و متن
  بی‌صدا غلط می‌شد (بازنمونه‌برداری خودکار پیاده نیست). AEC هم فقط 16k را می‌پذیرد.
- **رفع:** خطای پیکربندی صریح فارسی؛ در `/ready` و startup دیده می‌شود.
- **تست:** `tests/test_tts.py::test_non_16k_capture_rate_is_a_configuration_error`.
- **وضعیت:** رفع‌شده + تست

### P2-08 — رشد نامحدود RAM در مکالمهٔ طولانی (الزام ۲۰)

- **فایل:** `server/conversation.py`
- **ریشه:** `Conversation.messages` append-only در RAM بود، مثل `_utterance_ids`. یک دستیار ۲۴/۷
  می‌توانست ساعت‌ها حافظه جمع کند؛ `Pipeline` هم `max_stored_messages` تنظیمات را منتقل نمی‌کرد.
- **رفع:** پنجرهٔ RAM با `PVAI_HISTORY_MAX_STORED` (پیش‌فرض ۵۰۰) که فایل JSONL روی دیسک را
  ناقص نمی‌کند؛ `_utterance_ids` با deque محدود (۲۰۴۸) و eviction؛ انتقال تنظیمات در `Pipeline`.
- **تست:** `tests/test_conversation.py::test_in_memory_window_is_bounded_while_the_disk_log_stays_complete`،
  `test_utterance_dedupe_window_is_bounded`، `tests/test_long_run.py` (۱۲۰ نوبت، بررسی کرانه‌ها در هر نوبت).
- **وضعیت:** رفع‌شده + تست

### P1-14 — پاسخ قطع‌شده در جملهٔ اول، کل متن تولیدشده را «شنیده‌شده» ثبت می‌کرد

- **فایل:** `server/streaming_voice.py` → `_commit_turn`, `play`
- **ریشه:** شرط `if status == INTERRUPTED and turn.spoken:` وقتی هیچ جمله‌ای کامل نشده بود
  (قطع در جملهٔ اول — رایج‌ترین حالت barge-in) به متن کامل `turn.text` برمی‌گشت؛ یعنی جمله‌هایی
  که هرگز پخش نشدند وارد تاریخچه و (در صورت `PVAI_HISTORY_INCLUDE_INTERRUPTED`) وارد context
  مدل می‌شدند. کامنت کد خلاف این را ادعا می‌کرد.
- **رفع:** `AssistantTurn.current` جملهٔ در حال پخش را نگه می‌دارد؛ ثبت سه‌حالته شد:
  جمله‌های کامل‌شده → همان‌ها؛ جملهٔ آغازشده → همان؛ هیچ صدایی نبود (قطع حین thinking) →
  متن تولیدشده فقط برای بازبینی (خارج از context).
- **تست:** `tests/test_bargein.py::test_sentences_queued_behind_the_spoken_one_never_play_after_barge_in`
  (همچنین ثابت می‌کند جمله‌های صف‌شده بعد از barge-in هرگز سنتز نمی‌شوند).
- **وضعیت:** رفع‌شده + تست

### P1-15 — سنجه‌های سیستمی در مسیر زنده هرگز ثبت نمی‌شدند

- **فایل:** `server/audio_io.py` → `pump`
- **ریشه:** `audio_callback_jitter_ms`, `queue_depth`, `cpu_usage_percent`, `ram_usage_mb` فقط
  نام‌های اعلام‌شده در `METRIC_NAMES` بودند؛ در اجرای واقعی هیچ‌جا مقدار نمی‌گرفتند، پس سؤال
  «آیا ماشین اشباع شده؟» بدون اجرای بنچمارک آفلاین پاسخ نداشت.
- **رفع:** انتشار دوره‌ای (پیش‌فرض ۱ ثانیه) از `duplex.stats()`، عمق صف capture+speech و
  نمونهٔ CPU/RAM با یک sampler پایدار؛ شمارش تضاعفی `audio_callback_count`.
- **تست:** `tests/test_audio_io.py::test_pump_publishes_system_and_queue_metrics`.
- **وضعیت:** رفع‌شده + تست (اعداد واقعی دستگاه: NOT MEASURED)

### P1-16 — خطای LLM، ماشین حالت را در `THINKING` رها می‌کرد

- **فایل:** `server/streaming_voice.py` → `Pipeline.respond` (شاخهٔ exception)
- **ریشه:** پاسخ ناموفق نشانگر commit در صف پخش نمی‌گذارد؛ هیچ مسیری هم حالت را عوض نمی‌کرد.
  نتیجه: `state=THINKING` بدون هیچ `pending_turn`، و گفتار بعدی کاربر به‌عنوان barge-in
  «نجات» می‌داد — با یک رویداد `interrupted` گمراه‌کننده و شمارندهٔ generation اضافی.
- **رفع:** پس از commit با وضعیت `failed`، اگر نوبتی معلق نمانده و صف پخش خالی است، گذار به
  `LISTENING`/`IDLE` با دلیل `llm_error` انجام می‌شود؛ نوبت بعدی مستقیماً پاسخ می‌گیرد.
- **تست:** `tests/test_streaming_voice.py::test_llm_failure_releases_the_state_and_the_next_turn_recovers`
  (پاسخ ۵۰۰ → `error`، وضعیت آزاد، نوبت بعدی موفق و بدون `state_violation`).
- **وضعیت:** رفع‌شده + تست

### P2-09 — بنچمارک، هفت گروه الزامی را پوشش نمی‌داد

- **فایل:** `tools/benchmark_voice.py`
- **ریشه:** فقط idle/asr/llm/tts/combined/bargein وجود داشت؛ «ASR+LLM» و «مکالمهٔ کامل دوطرفه»
  جدا اندازه‌گیری نمی‌شدند.
- **رفع:** سناریوهای `asr_llm`، `asr_llm_tts` و `duplex` (چند نوبت + یک قطع، با شمارش پیام‌های
  گم‌نشده)؛ `combined` به‌عنوان نام تاریخی حفظ شد.
- **تست:** `tests/test_benchmark_tools.py::test_benchmark_scenarios_are_declared`.
- **وضعیت:** رفع‌شده + تست؛ اعداد واقعی هدف NOT MEASURED

### P3-04 — `describe()` آستانهٔ مؤثر را گزارش نمی‌کرد

- **فایل:** `server/vad.py` → `describe`
- **ریشه:** `offset_level` مقدار پایه را نشان می‌داد، نه `continuation_level` بعد از کف نویز تطبیقی؛
  کالیبراسیون در اتاق نویزی گمراه‌کننده بود.
- **رفع:** گزارش `continuation_level`.
- **تست:** `tests/test_vad.py::test_describe_reports_the_effective_continuation_level`.
- **وضعیت:** رفع‌شده + تست

### مستندات و ابزارهای تکمیل‌شده در همین مرحله

| مورد | وضعیت |
|---|---|
| `docs/CONFIGURATION.md` (مرجع کامل `PVAI_*`؛ در آن زمان ۴۸ متغیر، اکنون ۸۲) | افزوده شد (قبلاً `config.py` به آن ارجاع می‌داد ولی وجود نداشت) |
| `docs/PROTOCOL.md` (قرارداد WebSocket/ESP32) | به‌روزرسانی شد: transport سرور پیاده‌شده، firmware همچنان نشده |
| `docs/TROUBLESHOOTING.md` | افزوده شد؛ نگاشت پیام واقعی کد به راه‌حل |
| `docs/HARDWARE_VALIDATION.md` | افزوده شد؛ چک‌لیست ۱۴ مرحله‌ای سناریوی پذیرش |
| `docs/BENCHMARKS.md` | افزوده شد؛ روش‌شناسی + جدول sandbox با برچسب + NOT MEASURED هدف |
| `docs/STATUS.md`, `docs/STREAMING_ARCHITECTURE.md` | بازنویسی؛ ادعاهای کهنه (۵۴ تست، «۶ نوبت در RAM»، «VAD پیاده‌نشده»، «حذف نتیجهٔ ASR منسوخ») حذف شد |
| `tests/test_state_machine.py` | افزوده شد (در سند قبلی ادعا شده بود ولی وجود نداشت) |
| `benchmarks/persian_eval.py` + `persian_eval_set.json` | افزوده شد (ادعای قبلی بدون فایل بود) |
| `tools/benchmark_voice.py` | افزوده شد (ادعای قبلی بدون فایل بود) |
| `.env.example` | تکمیل شد تا هر متغیر پیکربندی واقعی را نشان دهد |
| `docs/SPEC.md` | بخش ۱۷ افزوده شد (قرارداد موتور مکالمه ۱.۱)؛ قبلاً در این مرحله به‌روز نشده بود |
| `docs/AUDIT_REPORT.md` | گزارش نهایی A–F داخل مخزن (قبلاً فقط در پاسخ چت بود) |
| `tests/test_bargein.py` | تست‌های اختصاصی صدای کهنه و اکوی پخش |

---

## مرحلهٔ تکمیلی — transport صوتی LAN (۸ نوامبر ۲۰۲۶)

### ن-۱ — `play_end` پیش از فریم‌های صوتی ارسال می‌شد (نوبت چندجمله‌ای)

- **فایل:** `server/ws_voice.py` → نگاشت event‌ها و `_sender`
- **ریشه:** `tts_end` (مرز *جمله*) به `play_end` (مرز *نوبت*) نگاشت می‌شد و `_sender` همیشه کنترل را
  بر صدا مقدم می‌داشت. نتیجه: دستگاه «پایان پخش» را قبل از رسیدن بایت‌های همان جمله می‌گرفت و در
  پاسخ چندجمله‌ای چند `play_end` می‌دید.
- **رفع:** `play_end` فقط از `response_end` با `status=complete` ساخته می‌شود، `played_ms` از تعداد
  بلوک‌های تولیدشدهٔ همان نوبت می‌آید، و `_sender` تا خالی‌نشدن صف پخش، `play_end` را عقب می‌اندازد.
- **تست:** `tests/test_ws_voice.py::test_playback_frames_are_valid_protocol_frames` و
  `::test_one_play_end_per_turn_not_per_sentence`

### ن-۲ — پیام خطای دست‌دهی هرگز به دستگاه نمی‌رسید

- **فایل:** `server/ws_voice.py` → `_handshake`
- **ریشه:** خطا با `_queue_control` در صف می‌رفت، اما task فرستنده فقط بعد از دست‌دهی موفق شروع
  می‌شد؛ پس دستگاه فقط close code ۱۰۰۸ می‌دید، بدون دلیل.
- **رفع:** `_reject()` پیام خطا را مستقیم می‌فرستد و بعد می‌بندد.
- **تست:** چهار تست رد دست‌دهی در `tests/test_ws_voice.py`.

### ن-۳ — `interrupted` بدون `turn_id` و بدون پاک‌سازی صف پخش

- **فایل:** `server/streaming_voice.py` (`barge_in`)، `server/ws_voice.py`
- **ریشه:** event «interrupted» شناسهٔ نوبت را حمل نمی‌کرد، بنابراین `play_stop` با `turn_id=0` می‌رفت؛
  و صف پخش دستی خالی نمی‌شد، پس ممکن بود فریمی از نوبت لغوشده بعد از `play_stop` برود.
- **رفع:** `turn_id` به event اضافه شد و `ws_voice` پیش از ارسال `play_stop` صف را drain می‌کند.
- **تست:** `::test_device_interrupt_cancels_playback_and_reports_play_stop`،
  `::test_cancelled_playback_frames_are_dropped_before_play_stop`

### ن-۴ — قطع کلاینت SSE پایان‌دهی به تولید را تست نکرده بود (OPEN-03 سابق)

- **رفع:** تست ASGI سطح‌پایین `tests/test_api.py::test_sse_client_disconnect_cancels_upstream_generation`
  پس از `http.disconnect` بررسی می‌کند که generator بالادست بسته و تولید متوقف شود.

---

## مرحلهٔ سوم — بازتولید و رفع چهار باگ (۸ نوامبر ۲۰۲۶)

هر چهار مورد **ابتدا با اجرای واقعی بازتولید شد** (اسکریپت بازتولید، خروجی ثبت‌شده)، بعد رفع و
تست شد. فایل‌های تغییر‌یافته: `server/vad.py`, `server/streaming_voice.py`, `server/metrics.py`.

### B1-01 — پاسخ دستیار بی‌صدا گم می‌شد (نویز کوتاه، utterance ردشده)

- **فایل:** `server/vad.py` (`EnergyVAD._finish`), `server/streaming_voice.py` (`feed`, `_on_final`)
- **ریشه:** `_finish` گفتار کوتاه‌تر از `min_voiced_frames` (پیش‌فرض ۸ فریم = ۱۶۰ms) را دور
  می‌ریخت و فقط `(False, None)` برمی‌گرداند — همان چیزی که «هیچ اتفاقی نیفتاد» را هم نشان
  می‌داد. ولی onset از ۳ فریم فعال شده بود و `_begin_utterance` یک utterance ساخته،
  `_latest_utterance_id` را بالا برده و state را `LISTENING` کرده بود. نتیجهٔ بازتولید‌شده:
  صدا زدن `feed_utterance(speech=20, silence=32)` سپس `feed_utterance(speech=4, silence=32)`
  با ASR مسدود → فقط `response_skipped`، بدون `tts_start`، `state=listening` و
  `utterance.id=2` که هرگز بسته نمی‌شد.
- **رفع:**
  - `EnergyVAD.feed_event()` مرز صریح برمی‌گرداند: `onset` / `segment` / `rejected`
    (‏`feed()` قدیمی سازگار ماند). `rejected_utterances` هم شمرده می‌شود.
  - `Pipeline._reject_utterance()`: utterance لغو، partial دور ریخته، رویداد
    `utterance_rejected` منتشر، و `_settle_after_utterance()` به `IDLE` (یا `TRANSCRIBING`
    اگر final در صف است) برمی‌گردد.
  - `_latest_utterance_id` فقط در `_on_final` با متن معتبر جلو می‌رود؛ onset دیگر آن را
    بالا نمی‌برد. skip فقط وقتی رخ می‌دهد که نوبت جدیدتر **پاسخ گرفته باشد**
    (`response_skipped` با `reason=newer_final_answered`).
  - utterance بعدی که خالی/ناموفق بود در `_pending_answers` می‌ماند (کران‌دار) و
    `start_next_pending()` پاسخ آخرین پیام بی‌پاسخ را می‌دهد (`response_recovered`)،
    با تشخیص «قبلاً پاسخ گرفته» از خود `Conversation` تا پاسخ تکراری ساخته نشود.
- **تست:** `tests/test_short_noise_utterance.py` (۳ سناریوی الزامی: نویز کوتاه، نویز با ASR
  خالی، نویز بعد از final + دو حالت تکمیلی)، `tests/test_vad.py` (دو تست مرز صریح)،
  و به‌روزرسانی `test_final_asr_in_flight_survives_new_speech_onset` (که قبلاً رفتار باگ‌دار
  را assert می‌کرد).
- **وضعیت:** رفع‌شده + تست

### B2-01 — مسیر exception در `Pipeline.play` نوبت را در `SPEAKING` رها می‌کرد

- **فایل:** `server/streaming_voice.py` → `Pipeline.play`
- **ریشه:** شاخهٔ exception فقط `_commit_turn(..., failed)` می‌کرد، `token.set()` می‌زد و
  `continue` می‌کرد؛ state در `SPEAKING` می‌ماند و `turn.current` هم (اگر جمله نیمه‌کاره بود)
  پاک نمی‌شد. بازتولید: `state=speaking`، و گفتار بعدی رویداد `interrupted` با `turn_id=0`
  تولید می‌کرد.
- **ریشهٔ دوم (همان اجرا پیدا شد):** توکن سراسری `self.cancel` set می‌شد و جایگزین نمی‌شد؛
  هر پاسخ بعدی در اولین `token.is_set()` بی‌صدا abort می‌شد — یعنی یک خطای TTS دستیار را
  برای بقیهٔ نشست لال می‌کرد.
- **رفع:** مسیر TTS و مسیر LLM هر دو از `_fail_turn()` می‌گذرند: commit یک‌بار با وضعیت
  `failed`، پاک‌کردن `turn.current`، `turn.token.set()`، جایگزینی `self.cancel` اگر set باشد،
  حذف نشانگرهای مردهٔ همان نوبت از صف پخش، و `_release_after_failure()` برای گذار به
  `LISTENING`/`IDLE` وقتی صف خالی و `pending_turn is None` است.
- **تست:** `tests/test_turn_failures.py::test_tts_failure_leaves_speaking_and_is_not_a_barge_in`,
  `::test_conversation_recovers_after_a_tts_failure`
- **وضعیت:** رفع‌شده + تست

### B3-01 — `respond` با پیام‌های خالی: نه commit، نه خروج از `THINKING`

- **فایل:** `server/streaming_voice.py` → `Pipeline.respond`
- **ریشه:** `if not messages: return` قبل از هر commit/گذار. `pending_turn` می‌ماند، state روی
  `THINKING` قفل می‌شد و هیچ `response_end` برای نوبتی که `start_reply` اعلام کرده بود منتشر
  نمی‌شد. (مسیر `context_with_system` که exception بدهد هم چون خارج از `try` بود، task را با
  استثنای خام می‌کشت.)
- **رفع:** هر دو حالت (context خالی، خطای ساخت تاریخچه) به `_fail_turn()` با دلیل صریح
  (`empty_context` / `history_error`) می‌روند: `response_end` با وضعیت `failed`، پاک‌شدن
  `pending_turn`، خروج از `THINKING`، و رویداد `error` قابل‌فهم. هیچ درخواستی به مدل نمی‌رود.
- **تست:** `tests/test_turn_failures.py::test_empty_context_fails_the_turn_instead_of_wedging_thinking`,
  `::test_history_failure_is_a_failed_turn_not_a_stuck_state`,
  `::test_empty_context_turn_does_not_block_the_next_utterance`
- **وضعیت:** رفع‌شده + تست

### P0-04 — final پشت partial در حال decode معطل می‌ماند (بدون سنجه و بدون راه فرار)

- **فایل:** `server/streaming_voice.py` → `AsrScheduler`, `Pipeline.recognize`
- **ریشه:** یک actor تنها برای Whisper (الزام CPU دوهسته‌ای) یعنی partialی که شروع شده تا پایان
  decode خودش (تا `PVAI_ASR_PARTIAL_WINDOW_S` = ۶ ثانیه صوت) قابل توقف نبود؛ final پشت آن
  می‌ماند. تأخیر هم قابل انتساب نبود: `asr_final_latency_ms` انتظار صف، انتظار پشت partial و
  خود decode را در یک عدد جمع می‌کرد.
- **رفع:**
  - `AsrJob.started_at` + سه سنجه: `asr_final_dispatch_wait_ms`،
    `asr_final_wait_behind_partial_ms`، `asr_final_decode_ms` (به‌علاوهٔ
    `asr_partial_decode_ms`, `asr_partial_wasted_ms`).
  - `AsrScheduler.preempt()`/`begin()`/`end()`: به‌محض صف‌شدن final، توکن partial در حال اجرا
    set می‌شود؛ backendی که `transcribe_pcm(pcm, token, job)` را می‌پذیرد همان‌جا متوقف
    می‌شود (`asr_partial_preempted`/`asr_partial_cancelled`). backend قدیمی
    `transcribe_pcm(pcm)` سازگار می‌ماند: نتیجه‌اش دور ریخته می‌شود و همان تأخیر ثبت می‌شود.
  - partial منسوخ (final همان utterance یا جدیدتر در صف) در `submit_partial` رد می‌شود.
  - flag فقط با «همان jobی که preempt شده» تفسیر می‌شود، پس یک partial سالم که هم‌زمان با
    رسیدن partial جدید تمام شده، دور ریخته نمی‌شود.
- **تست:** `tests/test_asr_preemption.py` (۵ تست). عدد SIMULATED در sandbox: با partial
  ۲ ثانیه‌ای، پاسخ از **۱۹۶۵ms به ۳ms** رسید. عدد دستگاه واقعی **NOT MEASURED**.
- **وضعیت:** رفع‌شده + تست (SIMULATED)

---

## مرحلهٔ چهارم — بازتولید و رفع چهار باگ مسیر صوتی (۸ نوامبر ۲۰۲۶)

فایل‌ها: `server/audio_io.py` (`stream_forever`, `pump`)، `server/aec.py` (`DuplexAudio`)،
`server/streaming_voice.py` (`Pipeline.reset`). هر چهار مورد **اول بازتولید شد** (خروجی در متن)،
بعد رفع و تست شد. تست‌ها: `tests/test_audio_io.py` (+۱۱ تست جدید).

### A1-01 — حلقهٔ بازگشایی بی‌پایان برای دستگاهی که باز می‌شود ولی فریم نمی‌دهد

- **فایل:** `server/audio_io.py` → `stream_forever`
- **ریشه:** `attempts = 0` بلافاصله داخل `with sd.RawStream(...)` اجرا می‌شد، یعنی به‌محض
  موفقیت `__enter__` و **قبل** از آنکه معلوم شود دستگاه واقعاً صدا می‌دهد. میکروفون mute،
  دستگاه exclusive یا هدست بلوتوثی که کانال ورودی را واگذار کرده، هر بار `AudioStalled`
  می‌داد و هر بار شمارنده صفر می‌شد؛ پس `PVAI_AUDIO_REOPEN_ATTEMPTS` هرگز پر نمی‌شد.
- **بازتولید:** fake sd که باز می‌شود و ۳ فریم می‌دهد و بعد ساکت می‌شود، با سقف ۲ →
  در ۳ ثانیه **۱۲ بار** باز شد و همچنان ادامه داشت (اجرای مقایسه‌ای old/new هم همین را نشان داد:
  `old: STILL LOOPING (12 opens, cap 2)` در برابر `new: VoiceError after 3 opens`).
- **رفع:** سهمیهٔ تلاش‌ها فقط وقتی ریست می‌شود که همان تلاش دست‌کم
  `PVAI_AUDIO_REOPEN_STABLE_FRAMES` فریم **واقعی** تحویل داده باشد (پیش‌فرض ۵۰ = ۱ ثانیه).
  بعد از سقف، `VoiceError` فارسی و عملی می‌آید که دقیقاً همین سه علت را نام می‌برد
  (mute / exclusive / هدست) و به `PVAI_AUDIO_INPUT_DEVICE` ارجاع می‌دهد. تابع `_retry()`
  این تفکیک را انجام می‌دهد: «دستگاهی که کار می‌کرد و مرد» یک بازگشایی می‌گیرد،
  «دستگاهی که هرگز یک فریم هم نداد» سهمیه را مصرف می‌کند.
- **تست:** `test_silent_device_exhausts_the_reopen_budget_instead_of_looping_forever`,
  `test_a_short_burst_of_frames_does_not_refund_the_reopen_budget`,
  `test_a_device_that_delivered_real_frames_earns_a_fresh_budget`,
  `test_stable_frames_threshold_is_configurable`
- **وضعیت:** رفع‌شده + تست (hardware: NOT TESTED)

### A2-01 — دو ساعت متفاوت در سنجش stall (`monotonic` در برابر `perf_counter`)

- **فایل:** `server/audio_io.py` → `pump`، `server/aec.py` → `DuplexAudio._measure`
- **ریشه:** watchdog با `time.monotonic() - last_frame_at` حساب می‌کرد ولی
  `_measure` مقدار `last_frame_at` را با `time.perf_counter()` می‌نوشت. مبدأ `perf_counter`
  تعریف‌نشده است و روی Windows (سیستم هدف) با `monotonic` هم‌مبدأ نیست؛ نتیجه بسته به علامت
  اختلاف می‌تواند «stall کاذب روی stream سالم» یا «پذیرش بی‌صدای دستگاه مرده» باشد.
- **رفع:** همهٔ مُهرهای زمانی این مسیر از `time.monotonic` می‌آیند (`DuplexAudio._measure`,
  `opened()`, `pump`). `_stall_baseline()` تنها جایی است که watchdog ساعت می‌خواند.
- **تست:** `test_callback_timestamps_share_the_watchdogs_clock` (با `perf_counter` جابه‌جاشده،
  مهر زمانی باید داخل بازهٔ `monotonic` قبل/بعد بماند)
  و `test_healthy_stream_is_not_stalled_when_perf_counter_has_another_origin`.
- **وضعیت:** رفع‌شده + تست

### A3-01 — دستگاهی که هرگز callback نمی‌دهد هیچ‌وقت stall تشخیص داده نمی‌شد

- **فایل:** `server/audio_io.py` → `pump`، `server/aec.py` → `DuplexAudio.opened`
- **ریشه:** شرط `if last is not None and ...` باعث می‌شد تا اولین callback، `last_frame_at`
  همیشه `None` باشد؛ پس «میکروفونی که stream باز می‌کند و هیچ فریمی نمی‌دهد» و
  «دستگاه سالم» از نظر watchdog یکسان بودند. باگ A1-01 هم دقیقاً به همین دلیل بی‌پایان می‌شد.
- **بازتولید:** fake sd بدون هیچ callback با سقف ۳ → فقط ۱ بار باز شد و تا ابد منتظر ماند.
- **رفع:** دو مبنا: `DuplexAudio.opened()` لحظهٔ باز شدن stream را ثبت می‌کند و
  `stall_baseline()` بین «آخرین فریم» و «لحظهٔ باز شدن» انتخاب می‌کند؛ در `pump` هم اگر
  هیچ مبنایی نبود، لحظهٔ شروع خود watchdog مبنا می‌شود (هرگز بررسی غیرفعال نمی‌شود).
- **تست:** `test_pump_detects_a_stream_that_never_delivered_a_frame`,
  `test_stall_is_measured_from_stream_open_not_only_from_the_first_frame`
- **وضعیت:** رفع‌شده + تست

### A4-01 — بازگشایی stream، حالت VAD/Pipeline و AEC را ریست نمی‌کرد

- **فایل:** `server/audio_io.py` → `stream_forever`/`reset_stream_state`،
  `server/aec.py` → `DuplexAudio.reset_state`، `server/streaming_voice.py` → `Pipeline.reset`
- **ریشه:** فقط مسیر overflow ریست می‌کرد. بعد از reopen، detector ممکن بود نیمهٔ یک utterance
  را از قبل از قطعی نگه دارد و با صدای بعد از gap کاملش کند (ترنسکریپت ساختگی)، و فیلتر AEC
  مسیر پژواکِ دستگاهی را که دیگر وجود ندارد نگه می‌داشت.
- **بازتولید:** fake sd که یک فریم می‌دهد و می‌میرد → `detector.reset: 0 | aec.reset: 0`.
- **رفع:** `reset_stream_state()` روی هر reopen صدا زده می‌شود: `DuplexAudio.reset_state()`
  (خالی‌کردن صف capture و پاک‌کردن مهرهای زمانی)، `aec.reset()`، و `Pipeline.reset()`
  که utterance ناتمام و partial را رها می‌کند، رویداد `input_reset` می‌دهد، به `IDLE`
  برمی‌گردد — و **به صف final دست نمی‌زند** (همان قرارداد `overflow`).
- **تست:** `test_reopen_resets_detector_aec_and_buffered_audio`,
  `test_reset_stream_state_handles_plain_fakes`,
  `test_pipeline_reset_abandons_the_unfinished_utterance_but_keeps_finals`
- **وضعیت:** رفع‌شده + تست

سنجه‌های تازه: `audio_reopen_count`, `audio_reopen_attempts`, `audio_reopen_reset_count`.

## مرحلهٔ پنجم — بازتولید و رفع باگ «نشستِ resum‌شدهٔ خالی» (۸ نوامبر ۲۰۲۶)

### C1-01 — `create_conversation` هرگز فایل موجود را نمی‌خواند: context از دست می‌رفت و شناسه‌ها تکرار می‌شدند

- **فایل:** `server/conversation.py` → `create_conversation`, `Conversation.load`؛
  `server/streaming_voice.py` → `Pipeline.__init__`, `create_pipeline`؛
  `server/ws_voice.py` → `_open_pipeline`
- **ریشه:** فقط `Conversation.load` فایل JSONL را می‌خواند و تنها `/api/chat` از آن استفاده
  می‌کرد. `create_conversation(settings, session_id="demo")` یک `Conversation` **خالی** می‌ساخت
  و همان `session_id` را به فایل موجود می‌چسباند. نتیجه سه‌گانه بود:
  1. `messages` خالی ⇒ context مدل فقط نوبت جدید بود (الزام ۱۷ در `docs/STATUS.md` نقض می‌شد)،
  2. `_next_id`/`_next_turn` از ۱ شروع می‌شد ⇒ `id` و `turn_id` تکراری در همان فایل،
  3. مجموعهٔ dedupe خالی بود ⇒ `utterance_id`های ذخیره‌شده دوباره پذیرفته می‌شدند.
  علاوه بر این `Pipeline._next_utterance_id` همیشه از ۱ شروع می‌شد، پس در نشست resum‌شده
  **اولین utterance تازه** شناسه‌ای می‌گرفت که از قبل در فایل بود و بی‌صدا به‌عنوان «تکرار»
  دور انداخته می‌شد — رویداد `transcript_final` می‌آمد ولی نه پیام کاربر و نه پاسخی ثبت نمی‌شد.
- **بازتولید:** `/tmp/repro_conv.py` (دو بار `create_conversation(settings, session_id="demo")`)
  ⇒ نشست دوم `messages: []`، `next id 1`، فایل `ids: [1, 2, 1, 2]`، `turn_ids: [None, 1, None, 1]`،
  context فقط شامل دو پیام جدید. `/tmp/repro_utt.py` ⇒ `NEW UTTERANCE DROPPED`.
- **رفع:**
  - `create_conversation` هر نشستی را که قرار است در آن نوشته شود از `Conversation.load`
    می‌خواند (`resume_target` انتخاب می‌کند کدام نشست)؛ بنابراین `_next_id`، `_next_turn`،
    مجموعهٔ dedupe و `_next_utterance_id` همه از حداکثر موجود ادامه می‌یابند.
  - `Conversation.next_utterance_id()` افزوده شد و `Pipeline` شمارندهٔ utterance خود را از آن
    می‌گیرد (نشست تازه همان `1` می‌ماند؛ تست جداگانه تضمینش می‌کند).
  - **تصمیم دربارهٔ پیش‌فرض resume (مستندشده):** `PVAI_HISTORY_RESUME=on` پیش‌فرض است و فقط
    مسیر **زندهٔ محلی** را تحت تأثیر می‌گذارد: با نبود `session_id`، نشستِ
    `PVAI_VOICE_SESSION_ID` (اگر تنظیم شده باشد) وگرنه آخرین نشست نوشته‌شده ادامه می‌یابد.
    `PVAI_HISTORY_RESUME=off` رفتار قدیمی (هر اجرا نشست تازه) را برمی‌گرداند.
    `session_id` صریح همیشه resume می‌شود، مستقل از این کلید.
  - **استثنای لازم برای انزوا:** `ws_voice._open_pipeline` با `resume=False` صدا می‌زند، پس
    دستگاهی که `session_id` نمی‌فرستد هرگز به نشست محلی/دستگاه دیگر نمی‌چسبد
    (`test_device_without_a_session_id_never_joins_the_last_local_session`).
    `/api/chat` بدون `session_id` هم دست‌نخورده می‌ماند (همیشه RAM-only).
- **تست:** `test_create_conversation_resumes_an_explicit_session`,
  `test_resumed_conversation_deduplicates_stored_utterances`,
  `test_resume_defaults_to_the_last_session_for_the_local_loop`,
  `test_history_resume_off_keeps_every_start_fresh`,
  `test_voice_session_id_pins_the_local_session`,
  `test_resume_with_persistence_off_starts_fresh`,
  `test_streaming_voice.py::test_resumed_pipeline_continues_ids_and_keeps_the_first_new_utterance`,
  `test_streaming_voice.py::test_fresh_pipeline_still_starts_its_utterance_ids_at_one`,
  `test_ws_voice.py::test_device_resumes_a_session_with_its_earlier_context`,
  `test_ws_voice.py::test_resumed_device_session_context_reaches_the_model`
- **وضعیت:** رفع‌شده + تست (هر ۱۰ تست روی کد قدیم شکست می‌خورند)

### C1-02 — `ConversationStore.sanitize` حروف/ارقام یونیکد و نام‌های رزروشدهٔ ویندوز را می‌پذیرفت

- **فایل:** `server/conversation.py` → `sanitize`, `append`
- **ریشه:** `char.isalnum()` حروف فارسی/عربی و ارقام فارسی را قبول می‌کرد و آن‌ها داخل نام فایل
  می‌رفتند؛ روی ویندوز (case folding و نرمال‌سازی یونیکد) دو شناسهٔ متفاوت می‌توانند یک فایل
  شوند. نام‌های دستگاه (`CON`, `NUL`, `AUX`, `COM1`…) هم پذیرفته می‌شدند، در حالی که
  `<name>.jsonl` روی ویندوز ساخته نمی‌شود.
- **بازتولید:** `sanitize('CON')`, `sanitize('café')`, `sanitize('sess-١٢٣')` همه پذیرفته می‌شدند.
- **رفع:** الفبای مجاز فقط `[A-Za-z0-9_-]` (`SESSION_ID_ALPHABET`)؛ `WINDOWS_RESERVED_NAMES`
  (شامل `COM1`–`COM9`, `LPT1`–`LPT9`, `clock$`) رد می‌شود؛ و چون `S-DEMO`/`s-demo` روی
  فایل‌سیستم حساس‌نبودن به بزرگی/کوچکی یک فایل‌اند، `append` شناسه‌ای را که فقط در بزرگی/کوچکی
  با نشست موجود فرق دارد رد می‌کند (`store.error` توضیح می‌دهد) تا لاگ دو نشست قاطی نشود.
- **تست:** `test_session_id_rejects_non_ascii_letters_and_digits`,
  `test_session_id_rejects_windows_reserved_device_names`,
  `test_session_id_keeps_the_documented_alphabet`,
  `test_case_insensitive_collision_is_refused_instead_of_sharing_a_file`,
  `test_settings_reject_an_invalid_voice_session_id`
- **وضعیت:** رفع‌شده + تست. **سازگاری:** شناسه‌های غیر‑ASCII که قبلاً ذخیره شده‌اند دیگر از طریق
  API/CLI قابل خواندن نیستند؛ چنین نشستی فقط با تغییر نام فایل به الفبای مجاز بازیابی می‌شود.

### C1-03 — `sessions()` برای شمردن پیام‌ها کل فایل را JSON-parse می‌کرد و event loop را می‌بست

- **فایل:** `server/conversation.py` → `sessions`, `count_messages`؛ `server/main.py` → `GET /api/sessions`
- **ریشه:** در هندلر async، `sessions()` هر فایل را کامل می‌خواند و هر خط را `json.loads`
  می‌کرد فقط برای شمردن `kind == "message"`. یک فایل ۱۱ مگابایتی ۲۰٬۰۰۰ پیامی ۶۲٫۵ms طول
  می‌کشید و همان‌جا روی event loop اجرا می‌شد (صدای زنده و WebSocket پشت آن می‌ماند).
- **رفع:** `count_messages` (و `_count_messages`) با خواندن باینری خط‌به‌خط و تست پیشوند/پسوند
  `{"kind": "message"…}` می‌شمارد؛ هر خط غیرمنتظره (خط پارهٔ آخر فایل، فایل دست‌کاری‌شده) به
  مسیر دقیق `json.loads` می‌رود تا شمارش هرگز بیشتر از واقع نشود. هندلر هم با
  `asyncio.to_thread(store.sessions)` اجرا می‌شود.
- **اندازه‌گیری (همان فایل در sandbox):** ۶۲٫۵ms → ۱۳٫۹ms، با شمارش درست ۲۰٬۰۰۰.
- **تست:** `test_session_listing_counts_messages_without_parsing_the_whole_file`,
  `test_latest_session_follows_the_write_time`
- **وضعیت:** رفع‌شده + تست

### C1-04 — `history_persist=true` متن کامل گفتار را بی‌پایان نگه می‌داشت

- **فایل:** `server/config.py`, `server/conversation.py` → `purge_older_than`/`purge_expired`,
  `server/main.py` (startup), `server/sessions.py` → `purge`
- **ریشه:** `PVAI_HISTORY_PERSIST` پیش‌فرض روشن است و هر ترنسکریپت به‌صورت متن ساده در JSONL
  می‌ماند؛ هیچ سیاست نگه‌داری‌ای نبود، پس هم مسئلهٔ حریم خصوصی بود و هم دیسک، بدون آنکه کاربر
  بدون اجرای CLI ببیند.
- **رفع:** `PVAI_HISTORY_RETENTION_DAYS` (پیش‌فرض `0` = نگه‌داشتن همه، سازگار با رفتار قبلی).
  با مقدار مثبت، نشست‌هایی که mtime آن‌ها قدیمی‌تر است در startup API پاک می‌شوند
  (`purge_expired`) و با `python -m server.sessions purge [--days N]` دستی هم اجرا می‌شود.
- **تست:** `test_retention_purges_only_sessions_older_than_the_limit`,
  `test_retention_zero_keeps_everything_and_disabled_persistence_is_a_no_op`,
  `test_env_history_resume_and_retention_are_parsed`
- **وضعیت:** رفع‌شده + تست

## مرحلهٔ ششم — بازتولید و رفع باگ‌های `.env` (۸ نوامبر ۲۰۲۶)

### D1-01 — کامنت هم‌خط در `.env` جزو مقدار می‌شد و مقدار عددی بی‌صدا به پیش‌فرض برمی‌گشت

- **فایل:** `server/config.py` → `load_dotenv`, `_env_int/_env_float/_env_optional_bool/_env_choice`
- **ریشه:** `load_dotenv` خط را فقط با `strip()` تمیز می‌کرد و کل باقیمانده را مقدار می‌گذاشت، در حالی که
  خودِ `.env.example` چند متغیر را با توضیح هم‌خط مستند کرده است:
  `PVAI_N_THREADS=2          # 0 = auto…`. با `Copy-Item .env.example .env` (همان قدم اول README)
  مقدار رشتهٔ `'2          # 0 = auto…'` می‌شد؛ `int(...)` خطا می‌داد و `_env_int` **بی‌صدا**
  پیش‌فرض را برمی‌گرداند. نتیجه: `PVAI_N_THREADS=2` عملاً `0` بود. همین برای
  `PVAI_N_GPU_LAYERS`, `PVAI_VOICE_RECORD_SECONDS` و `PVAI_AEC_DELAY_MS` صادق بود — کسی که
  `PVAI_AEC_DELAY_MS` را کالیبره می‌کرد و کنارش کامنت می‌گذاشت، مقدارش را از دست می‌داد.
- **بازتولید:** کپی `.env.example` به `.env` و `Settings.from_env()` ⇒
  `n_threads=0` (فایل: 2)، `n_gpu_layers=0` (فایل: 7)، `voice_record_seconds=5.0` (فایل: 7)،
  `aec_delay_ms=40` (فایل: 55) — هر چهار مقدار نادیده.
- **رفع:** `strip_inline_comment()` کامنت را حذف می‌کند: `#` فقط در ابتدای مقدار یا بعد از فاصله
  کامنت است و هرگز داخل کوتیشن نیست، پس `PVAI_SYSTEM_PROMPT="یک # دو"` سالم می‌ماند و
  `abc#def` یک مقدار است. `.env.example` هم بازنویسی شد (توضیح‌ها به خط جدا) و
  `tests/test_env_config.py::test_env_example_has_no_inline_comments_left_to_bite_anyone`
  جلوگیری می‌کند دوباره برنگردد.
- **تست:** `test_copying_env_example_produces_the_values_it_documents` (هر ۱۳ متغیر عددی)،
  `test_strip_inline_comment` (۹ حالت)، `test_commented_values_survive_a_round_trip`
- **وضعیت:** رفع‌شده + تست

### D1-02 — مقدار نامعتبر یا کلمپ‌شده هیچ اثری از خود نمی‌گذاشت

- **فایل:** `server/config.py` → `ENV_ISSUES`, `Settings.env_issues`, `Settings.config_errors`
- **ریشه:** `_env_int/_env_float/_env_optional_bool` در هر سه حالت (غیرعددی، بولی نامفهوم، خارج از
  بازه) به مقدار پیش‌فرض یا حد مجاز برمی‌گشتند و **هیچ** لاگ/خطایی تولید نمی‌کردند. یعنی یک
  `.env` کاملاً غلط از یک `.env` درست قابل تشخیص نبود.
- **رفع:** هر رد/کلمپ با `logger.warning` (شامل نام متغیر و دلیل) لاگ می‌شود، در
  `ENV_ISSUES` ثبت و در `Settings.from_env()` به `Settings.env_issues` منتقل می‌شود (بعد از هر
  فراخوانی پاک می‌شود تا در یک پروسهٔ طولانی انبار نشود)، و `config_errors()` آن‌ها را برمی‌گرداند —
  بنابراین `GET /ready` (بخش `configuration`) و پیام startup مسیر زنده نشانش می‌دهند.
- **تست:** `test_invalid_number_is_reported_not_silently_defaulted`,
  `test_clamped_value_is_reported`, `test_invalid_boolean_and_choice_are_reported`,
  `test_a_clean_environment_reports_nothing`, `test_issues_do_not_accumulate_across_calls`,
  `test_ready_endpoint_surfaces_rejected_values`
- **وضعیت:** رفع‌شده + تست

### D1-03 — `.env` فقط از پوشهٔ جاری خوانده می‌شد

- **فایل:** `server/config.py` → `load_dotenv`, `dotenv_candidates`, `PROJECT_ROOT`
- **ریشه:** `env_path = Path.cwd() / ".env"`. اجرا از هر مسیری غیر از ریشهٔ پروژه (Task Scheduler،
  IDE، اسکریپت داخل `tools/`) یعنی `.env` **بی‌صدا** نادیده گرفته می‌شد و سرویس با پیش‌فرض‌ها
  بالا می‌آمد.
- **رفع:** `dotenv_candidates()` اول `.env` پوشهٔ جاری را می‌بیند و بعد `.env` ریشهٔ پروژه
  (`PROJECT_ROOT`) را؛ اولین فایل موجود برنده است، پس `cd` به یک پوشهٔ استقرار هنوز اولویت دارد.
  مسیر خوانده‌شده در `DOTENV_LOADED` ثبت می‌شود.
- **تست:** `test_dotenv_is_found_from_any_working_directory`,
  `test_current_directory_env_wins_over_the_project_root`, `test_explicit_path_is_used_verbatim`
- **وضعیت:** رفع‌شده + تست

### D1-04 — `PVAI_N_THREADS` / `PVAI_N_GPU_LAYERS` / `PVAI_N_CTX` هیچ‌جا استفاده نمی‌شدند

- **فایل:** `server/config.py` → `llama_server_command`, `_print_launch`؛ `tools/start_llama.ps1`
- **ریشه:** این سه فیلد فقط در dataclass تعریف شده بودند و هیچ مصرف‌کننده‌ای نداشتند؛ کلاینت HTTP
  آن‌ها را نمی‌فرستد (llama-server مسیر مدل و تعداد thread را خودش تعیین می‌کند). در همان حال
  README دستور دستی با `-t 4` داشت ولی `.env.example` می‌گفت ۲ — یعنی مستندات دو چیز متفاوت
  می‌گفتند و هیچ‌کدام از `.env` اثر نمی‌گرفت.
- **رفع:** `Settings.llama_server_command()` همین تنظیمات را به `-m/-c/--host/--port/-t/-ngl`
  تبدیل می‌کند (`-t` فقط وقتی `n_threads>0` باشد؛ `0` = خودِ llama-server) و
  `python -m server.config --launch [--launch-json]` آن را چاپ می‌کند.
  `tools/start_llama.ps1` همان خروجی JSON را می‌خواند و سرور را اجرا می‌کند (`-DryRun` برای
  دیدن دستور، `-Exe` برای مسیر باینری). مستندات یکسان شدند: `.env.example` روی
  `PVAI_N_THREADS=4` (۲ هستهٔ فیزیکی × ۲ رشتهٔ منطقی، همان پیکربندی `docs/SPEC.md`) و
  README/PC_VOICE دیگر `-t 4` را دستی تکرار نمی‌کنند.
- **تست:** `test_launch_command_uses_the_configured_flags`,
  `test_launch_command_omits_threads_when_auto`,
  `test_the_file_flags_and_the_documented_manual_command_agree`,
  `test_launch_cli_prints_the_command`, `test_launch_cli_warns_about_rejected_values`
- **وضعیت:** رفع‌شده + تست (اجرای واقعی PowerShell در sandbox تست نشده؛ **NOT MEASURED**)

---

<a id="section-17"></a>

## بخش 17 — docs/AUDIT_REPORT.md

# گزارش نهایی بازبینی موتور مکالمهٔ PersianVoiceAI

> **سند تاریخی:** وضعیت و اندازه‌های زیر مربوط به بازبینی همان commit هستند.
> وضعیت جاری در [STATUS](STATUS.md) و [راهنمای یکپارچه](PROJECT_GUIDE.md) است؛
> مثلاً پیش‌فرض partial اکنون ۳ ثانیه است، نه ۶ ثانیهٔ این گزارش.

مبنا: commit `9654a68` → شاخهٔ `arena/31347dde-persianvoiceai`. این سند همان خروجی‌های A تا F
راستی‌آزمایی است و **تنها** چیزهایی را «انجام‌شده» می‌نامد که کد و تست دارند.

---

## A. دفترچهٔ ایراد (خلاصهٔ اجرایی)

| شناسه | شدت | فایل / تابع | ریشه | رفع | تست |
|---|---|---|---|---|---|
| P0-01 | P0 | `streaming_voice.py` (`interrupt`, `recognize`, `offer`, `feed`) | صف مشترک partial/final، بررسی `generation` بعد از decode، خالی‌کردن صف در interrupt | تفکیک `Utterance`/`AssistantTurn`/`PartialASR`/`FinalASR`؛ صف final FIFO بدون‌افت؛ dedupe با `utterance_id` | `test_utterance_lifecycle.py` (۶ سناریو) |
| P0-02 | P0 | `streaming_voice.py`, `voice.py` | decode کل بخش در حال رشد هر ۰٫۸s، head-of-line blocking | پنجرهٔ partial محدود ۶s، اولویت مطلق final، سنجه‌های `asr_*` | `test_asr_scheduler.py` |
| P0-03 | P0 | `config.py`, `voice.py` | ۴ thread برای Whisper روی ۲ هستهٔ فیزیکی | `PVAI_ASR_THREADS` جدا، صف محدود، AEC خارج از callback | `test_resources.py`, `test_audio_io.py` |
| P1-01 | P1 | `respond` | system prompt در مسیر صوتی ارسال نمی‌شد | `context_with_system` در هر دو مسیر | `test_system_prompt_is_sent_to_the_model` |
| P1-02 | P1 | `feed` | لغو یکسان در پنج وضعیت مختلف | ماشین حالت + barge-in مقید | `test_state_machine.py` |
| P1-03 | P1 | `live_voice.Endpoint` | آستانهٔ ثابت، بدون هیسترزیس/پیش‌رول | `server/vad.py` | `test_vad.py` (۱۲ تست) |
| P1-04 | P1 | `live_voice.Speaker` | تک‌صدا، نبود سنجهٔ تأخیر | انتخاب male/female، callbackهای first chunk/frame/complete | `test_tts.py` |
| P1-05 | P1 | `streaming_voice` history | فقط RAM، برش با تعداد پیام، پاسخ قطع‌شده بدون وضعیت | `server/conversation.py` + JSONL | `test_conversation.py`, `test_sessions.py` |
| P1-06 | P1 | `main.py` | نبود readiness و جداسازی نشست | `/ready`, `/api/sessions`, `session_id` | `test_api.py` (+ جداسازی نشست) |
| P1-07 | P1 | `run_live` | دستگاه پیش‌فرض، بدون validation/recovery | `server/audio_io.py` | `test_audio_io.py` |
| P1-08 | P1 | `aec.py` | فریم نامعتبر `ValueError`، نبود وضعیت startup | `aec_status`, پیام فارسی، سنجهٔ `aec_process_ms` | `test_aec.py` |
| P1-09 | P1 | — | بدون سنجه | `server/metrics.py` | `test_metrics.py` |
| P1-10 | P1 | `AsrScheduler.next` | lost wakeup: `check → clear → wait` | clear قبل از بررسی مجدد | `test_submit_landing_between_check_and_wait_is_not_lost` |
| P1-11 | P1 | `Pipeline.play` | حالت پس از commit در `SPEAKING` گیر می‌کرد | گذار به LISTENING/IDLE | `test_pipeline_walks_the_documented_states_for_one_exchange` |
| P1-12 | P1 | `Speaker._chunk_pcm` | نرخ بومی Piper (22.05k) داخل stream 16k | `_to_mono_rate` | `test_speaker_only_path_resamples_piper_rate_to_the_output_stream` |
| P1-13 | P1 | `resources._LinuxSampler` | baseline CPU صفر ⇒ میلیون‌درصد | prime با tick فعلی | `test_fresh_sampler_first_reading_is_plausible` |
| P1-14 | P1 | `_commit_turn` | قطع در جملهٔ اول، کل متن تولیدشده را «شنیده‌شده» ثبت می‌کرد | `turn.current` و سه‌حالت ثبت | `test_bargein.py::test_sentences_queued_behind_the_spoken_one_never_play_after_barge_in` |
| P2-01…P2-08 | P2 | — | نبود سنجه/بنچمارک/پنجرهٔ partial/`pending_history`/`vad_filter`/برش ناهمگام/نرخ نمونه‌برداری/RAM نامحدود | همه رفع‌شده | `test_metrics`, `test_benchmark_tools`, `test_vad`, `test_conversation`, `test_long_run` |
| P3-01…P3-04 | P3 | — | نام‌گذاری، مستندات کهنه، نبود ارزیابی فارسی، گزارش آستانهٔ مؤثر | همه رفع‌شده | `test_persian`, `test_vad` |

موارد باز: **OPEN-01** توقف قطعی inference در حال اجرا (نیازمند process isolation)،
**OPEN-02** firmware ESP32/رجیستری/OTA (transport سرور و تست قطع SSE بسته شدند — بخش G)،
**OPEN-03** اکوی واقعی اتاق، **OPEN-04** VAD عصبی، **OPEN-05** Command Broker/IoT.

## B. فایل‌های تغییریافته

**افزوده (کد):** `server/audio_io.py`, `conversation.py`, `metrics.py`, `persian.py`, `protocol.py`,
`readiness.py`, `resources.py`, `sessions.py`, `vad.py`
**اصلاح (کد):** `streaming_voice.py`, `live_voice.py`, `config.py`, `aec.py`, `voice.py`, `main.py`
**افزوده (ابزار/داده):** `tools/benchmark_voice.py`, `benchmarks/persian_eval.py`,
`benchmarks/persian_eval_set.json`, `benchmarks/results/voice-sandbox-20261108.json`
**افزوده (تست):** `test_utterance_lifecycle`, `test_asr_scheduler`, `test_state_machine`,
`test_bargein`, `test_long_run`, `test_benchmark_tools`, `test_audio_io`, `test_aec`, `test_api`,
`test_conversation`, `test_metrics`, `test_persian`, `test_protocol`, `test_resources`,
`test_sessions`, `test_streaming_voice`, `test_tts`, `test_vad`, `test_voice`, `voicekit.py`
**مستندات:** `README`, `AGENTS`, `.env.example`, `docs/STATUS`, `docs/SPEC` (بخش ۱۷),
`docs/PC_VOICE`, `docs/STREAMING_ARCHITECTURE`, `docs/AEC`, `docs/ISSUE_REGISTER`,
`docs/CONFIGURATION`, `docs/PROTOCOL`, `docs/BENCHMARKS`, `docs/TROUBLESHOOTING`,
`docs/HARDWARE_VALIDATION`, `docs/AUDIT_REPORT` (همین فایل)

حذف فایل: ندارد. وابستگی جدید: ندارد (numpy/scipy از قبل در `requirements-voice.txt` بودند).

## C. گزارش تست

```text
$ python -m pytest
235 passed, 2 skipped
```

- ۲ skip فقط تست‌های native SpeexDSP (کتابخانه در sandbox ساخته نشده).
- تست پایه پیش از این کار: ۵۳ passed.
- همهٔ تست‌ها fake/ساختگی: بدون مدل، بدون میکروفون، بدون شبکهٔ واقعی.
- **تست سخت‌افزاری انجام نشده**؛ چک‌لیست آن در `docs/HARDWARE_VALIDATION.md` است.

نگاشت ۲۰ الزام به تست در `docs/STATUS.md` §۲ آمده است (به‌روزرسانی‌شده با تست‌های تازه).

## D. گزارش کارایی

| معیار | sandbox | i7-6500U |
|---|---|---|
| coordinator jitter p95 | ۰٫۳۹ ms (MEASURED) | NOT MEASURED |
| RAM idle (بدون مدل) | ۲۹٫۵ MB (MEASURED) | NOT MEASURED |
| `asr_ms` / `asr_rtf` | — | **NOT MEASURED** |
| `asr_final_latency_ms` | — | **NOT MEASURED** |
| `llm_ttft_ms` / `tok/s` | — | **NOT MEASURED** |
| `tts_first_chunk_ms` / `first_audible_ms` | — | **NOT MEASURED** |
| `barge_in_latency_ms` | <۱ms (SIMULATED) | **NOT MEASURED** |
| `audio_callback_jitter_ms` (دستگاه) | — | **NOT MEASURED** |

گزارش کامل با برچسب‌ها: `docs/BENCHMARKS.md`.

## E. کارهای باقی‌مانده

1. تهیهٔ وزن‌ها و اجرای `tools/benchmark_voice.py` روی هدف (۷ گروه اندازه‌گیری).
2. چک‌لیست `HARDWARE_VALIDATION.md` (barge-in با گوش، false barge-in، کالیبراسیون AEC).
3. ضبط ۴۰ جمله و `benchmarks/persian_eval.py --audio-dir` برای WER/CER واقعی.
4. OPEN-01: process isolation برای توقف قطعی inference.
5. OPEN-02 (باقی‌مانده): firmware ESP32 پس از پاس‌شدن تست‌های PC؛ transport سرور انجام شد.

## F. چک‌لیست پذیرش

| الزام | پیاده | تست واحد | تست یکپارچه | تست سخت‌افزار | مستند |
|---|---|---|---|---|---|
| گم نشدن final ASR / چند utterance | ✅ | ✅ | ✅ | ⛔ | ✅ |
| barge-in + عدم پخش صدای کهنه | ✅ | ✅ | ✅ | ⛔ | ✅ |
| VAD (آرام/نویز/پیش‌رول/بخش‌بندی) | ✅ | ✅ | ✅ | ⛔ | ✅ |
| AEC + fallback صریح + عدم false barge-in در سکوت | ✅ | ✅ (مصنوعی) | ✅ | ⛔ | ✅ |
| TTS زن/مرد + لغو + نرخ درست | ✅ | ✅ | ✅ | ⛔ | ✅ |
| تاریخچهٔ ماندگار + برش توکنی + ثبت فقط شنیده‌شده | ✅ | ✅ | ✅ | — | ✅ |
| readiness / سنجه‌های زنده (jitter، صف، CPU/RAM) | ✅ | ✅ | ✅ | ⛔ | ✅ |
| آفلاین بودن | ✅ | ✅ | ✅ | ⛔ | ✅ |
| پروتکل ESP32 | قرارداد ✅ | ✅ | ⛔ | ⛔ | ✅ |

علامت ⛔ به معنی «انجام نشده»، نه «شکست‌خورده» است. هیچ ردیفی «تأییدشده روی سخت‌افزار» نیست.

## G. تکمیل — transport صوتی LAN و لغو استریم با قطع کلاینت (۸ نوامبر ۲۰۲۶)

| مورد | وضعیت | شاهد |
|---|---|---|
| `WS /ws/voice` با احراز هویت توکن، pipeline مستقل برای هر دستگاه | انجام شد (تست fake) | `server/ws_voice.py`, `tests/test_ws_voice.py` (۱۴ تست) |
| `play_end` مرز نوبت و پس از همهٔ فریم‌ها | انجام شد | `test_playback_frames_are_valid_protocol_frames`, `test_one_play_end_per_turn_not_per_sentence` |
| barge-in دستگاه: drain صف پخش + `play_stop` با `turn_id` | انجام شد | `test_cancelled_playback_frames_are_dropped_before_play_stop` |
| لغو تولید پس از قطع کلاینت SSE | انجام شد | `tests/test_api.py::test_sse_client_disconnect_cancels_upstream_generation` |
| firmware / LAN واقعی / رجیستری / OTA | **انجام نشده — NOT MEASURED** | `docs/HARDWARE_VALIDATION.md` |
