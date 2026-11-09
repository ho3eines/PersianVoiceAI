# عیب‌یابی — پیام‌ها، علت و راه‌حل

پیام‌های این سند همان متن‌هایی هستند که کد تولید می‌کند (`grep` در `server/` قابل تأیید است).
هیچ خطای دامنه‌ای به‌صورت traceback خام به کاربر نمی‌رسد؛ اگر traceback دیدید، آن یک باگ است.

## ۰. اولین قدم همیشه

```powershell
curl http://127.0.0.1:8000/ready
curl http://127.0.0.1:8000/health
```

`/ready` برای هر بخش (configuration، asr_model، tts_model، dependencies، aec، storage،
llm_backend) وضعیت `ok` و پیام فارسی می‌دهد و کل پاسخ `503` می‌شود اگر چیزی ناقص باشد.
`/health` فقط liveness برنامه (`{"status":"ok"}`) است و اتصال backend را بررسی نمی‌کند.
اگر توکن REST تنظیم شده، برای `/ready` هدر Authorization لازم است.

---

## ۱. مدل‌ها و فایل‌ها

| پیام | علت | راه‌حل |
|---|---|---|
| «مدل Whisper محلی موجود نیست؛ PVAI_ASR_MODEL_PATH را تنظیم کنید.» | مسیر پوشه یا `model.bin` نیست | پوشهٔ CTranslate2 کامل (نه فایل PyTorch) را در مسیر بگذارید |
| «فایل‌های مدل Whisper ناقص است: model.bin» | فقط بخشی از مدل کپی شده | `config.json`, `tokenizer.json`, `vocabulary.txt` و وزن‌ها را کامل کنید |
| «مدل Piper موجود نیست؛ …» | `.onnx` نیست | `PVAI_TTS_MODEL_PATH` یا `PVAI_TTS_MODEL_MALE/FEMALE` را درست کنید |
| «فایل تنظیمات مدل Piper (.onnx.json) موجود نیست» | فایل کنار مدل کپی نشده | هر دو فایل `.onnx` و `.onnx.json` لازم است |
| «بارگذاری مدل Piper ناموفق بود؛ …onnxruntime…» | نسخهٔ piper/onnxruntime ناسازگار | `requirements-voice.txt` را در همان venv نصب کنید |
| «بارگذاری مدل Whisper ناموفق بود؛ … PVAI_ASR_DEVICE/PVAI_ASR_COMPUTE_TYPE» | `cuda` بدون GPU/کتابخانه یا `float16` روی CPU | `PVAI_ASR_DEVICE=cpu`, `PVAI_ASR_COMPUTE_TYPE=int8` |

## ۲. صدا و دستگاه

| پیام | علت | راه‌حل |
|---|---|---|
| «هیچ دستگاه صوتی به سیستم متصل نیست.» | PortAudio دستگاهی نمی‌بیند | درایور/هدفون را بررسی کنید؛ `python -m server.voice --list-devices` |
| «شمارهٔ دستگاه … نامعتبر است» | index بیرون از فهرست | فهرست را دوباره بگیرید (شماره‌ها با اتصال دستگاه تغییر می‌کنند) |
| «دستگاه … با نام «…» پیدا نشد یا کانال لازم را ندارد» | نام اشتباه یا دستگاه فقط ورودی/خروجی است | نام دقیق از فهرست؛ یا index |
| «نام دستگاه … مبهم است (…)» | چند دستگاه با همان زیررشته | از index دقیق استفاده کنید |
| «دستگاه … نرخ 16000Hz تک‌کاناله را نمی‌پذیرد» | میکروفون فقط 44.1/48k را می‌پذیرد | در Windows از "Advanced" دستگاه نرخ 16k را فعال کنید یا دستگاه دیگری انتخاب کنید (بازنمونه‌برداری خودکار ورودی زندهٔ دستگاه پیاده نشده؛ تبدیل خودکار فقط در ورودی فایل ASR است) |
| «جریان صوتی بیش از N ثانیه فریم نداد؛ دستگاه قطع شده است.» | USB/هدست جدا شد یا درایور هنگ کرد | دستگاه را وصل کنید؛ برنامه تا `PVAI_AUDIO_REOPEN_ATTEMPTS` بازگشایی می‌کند و بعد خطای روشن می‌دهد |
| «ورودی صوت عقب افتاد؛ نوبت جاری حذف شد.» | صف capture پر شد (CPU یا دیسک کند) | بار سیستم را کم کنید؛ `PVAI_AUDIO_BLOCK_MS` و threadها را بررسی کنید. **finalهای قبلی حذف نمی‌شوند** |

## ۳. AEC

