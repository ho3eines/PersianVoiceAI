# مرجع پیکربندی — متغیرهای `PVAI_*`

این سند **قرارداد واقعی `server/config.py`** است: هر متغیری که در کد خوانده می‌شود اینجا فهرست شده،
با پیش‌فرض واقعی، بازهٔ مجاز و اثری که روی خط لوله دارد. مقدار پیش‌فرض داخل کد است؛ `.env` یا
متغیر محیطی آن را override می‌کند و متغیر محیطی همیشه بر `.env` اولویت دارد.

قواعد کلی:

- نام متغیرها انگلیسی و با پیشوند `PVAI_` است؛ مقدار متنی می‌تواند فارسی باشد (`PVAI_SYSTEM_PROMPT`).
- عدد خارج از بازهٔ مجاز **به نزدیک‌ترین حد مجاز** بریده می‌شود، نه اینکه برنامه را متوقف کند.
  مقدار غیرعددی نادیده گرفته می‌شود و پیش‌فرض می‌ماند.
- مقادیر بولی: `1/true/yes/on` و `0/false/no/off`؛ `none/auto/default` برای متغیرهای سه‌حالته مقدار «خودکار».
- خطاهای ساختاری (مثل صدای نامعتبر یا نرخ نمونه‌برداری نادرست) با `Settings.config_errors()` برگردانده
  می‌شوند و در `GET /ready` دیده می‌شوند؛ مسیر زنده در این حالت اجرا نمی‌شود.
- هیچ متغیری صدا یا متن را به اینترنت نمی‌فرستد. تنها مقصد شبکه‌ای `PVAI_LLAMA_SERVER_URL` است و
  `server/voice.py` آدرس عمومی (غیرخصوصی/غیرloopback) را رد می‌کند.

---

## ۱. مدل متنی و llama-server

| متغیر | پیش‌فرض | معنا |
|---|---|---|
| `PVAI_MODEL_PATH` | `models/qwen3.5-4b/Qwen3.5-4B-Q4_K_M.gguf` | مدل منتخب پس از ارزیابی محلی لپ‌تاپ؛ مسیر GGUF باید محلی باشد |
| `PVAI_LLAMA_SERVER_URL` | `http://127.0.0.1:8080` | آدرس محلی llama-server؛ باید private/loopback باشد |
| `PVAI_N_CTX` | `4096` | پنجرهٔ context (۵۱۲–۳۲۷۶۸) |
| `PVAI_N_THREADS` | `0` | thread سمت llama-server؛ `0` = خودکار. مستقل از threadهای Whisper است |
| `PVAI_N_GPU_LAYERS` | `0` | تعداد لایه روی GPU؛ روی GeForce 940MX (۲GB) پیش‌فرض CPU حفظ می‌شود |
| `PVAI_SYSTEM_PROMPT` | متن فارسی پیش‌فرض | system prompt؛ در **هر دو** مسیر متنی و صوتی به مدل می‌رود |
| `PVAI_TEMPERATURE` | `0.7` | دمای نمونه‌گیری (۰–۲) |
| `PVAI_MAX_TOKENS` | `512` | سقف توکن پاسخ |
| `PVAI_TIMEOUT` | `180` | مهلت درخواست به llama-server (ثانیه) |
| `PVAI_LLM_ENABLE_THINKING` | `false` | `chat_template_kwargs` برای Qwen؛ `false` تا `reasoning_content` به صدا تبدیل نشود |

## ۲. سرویس API

| متغیر | پیش‌فرض | معنا |
|---|---|---|
| `PVAI_HOST` | `127.0.0.1` | آدرس bind. `0.0.0.0` فقط در شبکهٔ خصوصی و با در نظر گرفتن نبود احراز هویت |
| `PVAI_PORT` | `8000` | پورت API |
| `PVAI_LOG_LEVEL` | `INFO` | سطح لاگ |

## ۳. ASR (faster-whisper / CTranslate2)

| متغیر | پیش‌فرض | معنا |
|---|---|---|
| `PVAI_ASR_MODEL_PATH` | `models/whisper-small` | پوشهٔ مدل CTranslate2 شامل `model.bin`؛ دانلود خودکار مسدود است (`local_files_only=True`) |
| `PVAI_ASR_DEVICE` | `cpu` | `cpu` یا `cuda`؛ CUDA روی 940MX بدون تست تأیید نشده |
| `PVAI_ASR_COMPUTE_TYPE` | `int8` | `int8` روی CPU؛ `float16` فقط با GPU معتبر |
| `PVAI_ASR_THREADS` | `2` | بودجهٔ thread مخصوص Whisper، جدا از `PVAI_N_THREADS` (هدف: ۲ هستهٔ فیزیکی) |
| `PVAI_ASR_BEAM_SIZE` | `1` | beam search؛ ۱ سریع‌ترین، بیشتر = دقیق‌تر و کندتر |
| `PVAI_ASR_VAD_FILTER` | `false` | VAD داخلی Whisper؛ مرجع پایان‌گفتار ما نیست (VAD خودمان تصمیم می‌گیرد) |
| `PVAI_ASR_PARTIAL_WINDOW_S` | `6.0` | طول پنجرهٔ هم‌پوشان برای partial؛ cost هر decode را ثابت نگه می‌دارد. باید ≥ ۲× `PVAI_VOICE_ASR_INTERVAL` باشد |
| `PVAI_ASR_MAX_FINAL_JOBS` | `16` | آستانهٔ هشدار عمق صف final؛ **هیچ final حذف نمی‌شود** |
| `PVAI_ASR_WARMUP` | `true` | لود و warmup مدل در startup به‌جای اولین utterance |

