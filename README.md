# Persian Voice AI

دستیار فارسی آفلاین با پردازش روی PC و هدف توسعهٔ ترمینال ESP32-S3 در LAN.
هیچ API ابری برای متن یا صوت استفاده نمی‌شود.

## مسیر فعلی PC

میکروفون پیوسته → snapshot هم‌پوشان → Whisper افزایشی (partial/final) → FastAPI → llama-server (async SSE)
→ جمله‌بندی تدریجی پاسخ → Piper محلی → پخش PCM.

**حالت پیش‌فرض زنده است و Enter لازم ندارد.** حذف اکو با SpeexDSP و جریان دوطرفه اضافه شده؛
کتابخانهٔ native باید نصب باشد. عملکرد بدون هدفون نیازمند تست و تنظیم تأخیر روی دستگاه واقعی است.
ASR حین صحبت با snapshotهای هم‌پوشان اجرا و متن partial منتشر می‌کند؛ پس از سکوت متن final به مدل می‌رود.
این adapter افزایشی روی Whisper است، نه مدل ذاتاً streaming؛ سرعت واقعی نیازمند تست PC است.

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

[راهنمای کامل PC، مدل‌ها، تنظیم حساسیت و محدودیت‌ها](docs/PC_VOICE.md)

## وضعیت صادقانه

- کلاینت PC: actorهای مستقل capture، ASR افزایشی، async SSE و TTS؛ generation/cancellation و صف‌های محدود.
- تست خودکار بدون مدل/سخت‌افزار موجود است؛ اجرای واقعی مدل‌های صوتی و Qwen روی PC هنوز لازم است.
- Qwen جایگزین **پیش‌فرض تنظیمات** MiniCPM شده؛ فایل وزن در مخزن نیست و بنچمارکش ثبت نشده است.
- AEC محلی پیاده شده و با اکو/دوصدایی مصنوعی تست شده؛ اتاق و دستگاه واقعی تأیید نشده‌اند.
- ASR ذاتاً streaming، چنددستگاهی و firmware ESP32 کامل نیستند.
- اجرای CPU پیش‌فرض است؛ بنچمارک قدیمی GPU روی MiniCPM به Qwen قابل تعمیم نیست.

## API و چت متنی

| مسیر | کار |
|---|---|
| `GET /health` | وضعیت اتصال backend و مدل |
| `GET /v1/models` | مدل‌های بارشده در llama-server |
| `POST /v1/chat/completions` | چت JSON یا SSE با `stream=true` |
| `POST /api/chat` | پاسخ سادهٔ یک‌پیامی |

چت متنی: `python -m server.cli` یا `tools/chat.ps1`.
مستندات API: `http://127.0.0.1:8000/docs`.
FastAPI از طریق **async HTTP به llama-server** متصل می‌شود؛ `llama-cpp-python` لازم نیست.

## تست و بنچمارک

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\tools\benchmark.ps1
```

بنچمارک فعلی متن را می‌سنجد، نه کل زنجیرهٔ صوت. دادهٔ خصوصی و وزن‌ها وارد Git نشوند.
API فعلی احراز هویت ندارد؛ پورت‌ها را عمومی نکنید.

## مستندات

- [AGENTS.md](AGENTS.md): قواعد توسعه
- [SPEC](docs/SPEC.md): معماری و تصمیم‌ها
- [STATUS](docs/STATUS.md): پیاده‌سازی و نتایج ثبت‌شده
- [PC_VOICE](docs/PC_VOICE.md): مسیر فعلی اجرای صوت PC

- [معماری Streaming جدید](docs/STREAMING_ARCHITECTURE.md): رویدادها، لغو، صف‌ها و معیار پذیرش

- [نصب و تنظیم حذف اکو](docs/AEC.md): SpeexDSP، تأخیر و چک‌لیست بلندگو/میکروفون
