# مرجع پیکربندی — متغیرهای `PVAI_*`

این سند **قرارداد واقعی `server/config.py`** است: هر متغیری که در کد خوانده می‌شود اینجا فهرست شده،
با پیش‌فرض واقعی، بازهٔ مجاز و اثری که روی خط لوله دارد. مقدار پیش‌فرض داخل کد است؛ `.env` یا
متغیر محیطی آن را override می‌کند و متغیر محیطی همیشه بر `.env` اولویت دارد.

قواعد کلی:

- نام متغیرها انگلیسی و با پیشوند `PVAI_` است؛ مقدار متنی می‌تواند فارسی باشد (`PVAI_SYSTEM_PROMPT`).
- **کامنت هم‌خط حذف می‌شود**: در `PVAI_N_THREADS=2   # توضیح` مقدار `2` است، نه رشتهٔ کامل.
  `#` فقط وقتی کامنت است که ابتدای مقدار باشد یا قبلش فاصله باشد، و هرگز داخل کوتیشن نیست؛
  پس `PVAI_SYSTEM_PROMPT="یک # دو"` دست‌نخورده می‌ماند. نوشتن توضیح در خط جدا همیشه امن‌تر است
  (نام دستگاه با فاصله مثل `Realtek Microphone` اگر بعدش ` # ...` بیاید بریده می‌شود).
- عدد خارج از بازهٔ مجاز **به نزدیک‌ترین حد مجاز** بریده می‌شود، نه اینکه برنامه را متوقف کند.
- **هیچ مقدار نامعتبری بی‌صدا نادیده گرفته نمی‌شود**: مقدار غیرعددی، بولی نامفهوم یا کلمپ‌شده
  با `WARNING` (شامل نام متغیر) لاگ می‌شود، در `Settings.env_issues` می‌نشیند و از راه
  `Settings.config_errors()` در `GET /ready` و پیام startup دیده می‌شود.
- مقادیر بولی: `1/true/yes/on` و `0/false/no/off`؛ `none/auto/default` برای متغیرهای سه‌حالته مقدار «خودکار».
- خطاهای ساختاری (مثل صدای نامعتبر یا نرخ نمونه‌برداری نادرست) با `Settings.config_errors()` برگردانده
  می‌شوند و در `GET /ready` دیده می‌شوند؛ مسیر زنده در این حالت اجرا نمی‌شود.
- **محل `.env`**: اول `.env` در پوشهٔ جاری، بعد `.env` در ریشهٔ پروژه جست‌وجو می‌شود (اولی که
  وجود داشته باشد برنده است). قبلاً فقط پوشهٔ جاری بود، پس اجرا از مسیر دیگر `.env` را
  **بی‌صدا** نادیده می‌گرفت.
- هیچ متغیری صدا یا متن را به اینترنت نمی‌فرستد. آدرس backend در `PVAI_LLAMA_SERVER_URL` تعیین می‌شود؛ کلاینت صوتی
  API محلی را از `PVAI_HOST`/`PVAI_PORT` می‌سازد و نشانی عمومی را رد می‌کند.
  ابزار دانلود وزن جدا از مسیر inference است و فقط با اقدام صریح اپراتور اجرا می‌شود.

---

## ۱. مدل متنی و llama-server

