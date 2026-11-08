# دستیار صوتی PC — شنیدن پیوسته و پاسخ استریم

## مسیر فعلی — معماری جدید

[قرارداد معماری streaming](STREAMING_ARCHITECTURE.md) مرجع رفتار اجرایی است.
مسیر قبلی «منتظر پایان گفتار بمان و سپس ASR را شروع کن» جایگزین شده است:

`PCM زنده → snapshot هم‌پوشان → ASR partial/final → async SSE → صف جمله → Piper PCM`

بدون Enter صحبت کنید. تقریباً هر ۰٫۸ ثانیه از بخش گفتار در حال دریافت یک snapshot برای Whisper
فرستاده می‌شود و `transcript_partial` در همان حین نمایش داده می‌شود. بعد از حدود ۵۰۰ms سکوت،
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
PVAI_TTS_VOICE=female              # male | female
PVAI_TTS_MODEL_FEMALE=models/piper/fa_IR_female.onnx
PVAI_TTS_MODEL_MALE=models/piper/fa_IR_male.onnx
PVAI_AUDIO_INPUT_DEVICE=Microphone  # زیررشتهٔ نام یا index
PVAI_AUDIO_OUTPUT_DEVICE=Speakers
PVAI_VOICE_VAD_THRESHOLD=0.015
PVAI_VOICE_ASR_INTERVAL=0.8
PVAI_HISTORY_PERSIST=true
```

مرجع کامل ۴۸ متغیر و اثر هر کدام: [CONFIGURATION.md](CONFIGURATION.md).

Thinking برای Qwen در `chat_template_kwargs` غیرفعال است؛ llama-server باید این گزینه را
پشتیبانی کند. فیلد `reasoning_content` دیگر به‌عنوان جواب یا صدا استفاده نمی‌شود.

## اجرا — سه ترمینال

```powershell
# ۱: مسیر باینری را مطابق نصب خود تغییر دهید
D:\Lama.cpp\bin\llama-server.exe -m models\Qwen3-4B-Q4_K_M.gguf -c 4096 --host 127.0.0.1 --port 8080 -t 4
# ۲: API
.\tools\start_server.ps1
# ۳: کلاینت صوتی زنده (SpeexDSP نصب و مسیر DLL/SO تنظیم شده باشد)
.\.venv\Scripts\python.exe -m server.voice
```

برای خروج Ctrl+C بزنید. **تاریخچهٔ مکالمه با خروج پاک نمی‌شود و اجرای بعدی همان نشست را ادامه
می‌دهد**: با `PVAI_HISTORY_PERSIST=true` در `data/conversations/<session_id>.jsonl` می‌ماند و
`PVAI_HISTORY_RESUME=on` (پیش‌فرض) هنگام شروع، آخرین نشست را می‌خواند؛ پیام‌ها در startup چاپ
می‌شوند (`ادامهٔ نشست «…» با N پیام قبلی`). برای شروع تازه `PVAI_HISTORY_RESUME=off` بگذارید یا
`python -m server.sessions delete-all` را اجرا کنید. با `PVAI_VOICE_SESSION_ID` می‌توانید یک
شناسهٔ ثابت انتخاب کنید. بازبینی/حذف: `python -m server.sessions list|show|delete|delete-all`.
پنجرهٔ RAM با `PVAI_HISTORY_MAX_STORED` محدود است، فایل دیسک کامل می‌ماند؛ برای جلوگیری از
نگه‌داشتن بی‌پایان متن گفتار، `PVAI_HISTORY_RETENTION_DAYS` (پیش‌فرض `0` = همه) و
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
- llama-server را هم محلی نگه دارید؛ API فعلی احراز هویت ندارد و نباید عمومی شود.
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
