# Persian Voice AI

دستیار فارسی آفلاین با پردازش روی PC و هدف توسعهٔ ترمینال ESP32-S3 در LAN.
هیچ API ابری برای متن یا صوت استفاده نمی‌شود.

## مسیر فعلی PC

```text
میکروفون پیوسته (20ms) → AEC SpeexDSP → VAD با هیسترزیس و پیش‌رول
   → partial هم‌پوشان (۶s) + صف final بدون‌افت  →  Whisper محلی
   → AssistantTurn روی llama-server (async SSE)  →  جمله‌بندی تدریجی
   → Piper محلی (زن/مرد) → پخش 20ms در duplex
```

**حالت پیش‌فرض زنده است و Enter لازم ندارد.** شروع صحبت جدید پاسخ دستیار را قطع می‌کند
(barge-in) اما **هیچ‌وقت نتیجهٔ final ASR قبلی را از بین نمی‌برد** — این تفکیک، هستهٔ
`Utterance` / `AssistantTurn` / `PartialASR` / `FinalASR` است.
ASR حین صحبت با پنجره‌های هم‌پوشان اجرا می‌شود؛ این adapter افزایشی روی Whisper است،
نه مدل ذاتاً streaming. سرعت واقعی روی هدف هنوز اندازه‌گیری نشده است (NOT MEASURED).

## شروع

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-voice.txt
Copy-Item .env.example .env
```

کتابخانهٔ native **SpeexDSP** را نیز طبق [راهنمای AEC](docs/AEC.md) نصب کنید؛ فقط pip کافی نیست.
سپس مدل‌های **Qwen3-4B-Q4_K_M GGUF**، **Whisper Small CTranslate2** و **Piper فارسی** را
محلی تهیه و مسیرشان را در `.env` تنظیم کنید. مدل‌ها دانلود خودکار نمی‌شوند.
اگر `.env` موجود است، فقط تنظیمات آن را ویرایش کنید.

```powershell
# ترمینال ۱؛ مسیر باینری و مدل متناسب با سیستم خودتان
D:\Lama.cpp\bin\llama-server.exe -m models\Qwen3-4B-Q4_K_M.gguf -c 4096 --host 127.0.0.1 --port 8080 -t 4
# ترمینال ۲
.\tools\start_server.ps1
# ترمینال ۳
.\.venv\Scripts\python.exe -m server.voice
```

[راهنمای کامل PC، مدل‌ها، تنظیم حساسیت و محدودیت‌ها](docs/PC_VOICE.md) ·
[مرجع همهٔ متغیرهای `.env`](docs/CONFIGURATION.md)

## وضعیت صادقانه

- تست خودکار: **۲۶۹ passed، ۵ skipped** (بدون مدل/میکروفون/شبکه؛ skipها فقط native SpeexDSP و numpy).
  این تست‌ها منطق و کنترل جریان را ثابت می‌کنند، نه کیفیت صدا.
- مدل‌های واقعی (Whisper/Piper/Qwen) در مخزن نیستند؛ در sandbox نصب هم نبودند، پس
  ASR/TTS/LLM واقعی **NOT MEASURED** است.
- AEC با اکو/دوصدایی مصنوعی تست شده (کاهش ۵۷٫۸dB در سیگنال ساختگی)؛ اتاق و دستگاه واقعی
  تأیید نشده‌اند. بدون هدفون، کالیبراسیون `PVAI_AEC_DELAY_MS` لازم است.
- تاریخچهٔ مکالمه محلی و ماندگار است (`data/conversations/*.jsonl`)؛ پنجرهٔ RAM محدود است.
- پروتکل WebSocket صوتی ESP32-S3 و **transport سمت سرور** پیاده شده‌اند (`WS /ws/voice`، خاموش به‌صورت
  پیش‌فرض، با توکن اجباری)؛ **firmware و تست LAN واقعی انجام نشده‌اند**.
- اجرای CPU پیش‌فرض است؛ بنچمارک قدیمی GPU روی MiniCPM به Qwen قابل تعمیم نیست.

## API و چت متنی

| مسیر | کار |
|---|---|
| `GET /health` | وضعیت اتصال backend و مدل |
| `GET /ready` | چک‌لیست کامل: تنظیمات، مدل ASR/TTS، وابستگی‌ها، AEC، ذخیره‌سازی، backend (503 اگر ناقص) |
| `GET /api/protocol` | قرارداد انتقال ESP32 (`transport_implemented: true`، `firmware_implemented: false`) |
| `WS /ws/voice` | صدای دستگاه LAN (opt-in با `PVAI_WS_ENABLED` و `PVAI_WS_TOKEN`) |
| `GET /v1/models` | مدل‌های بارشده در llama-server |
| `POST /v1/chat/completions` | چت JSON یا SSE با `stream=true` |
| `POST /api/chat` | پاسخ سادهٔ یک‌پیامی با `session_id` اختیاری |
| `GET/DELETE /api/sessions[/{id}]` | بازبینی و حذف نشست‌های محلی |

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
```

اعداد sandbox و روش‌شناسی در [BENCHMARKS](docs/BENCHMARKS.md) است؛ اعداد i7-6500U تا اجرای
واقعی `NOT MEASURED` نوشته می‌شوند. دادهٔ خصوصی و وزن‌ها وارد Git نشوند.
API فعلی احراز هویت ندارد؛ پورت‌ها را عمومی نکنید.

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