| متغیر | پیش‌فرض | معنا |
|---|---|---|
| `PVAI_MODEL_PATH` | `models/Qwen3-4B-Q4_K_M.gguf` | مسیر GGUF برای llama-server (خود سرویس آن را لود می‌کند؛ فایل باید محلی باشد) |
| `PVAI_LLAMA_SERVER_URL` | `http://127.0.0.1:8080` | آدرس محلی llama-server؛ باید private/loopback باشد |
| `PVAI_N_CTX` | `4096` | پنجرهٔ context (۵۱۲–۳۲۷۶۸)؛ به `llama-server -c` داده می‌شود |
| `PVAI_N_THREADS` | `0` | thread سمت llama-server؛ `0` = خودکار (پرچم `-t` ارسال نمی‌شود). مستقل از threadهای Whisper است؛ `.env.example` مقدار پیشنهادی `4` را تنظیم می‌کند، نه پیش‌فرض `0` |
| `PVAI_N_GPU_LAYERS` | `0` | تعداد لایه روی GPU (`-ngl`)؛ روی GeForce 940MX (۲GB) پیش‌فرض CPU حفظ می‌شود |
| `PVAI_SYSTEM_PROMPT` | متن فارسی پیش‌فرض | system prompt؛ در **هر دو** مسیر متنی و صوتی به مدل می‌رود |
| `PVAI_TEMPERATURE` | `0.7` | دمای نمونه‌گیری (۰–۲) |
| `PVAI_MAX_TOKENS` | `512` | سقف توکن پاسخ |
| `PVAI_TIMEOUT` | `180` | مهلت درخواست به llama-server (ثانیه) |
| `PVAI_LLM_ENABLE_THINKING` | `false` | `chat_template_kwargs` برای Qwen؛ `false` تا `reasoning_content` به صدا تبدیل نشود |

این چهار متغیر (`PVAI_MODEL_PATH`, `PVAI_N_CTX`, `PVAI_N_THREADS`, `PVAI_N_GPU_LAYERS`) توسط
کلاینت HTTP خوانده **نمی‌شوند**؛ llama-server مسیر مدل و thread را خودش تعیین می‌کند. برای اینکه
این تنظیمات واقعاً اثر کنند، اسکریپت راه‌انداز آن‌ها را به پرچم تبدیل می‌کند:

```powershell
.\tools\start_llama.ps1 -Exe D:\Lama.cpp\bin\llama-server.exe   # اجرا
.\tools\start_llama.ps1 -DryRun                                  # فقط نمایش دستور
.venv\Scripts\python.exe -m server.config --launch                # دستور خام
```

> اگر llama-server را دستی اجرا می‌کنید، پرچم‌هایتان باید با `.env` هم‌خوان باشد؛ در غیر این صورت
> **llama-server برنده می‌شود** و تغییر `.env` اثری ندارد. (پیش‌فرض کد `PVAI_N_THREADS=0` است؛ مقدار پیشنهادی `.env.example` برابر `4` است.
> اندازه‌گیری قدیمی MiniCPM در SPEC تضمین عملکرد Qwen نیست.)

## ۲. سرویس API

| متغیر | پیش‌فرض | معنا |
|---|---|---|
| `PVAI_HOST` | `127.0.0.1` | آدرس bind. `0.0.0.0` فقط با توکن REST و در شبکهٔ خصوصی |
| `PVAI_PORT` | `8000` | پورت API |
| `PVAI_LOG_LEVEL` | `INFO` | سطح لاگ |

## ۳. ASR (faster-whisper / CTranslate2)