## ۴. TTS (Piper)

| متغیر | پیش‌فرض | معنا |
|---|---|---|
| `PVAI_TTS_MODEL_PATH` | `models/piper/fa_IR.onnx` | مدل پیش‌فرض/عمومی Piper |
| `PVAI_TTS_MODEL_FEMALE` | خالی | مسیر مدل صدای زن؛ اگر خالی باشد `PVAI_TTS_MODEL_PATH` استفاده می‌شود |
| `PVAI_TTS_MODEL_MALE` | خالی | مسیر مدل صدای مرد؛ همان قاعده |
| `PVAI_TTS_VOICE` | `female` | `male` یا `female`. مقدار دیگر خطای پیکربندی است |

هر مدل `.onnx` **باید** فایل کنارش `.onnx.json` داشته باشد؛ وگرنه startup/readiness پیام فارسی می‌دهد.

## ۵. VAD و پایان‌گفتار

| متغیر | پیش‌فرض | معنا |
|---|---|---|
| `PVAI_VOICE_VAD_THRESHOLD` | `0.015` | آستانهٔ RMS شروع گفتار (۰٫۰۰۱–۰٫۵) |
| `PVAI_VOICE_VAD_OFFSET_RATIO` | `0.6` | هیسترزیس: آستانهٔ ادامهٔ گفتار = آستانهٔ شروع × این نسبت (۰٫۰۵–۱) |
| `PVAI_VOICE_VAD_ONSET_MS` | `60` | چند میلی‌ثانیه صدای بلند برای اعلام onset |
| `PVAI_VOICE_SILENCE_MS` | `600` | سکوت لازم برای endpoint |
| `PVAI_VOICE_VAD_PRE_ROLL_MS` | `200` | پیش‌رول نگه‌داشته‌شده تا حروف اولیه («س»، «ش»، «خ») بریده نشوند |
| `PVAI_VOICE_VAD_MIN_SPEECH_MS` | `160` | حداقل گفتار برای پذیرش یک utterance (سرفه رد می‌شود) |
| `PVAI_VOICE_VAD_MAX_SEGMENT_S` | `15.0` | سقف طول یک بخش؛ گفتار بلندتر تقسیم می‌شود و **همهٔ بخش‌ها** حفظ می‌شوند |
| `PVAI_VOICE_VAD_NOISE_ADAPT` | `true` | تطبیق کف نویز (پنکه/کولر) |
| `PVAI_VOICE_VAD_NOISE_MULTIPLIER` | `2.5` | ضریب کف نویز برای آستانهٔ مؤثر |
| `PVAI_VOICE_BARGE_IN_MS` | `200` | چند میلی‌ثانیه گفتار برای قطع پاسخ دستیار |
| `PVAI_VOICE_BARGE_IN_MARGIN` | `1.5` | افزایش موقت آستانهٔ VAD هنگام پخش (کاهش false barge-in؛ جایگزین AEC نیست) |

## ۶. زمان‌بندی خط لوله

| متغیر | پیش‌فرض | معنا |
|---|---|---|
| `PVAI_VOICE_ASR_INTERVAL` | `0.8` | فاصلهٔ ارسال partial (۰٫۳–۵ ثانیه) |
| `PVAI_VOICE_SPEECH_QUEUE` | `8` | صف محدود جمله‌ها؛ پر شدنش به SSE backpressure می‌دهد |
| `PVAI_VOICE_RECORD_SECONDS` | `5` | فقط حالت عیب‌یابی `--turn-based` |

## ۷. دستگاه و انتقال صدا

| متغیر | پیش‌فرض | معنا |
|---|---|---|
| `PVAI_AUDIO_INPUT_DEVICE` | خالی | نام (زیررشته) یا index میکروفون؛ خالی = پیش‌فرض سیستم. نام مبهم خطا می‌دهد |
| `PVAI_AUDIO_OUTPUT_DEVICE` | خالی | همان قاعده برای خروجی |
| `PVAI_AUDIO_SAMPLE_RATE` | `16000` | **باید ۱۶۰۰۰ باشد**؛ قرارداد PCM16 mono 16k برای VAD/ASR/AEC. مقدار دیگر خطای پیکربندی است |
| `PVAI_AUDIO_BLOCK_MS` | `20` | طول بلوک (۱۰/۲۰/۴۰) |
| `PVAI_AUDIO_STALL_S` | `2.0` | اگر بیش از این مدت فریمی نرسد، دستگاه قطع‌شده فرض می‌شود |
| `PVAI_AUDIO_REOPEN_ATTEMPTS` | `3` | تعداد تلاش بازگشایی stream پیش از خطای نهایی |

