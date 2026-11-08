# Persian Voice AI

دستیار صوتی فارسی **کاملاً آفلاین** در شبکهٔ داخلی (LAN): ترمینال صوتی ESP32-S3 به‌همراه پردازش سنگین روی PC (ASR، LLM، TTS، VAD/AEC) با Backend پایتون و FastAPI.

> اصل پروژه: هیچ صدا یا داده‌ای به Cloud یا API اینترنتی فرستاده نمی‌شود.

## مستندات

| سند | محتوا |
|---|---|
| [`AGENTS.md`](AGENTS.md) | نقطهٔ ورود هر مدل/عامل: قوانین تغییر، نقشهٔ مخزن، قراردادهای کد |
| [`docs/SPEC.md`](docs/SPEC.md) | مستند جامع v1.0: معماری، سخت‌افزار، صوت و شبکه، ASR/LLM/TTS، امنیت، نقشهٔ راه |
| [`docs/STATUS.md`](docs/STATUS.md) | وضعیت واقعی پیاده‌سازی، تست‌ها و گام بعدی |

## وضعیت فعلی — پایان PC-0.2 / آماده‌سازی PC-1

هستهٔ API محلی با FastAPI و کلاینت `llama-server` پیاده‌سازی شده و با Qwen3.5-4B Q4_K_M روی لپ‌تاپ هدف به‌صورت پایان‌به‌پایان بررسی شده است. این مدل فعلاً انتخاب پایهٔ PC-1 است؛ امتیاز کیفیت آن مقدماتی است و RAG، ASR، TTS و ESP32 هنوز پیاده‌سازی نشده‌اند.

| معیار موفقیت | وضعیت |
|---|---|
| API محلی و CLI | ✅ پیاده‌سازی شده؛ تست‌های خودکار با بک‌اند ساختگی اجرا می‌شوند |
| اتصال به llama-server | ✅ کلاینت HTTP محلی پیاده‌سازی شده |
| اجرای مدل GGUF و کیفیت پاسخ فارسی | ✅ Qwen3.5 اجرا شد؛ ارزیابی کیفی مقایسه‌ای مقدماتی در `docs/STATUS.md` ثبت شده |
| زمان پاسخ | ✅ TTFT / tok/s در API اندازه‌گیری می‌شود |

> **معماری فعلی:** FastAPI از طریق HTTP محلی به فرایند جداگانهٔ `llama-server` وصل می‌شود. موتور `llama.cpp` مدل GGUF را بارگذاری می‌کند. آزمون‌های خودکار API به مدل واقعی نیاز ندارند.

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
                                     │ HTTP محلی        │
                                     │ 127.0.0.1:8080   │
                                     └────────┬─────────┘
                                              │
                                              ▼
                                     ┌──────────────────┐
                                     │ llama-server     │
                                     │ llama.cpp engine │
                                     │ models/*.gguf    │
                                     └──────────────────┘
```

- **llama-server** فرایند جداگانهٔ موتور استنتاج llama.cpp است؛ FastAPI با HTTP روی loopback به آن وصل می‌شود.
- **FastAPI** لایهٔ پروژه: پرامپت سیستمی فارسی، نرمال‌سازی ورودی، اندازه‌گیری زمان (TTFT، tok/s)، خطاهای فارسی و endpoint ساده برای دستگاه‌های آینده.
- **CLI** فقط با API پروژه حرف می‌زند؛ یعنی همان مسیری که فردا ESP32 یا رابط وب استفاده می‌کند.

## پیش‌نیازها

- ویندوز ۱۰/۱۱
- Python 3.12
- یک بیلد محلی `llama-server.exe` از پروژهٔ llama.cpp
- مدل **GGUF** در `models/` (پیش‌فرض: `qwen3.5-4b/Qwen3.5-4B-Q4_K_M.gguf`، حدود ۲.۵۵ GiB)
- رم ۱۶ گیگابایت برای پیکربندی آزموده‌شده؛ اوج working set مدل حدود ۵ GiB ثبت شد
- اتصال اینترنت برای دریافت وابستگی‌های کوچک Python و انتخاب/دریافت فایل GGUF؛ متن مکالمه به اینترنت فرستاده نمی‌شود

## راه‌اندازی سریع

### ۱) محیط پایتون و وابستگی‌ها

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

`requirements.txt` شامل FastAPI، Uvicorn، HTTPX و pytest است؛ خود llama.cpp جداگانه به‌صورت `llama-server.exe` اجرا می‌شود.

### ۲) مدل GGUF

فایل مدل باید به‌صورت محلی در `models/` قرار گیرد. مسیر یکسان فایل را هم به گزینهٔ `-m` در `llama-server` و هم به `PVAI_MODEL_PATH` در `.env` بدهید.

### ۳) اجرای llama-server

در ترمینال اول، از ریشهٔ مخزن اجرا کنید:

```powershell
D:\Lama.cpp\vulkan-b12186\bin\llama-server.exe -m models\qwen3.5-4b\Qwen3.5-4B-Q4_K_M.gguf -c 4096 --host 127.0.0.1 --port 8080 -t 4 --reasoning-format deepseek --reasoning off -ngl 0 --device none
```

مسیر و تعداد رشته‌ها را متناسب با فایل GGUF و سخت‌افزار عوض کنید. `PVAI_N_CTX`، `PVAI_N_THREADS` و `PVAI_N_GPU_LAYERS` تنظیمات llama-server نیستند و از `.env` حذف شده‌اند.
روی 940MX اجرای Vulkan با `--device Vulkan0 --fit on` ممکن است؛ در آزمون محلی سرعت تولید را افزایش نداد، ولی حدود ۰.۹ GiB از RAM پردازه کم کرد.

### ۴) اجرای API

```powershell
.\tools\start_server.ps1
```

یا مستقیم:

```powershell
.\.venv\Scripts\python.exe -m uvicorn server.main:app --host 127.0.0.1 --port 8000
```

مستندات تعاملی: <http://127.0.0.1:8000/docs>

### ۵) گفت‌وگوی فارسی

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
| `PVAI_MODEL_PATH` | `models/qwen3.5-4b/Qwen3.5-4B-Q4_K_M.gguf` | مسیر فایل مدل GGUF |
| `PVAI_TEMPERATURE` | `0.7` | خلاقیت پاسخ |
| `PVAI_MAX_TOKENS` | `512` | سقف توکن پاسخ |
| `PVAI_HOST` | `127.0.0.1` | آدرس bind سرویس |
| `PVAI_PORT` | `8000` | پورت سرویس |
| `PVAI_LOG_LEVEL` | `INFO` | سطح لاگ |

## عیب‌یابی

| نشانه | راه‌حل |
|---|---|
| `503 model_not_loaded` | `llama-server` مدل را لود نکرده یا نام مدل با `PVAI_MODEL_PATH` هم‌خوان نیست. |
| `502 generation_error` | خطای llama.cpp (معمولاً OOM یا مدل خراب). لاگ سرور را ببینید. |
| پاسخ بسیار کند | `PVAI_MAX_TOKENS` را کم کنید یا مدل کوچک‌تر بگیرید؛ تعداد رشته‌ها را در فرمان اجرای `llama-server` تنظیم کنید. |
| اجرای اسکریپت خطای Execution Policy می‌دهد | سیاست امنیتی را تغییر ندهید؛ متن کامل خطا را برای بررسی ثبت کنید. |
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