| متغیر | پیش‌فرض | معنا |
|---|---|---|
| `PVAI_ASR_MODEL_PATH` | `models/whisper-small` | پوشهٔ مدل CTranslate2 شامل `model.bin`؛ دانلود خودکار مسدود است (`local_files_only=True`) |
| `PVAI_ASR_DEVICE` | `cpu` | `cpu`، `auto`، `cuda` یا `mps`؛ مقدار دیگر (`gpu` و مانند آن) در `env_issues`/`/ready` گزارش و به پیش‌فرض `cpu` برگردانده می‌شود. تشخیص CUDA با CTranslate2؛ نبود CUDA یا شکست بارگذاری GPU به CPU/int8 با لاگ برمی‌گردد؛ MPS پشتیبانی نمی‌شود و به CPU/int8 برمی‌گردد. CUDA واقعی روی 940MX تأیید نشده |
| `PVAI_ASR_COMPUTE_TYPE` | `int8` | `int8` روی CPU؛ `float16` فقط با GPU معتبر |
| `PVAI_ASR_THREADS` | `2` | بودجهٔ thread مخصوص Whisper، جدا از `PVAI_N_THREADS` (هدف: ۲ هستهٔ فیزیکی) |
| `PVAI_ASR_BEAM_SIZE` | `1` | beam search؛ ۱ سریع‌ترین، بیشتر = دقیق‌تر و کندتر |
| `PVAI_ASR_VAD_FILTER` | `false` | VAD داخلی Whisper؛ مرجع پایان‌گفتار ما نیست (VAD خودمان تصمیم می‌گیرد) |
| `PVAI_ASR_LANGUAGE` | `fa` | زبان Whisper. پیش‌تر در کد **سخت‌کد** بود. `auto` = انتخاب خود Whisper (برای گویندهٔ دوزبانه) |
| `PVAI_ASR_INITIAL_PROMPT` | خالی | متن آغازین؛ املا/واژگان را جهت می‌دهد (بدون آن Whisper گاهی پاسخ فارسی را با حروف عربی می‌نویسد) |
| `PVAI_ASR_NO_SPEECH_THRESHOLD` | `0.6` | آستانهٔ `no_speech_prob` خود Whisper |
| `PVAI_ASR_LOG_PROB_THRESHOLD` | `-1.0` | اگر `avg_logprob` سگمنت از این کمتر باشد، دور ریخته می‌شود |
| `PVAI_ASR_COMPRESSION_RATIO` | `2.4` | بالاتر از این = حلقهٔ تکرار، دور ریخته می‌شود |
| `PVAI_ASR_MAX_NO_SPEECH_PROB` | `0.75` | اگر `no_speech_prob` سگمنت از این بیشتر باشد، **کل سگمنت** حذف می‌شود |
| `PVAI_ASR_HALLUCINATION_PHRASES` | فهرست فارسی/انگلیسی | عبارت‌های توهمی روی سکوت/نویز، جداشده با ویرگول. خالی = فیلتر خاموش (توصیه نمی‌شود) |
| `PVAI_ASR_PARTIAL_WINDOW_S` | `3.0` | طول پنجرهٔ هم‌پوشان برای partial؛ cost هر decode را ثابت نگه می‌دارد. باید ≥ ۲× `PVAI_VOICE_ASR_INTERVAL` باشد |
| `PVAI_ASR_MAX_FINAL_JOBS` | `16` | آستانهٔ هشدار عمق صف final؛ **هیچ final حذف نمی‌شود** |
| `PVAI_ASR_WARMUP` | `true` | لود و warmup مدل در startup به‌جای اولین utterance |

### فیلتر توهم Whisper

Whisper روی سکوت و نویز «هیچ» برنمی‌گرداند؛ یک جملهٔ ساختگی برمی‌گرداند
(«زیرنویس‌ها توسط تیم ما تهیه شده است»، «ممنون از تماشای شما»). چون متن ناتهی یک نوبت
کامل LLM را شروع می‌کند، یک توهم یعنی پاسخ مطمئن به پرسشی که کسی نپرسیده.

چهار سیگنال مستقل، هر سگمنت را رد می‌کند: `no_speech_prob`، `avg_logprob`،
`compression_ratio` و فهرست عبارت‌ها. فیلتر عمداً محافظه‌کار است: فقط عبارت‌های کوتاه
(`WHOLE_TRANSCRIPT_ONLY` در `server/voice.py`) باید **کل متن** باشند، وگرنه
«به مادرم گفتم خداحافظ و رفتم» هم دور ریخته می‌شد — بدتر از رد نکردن یک توهم.
شمارندهٔ `asr_segments_rejected` در متریک‌ها ثبت می‌شود.

`benchmarks/persian_eval.py` نرخ **پذیرش کاذب** را جدا گزارش می‌کند: ورودی‌های
`kind=no_speech` در مجموعه (سکوت/نویز/تلویزیون) مرجع متنی ندارند و WER نمی‌توانند
بسنجند. با `--no-speech-dir DIR` روی wavهای واقعی هم اندازه‌گیری می‌شود؛ بدون فایل صوتی
گزارش صریحاً **NOT MEASURED** است، نه صفر.
- firmware، رجیستری دستگاه و OTA وجود ندارند؛ `docs/PROTOCOL.md` این تفکیک را صریح می‌گوید.

## ۴. TTS (Piper)

