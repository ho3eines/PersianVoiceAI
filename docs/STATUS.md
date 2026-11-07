# وضعیت اجرایی پروژه

آخرین به‌روزرسانی: ۷ نوامبر ۲۰۲۶ — درس ۱ کامل: FastAPI + llama.cpp (GGUF) + CLI فارسی.

این سند فقط چیزهایی را ثبت می‌کند که **واقعاً اجرا و بررسی شده‌اند**. هدف معماری در
[`SPEC.md`](SPEC.md) است.

## ۱. چه چیزی الان کار می‌کند

| قابلیت | مسیر | وضعیت |
|---|---|---|
| سرویس FastAPI محلی | `server/main.py` | ✅ اجرا و تست‌شده |
| کلاینت llama.cpp با اندازه‌گیری زمان | `server/llm.py` | ✅ پیاده‌شده (async HTTP به llama-server) |
| پیکربندی از env/`.env` (PVAI_*) | `server/config.py` | ✅ تست‌شده |
| چت فارسی ترمینال (stream) | `server/cli.py` | ✅ پیاده‌شده |
| بنچمارک ۸ پرامپت فارسی | `benchmarks/` | ✅ اجرا شده، نتایج ثبت‌شده |
| مجموعهٔ تست | `tests/` | ✅ **۱۷ تست سبز** |

### API

| متد | مسیر | توضیح |
|---|---|---|
| `GET` | `/` | معرفی سرویس و نسخه |
| `GET` | `/health` | وضعیت سرویس + لود مدل llama.cpp + موجودبودن فایل GGUF |
| `GET` | `/v1/models` | فهرست مدل‌های محلی (فایل GGUF در `models/`) |
| `POST` | `/v1/chat/completions` | چت سازگار با OpenAI (JSON یا SSE) |
| `POST` | `/api/chat` | پاسخ یک‌پیامی ساده برای کلاینت‌های سبک |

خطاهای دامنه‌ای به کدهای `503` (بک‌اند/مدل در دسترس نیست)، `502` (خطای تولید) نگاشت می‌شوند.

## ۲. اجرای تأییدشده (دستورهای واقعی و خروجی)

```powershell
# ۱. وابستگی‌ها
.\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt   # ✅ fastapi, uvicorn, httpx, pytest

# ۲. تست‌ها (بدون مدل) — ۱۷ تست سبز
.\\.venv\\Scripts\\python.exe -m pytest
# ========================== 17 passed in ~2.2s ==========================

# ۳. llama-server (بک‌اند) — در پورت ۸۰۸۰
D:\\Lama.cpp\\bin\\llama-server.exe -m models\\MiniCPM5-1B-Q8_0.gguf -c 4096 --host 127.0.0.1 --port 8080 -t 4
# ✅ مدل لود شده، /health و /v1/models پاسخ می‌دهند

# ۴. FastAPI سرور — در پورت ۸۰۰۰
.\\tools\\start_server.ps1
# ✅ /health: status=ok, model_available=true, backend.reachable=true

# ۵. تأیید پایان‌به‌پایان (verify_lesson01.py)
.\\.venv\\Scripts\\python.exe tools\\verify_lesson01.py
# ✅ همه معیارهای لایهٔ زیرساخت PASS
# ⚠️ مدل MiniCPM5-1B خروجی را در reasoning_content می‌نویسد و پوشش فارسی ندارد

# ۶. بنچمارک کیفیت/سرعت (benchmark.ps1)
.\\tools\\benchmark.ps1
# ✅ ۸ پرامپت اجرا شد، نتایج در benchmarks/results/lesson01-20261107-142850.json
```

### نتایج بنچمارک (PC واقعی: i7-6500U، ۴ هسته، CPU-only)

| متریک | مقدار |
|---|---|
| میانگین TTFT | **۱.۴۵ ثانیه** |
| میانه TTFT | **۱.۴۳ ثانیه** |
| P95 TTFT | **۱.۸۶ ثانیه** |
| میانگین زمان کل | **۱۱.۴۵ ثانیه** |
| میانگین سرعت تولید | **۱۹.۹ توکن/ثانیه** |

> **نکته مهم:** مدل MiniCPM5-1B-Q8_0 یک مدل «تفکرکننده» (thinking) است و خروجی اصلی را در فیلد `reasoning_content` برمی‌گرداند. پاسخ‌های مستقیم فارسی تولید نمی‌کند. برای تولید فارسی، مدل Qwen3-4B-Q4_K_M یا مشابه پیشنهاد می‌شود. این بنچمارک لایهٔ **زیرساخت** (FastAPI ↔ llama-server ↔ GGUF) را تأیید می‌کند.

### محیط PC-0.2 (درس ۲) — بررسی‌شده

| بررسی | نتیجه |
|---|---|
| پایتون سیستم | ۳.۱۲.۱۰ در `C:\Users\Ho3ein\AppData\Local\Programs\Python\Python312` |
| `py --list` | `No installed Pythons found!` — launcher نسخه‌ها را ثبت نکرده؛ از `python` استفاده می‌شود |
| `git --version` | 2.51.0.windows.1 |
| `nvidia-smi` | GeForce 940MX، compute 5.0، 2048 MiB، درایور 581.29 |
| فضای دیسک | `C:` ۴۶.۸۳ — `D:` ۱۲۶.۶۵ — `E:` ۳۴.۱۸ — `F:` ۱۸۶.۶۷ گیگابایت آزاد |
| `.venv` | Python 3.12.10، pip 25.0.1 |
| ساختار پوشه | `data`, `models`, `docs`, `scripts`, `logs` موجود |
| تست‌ها | ۱۷ تست سبز |

### آزمایش GPU (Vulkan، b12186)

`--list-devices` کارت را شناسایی می‌کند: `Vulkan0: NVIDIA GeForce 940MX (2215 MiB, 1913 MiB free)`.
روی MiniCPM5-1B-Q8_0 با `llama-bench -p 512 -n 128`:

| حالت | pp512 | tg128 |
|---|---|---|
| CPU (`-ngl 0`) | ۸۰.۶۸ t/s | ۱۷.۴۹ t/s |
| GPU (`-ngl 99`، Vulkan0) | ۵۹.۹۱ t/s | ۱۲.۳۲ t/s |

GPU کار می‌کند اما کندتر از CPU است (Maxwell بدون `fp16`، ۲ گیگابایت VRAM). تصمیم: اجرا روی CPU می‌ماند.

## ۳. چه چیزی هنوز پیاده نشده

- **Firmware ESP32-S3** (`firmware/esp32s3_terminal/`): در این مخزن وجود ندارد. کد V1-A
  (I2S RX/TX، پخش 440Hz، RMS) هنوز اینجا نیست و روی برد واقعی build/تست نشده است.
- **Wi-Fi/WebSocket و انتقال PCM دوطرفه** (V1.1)
- **ASR** (faster-whisper)، **TTS** فارسی، **VAD/AEC/Barge-in**
- **Command Broker** و کنترل GPIO/رله
- **احراز هویت دستگاه، OTA، چنددستگاهی**

## ۴. گام بعدی

1. درس ۲ (PC-0.2): انجام شد — محیط بررسی و آمادگی برای PC-1 اعلام شد.
2. افزودن `firmware/esp32s3_terminal/` (ESP-IDF 5.4.4) و تست مستقل میکروفون/بلندگو (فاز بعد).
3. شروع V1.1: WebSocket + فریم‌های PCM 20–40ms و پیام‌های کنترلی `SPEC.md` §۶.
4. انتخاب و تست مدل فارسی‌زبان (Qwen3-4B-Q4_K_M) برای جایگزینی MiniCPM.