| پیام | علت | راه‌حل |
|---|---|---|
| «کتابخانهٔ SpeexDSP پیدا نشد؛ PVAI_AEC_LIBRARY را تنظیم کنید یا AEC را صریحاً خاموش کنید.» | DLL/SO نصب نیست | طبق [AEC.md](AEC.md) نصب کنید یا `PVAI_AEC_ENABLED=false` (با هدفون) |
| «نمادهای لازم در کتابخانهٔ SpeexDSP نیست: …» | کتابخانهٔ اشتباه (نسخهٔ ۳۲بیتی/بدون echo) | DLL ۶۴بیتی صحیح |
| «AEC فقط با 16kHz و فریم 20ms کار می‌کند» | تنظیمات صوتی تغییر کرده | `PVAI_AUDIO_SAMPLE_RATE=16000` و `PVAI_AUDIO_BLOCK_MS=20` |
| AEC فعال است ولی دستیار خودش را قطع می‌کند | تأخیر مرجع نامناسب یا اکوی زیاد | `PVAI_AEC_DELAY_MS` را ۲۰ms‌به‌۲۰ms جابه‌جا و تست کنید؛ `PVAI_VOICE_BARGE_IN_MARGIN` را بالا ببرید؛ در نهایت هدفون |

هشدار: موفقیت تست مصنوعی به معنی حذف اکوی اتاق واقعی نیست. تا کالیبراسیون، بدون هدفون
ممکن است barge-in نادرست رخ دهد.

## ۴. گفتار تشخیص داده نمی‌شود یا زود تمام می‌شود

| نشانه | تنظیم |
|---|---|
| صدای آرام تشخیص داده نمی‌شود | `PVAI_VOICE_VAD_THRESHOLD` را کم کنید (مثلاً ۰٫۰۱ → ۰٫۰۰۵) |
| نویز پنکه utterance می‌سازد | آستانه را زیاد کنید؛ `PVAI_VOICE_VAD_NOISE_MULTIPLIER` را بالا ببرید؛ `PVAI_VOICE_VAD_MIN_SPEECH_MS` را بیشتر کنید |
| جمله وسط مکث کوتاه قطع می‌شود | `PVAI_VOICE_SILENCE_MS` را زیاد کنید (مثلاً ۸۰۰) |
| حرف اول کلمه بریده می‌شود | `PVAI_VOICE_VAD_PRE_ROLL_MS` را زیاد کنید (تا ۴۰۰) |
| پایان جمله دیر تشخیص داده می‌شود | `PVAI_VOICE_SILENCE_MS` را کم کنید |
| گفتار بلند چند تکه می‌شود | طبیعی است (سقف `PVAI_VOICE_VAD_MAX_SEGMENT_S`)؛ همهٔ تکه‌ها حفظ می‌شوند و در تاریخچه به ترتیب می‌آیند |
| هیچ `transcript_partial` نمی‌بینم | `PVAI_VOICE_ASR_INTERVAL` را کم کنید؛ اگر CPU عقب است interval را **زیاد** کنید (Whisper کندتر از real-time است) |

رویدادهای `speech_start`/`speech_end` شامل `rms`، `noise_floor` و `boundary` هستند؛ برای
کالیبراسیون همین اعداد را در سکوت و در گفتار مقایسه کنید.

## ۵. پاسخ‌دهی و barge-in

| نشانه | علت | راه‌حل |
|---|---|---|
| «پاسخ استریم ناموفق بود؛ اتصال مدل و llama-server را بررسی کنید.» | llama-server قطع/خطا | `tools/start_server.ps1`، `/health` |
| «استریم پاسخ ناقص بود.» | اتصال SSE وسط کار بسته شد | سرور و مدل را بررسی کنید؛ پاسخ ناقص در تاریخچه ثبت نمی‌شود |
| «تولید یا پخش صدا ناموفق بود؛ مدل Piper و دستگاه خروجی را بررسی کنید.» | مدل/دستگاه خروجی | `/ready` و فهرست دستگاه‌ها |
| «تشخیص گفتار ناموفق بود؛ مدل محلی Whisper را بررسی کنید.» | خطای decode | مدل و RAM را بررسی کنید؛ utterance بعدی ادامه می‌دهد |
| دستیار حرف کاربر را قطع نمی‌کند | آستانه یا AEC | `PVAI_VOICE_BARGE_IN_MS` را کم کنید (مثلاً ۱۲۰) و margin را پایین بیاورید |
| دستیار با کوچک‌ترین صدا قطع می‌شود | echo/noise | margin را بالا ببرید، AEC را کالیبره کنید |
| اولین utterance کند است | warmup خاموش | `PVAI_ASR_WARMUP=true` (پیش‌فرض) تا مدل در startup لود شود |

## ۶. API و نشست‌ها