| متغیر | پیش‌فرض | معنا |
|---|---|---|
| `PVAI_TTS_MODEL_PATH` | `models/piper/fa_IR.onnx` | مدل پیش‌فرض/عمومی Piper |
| `PVAI_TTS_MODEL_FEMALE` | خالی | مسیر مدل صدای زن؛ اگر خالی باشد `PVAI_TTS_MODEL_PATH` استفاده می‌شود |
| `PVAI_TTS_MODEL_MALE` | خالی | مسیر مدل صدای مرد؛ همان قاعده |
| `PVAI_TTS_VOICE` | `female` | `male` یا `female`. مقدار دیگر خطای پیکربندی است |

هر مدل `.onnx` **باید** فایل کنارش `.onnx.json` داشته باشد؛ وگرنه startup/readiness پیام فارسی می‌دهد.

### آماده‌سازی متن پیش از سنتز (`server.persian.prepare_for_tts`)

پیش‌تر `server/persian.py` توابع `normalize` و `spoken_numbers` را داشت ولی **هیچ ماژولی
آن را import نمی‌کرد**؛ خروجی خام LLM به Piper می‌رفت. Piper هرچه بگیرد می‌خواند: `**` را
صدا می‌کند، هر شمارهٔ فهرست را با «نقطه» تمام می‌کند و روی ایموجی مکث می‌کند. بدتر اینکه
جمله‌بندی روی نقطهٔ `1.` می‌شکست:
`"**مهم:** سه مورد:\n1. اول\n2. دوم"` → `['**مهم:** سه مورد:\n1.', 'اول\n2.', 'دوم']`.

اکنون هر جمله پیش از رسیدن به Piper از این مرحله می‌گذرد:

- حذف markdown (پررنگ/مورب/عنوان/نقل‌قول/خط افقی)، بلوک کد، لینک (برچسب می‌ماند، URL حذف)،
  تگ HTML، ایموجی و ستارهٔ سرگردان؛
- نشانهٔ فهرست (`1.`, `-`, `•`) حذف می‌شود، نه اینکه جمله را بشکند؛
- ارقام فارسی/عربی/انگلیسی به **حروف** تبدیل می‌شوند (`1234` → «یک هزار و دویست و سی و چهار»،
  `۳.۵` → «سه ممیز پنج»)، همچنین ساعت (`10:30` → «ده و سی دقیقه»)، درصد («بیست و پنج درصد»)،
  ارز («پنج دلار») و نسخه (`2.1.0` → «دو نقطه یک نقطه صفر»)؛
- نیم‌فاصله و **همزهٔ اضافه** (`U+0654`) حفظ می‌شوند: حذف دومی «جملهٔ» را به «جمله» تبدیل می‌کند
  و تلفظ را عوض می‌کند؛ اعراب (فتحه/کسره/…) حذف می‌شود چون Piper آن‌ها را واکهٔ جدا می‌خواند؛
- خروجی idempotent است، چون ممکن است متنی از قبل آماده‌شده دوباره پردازش شود.

جمله‌بندی (`server.live_voice.sentences`) هم اصلاح شد: نقطه‌ای که به شمارهٔ فهرست یا اعشار
تعلق دارد مرز جمله نیست، و در حالت streaming یک قطعهٔ پایانی (`2.`) تا رسیدن ادامهٔ متن نگه
داشته می‌شود. متن **خام** همچنان در رونوشت و در پیام دستگاه می‌ماند؛ فقط صدای Piper تغییر می‌کند.

