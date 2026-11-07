# وضعیت اجرایی پروژه

آخرین به‌روزرسانی: ۷ نوامبر ۲۰۲۶ — درس ۱ کامل: FastAPI + llama.cpp (GGUF) + CLI فارسی.

این سند فقط چیزهایی را ثبت می‌کند که **واقعاً اجرا و بررسی شده‌اند**. هدف معماری در
[`SPEC.md`](SPEC.md) است.

## ۱. چه چیزی الان کار می‌کند

| قابلیت | مسیر | وضعیت |
|---|---|---|
| سرویس FastAPI محلی | `server/main.py` | ✅ پیاده‌شده، انتظار `llama-cpp-python` نصب |
| کلاینت llama.cpp با اندازه‌گیری زمان | `server/llm.py` | ✅ بازنویسی شده (async thread-pool) |
| پیکربندی از env/`.env` (PVAI_*) | `server/config.py` | ✅ بازنویسی شده |
| چت فارسی ترمینال (stream) | `server/cli.py` | ✅ پیاده‌شده |
| بنچمارک ۸ پرامپت فارسی | `benchmarks/` | ✅ پیاده‌شده، نتیجهٔ رسمی ثبت نشده |
| مجموعهٔ تست | `tests/` | ⏳ نیاز به به‌روزرسانی برای fake llama.cpp |

### API

| متد | مسیر | توضیح |
|---|---|---|
| `GET` | `/` | معرفی سرویس و نسخه |
| `GET` | `/health` | وضعیت سرویس + لود مدل llama.cpp + موجودبودن فایل GGUF |
| `GET` | `/v1/models` | فهرست مدل‌های محلی (فایل GGUF در `models/`) |
| `POST` | `/v1/chat/completions` | چت سازگار با OpenAI (JSON یا SSE) |
| `POST` | `/api/chat` | پاسخ یک‌پیامی ساده برای کلاینت‌های سبک |

خطاهای دامنه‌ای به کدهای `503` (مدل لود نشده)، `502` (خطای تولید/بک‌اند) نگاشت می‌شوند.

## ۲. چه چیزی هنوز پیاده نشده

- **Firmware ESP32-S3** (`firmware/esp32s3_terminal/`): در این مخزن وجود ندارد. کد V1-A
  (I2S RX/TX، پخش 440Hz، RMS) هنوز اینجا نیست و روی برد واقعی build/تست نشده است.
- **Wi-Fi/WebSocket و انتقال PCM دوطرفه** (V1.1)
- **ASR** (faster-whisper)، **TTS** فارسی، **VAD/AEC/Barge-in**
- **Command Broker** و کنترل GPIO/رله
- **احراز هویت دستگاه، OTA، چنددستگاهی**

## ۳. اجرای تأییدشده (دستورهای واقعی)

```powershell
# ۱. وابستگی‌ها (شامل llama-cpp-python)
.\.venv\Scripts\python.exe -m pip install -r requirements.txt   # ⏳ در انتظار اجرا

# ۲. تست‌ها (بدون مدل) — نیاز به به‌روزرسانی fake LLM دارد
.\.venv\Scripts\python.exe -m pytest                            # ⏳ در انتظار اجرا
```

پیش‌نیاز اجرای واقعی چت:
1. فایل مدل `models/MiniCPM5-1B-Q8_0.gguf` در محل باشد (کپی شده ✅).
2. `.\tools\start_server.ps1` → سرور روی `http://127.0.0.1:8000`.
3. در ترمینال دیگر: `.\tools\chat.ps1`.

> **وضعیت Ollama (بایگانی‌شده):**
> - نصاب `OllamaSetup.exe` نسخه v0.40.15 (۱٬۶۱۱٫۴ MB) دانلود شده بود اما **نصب نشد** (سایلنت و مرئی هر دو شکست خوردند).
> - پروژه به **llama.cpp + GGUF** سوئیچ کرد؛ Ollama دیگر پیش‌نیاز نیست.

## ۴. گام بعدی

1. نصب `llama-cpp-python` در venv و اجرای `pytest` (باید سبز شود).
2. اجرای `tools\verify_lesson01.py` و `tools\benchmark.ps1` روی PC واقعی و ثبت TTFT/tok/s در `benchmarks/results/`.
3. درس ۲ (PC-0.2): دریافت خروجی‌های کاربر (python/py/git/nvidia-smi/دیسک) و ارزیابی آمادگی برای PC-1.
4. افزودن `firmware/esp32s3_terminal/` (ESP-IDF 5.4.4) و تست مستقل میکروفون/بلندگو (فاز بعد).
5. شروع V1.1: WebSocket + فریم‌های PCM 20–40ms و پیام‌های کنترلی `SPEC.md` §۶.