## ۸. AEC (SpeexDSP)

| متغیر | پیش‌فرض | معنا |
|---|---|---|
| `PVAI_AEC_ENABLED` | `true` | با `false` صریح، bypass کامل (برای تست با هدفون). نبود کتابخانه با AEC فعال = خطای صریح، نه bypass خاموش |
| `PVAI_AEC_LIBRARY` | خالی | مسیر مطلق DLL/SO؛ خالی = جست‌وجوی سیستمی |
| `PVAI_AEC_FILTER_MS` | `250` | طول فیلتر تطبیقی (۱۰۰–۱۰۰۰ ms) |
| `PVAI_AEC_DELAY_MS` | `40` | تأخیر مرجع پخش تا میکروفون؛ **باید روی دستگاه واقعی کالیبره شود** |

AEC فقط با ۱۶kHz و بلوک ۲۰ms معنا دارد؛ `GET /ready` این را بررسی می‌کند.

## ۹. حافظهٔ مکالمه (فقط محلی)

| متغیر | پیش‌فرض | معنا |
|---|---|---|
| `PVAI_HISTORY_PERSIST` | `true` | ذخیرهٔ JSONL محلی زیر `PVAI_DATA_DIR/conversations` |
| `PVAI_DATA_DIR` | `data` | ریشهٔ دادهٔ محلی (در `.gitignore`) |
| `PVAI_HISTORY_MAX_MESSAGES` | `24` | سقف تعداد پیام در context (پس از برش توکنی) |
| `PVAI_HISTORY_TOKEN_BUDGET` | `1536` | بودجهٔ تخمینی توکن context (حداقل ۱۲۸) |
| `PVAI_HISTORY_MAX_STORED` | `500` | پنجرهٔ RAM؛ فایل روی دیسک کامل می‌ماند (جلوگیری از رشد نامحدود حافظه) |
| `PVAI_HISTORY_INCLUDE_INTERRUPTED` | `false` | آیا پاسخ قطع‌شده در context مدل بیاید (برای بازبینی همیشه ذخیره می‌شود) |

## ۱۰. انتقال صوتی WebSocket (LAN)

| متغیر | پیش‌فرض | معنا |
|---|---|---|
| `PVAI_WS_ENABLED` | `false` | ثبت `WS /ws/voice` برای دستگاه‌های LAN. خاموش = هیچ پورت/مسیر صوتی اضافه‌ای باز نمی‌شود |
| `PVAI_WS_TOKEN` | خالی | توکن مشترک دستگاه؛ با `PVAI_WS_ENABLED=true` الزامی است، وگرنه `config_errors` خطا می‌دهد و هر اتصال با `auth_not_configured` رد می‌شود |

- پورت همان `PVAI_PORT` برنامه است؛ مسیر جدا `WS /ws/voice`.
- `/ready` کلید `ws_voice` را برمی‌گرداند: خاموش / روشن بدون توکن (ناآماده) / آماده.
- firmware، رجیستری دستگاه و OTA وجود ندارند؛ `docs/PROTOCOL.md` این تفکیک را صریح می‌گوید.

## ۱۱. مشاهده‌پذیری

| متغیر | پیش‌فرض | معنا |
|---|---|---|
| `PVAI_METRICS_ENABLED` | `true` | فعال‌سازی سنجه‌ها (`server/metrics.py`)؛ خاموش = صفر overhead، داده‌ای برای بنچمارک نیست |

فهرست نام سنجه‌ها در `server/metrics.py::METRIC_NAMES` است و تست
`tests/test_metrics.py::test_documented_metric_names_cover_the_pipeline` هم‌خوانی آن با کد را بررسی می‌کند.

---

## نمونهٔ `.env` حداقلی برای هدف (i7-6500U)

```dotenv
PVAI_MODEL_PATH=models/Qwen3-4B-Q4_K_M.gguf
PVAI_N_THREADS=2
PVAI_ASR_THREADS=2
PVAI_ASR_PARTIAL_WINDOW_S=6
PVAI_TTS_VOICE=female
PVAI_TTS_MODEL_FEMALE=models/piper/fa_IR_female.onnx
PVAI_TTS_MODEL_MALE=models/piper/fa_IR_male.onnx
PVAI_AUDIO_INPUT_DEVICE=Microphone
PVAI_AUDIO_OUTPUT_DEVICE=Speakers
PVAI_AEC_ENABLED=true
PVAI_VOICE_VAD_THRESHOLD=0.015
PVAI_HISTORY_PERSIST=true
```

پس از تغییر، `GET /ready` را ببینید: هر متغیر نادرست به‌صورت خطای فارسی با نام همان بخش گزارش می‌شود.