system prompt پیش‌فرض هم صریح می‌گوید پاسخ با صدا خوانده می‌شود: بدون markdown، فهرست،
جدول، لینک و ایموجی، و ارقام با حروف. این لایهٔ دوم است؛ لایهٔ اول همان `prepare_for_tts` است
که حتی اگر مدل رعایت نکند، صدا را درست می‌کند.

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
| `PVAI_AUDIO_STALL_S` | `2.0` | اگر بیش از این مدت فریمی نرسد، دستگاه قطع‌شده فرض می‌شود. مبدأ شمارش، آخرین فریم است و اگر هیچ فریمی نرسیده باشد، لحظهٔ باز شدن stream |
| `PVAI_AUDIO_REOPEN_ATTEMPTS` | `3` | تعداد تلاش بازگشایی stream پیش از خطای نهایی |
| `PVAI_AUDIO_REOPEN_STABLE_FRAMES` | `50` | حداقل فریم واقعی که یک stream بازگشایی‌شده باید تحویل دهد تا «سالم» شمرده شود و سهمیهٔ تلاش‌ها ریست شود (۱ ثانیه با بلوک ۲۰ms). کمتر از این یعنی دستگاه باز می‌شود ولی صدا نمی‌دهد (mute/exclusive/Bluetooth) و باید سهمیه را مصرف کند |

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
| `PVAI_HISTORY_RESUME` | `on` | `on`: مسیر زندهٔ محلی در هر اجرا نشستِ قبلی را ادامه می‌دهد (الزام ۱۷). `off`: هر اجرا نشست تازه؛ `session_id` صریح همیشه resume می‌شود |
| `PVAI_VOICE_SESSION_ID` | (خالی) | اگر تنظیم شود، نشستِ ثابتی است که مسیر زندهٔ محلی همیشه در آن می‌نویسد (فقط `[A-Za-z0-9_-]`) |
| `PVAI_HISTORY_RETENTION_DAYS` | `30` | `0` = نگه‌داشتن همه. با مقدار مثبت، نشست‌هایی که این تعداد روز دست‌نخورده مانده‌اند در startup و هر ۲۴ ساعت و با `python -m server.sessions purge` پاک می‌شوند |

## ۱۰. انتقال صوتی WebSocket (LAN)

| متغیر | پیش‌فرض | معنا |
|---|---|---|
| `PVAI_WS_ENABLED` | `false` | ثبت `WS /ws/voice` برای دستگاه‌های LAN. خاموش = هیچ پورت/مسیر صوتی اضافه‌ای باز نمی‌شود |
| `PVAI_WS_TOKENS` | خالی | توکن per-device به شکل `device_id:token,device_id:token`. **ترجیح داده می‌شود**: بستن یک دستگاه بقیه را قطع نمی‌کند. جفت بدون `:` یا توکن تکراری خطای پیکربندی است |
| `PVAI_WS_TOKEN` | خالی | توکن مشترک همهٔ دستگاه‌ها؛ فقط با `PVAI_WS_ALLOW_SHARED_TOKENS=true` پذیرفته می‌شود. با `PVAI_WS_ENABLED=true` یکی از `PVAI_WS_TOKENS`/`PVAI_WS_TOKEN` الزامی است |
| `PVAI_WS_ALLOW_SHARED_TOKENS` | `false` | پذیرش `PVAI_WS_TOKEN` مشترک. با یک توکن برای همه، بستن دسترسی یک دستگاه یعنی بستن همه |
| `PVAI_WS_MAX_CONNECTIONS` | `4` | سقف اتصال هم‌زمان؛ اتصال اضافی `error` کد `too_many_connections` می‌گیرد |
| `PVAI_WS_CONNECT_RATE` | `10` | سقف سراسری اتصال جدید در دقیقه در فرایند سرور |
| `PVAI_WS_FRAME_RATE` | `200` | سقف فریم capture در ثانیه برای هر اتصال (نرخ اسمی ۵۰ = ۲۰ms؛ ۲۰۰ یعنی ۴ برابر). `0` = بی‌سقف |
| `PVAI_WS_HEARTBEAT` | `15` | فاصلهٔ `ping` سرور (ثانیه). `0` = خاموش |
| `PVAI_WS_IDLE_TIMEOUT` | `60` | اگر دستگاه این مدت هیچ پیامی نفرستد اتصال بسته می‌شود. `0` = خاموش |
| `PVAI_WS_PLAYBACK_PACING` | `true` | ارسال فریم پخش با نرخ واقعی. `false` فقط برای اشکال‌زدایی: دستگاه تا ۵ ثانیه بافر می‌کند و بعد از barge-in صدای لغوشده پخش می‌شود |
| `PVAI_WS_PLAYBACK_CREDIT_MS` | `200` | اعتبار اولیهٔ بافر دستگاه (میلی‌ثانیه) تا بین برست‌های سنتز underrun نشود |