| کد | معنا | راه‌حل |
|---|---|---|
| `503` با `backend unavailable` | llama-server در دسترس نیست | سرویس مدل را شروع کنید |
| `503` `model_not_loaded` | مدل در llama-server لود نشده | `-m` را بررسی کنید |
| `502` `generation error` | خطای تولید | لاگ llama-server |
| `400` «شناسهٔ نشست نامعتبر است.» | `session_id` کاراکتر غیرمجاز دارد | فقط حروف **انگلیسی**/رقم/`-`/`_`، حداکثر ۶۴ کاراکتر، و نه نام دستگاه رزروشدهٔ ویندوز (`CON`, `NUL`, `COM1`…) |
| `404` «نشست پیدا نشد.» | نشست ذخیره نشده | `PVAI_HISTORY_PERSIST=true` و `/api/sessions` |

## ۶.۱. تنظیمات `.env` بی‌اثر به نظر می‌رسد

| نشانه | علت | راه‌حل |
|---|---|---|
| مقدار `.env` عوض می‌شود ولی رفتار سرویس تغییر نمی‌کند | llama-server دستی با پرچم‌های خودش اجرا شده؛ `PVAI_N_THREADS`/`PVAI_N_CTX`/`PVAI_N_GPU_LAYERS` را کلاینت HTTP نمی‌خواند | با `tools/start_llama.ps1` اجرا کنید یا `python -m server.config --launch` را ببینید |
| `GET /ready` در بخش `configuration` خطای `PVAI_...` دارد | مقدار غیرعددی/نامفهوم بود و به پیش‌فرض برگشت (WARNING در لاگ) | مقدار آن متغیر را در `.env` اصلاح کنید |
| «مقدار کلمپ شد» در لاگ | عدد خارج از بازهٔ مجاز بود | بازهٔ مجاز را در `docs/CONFIGURATION.md` ببینید |
| `.env` ساخته شده ولی خوانده نمی‌شود | فایل در پوشهٔ جاری و ریشهٔ پروژه نیست | `.env` را در ریشهٔ پروژه بگذارید؛ `PVAI_DATA_DIR`/مسیرها نسبت به همان‌جا خوانده می‌شوند |

> کامنت هم‌خط حذف می‌شود، ولی توضیح را در خط جدا بنویسید: `PVAI_AUDIO_INPUT_DEVICE=Realtek Microphone`
> با یک ` # ...` در ادامه، نام دستگاه را می‌بُرد.

## ۷. عیب‌یابی داده و حافظه

```powershell
# فهرست و حذف نشست‌های محلی
.\.venv\Scripts\python.exe -m server.sessions list
.\.venv\Scripts\python.exe -m server.sessions show <session_id>
.\.venv\Scripts\python.exe -m server.sessions delete <session_id>
.\.venv\Scripts\python.exe -m server.sessions delete-all
.\.venv\Scripts\python.exe -m server.sessions purge --days 30
```

- داده در `PVAI_DATA_DIR/conversations/*.jsonl` است (در `.gitignore`).
- `PVAI_HISTORY_PERSIST=false` هیچ فایلی نمی‌سازد (تست: `test_persistence_can_be_disabled`).
- پاسخ‌های قطع‌شده با `status=interrupted` ذخیره می‌شوند و به‌طور پیش‌فرض وارد context مدل نمی‌شوند.
- **شروع تازه می‌خواهید؟** `PVAI_HISTORY_RESUME=off` بگذارید (هر اجرا نشست تازه) یا
  `python -m server.sessions delete-all`. برعکس، اگر بعد از restart مکالمه ادامه نیافت،
  `PVAI_HISTORY_PERSIST=true` و `PVAI_HISTORY_RESUME=on` را بررسی کنید؛ پیام startup
  «ادامهٔ نشست … با N پیام قبلی» نشان می‌دهد resume انجام شده است.
- **`sessions purge` می‌گوید «مدت نگه‌داری تعیین نشده است»؟** `PVAI_HISTORY_RETENTION_DAYS`
  را تنظیم کنید یا `--days N` بدهید (پیش‌فرض کد `30` روز است؛ مقدار `0` پاک‌سازی را غیرفعال می‌کند).

## ۸. اگر مشکل پیدا نشد

1. `PVAI_LOG_LEVEL=DEBUG`.
2. رویدادها JSON line هستند؛ `speech_start`/`speech_end`/`transcript_final`/`interrupted`/`error`
   را ذخیره کنید (بدون متن خصوصی اگر می‌خواهید گزارش بدهید).
3. `python tools/benchmark_voice.py --scenarios idle` را اجرا کنید تا مشخص شود مسئله در
   دستگاه/هماهنگ‌کننده است یا در مدل‌ها.
4. اگر پاسخ اشتباه است ولی جریان درست: مسئله کیفیت مدل است، نه خط لوله — به `docs/BENCHMARKS.md`
   بخش ارزیابی فارسی مراجعه کنید.
