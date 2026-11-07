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

هستهٔ متنی محلی پیاده‌شده است: یک مدل زبانی محلی روی PC اجرا می‌شود، کاربر به فارسی با آن گفت‌وگو می‌کند و همان مدل از طریق یک API محلی (`127.0.0.1`) در دسترس است. حافظهٔ ماندگار، RAG، ASR، TTS و ESP32 در درس‌های بعد اضافه می‌شوند.

| معیار موفقیت | وضعیت |
|---|---|
| مدل روی PC بالا می‌آید | ✅ |
| پاسخ فارسی متنی می‌دهد | ✅ |
| API محلی پاسخ می‌دهد | ✅ |
| زمان پاسخ قابل اندازه‌گیری است | ✅ |

جزئیات وضعیت واقعی و تست‌های اجراشده: [`docs/STATUS.md`](docs/STATUS.md)

## معماری

```
┌──────────────┐      HTTP/SSE       ┌─────────────────┐      HTTP       ┌──────────────┐
│  CLI فارسی   │ ──────────────────► │  FastAPI محلی   │ ──────────────► │    Ollama    │
│ server.cli   │  127.0.0.1:8000     │  server.main    │ 127.0.0.1:11434 │  + مدل GGUF  │
└──────────────┘                     └─────────────────┘                 └──────────────┘
                                              ▲
                                              │
                                     کلاینت‌های بعدی:
                                     ESP32، رابط وب، ASR/TTS
```

- **Ollama** مدل را روی CPU/GPU اجرا می‌کند و API محلی خودش را روی پورت `11434` می‌دهد.
- **FastAPI** لایهٔ پروژه است: پرامپت سیستمی فارسی، نرمال‌سازی ورودی، اندازه‌گیری زمان، خطاهای فارسی و endpoint ساده برای دستگاه‌های آینده.
- **CLI** فقط با API پروژه حرف می‌زند؛ یعنی همان مسیری که فردا ESP32 یا رابط وب استفاده می‌کند.

## پیش‌نیازها

- ویندوز ۱۰/۱۱
- Python 3.12
- Ollama (نسخهٔ نصب‌شده روی این PC: `0.40.15`)
- حداقل ~۸ گیگابایت RAM خالی (مدل پیشنهادی ~۲.۵ گیگابایت است)
- اتصال اینترنت فقط برای دانلود اولیهٔ مدل

## راه‌اندازی سریع

### ۱) Ollama و مدل

```powershell
ollama serve
ollama pull qwen3:4b
```

مدل پیشنهادی برای این سخت‌افزار `qwen3:4b` است. اگر سخت‌افزار قوی‌تری دارید، مدل بزرگ‌تر (مثلاً `qwen3:8b`) کیفیت فارسی بهتری می‌دهد؛ در عوض روی CPU کندتر است.

مدل‌ها در این پروژه بیرون از پوشهٔ کاربر و روی درایو `D:` نگه داشته می‌شوند:

```powershell
[Environment]::SetEnvironmentVariable("OLLAMA_MODELS", "D:\PersianVoiceAI\models\ollama", "User")
```

پس از تغییر این متغیر، Ollama را کامل ببندید و دوباره اجرا کنید.

### ۲) محیط پایتون

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

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
| `GET` | `/health` | وضعیت سرویس، Ollama و مدل |
| `GET` | `/v1/models` | فهرست مدل‌های محلی |
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

تست‌ها به مدل یا Ollama نیاز ندارند؛ لایهٔ مدل با fake جایگزین می‌شود.

## پیکربندی

همهٔ تنظیمات با متغیر محیطی یا فایل `.env` قابل تغییرند (نمونه: `.env.example`).

| متغیر | پیش‌فرض | توضیح |
|---|---|---|
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | آدرس Ollama |
| `PVAI_MODEL` | `qwen3:4b` | مدل پیش‌فرض |
| `PVAI_TEMPERATURE` | `0.7` | خلاقیت پاسخ |
| `PVAI_MAX_TOKENS` | `512` | سقف توکن پاسخ |
| `PVAI_NUM_CTX` | `4096` | پنجرهٔ زمینه |
| `PVAI_THINK` | `false` | حالت تفکر مدل‌های thinking |
| `PVAI_TIMEOUT` | `180` | مهلت پاسخ Ollama (ثانیه) |
| `PVAI_HOST` | `127.0.0.1` | آدرس bind سرویس |
| `PVAI_PORT` | `8000` | پورت سرویس |

## عیب‌یابی

| نشانه | راه‌حل |
|---|---|
| `503 backend_unavailable` | `ollama serve` اجرا نیست یا مدل هنوز بالا نیامده است. |
| `404 model_not_found` | `ollama pull qwen3:4b` را اجرا کنید. |
| پاسخ بسیار کند | روی CPU ضعیف طبیعی است؛ `PVAI_MAX_TOKENS` را کم کنید یا مدل کوچک‌تر بگیرید. |
| اجرای اسکریپت مسدود است | `powershell -ExecutionPolicy Bypass -File .\tools\start_server.ps1` |
| متن فارسی در ترمینال به‌هم‌ریخته است | از Windows Terminal استفاده کنید و `chcp 65001` را اجرا کنید. |
| پورت ۸۰۰۰ اشغال است | `PVAI_PORT=8001` را تنظیم کنید یا `--port` را عوض کنید. |

## ساختار پروژه

```
server/          سرویس FastAPI، کلاینت Ollama، CLI فارسی
benchmarks/      پرامپت‌های ارزیابی و اسکریپت اندازه‌گیری
tools/           اسکریپت‌های اجرا، بررسی محیط و تأیید درس
tests/           تست‌های واحد و API (بدون نیاز به مدل)
docs/            مستند جامع (SPEC)، وضعیت (STATUS) و گزارش درس
models/          مدل‌های محلی Ollama (خارج از git)
data/            داده‌های محلی (خارج از git)
```

## خارج از دامنهٔ درس ۱

ESP32، ASR، TTS، RAG، حافظهٔ ماندگار و شخصیت‌سازی پیشرفته. این موارد از درس ۲ به بعد اضافه می‌شوند.
