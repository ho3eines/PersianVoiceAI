# Persian Voice AI

دستیار صوتی فارسی **کاملاً آفلاین** در شبکهٔ داخلی (LAN): ترمینال صوتی ESP32-S3 به‌همراه پردازش سنگین روی PC (ASR، LLM، TTS، VAD/AEC) با Backend پایتون و FastAPI.

> اصل پروژه: هیچ صدا یا داده‌ای به Cloud یا API اینترنتی فرستاده نمی‌شود.

## مستندات

| سند | محتوا |
|---|---|
| [`AGENTS.md`](AGENTS.md) | نقطهٔ ورود هر مدل/عامل: قوانین تغییر، نقشهٔ مخزن، قراردادهای کد |
| [`docs/SPEC.md`](docs/SPEC.md) | مستند جامع v1.0: معماری، سخت‌افزار، صوت و شبکه، ASR/LLM/TTS، امنیت، نقشهٔ راه |
| [`docs/STATUS.md`](docs/STATUS.md) | وضعیت واقعی پیاده‌سازی، تست‌ها و گام بعدی |

## وضعیت فعلی — درس ۱

هستهٔ متنی محلی پیاده‌شده است: یک مدل زبانی **GGUF** روی PC با **llama.cpp** اجرا می‌شود، کاربر به فارسی با آن گفت‌وگو می‌کند و همان مدل از طریق یک API محلی (`127.0.0.1:8000`) در دسترس است. حافظهٔ ماندگار، RAG، ASR، TTS و ESP32 در درس‌های بعد اضافه می‌شوند.

| معیار موفقیت | وضعیت |
|---|---|
| مدل GGUF روی PC لود می‌شود | ⏳ در انتظار `llama-cpp-python` |
| پاسخ فارسی متنی می‌دهد | ⏳ در انتظار تست واقعی |
| API محلی پاسخ می‌دهد | ✅ کد آماده (OpenAI-compatible) |
| زمان پاسخ قابل اندازه‌گیری است | ✅ TTFT / tok/s در کد پیاده‌شده |

> **تغییر معماری:** Ollama حذف شد. بک‌اند اکنون **llama.cpp** (بیلد محلی `D:\Lama.cpp\bin`) + **llama-cpp-python** در FastAPI است. مدل پیش‌فرض: `models/MiniCPM5-1B-Q8_0.gguf` (۱.۰۷ GB).

جزئیات وضعیت واقعی و تست‌های اجراشده: [`docs/STATUS.md`](docs/STATUS.md)

## معماری

```
┌──────────────┐      HTTP/SSE       ┌─────────────────┐
│  CLI فارسی   │ ──────────────────► │  FastAPI محلی   │
│ server.cli   │  127.0.0.1:8000     │  server.main    │
└──────────────┘                     └────────┬────────┘
                                              │
                                              ▼
                                     ┌──────────────────┐
                                     │ llama-cpp-python │
                                     │ (thread-pool)    │
                                     └────────┬─────────┘
                                              │
                                              ▼
                                     ┌──────────────────┐
                                     │ llama.cpp engine │
                                     │ MiniCPM5-1B-Q8_0 │
                                     │ models/*.gguf    │
                                     └──────────────────┘
```

- **llama.cpp** موتور استنتاج است؛ باینری بیلدشده در `D:\Lama.cpp\bin\llama-server.exe` موجود است.
- **llama-cpp-python** بایندینگ پایتون برای فراخوانی async از FastAPI (با thread-pool).
- **FastAPI** لایهٔ پروژه: پرامپت سیستمی فارسی، نرمال‌سازی ورودی، اندازه‌گیری زمان (TTFT، tok/s)، خطاهای فارسی و endpoint ساده برای دستگاه‌های آینده.
- **CLI** فقط با API پروژه حرف می‌زند؛ یعنی همان مسیری که فردا ESP32 یا رابط وب استفاده می‌کند.

## پیش‌نیازها

- ویندوز ۱۰/۱۱
- Python 3.12
- **llama.cpp بیلدشده** (روی این سیستم در `D:\Lama.cpp\bin` موجود)
- مدل **GGUF** در `models/` (پیش‌فرض: `MiniCPM5-1B-Q8_0.gguf` ≈ ۱.۰۷ GB)
- حداقل ~۴ گیگابایت RAM خالی (مدل ۱.۰۷ GB + کانتکست)
- اتصال اینترنت فقط برای دانلود اولیهٔ وابستگی‌های پایتون (`llama-cpp-python`)

## راه‌اندازی سریع

### ۱) محیط پایتون و وابستگی‌ها

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

`requirements.txt` شامل `llama-cpp-python==0.2.90` است.

### ۲) مدل GGUF

مدل پیش‌فرض در `models/MiniCPM5-1B-Q8_0.gguf` کپی شده است. برای مدل دیگر، فایل `.gguf` را در `models/` قرار دهید و `PVAI_MODEL_PATH` در `.env` را تنظیم کنید.

### ۳) اجرای API

```powershell
.\tools\start_server.ps1
```

یا مستقیم:

```powershell
.\.venv\Scripts\python.exe -m uvicorn server.main:app --host 127.0.0.1 --port 8000
```

مستندات تعاملی: <http://127.0.0.1:8000/docs>

### ۴) گفت‌وگوی فارسی

در یک ترمینال دیگر:

```powershell
.\tools\chat.ps1
```

یا یک‌باره:

```powershell
.\tools\chat.ps1 --once "سلام! خودت را معرفی کن"
```

دستورهای داخل چت: `/راهنما`، `/پاک`، `/آمار`، `/خروج`.

