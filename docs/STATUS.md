# وضعیت اجرایی پروژه

آخرین به‌روزرسانی: ۷ نوامبر ۲۰۲۶ — پس از درس ۱ (هستهٔ متنی محلی).

این سند فقط چیزهایی را ثبت می‌کند که **واقعاً اجرا و بررسی شده‌اند**. هدف معماری در
[`SPEC.md`](SPEC.md) است.

## ۱. چه چیزی الان کار می‌کند

| قابلیت | مسیر | وضعیت |
|---|---|---|
| سرویس FastAPI محلی | `server/main.py` | ✅ اجرا و تست‌شده |
| کلاینت Ollama با اندازه‌گیری زمان | `server/llm.py` | ✅ تست‌شده (fake) |
| پیکربندی از env/`.env` | `server/config.py` | ✅ تست‌شده |
| چت فارسی ترمینال (stream) | `server/cli.py` | ✅ پیاده‌شده |
| بنچمارک ۸ پرامپت فارسی | `benchmarks/` | ✅ پیاده‌شده، نتیجهٔ رسمی ثبت نشده |
| مجموعهٔ تست | `tests/` | ✅ **۱۷ تست سبز** |

### API

| متد | مسیر | توضیح |
|---|---|---|
| `GET` | `/` | معرفی سرویس و نسخه |
| `GET` | `/health` | وضعیت سرویس + Ollama + موجودبودن مدل |
| `GET` | `/v1/models` | فهرست مدل‌های محلی |
| `POST` | `/v1/chat/completions` | چت سازگار با OpenAI (JSON یا SSE) |
| `POST` | `/api/chat` | پاسخ یک‌پیامی ساده برای کلاینت‌های سبک |

خطاهای دامنه‌ای به کدهای `503` (Ollama در دسترس نیست)، `404` (مدل نیست) و `502` (خطای بک‌اند)
نگاشت می‌شوند.

## ۲. چه چیزی هنوز پیاده نشده

- **Firmware ESP32-S3** (`firmware/esp32s3_terminal/`): در این مخزن وجود ندارد. کد V1-A
  (I2S RX/TX، پخش 440Hz، RMS) هنوز اینجا نیست و روی برد واقعی build/تست نشده است.
- **Wi-Fi/WebSocket و انتقال PCM دوطرفه** (V1.1)
- **ASR** (faster-whisper)، **TTS** فارسی، **VAD/AEC/Barge-in**
- **Command Broker** و کنترل GPIO/رله
- **احراز هویت دستگاه، OTA، چنددستگاهی**

## ۳. اجرای تأییدشده (دستورهای واقعی)

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt   # ✅ موفق
.\.venv\Scripts\python.exe -m pytest                            # ✅ 17 passed
```

پیش‌نیاز اجرای واقعی چت: `ollama serve` و `ollama pull qwen3:4b`، سپس
`.\tools\start_server.ps1` و در ترمینال دیگر `.\tools\chat.ps1`.

## ۴. گام بعدی

1. اجرای `tools\verify_lesson01.py` روی PC واقعی و ثبت TTFT/توکن‌بر‌ثانیه در `benchmarks/results/`.
2. افزودن `firmware/esp32s3_terminal/` (ESP-IDF 5.4.4) و تست مستقل میکروفون/بلندگو.
3. شروع V1.1: WebSocket + فریم‌های PCM 20–40ms و پیام‌های کنترلی `SPEC.md` §۶.
