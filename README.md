# Persian Voice AI

دستیار فارسی آفلاین با پردازش روی PC و هدف توسعهٔ ترمینال ESP32-S3 در LAN.
هیچ API ابری برای متن یا صوت استفاده نمی‌شود.

**[راهنمای یکپارچهٔ کامل پروژه](docs/PROJECT_GUIDE.md)** — همهٔ راهنماها در یک فایل،
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

کتابخانهٔ native **SpeexDSP** را نیز طبق [راهنمای AEC](docs/AEC.md) نصب کنید؛ فقط pip کافی نیست.
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

[راهنمای کامل PC، مدل‌ها، تنظیم حساسیت و محدودیت‌ها](docs/PC_VOICE.md) ·
[مرجع همهٔ متغیرهای `.env`](docs/CONFIGURATION.md)

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

اعداد sandbox و روش‌شناسی در [BENCHMARKS](docs/BENCHMARKS.md) است؛ اعداد i7-6500U تا اجرای
واقعی `NOT MEASURED` نوشته می‌شوند. دادهٔ خصوصی و وزن‌ها وارد Git نشوند.
وزن و ضبط خصوصی در مخزن نیست؛ مجموعهٔ متن ارزیابی و نتایج نمونه موجودند؛ مجوز کد GPL-3.0-or-later و مجوز هر مدل/صدا در
[THIRD_PARTY.md](THIRD_PARTY.md) آمده است.

## مستندات

- [AGENTS.md](AGENTS.md): قواعد توسعه و نقشهٔ مخزن
- [SPEC](docs/SPEC.md): معماری و تصمیم‌ها
- [STATUS](docs/STATUS.md): وضعیت واقعی پیاده‌سازی + نگاشت ۲۰ الزام به تست
- [ISSUE_REGISTER](docs/ISSUE_REGISTER.md): دفترچهٔ ایرادها با ریشه، رفع و تست
- [PC_VOICE](docs/PC_VOICE.md): نصب و اجرای صوت PC
- [STREAMING_ARCHITECTURE](docs/STREAMING_ARCHITECTURE.md): utterance/generation، صف‌ها، ماشین حالت، لغو
- [CONFIGURATION](docs/CONFIGURATION.md): مرجع کامل `PVAI_*`
- [AEC](docs/AEC.md): SpeexDSP، تأخیر، چک‌لیست بلندگو/میکروفون
- [PROTOCOL](docs/PROTOCOL.md): قرارداد WebSocket صوتی ESP32-S3 + transport سرور (firmware نشده)
- [BENCHMARKS](docs/BENCHMARKS.md): روش‌شناسی و نتایج برچسب‌دار
- [TROUBLESHOOTING](docs/TROUBLESHOOTING.md): پیام‌ها و راه‌حل
- [HARDWARE_VALIDATION](docs/HARDWARE_VALIDATION.md): چک‌لیست تست واقعی
- [PLATFORMS](docs/PLATFORMS.md): Windows/Linux، اسکریپت‌ها، تفاوت‌ها
- [THIRD_PARTY](THIRD_PARTY.md): مجوز کد، وابستگی‌ها و هر مدل/صدا

## مجوز

کد این مخزن **GPL-3.0-or-later** است ([LICENSE](LICENSE)). دلیل این انتخاب `piper-tts`
است که خود GPL-3.0-or-later است و in-process وارد می‌شود؛ مجوز MIT یا انحصاری برای این
مخزن با آن ناسازگار می‌شود. جزئیات و گزینه‌ها در [THIRD_PARTY.md](THIRD_PARTY.md).

وزن مدل‌ها در مخزن نیستند و مجوز هرکدام جداست: Qwen3-4B با Apache-2.0، وزن‌های
Whisper با MIT، و صدای Piper فارسی بسته به فایل صدا متفاوت است — پیش از عرضه،
`MODEL_CARD` همان صدا را بخوانید.

### دادهٔ سفارشی و آماده‌سازی آموزش

مخزن فعلی inference/evaluation است و trainer ندارد. برای قرارداد JSON/JSONL/CSV،
اعتبارسنجی صوت محلی و روش آماده‌سازی splitهای سفارشی به
[راهنمای داده‌ها](docs/DATASETS.md) و `python tools/prepare_dataset.py --help` مراجعه کنید.