> تاریخچهٔ گفت‌وگو فقط در حافظهٔ همین نشست CLI می‌ماند. ذخیره‌سازی و حافظهٔ ماندگار عمداً خارج از دامنهٔ درس ۱ است.

## API

| متد | مسیر | کار |
|---|---|---|
| `GET` | `/health` | وضعیت سرویس، لود مدل و فایل GGUF |
| `GET` | `/v1/models` | فهرست مدل‌های محلی (فایل‌های GGUF در `models/`) |
| `POST` | `/v1/chat/completions` | چت سازگار با OpenAI (JSON یا streaming) |
| `POST` | `/api/chat` | endpoint سادهٔ یک‌پیامی برای کلاینت‌های سبک |

### نمونهٔ ساده

```powershell
curl.exe -s http://127.0.0.1:8000/health
```

```python
import httpx

response = httpx.post(
    "http://127.0.0.1:8000/api/chat",
    json={"message": "سلام! در یک جمله بگو هوش مصنوعی محلی چیست."},
    timeout=300,
)
print(response.json()["reply"])
print(response.json()["timings"])
```

### نمونهٔ سازگار با OpenAI

```python
import httpx

response = httpx.post(
    "http://127.0.0.1:8000/v1/chat/completions",
    json={
        "messages": [
            {"role": "system", "content": "فقط به فارسی پاسخ بده."},
            {"role": "user", "content": "یک شعر کوتاه دربارهٔ باران بنویس."},
        ],
        "stream": False,
    },
    timeout=300,
)
data = response.json()
print(data["choices"][0]["message"]["content"])
print(data["timings"]["tokens_per_second"], "tok/s")
```

برای streaming، `"stream": True` بفرستید؛ پاسخ به صورت SSE و با رویداد پایانی `[DONE]` برمی‌گردد و زمان اولین توکن (`ttft_ms`) در پیام پایانی قرار دارد.

## اندازه‌گیری

```powershell
.\tools\benchmark.ps1
```

۸ پرامپت فارسی (گفت‌وگو، توضیح، ریاضی، پیروی از دستور، کد، دستور زبان، کاربرد ویندوز، صداقت) اجرا می‌شوند و TTFT، زمان کل و توکن بر ثانیه در `benchmarks/results/` ذخیره می‌شود.

بررسی سریع هر چهار معیار درس:

```powershell
.\.venv\Scripts\python.exe tools\verify_lesson01.py
```

## تست‌ها

```powershell
.\.venv\Scripts\python.exe -m pytest
```

تست‌ها به مدل یا llama.cpp نیاز ندارند؛ لایهٔ مدل با fake جایگزین می‌شود.

## پیکربندی

همهٔ تنظیمات با متغیر محیطی یا فایل `.env` قابل تغییرند (نمونه: `.env.example`).

| متغیر | پیش‌فرض | توضیح |
|---|---|---|
| `PVAI_MODEL_PATH` | `models/MiniCPM5-1B-Q8_0.gguf` | مسیر فایل مدل GGUF |
| `PVAI_N_CTX` | `4096` | سایز کانتکست توکن |
| `PVAI_N_THREADS` | `0` (auto) | تعداد ترد CPU (۰ = خودکار) |
| `PVAI_N_GPU_LAYERS` | `0` | لایه‌های GPU (۰ = CPU-only) |
| `PVAI_TEMPERATURE` | `0.7` | خلاقیت پاسخ |
| `PVAI_MAX_TOKENS` | `512` | سقف توکن پاسخ |
| `PVAI_HOST` | `127.0.0.1` | آدرس bind سرویس |
| `PVAI_PORT` | `8000` | پورت سرویس |
| `PVAI_LOG_LEVEL` | `INFO` | سطح لاگ |

## عیب‌یابی

| نشانه | راه‌حل |
|---|---|
| `503 model_not_loaded` | فایل GGUF در `models/` نیست یا `llama-cpp-python` نصب نشده. |
| `502 generation_error` | خطای llama.cpp (معمولاً OOM یا مدل خراب). لاگ سرور را ببینید. |
| پاسخ بسیار کند | روی CPU طبیعی است؛ `PVAI_MAX_TOKENS` را کم کنید یا مدل کوچک‌تر بگیرید. `PVAI_N_THREADS` را روی تعداد هسته‌ها تنظیم کنید. |
| اجرای اسکریپت مسدود است | `powershell -ExecutionPolicy Bypass -File .\tools\start_server.ps1` |
| متن فارسی در ترمینال به‌هم‌ریخته است | از Windows Terminal استفاده کنید و `chcp 65001` را اجرا کنید. |
| پورت ۸۰۰۰ اشغال است | `PVAI_PORT=8001` را تنظیم کنید یا `--port` را عوض کنید. |

## ساختار پروژه

```
server/          سرویس FastAPI، کلاینت llama.cpp، CLI فارسی
benchmarks/      پرامپت‌های ارزیابی و اسکریپت اندازه‌گیری
tools/           اسکریپت‌های اجرا، بررسی محیط و تأیید درس
tests/           تست‌های واحد و API (بدون نیاز به مدل)
AGENTS.md       قرارداد کار هر مدل/عامل (نقطهٔ ورود پروژه)
docs/           مستند جامع (SPEC) و وضعیت واقعی پروژه (STATUS)
models/          مدل‌های GGUF محلی (خارج از git)
data/            داده‌های محلی (خارج از git)
```

## خارج از دامنهٔ درس ۱

ESP32، ASR، TTS، RAG، حافظهٔ ماندگار و شخصیت‌سازی پیشرفته. این موارد از درس ۲ به بعد اضافه می‌شوند.