- پورت همان `PVAI_PORT` برنامه است؛ مسیر جدا `WS /ws/voice`.
- `/ready` (پشت توکن REST) کلید `ws_voice` را برمی‌گرداند: خاموش / روشن بدون توکن (ناآماده) / آماده.

## ۱۱. دسترسی REST

| متغیر | پیش‌فرض | معنا |
|---|---|---|
| `PVAI_API_TOKEN` | خالی | توکن Bearer برای همهٔ مسیرهای REST جز `/health`. **اجباری** وقتی `PVAI_HOST` غیر-loopback است، وگرنه برنامه در startup خطا می‌دهد |
| `PVAI_API_RATE_LIMIT` | `20` | درخواست REST در ثانیه به‌ازای هر کلاینت (توکن یا نشانی) |
| `PVAI_API_RATE_BURST` | `40` | اندازهٔ برست مجاز پیش از ۴۲۹ |

- وقتی توکن REST تنظیم شده، `/health` از احراز هویت معاف است (OPTIONS نیز معاف است) و فقط `{"status": "ok"}` برمی‌گرداند: بدون نام مدل، بدون مسیر داده.
  جزئیات در `/ready` است؛ با توکن خالی و bind فقط loopback، HTTP بدون احراز هویت کار می‌کند.
- پیش از این `GET /api/sessions/{id}` متن کامل گفتگو را بی‌احراز هویت برمی‌گرداند؛ با
  `PVAI_HOST=0.0.0.0` (که برای دستگاه LAN لازم است) هر کسی در شبکه آن را می‌دید.
- توکن روی شبکهٔ بدون TLS به‌صورت plaintext می‌رود؛ برای شبکهٔ غیرمورد‌اعتماد reverse proxy با
  `https://`/`wss://` بگذارید (بخش ۷ در `docs/PROTOCOL.md`).
- تمایز مهم: `Settings.config_errors()` **دروازهٔ سخت** است (`run_live` را متوقف می‌کند و
  `/ready` را ناآماده می‌کند). `Settings.config_warnings()` ریسک‌های آگاهانه را برمی‌گرداند —
  مثل `ws://` بدون TLS در LAN یا نبودن `PVAI_API_TOKEN` روی loopback — که در لاگ startup و در
  `checks.configuration.warnings` در `/ready` دیده می‌شوند و جلوی اجرا را نمی‌گیرند.

## ۱۲. مشاهده‌پذیری

| متغیر | پیش‌فرض | معنا |
|---|---|---|
| `PVAI_METRICS_ENABLED` | `true` | فعال‌سازی سنجه‌ها (`server/metrics.py`)؛ خاموش = ثبت سنجه‌ها غیرفعال؛ هزینهٔ فراخوانی‌های کد لزوماً صفر نیست |

فهرست نام سنجه‌ها در `server/metrics.py::METRIC_NAMES` است و تست
`tests/test_metrics.py::test_documented_metric_names_cover_the_pipeline` هم‌خوانی آن با کد را بررسی می‌کند.

---

## نمونهٔ `.env` حداقلی برای هدف (i7-6500U)

```dotenv
PVAI_MODEL_PATH=models/Qwen3-4B-Q4_K_M.gguf
PVAI_N_THREADS=2
PVAI_ASR_THREADS=2
PVAI_ASR_PARTIAL_WINDOW_S=3
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


### TLS مستقیم روی API/WS

| متغیر | پیش‌فرض | کاربرد |
|---|---|---|
| `PVAI_SSL_CERTFILE` | خالی | مسیر گواهی TLS برای uvicorn (همراه KEYFILE) |
| `PVAI_SSL_KEYFILE` | خالی | کلید خصوصی TLS؛ در git نگذارید |

هر دو را با هم تنظیم کنید؛ در LAN آدرس `https://` و `wss://` می‌شود.
`PVAI_API_TOKEN` و توکن per-device همچنان اجباری هستند؛ TLS جای احراز هویت نیست.
