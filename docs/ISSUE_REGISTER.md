# دفترچهٔ ایرادها — PersianVoiceAI Conversation Engine

این سند نتیجهٔ بازبینی واقعی کد (نه فرض) است و وضعیت هر ایراد را با تست متناظر نشان می‌دهد.
وضعیت‌ها: `رفع‌شده + تست`، `رفع‌شده + تست ناقص`، `باز`، `نیازمند سخت‌افزار`.

مبنا: commit `9654a68` (پایه)، شاخهٔ `arena/31347dde-persianvoiceai`.
تست پایه پیش از تغییر: `53 passed, 2 skipped` (تست‌های native AEC skip می‌شوند).
تست پس از مرحلهٔ تکمیلی: `237 passed, 2 skipped` (۲ skip فقط native AEC در sandbox بدون کتابخانه).

---

## خلاصهٔ شدت

| شدت | تعداد | موضوع |
|---|---|---|
| P0 | 3 | گم‌شدن نتیجهٔ final ASR؛ هزینهٔ بازشناسی تکراری؛ رقابت منابع CPU |
| P1 | 9 | VAD/پیش‌رول، لغو و ماشین حالت، system prompt، تاریخچه، AEC، دستگاه صوت، TTS، API/readiness، مشاهده‌پذیری |
| P2 | 6 | معیارها، بنچمارک، پنجرهٔ partial، پیام‌های خطا، هم‌ترازی صف‌ها |
| P3 | 3 | نام‌گذاری، مستندات ناهمگام، ورودی‌های کوچک |
| P1 (مرحلهٔ ۲) | 4 | lost-wakeup صف final، گیرکردن حالت در SPEAKING، نرخ اشتباه TTS بدون duplex، baseline غلط CPU |
| P2 (مرحلهٔ ۲) | 2 | نبود اعتبارسنجی نرخ نمونه‌برداری، رشد نامحدود RAM مکالمه |

---

## P0

### P0-01 — نتیجهٔ final ASR پس از شروع گفتار جدید گم می‌شود

- **شدت:** P0
- **فایل:** `server/streaming_voice.py`
- **توابع:** `Pipeline.interrupt`، `Pipeline.recognize`، `Pipeline.offer`، `Pipeline.feed`
- **ریشهٔ ایراد (سه مسیر مستقل):**
  1. `interrupt()` با هر `onset` صدا را پاک می‌کند: `while not self.snapshots.empty(): get_nowait()`.
     اگر final در صف مانده باشد (ASR هنوز آن را برنداشته)، همان‌جا حذف می‌شود.
  2. `recognize()` بعد از decode در thread، `item.generation != self.generation` را چک می‌کند.
     با شروع جملهٔ بعد، `generation` عوض شده و **final معتبر دور ریخته می‌شود**؛ این همان
     سناریوی «جملهٔ A ناپدید می‌شود» است.
  3. `offer()` یک mailbox یک‌تایی مشترک برای partial و final است: partial جملهٔ بعد می‌تواند
     final معلق جملهٔ قبل را بازنویسی کند.
  4. تقسیم بخش در سقف ۱۵ ثانیه بدون onset بعدی، `voiced` را صفر می‌کند و onset بعدی (۳ فریم بعد)
     `interrupt()` را روی final بخش قبلی اجرا می‌کند؛ گفتار طولانی بخش‌های خود را از دست می‌دهد.
- **رفع:** جداسازی کامل `UserUtterance` (شناسهٔ یکتا و افزایشی)، `AssistantGeneration`
  (شمارندهٔ مستقل barge-in)، `PartialASR` (mailbox یک‌جای قابل‌جایگزینی) و `FinalASR`
  (صف FIFO بدون‌افت). لغو پاسخ فقط task پاسخ و پخش را لغو می‌کند و به صف final دست نمی‌زند.
  dedupe با `utterance_id`؛ ثبت کاربر یک‌بار انجام می‌شود.
- **تست:** `tests/test_utterance_lifecycle.py` (۶ سناریوی الزامی) + به‌روزرسانی تست‌های قبلی.
- **وضعیت:** رفع‌شده + تست

### P0-02 — بازشناسی تکراری و پرهزینهٔ ASR

- **شدت:** P0
- **فایل:** `server/streaming_voice.py`, `server/voice.py`
- **ریشهٔ ایراد:** هر ۰٫۸ ثانیه `b"".join(self.detector.frames)` یعنی کل بخش در حال رشد به Whisper
  داده می‌شد؛ هزینهٔ کل تا سقف ۱۵ ثانیه حدوداً درجهٔ دوم رشد می‌کند. یک actor سریالی هم
  یعنی final پشت partial‌های طولانی می‌ماند (head-of-line blocking). هیچ شمارنده/سنجه‌ای نبود.
- **رفع:** پنجرهٔ partial محدود و هم‌پوشان (`PVAI_ASR_PARTIAL_WINDOW_S`, پیش‌فرض ۶ ثانیه)؛
  audio کامل برای final حفظ می‌شود؛ صف final با اولویت مطلق و بدون‌افت؛ حذف partial منسوخ
  هنگام رسیدن final؛ محدودسازی فراخوانی هم‌زمان Whisper به یک actor؛ سنجه‌های
  `asr_ms`, `asr_rtf`, `asr_final_latency_ms`, `asr_partial_latency_ms`, `asr_queue_depth`,
  `asr_dropped_partial_count`, `asr_final_job_count`.
- **تست:** `tests/test_asr_scheduler.py`, `tests/test_metrics.py`
- **وضعیت:** رفع‌شده + تست (کیفیت/سرعت واقعی وزن‌ها سنجیده نشده: **NOT MEASURED**)

### P0-03 — رقابت منابع روی CPU دوهسته‌ای

- **شدت:** P0
- **فایل:** `server/voice.py`, `server/config.py`, `server/streaming_voice.py`
- **ریشهٔ ایراد:** `cpu_threads=self.settings.n_threads or 4`؛ با `PVAI_N_THREADS=0` یعنی ۴ thread
  برای Whisper روی ماشینی با ۲ هستهٔ فیزیکی، هم‌زمان با LLM و Piper. هیچ بودجهٔ thread،
  هیچ اندازه‌گیری بار و هیچ backpressure قابل‌مشاهده‌ای وجود نداشت.
- **رفع:** `PVAI_ASR_THREADS` جدا از `PVAI_N_THREADS` با پیش‌فرض ۲؛ تست بار و سنجهٔ
  CPU/RAM (`server/resources.py`, `tools/benchmark_voice.py`)؛ صف‌های محدود با شمارش عمق؛
  AEC خارج از callback؛ اندازه‌گیری jitter callback.
- **تست:** `tests/test_resources.py`, `tests/test_audio_io.py`
- **وضعیت:** رفع‌شده + تست؛ **اعداد واقعی i7-6500U اندازه‌گیری نشده: NOT MEASURED**

---

## P1

### P1-01 — system prompt در مسیر صوتی ارسال نمی‌شد

- **فایل:** `server/streaming_voice.py` → `respond`
- **ریشه:** payload فقط `messages` تاریخچه + پیام کاربر بود؛ `Settings.system_prompt` هرگز
  در مسیر زنده به مدل نمی‌رسید (فقط مسیر متنی از `LlamaServerClient` آن را تزریق می‌کرد).
- **رفع:** `messages = [system] + context` در مسیر صوتی؛ قابل تنظیم با `PVAI_SYSTEM_PROMPT`.
- **تست:** `tests/test_utterance_lifecycle.py::test_system_prompt_is_sent`
- **وضعیت:** رفع‌شده + تست

### P1-02 — نبود ماشین حالت و یکسان‌بودن لغو در پنج وضعیت

- **فایل:** `server/streaming_voice.py`
- **ریشه:** `feed()` با هر onset بی‌قید `interrupt()` را صدا می‌زد: هنگام بی‌کاری، هنگام ASR،
  هنگام تولید LLM، هنگام سنتز TTS و هنگام پخش. نتیجه: لغو کارهای بی‌ربط و از دست رفتن final.
- **رفع:** `VoiceState` صریح (`IDLE`, `LISTENING`, `TRANSCRIBING`, `THINKING`, `SPEAKING`) با
  جدول گذار قابل‌تست؛ barge-in فقط وقتی فعالیت دستیار وجود دارد؛ گفتار هنگام ASR فقط
  utterance جدید می‌سازد.
- **تست:** `tests/test_state_machine.py`, `tests/test_utterance_lifecycle.py`
- **وضعیت:** رفع‌شده + تست

### P1-03 — VAD انرژی با آستانهٔ ثابت، بدون هیسترزیس و پیش‌رول

- **فایل:** `server/live_voice.py` → `Endpoint`
- **ریشه:** آستانهٔ واحد، `voiced >= 3` سخت‌کد، حداقل ۸ فریم، بدون پیش‌رول واقعی
  (حداکثر ۲ فریم قبل از onset)، بدون تطبیق نویز، سکوت ۵۰۰ms ثابت؛ در سقف ۱۵ ثانیه فریم‌های
  ابتدای بخش بعد دور ریخته می‌شد.
- **رفع:** `server/vad.py` با `EnergyVAD`: هیسترزیس onset/offset، پیش‌رول حلقه‌ای، حداقل
  گفتار قابل تنظیم، کف نویز تطبیقی، پارامترهای کامل از `.env`؛ کلاس `Endpoint` سازگار
  با API قبلی باقی مانده است.
- **تست:** `tests/test_vad.py`
- **وضعیت:** رفع‌شده + تست (صوت واقعی میکروفون **NOT TESTED**)

### P1-04 — TTS تک‌صدا و بدون سنجهٔ تأخیر

- **فایل:** `server/live_voice.py`, `server/config.py`
- **ریشه:** تنها `Settings.tts_model_path`؛ بدون انتخاب مرد/زن؛ `tts_start` زمان اولین صدای
  واقعی نبود؛ خطاها به پیام عمومی تبدیل می‌شدند؛ پایان پخش با «تحویل به صف» اشتباه می‌شد.
- **رفع:** `PVAI_TTS_VOICE=male|female` + مسیر جدا برای هر صدا، اعتبارسنجی فایل
  `.onnx`/`.onnx.json` در startup، callbackهای اولین chunk و اولین فریم و مدت پخش، خطای
  قابل‌فهم برای دستگاه/مدل.
- **تست:** `tests/test_tts.py`
- **وضعیت:** رفع‌شده + تست؛ کیفیت تلفظ فارسی با وزن واقعی **NOT MEASURED**

### P1-05 — تاریخچهٔ مکالمه ناقص و ناسازگار با مستندات

- **فایل:** `server/streaming_voice.py`, `server/voice.py`
- **ریشه:** فقط RAM، برش بر اساس تعداد پیام (`[-10:]` هنگام درخواست و `[-12:]` هنگام ثبت)،
  پیام کاربر اگر پاسخ شکست می‌خورد هرگز ثبت نمی‌شد، پاسخ قطع‌شده بدون وضعیت دور ریخته
  می‌شد، session/conversation id وجود نداشت، امکان بازبینی/حذف نبود.
- **رفع:** `server/conversation.py` با `Message` (وضعیت `complete|interrupted|failed`)،
  `Conversation` (dedupe بر اساس utterance، برش token-aware)، `ConversationStore`
  (JSONL محلی، قابل خاموش‌کردن، list/delete) و CLI `python -m server.sessions`.
- **تست:** `tests/test_conversation.py`
- **وضعیت:** رفع‌شده + تست

### P1-06 — API بدون readiness و بدون جداسازی نشست

- **فایل:** `server/main.py`
- **رفع:** `/ready` با وضعیت مدل/TTS/ASR/AEC/وابستگی‌ها، `/health` توسعه‌یافته،
  `/api/sessions` (list/show/delete) و پارامتر `session_id` در `/api/chat`.
- **تست:** `tests/test_api.py`
- **وضعیت:** رفع‌شده + تست

### P1-07 — مدیریت دستگاه صوتی

- **فایل:** `server/streaming_voice.py` (`run_live`)
- **ریشه:** فقط دستگاه پیش‌فرض سیستم، فقط 16k، بدون `check_input_settings`، بدون تشخیص
  قطع دستگاه؛ خطای callback فقط `overflow` می‌شد و حلقه بی‌پایان خطا تولید می‌کرد.
- **رفع:** `server/audio_io.py`: انتخاب ورودی/خروجی با نام یا index، اعتبارسنجی نرخ/کانال،
  پیام خطای فارسی با فهرست دستگاه‌ها، watchdog قطع جریان و بازگشایی محدود.
- **تست:** `tests/test_audio_io.py`
- **وضعیت:** رفع‌شده + تست مصنوعی؛ قطع/وصل دستگاه واقعی **NOT TESTED**

### P1-08 — AEC: تأخیر، فریم نامعتبر و نبود وضعیت در startup

- **فایل:** `server/aec.py`, `server/streaming_voice.py`
- **ریشه:** `process()` برای هر فریم غیر ۶۴۰ بایت `ValueError` می‌دهد؛ اگر دستگاه blocksize را
  رعایت نکند، لاگ با خطای تکراری پر می‌شود به‌جای پیام عملی. وضعیت AEC در readiness نبود،
  تأخیر پیش‌فرض بدون هشدار استفاده می‌شد و زمان پردازش سنجیده نمی‌شد.
- **رفع:** `aec_status()` برای readiness/startup، اعتبارسنجی طول فریم با پیام قابل‌فهم،
  سنجهٔ `aec_process_ms`، `PVAI_AEC_DELAY_MS` مستند، حفظ حالت صریح‌خطا (بدون bypass خاموش).
- **تست:** `tests/test_aec.py`
- **وضعیت:** رفع‌شده + تست مصنوعی؛ حذف اکوی اتاق واقعی **NOT TESTED**

### P1-09 — مشاهده‌پذیری

- **رفع:** `server/metrics.py` (شمارنده/gauge/خلاصهٔ هیستوگرام بدون رشد نامحدود) + رویداد
  `metrics` در خط لوله + `asr_*`, `llm_*`, `tts_*`, `barge_in_*`, `audio_callback_jitter_ms`,
  `cpu_usage_percent`, `ram_usage_mb`, `queue_depth`.
- **تست:** `tests/test_metrics.py`
- **وضعیت:** رفع‌شده + تست

---

## P2 / P3

| شناسه | شدت | موضوع | وضعیت |
|---|---|---|---|
| P2-01 | P2 | نبود هیچ سنجه‌ای در مسیر صوت | رفع‌شده + تست |
| P2-02 | P2 | نبود بنچمارک زنجیرهٔ صوت | رفع‌شده (`tools/benchmark_voice.py`، ۶ سناریو، برچسب MEASURED/SIMULATED/NOT MEASURED، تست `test_benchmark_tools.py`)؛ اعداد واقعی هدف NOT MEASURED |
| P2-03 | P2 | `agreement()` بدون لنگر پنجره | رفع‌شده: پیشوند فقط وقتی پنجره ثابت است |
| P2-04 | P2 | `pending_history` ویژگی پویا | رفع‌شده (`AssistantTurn` صریح) |
| P2-05 | P2 | `vad_filter=True` اجباری Whisper | رفع‌شده و قابل تنظیم |
| P2-06 | P2 | ناسازگاری `[-10:]`/`[-12:]` | رفع‌شده: `PVAI_HISTORY_MAX_MESSAGES` + بودجهٔ token |
| P3-01 | P3 | نام `Snapshot`/انتساب‌های پویا | رفع‌شده |
| P3-02 | P3 | مستندات ادعای «شش نوبت» و «۵۴ تست» بدون به‌روزرسانی | رفع‌شده در `docs/STATUS.md` |
| P3-03 | P3 | نبود نرمال‌سازی فارسی و سنجهٔ WER/CER | رفع‌شده (`server/persian.py`, `benchmarks/persian_eval.py`, مجموعهٔ ۴۰ جملهٔ `benchmarks/persian_eval_set.json`)؛ اعداد واقعی NOT MEASURED |

---

## موارد باز (بدون رفع در این مرحله)

| شناسه | شدت | موضوع | دلیل |
|---|---|---|---|
| OPEN-01 | P1 | توقف قطعی inference در حال اجرا (thread درجا قابل kill نیست) | نیازمند process isolation؛ خارج از دامنهٔ این مرحله |
| OPEN-02a | — | transport سمت سرور WebSocket صوتی + احراز هویت دستگاه | **رفع‌شده** (`server/ws_voice.py`، ۱۴ تست در `tests/test_ws_voice.py`، متریک‌های `ws_*`، کلید `ws_voice` در `/ready`) |
| OPEN-02b | P1 | firmware ESP32-S3، رجیستری دستگاه و OTA | هیچ کد سخت‌افزاری در مخزن نیست؛ تا پاس‌شدن پذیرش PC شروع نمی‌شود |
| OPEN-03 | P1 | صحت اکوی اتاق واقعی و تأخیر دستگاه | نیازمند سخت‌افزار |
| OPEN-04 | P2 | VAD عصبی محلی (Silero/WebRTC) | وابستگی جدید بدون سنجش؛ حالت انرژی با هیسترزیس فعلی حفظ شد |
| OPEN-05 | P2 | Command Broker / IoT و OTA | خارج از دامنهٔ موتور مکالمه |


---

## مرحلهٔ تکمیلی — ایرادهای تازه‌یافته (۸ نوامبر ۲۰۲۶)

این موارد در بازبینی همان کدِ رفع‌شده پیدا شدند، نه در پایه. همه با تست بازگشتی پوشش داده شده‌اند.

### P1-10 — lost wakeup در `AsrScheduler.next()`: final می‌توانست برای همیشه در صف بخوابد

- **فایل:** `server/streaming_voice.py` → `AsrScheduler.next`
- **ریشه:** ترتیب `بررسی صف‌ها → self._wakeup.clear() → await wait()`. اگر `submit_final` دقیقاً
  در همین پنجره اجرا می‌شد، پرچم را set می‌کرد و actor بلافاصله پاکش می‌کرد؛ بعد تا submit بعدی
  (که ممکن بود هرگز نیاید) خواب می‌ماند. همان علامتِ «جمله ناپدید شد»، با علت متفاوت.
- **رفع:** ابتدا `clear()` و سپس بررسی مجدد صف (`if self.finals or partial: continue`).
- **تست:** `tests/test_asr_scheduler.py::test_submit_landing_between_check_and_wait_is_not_lost`
  (با پیاده‌سازی قدیم deterministic timeout می‌دهد — تأیید شده) و `test_next_waits_then_returns_a_later_final`.
- **وضعیت:** رفع‌شده + تست

### P1-11 — حالت در `SPEAKING` گیر می‌کرد و هر گفتار بعدی «barge-in» تلقی می‌شد

- **فایل:** `server/streaming_voice.py` → `Pipeline.play`
- **ریشه:** نشانگر commit (آیتم با متن خالی) آخرین قلم صف پخش است. شرط گذار به
  `LISTENING/IDLE` در شاخهٔ جملهٔ واقعی `reply.done()` را می‌خواست که در آن لحظه هنوز false بود،
  و شاخهٔ نشانگر هیچ گذاری نداشت. نتیجه: `state` تا ابد `SPEAKING`.
- **اثر:** `_begin_utterance` از حالت SPEAKING به LISTENING نمی‌رود، `_maybe_barge_in` هر onset را
  barge-in می‌بیند، و وضعیت گزارش‌شده به کاربر/دستگاه دروغ است.
- **رفع:** در مصرف نشانگر، اگر صف خالی و `pending_turn is None` باشد گذار به LISTENING/IDLE.
- **تست:** `tests/test_state_machine.py::test_pipeline_walks_the_documented_states_for_one_exchange`.
- **وضعیت:** رفع‌شده + تست

### P1-12 — پخش TTS بدون duplex با نرخ اشتباه (۲۲٫۰۵k داخل stream 16k)

- **فایل:** `server/live_voice.py` → `Speaker._chunk_pcm`, `_open_output`
- **ریشه:** مسیر بدون duplex بایت‌های بومی chunk را می‌نوشت در حالی که stream با
  `PVAI_AUDIO_SAMPLE_RATE` باز می‌شد؛ وزن‌های فارسی Piper معمولاً ۲۲٫۰۵kHz هستند، یعنی پخش
  ~۲۷٪ سریع و با زیروبمی غلط. مسیر duplex (AEC) درست بود، پس باگ فقط در حالت تک‌مسیره دیده می‌شد.
- **رفع:** `_to_mono_rate(chunk, target)`؛ وقتی نرخ/کانال با stream یکی است بایت خام (بدون numpy)،
  وگرنه resample به نرخ stream.
- **تست:** `tests/test_tts.py::test_speaker_only_path_resamples_piper_rate_to_the_output_stream`،
  `test_native_rate_chunk_is_written_verbatim_without_resampling`.
- **وضعیت:** رفع‌شده + تست (کیفیت شنیداری با گوش: NOT TESTED)

### P1-13 — baseline غلط سنجهٔ CPU در Linux (اعداد میلیون‌درصدی)

- **فایل:** `server/resources.py` → `_LinuxSampler.__init__`
- **ریشه:** `_last_cpu = 0.0` به‌جای tick فعلی؛ اولین نمونه، کل CPU زمان‌عمر پروسه را بر چند
  میکروثانیه تقسیم می‌کرد. در اولین اجرای بنچمارک `cpu_usage_percent = 1,956,947.20` ثبت شد —
  یعنی سنجه‌ای که برای تصمیم‌گیری بود، بی‌معنا. (پیدا شدنش مدیون همین ابزار بنچمارک است.)
- **رفع:** prime کردن baseline با `_total_ticks()`؛ بنچمارک هم یک sampler پایدار برای نمونهٔ قبل/بعد
  نگه می‌دارد.
- **تست:** `tests/test_resources.py::test_fresh_sampler_first_reading_is_plausible`.
- **وضعیت:** رفع‌شده + تست

### P2-07 — نبود اعتبارسنجی نرخ نمونه‌برداری در مسیر زنده

- **فایل:** `server/config.py` → `config_errors`
- **ریشه:** `PVAI_AUDIO_SAMPLE_RATE=48000` پذیرفته می‌شد؛ VAD/Whisper قرارداد 16k دارند و متن
  بی‌صدا غلط می‌شد (بازنمونه‌برداری خودکار پیاده نیست). AEC هم فقط 16k را می‌پذیرد.
- **رفع:** خطای پیکربندی صریح فارسی؛ در `/ready` و startup دیده می‌شود.
- **تست:** `tests/test_tts.py::test_non_16k_capture_rate_is_a_configuration_error`.
- **وضعیت:** رفع‌شده + تست

### P2-08 — رشد نامحدود RAM در مکالمهٔ طولانی (الزام ۲۰)

- **فایل:** `server/conversation.py`
- **ریشه:** `Conversation.messages` append-only در RAM بود، مثل `_utterance_ids`. یک دستیار ۲۴/۷
  می‌توانست ساعت‌ها حافظه جمع کند؛ `Pipeline` هم `max_stored_messages` تنظیمات را منتقل نمی‌کرد.
- **رفع:** پنجرهٔ RAM با `PVAI_HISTORY_MAX_STORED` (پیش‌فرض ۵۰۰) که فایل JSONL روی دیسک را
  ناقص نمی‌کند؛ `_utterance_ids` با deque محدود (۲۰۴۸) و eviction؛ انتقال تنظیمات در `Pipeline`.
- **تست:** `tests/test_conversation.py::test_in_memory_window_is_bounded_while_the_disk_log_stays_complete`،
  `test_utterance_dedupe_window_is_bounded`، `tests/test_long_run.py` (۱۲۰ نوبت، بررسی کرانه‌ها در هر نوبت).
- **وضعیت:** رفع‌شده + تست

### P1-14 — پاسخ قطع‌شده در جملهٔ اول، کل متن تولیدشده را «شنیده‌شده» ثبت می‌کرد

- **فایل:** `server/streaming_voice.py` → `_commit_turn`, `play`
- **ریشه:** شرط `if status == INTERRUPTED and turn.spoken:` وقتی هیچ جمله‌ای کامل نشده بود
  (قطع در جملهٔ اول — رایج‌ترین حالت barge-in) به متن کامل `turn.text` برمی‌گشت؛ یعنی جمله‌هایی
  که هرگز پخش نشدند وارد تاریخچه و (در صورت `PVAI_HISTORY_INCLUDE_INTERRUPTED`) وارد context
  مدل می‌شدند. کامنت کد خلاف این را ادعا می‌کرد.
- **رفع:** `AssistantTurn.current` جملهٔ در حال پخش را نگه می‌دارد؛ ثبت سه‌حالته شد:
  جمله‌های کامل‌شده → همان‌ها؛ جملهٔ آغازشده → همان؛ هیچ صدایی نبود (قطع حین thinking) →
  متن تولیدشده فقط برای بازبینی (خارج از context).
- **تست:** `tests/test_bargein.py::test_sentences_queued_behind_the_spoken_one_never_play_after_barge_in`
  (همچنین ثابت می‌کند جمله‌های صف‌شده بعد از barge-in هرگز سنتز نمی‌شوند).
- **وضعیت:** رفع‌شده + تست

### P1-15 — سنجه‌های سیستمی در مسیر زنده هرگز ثبت نمی‌شدند

- **فایل:** `server/audio_io.py` → `pump`
- **ریشه:** `audio_callback_jitter_ms`, `queue_depth`, `cpu_usage_percent`, `ram_usage_mb` فقط
  نام‌های اعلام‌شده در `METRIC_NAMES` بودند؛ در اجرای واقعی هیچ‌جا مقدار نمی‌گرفتند، پس سؤال
  «آیا ماشین اشباع شده؟» بدون اجرای بنچمارک آفلاین پاسخ نداشت.
- **رفع:** انتشار دوره‌ای (پیش‌فرض ۱ ثانیه) از `duplex.stats()`، عمق صف capture+speech و
  نمونهٔ CPU/RAM با یک sampler پایدار؛ شمارش تضاعفی `audio_callback_count`.
- **تست:** `tests/test_audio_io.py::test_pump_publishes_system_and_queue_metrics`.
- **وضعیت:** رفع‌شده + تست (اعداد واقعی دستگاه: NOT MEASURED)

### P1-16 — خطای LLM، ماشین حالت را در `THINKING` رها می‌کرد

- **فایل:** `server/streaming_voice.py` → `Pipeline.respond` (شاخهٔ exception)
- **ریشه:** پاسخ ناموفق نشانگر commit در صف پخش نمی‌گذارد؛ هیچ مسیری هم حالت را عوض نمی‌کرد.
  نتیجه: `state=THINKING` بدون هیچ `pending_turn`، و گفتار بعدی کاربر به‌عنوان barge-in
  «نجات» می‌داد — با یک رویداد `interrupted` گمراه‌کننده و شمارندهٔ generation اضافی.
- **رفع:** پس از commit با وضعیت `failed`، اگر نوبتی معلق نمانده و صف پخش خالی است، گذار به
  `LISTENING`/`IDLE` با دلیل `llm_error` انجام می‌شود؛ نوبت بعدی مستقیماً پاسخ می‌گیرد.
- **تست:** `tests/test_streaming_voice.py::test_llm_failure_releases_the_state_and_the_next_turn_recovers`
  (پاسخ ۵۰۰ → `error`، وضعیت آزاد، نوبت بعدی موفق و بدون `state_violation`).
- **وضعیت:** رفع‌شده + تست

### P2-09 — بنچمارک، هفت گروه الزامی را پوشش نمی‌داد

- **فایل:** `tools/benchmark_voice.py`
- **ریشه:** فقط idle/asr/llm/tts/combined/bargein وجود داشت؛ «ASR+LLM» و «مکالمهٔ کامل دوطرفه»
  جدا اندازه‌گیری نمی‌شدند.
- **رفع:** سناریوهای `asr_llm`، `asr_llm_tts` و `duplex` (چند نوبت + یک قطع، با شمارش پیام‌های
  گم‌نشده)؛ `combined` به‌عنوان نام تاریخی حفظ شد.
- **تست:** `tests/test_benchmark_tools.py::test_benchmark_scenarios_are_declared`.
- **وضعیت:** رفع‌شده + تست؛ اعداد واقعی هدف NOT MEASURED

### P3-04 — `describe()` آستانهٔ مؤثر را گزارش نمی‌کرد

- **فایل:** `server/vad.py` → `describe`
- **ریشه:** `offset_level` مقدار پایه را نشان می‌داد، نه `continuation_level` بعد از کف نویز تطبیقی؛
  کالیبراسیون در اتاق نویزی گمراه‌کننده بود.
- **رفع:** گزارش `continuation_level`.
- **تست:** `tests/test_vad.py::test_describe_reports_the_effective_continuation_level`.
- **وضعیت:** رفع‌شده + تست

### مستندات و ابزارهای تکمیل‌شده در همین مرحله

| مورد | وضعیت |
|---|---|
| `docs/CONFIGURATION.md` (مرجع کامل ۴۸ متغیر `PVAI_*`) | افزوده شد (قبلاً `config.py` به آن ارجاع می‌داد ولی وجود نداشت) |
| `docs/PROTOCOL.md` (قرارداد WebSocket/ESP32) | به‌روزرسانی شد: transport سرور پیاده‌شده، firmware همچنان نشده |
| `docs/TROUBLESHOOTING.md` | افزوده شد؛ نگاشت پیام واقعی کد به راه‌حل |
| `docs/HARDWARE_VALIDATION.md` | افزوده شد؛ چک‌لیست ۱۴ مرحله‌ای سناریوی پذیرش |
| `docs/BENCHMARKS.md` | افزوده شد؛ روش‌شناسی + جدول sandbox با برچسب + NOT MEASURED هدف |
| `docs/STATUS.md`, `docs/STREAMING_ARCHITECTURE.md` | بازنویسی؛ ادعاهای کهنه (۵۴ تست، «۶ نوبت در RAM»، «VAD پیاده‌نشده»، «حذف نتیجهٔ ASR منسوخ») حذف شد |
| `tests/test_state_machine.py` | افزوده شد (در سند قبلی ادعا شده بود ولی وجود نداشت) |
| `benchmarks/persian_eval.py` + `persian_eval_set.json` | افزوده شد (ادعای قبلی بدون فایل بود) |
| `tools/benchmark_voice.py` | افزوده شد (ادعای قبلی بدون فایل بود) |
| `.env.example` | تکمیل شد تا هر متغیر پیکربندی واقعی را نشان دهد |
| `docs/SPEC.md` | بخش ۱۷ افزوده شد (قرارداد موتور مکالمه ۱.۱)؛ قبلاً در این مرحله به‌روز نشده بود |
| `docs/AUDIT_REPORT.md` | گزارش نهایی A–F داخل مخزن (قبلاً فقط در پاسخ چت بود) |
| `tests/test_bargein.py` | تست‌های اختصاصی صدای کهنه و اکوی پخش |

---

## مرحلهٔ تکمیلی — transport صوتی LAN (۸ نوامبر ۲۰۲۶)

### ن-۱ — `play_end` پیش از فریم‌های صوتی ارسال می‌شد (نوبت چندجمله‌ای)

- **فایل:** `server/ws_voice.py` → نگاشت event‌ها و `_sender`
- **ریشه:** `tts_end` (مرز *جمله*) به `play_end` (مرز *نوبت*) نگاشت می‌شد و `_sender` همیشه کنترل را
  بر صدا مقدم می‌داشت. نتیجه: دستگاه «پایان پخش» را قبل از رسیدن بایت‌های همان جمله می‌گرفت و در
  پاسخ چندجمله‌ای چند `play_end` می‌دید.
- **رفع:** `play_end` فقط از `response_end` با `status=complete` ساخته می‌شود، `played_ms` از تعداد
  بلوک‌های تولیدشدهٔ همان نوبت می‌آید، و `_sender` تا خالی‌نشدن صف پخش، `play_end` را عقب می‌اندازد.
- **تست:** `tests/test_ws_voice.py::test_playback_frames_are_valid_protocol_frames` و
  `::test_one_play_end_per_turn_not_per_sentence`

### ن-۲ — پیام خطای دست‌دهی هرگز به دستگاه نمی‌رسید

- **فایل:** `server/ws_voice.py` → `_handshake`
- **ریشه:** خطا با `_queue_control` در صف می‌رفت، اما task فرستنده فقط بعد از دست‌دهی موفق شروع
  می‌شد؛ پس دستگاه فقط close code ۱۰۰۸ می‌دید، بدون دلیل.
- **رفع:** `_reject()` پیام خطا را مستقیم می‌فرستد و بعد می‌بندد.
- **تست:** چهار تست رد دست‌دهی در `tests/test_ws_voice.py`.

### ن-۳ — `interrupted` بدون `turn_id` و بدون پاک‌سازی صف پخش

- **فایل:** `server/streaming_voice.py` (`barge_in`)، `server/ws_voice.py`
- **ریشه:** event «interrupted» شناسهٔ نوبت را حمل نمی‌کرد، بنابراین `play_stop` با `turn_id=0` می‌رفت؛
  و صف پخش دستی خالی نمی‌شد، پس ممکن بود فریمی از نوبت لغوشده بعد از `play_stop` برود.
- **رفع:** `turn_id` به event اضافه شد و `ws_voice` پیش از ارسال `play_stop` صف را drain می‌کند.
- **تست:** `::test_device_interrupt_cancels_playback_and_reports_play_stop`،
  `::test_cancelled_playback_frames_are_dropped_before_play_stop`

### ن-۴ — قطع کلاینت SSE پایان‌دهی به تولید را تست نکرده بود (OPEN-03 سابق)

- **رفع:** تست ASGI سطح‌پایین `tests/test_api.py::test_sse_client_disconnect_cancels_upstream_generation`
  پس از `http.disconnect` بررسی می‌کند که generator بالادست بسته و تولید متوقف شود.

---

## مرحلهٔ سوم — بازتولید و رفع چهار باگ (۸ نوامبر ۲۰۲۶)

هر چهار مورد **ابتدا با اجرای واقعی بازتولید شد** (اسکریپت بازتولید، خروجی ثبت‌شده)، بعد رفع و
تست شد. فایل‌های تغییر‌یافته: `server/vad.py`, `server/streaming_voice.py`, `server/metrics.py`.

### B1-01 — پاسخ دستیار بی‌صدا گم می‌شد (نویز کوتاه، utterance ردشده)

- **فایل:** `server/vad.py` (`EnergyVAD._finish`), `server/streaming_voice.py` (`feed`, `_on_final`)
- **ریشه:** `_finish` گفتار کوتاه‌تر از `min_voiced_frames` (پیش‌فرض ۸ فریم = ۱۶۰ms) را دور
  می‌ریخت و فقط `(False, None)` برمی‌گرداند — همان چیزی که «هیچ اتفاقی نیفتاد» را هم نشان
  می‌داد. ولی onset از ۳ فریم فعال شده بود و `_begin_utterance` یک utterance ساخته،
  `_latest_utterance_id` را بالا برده و state را `LISTENING` کرده بود. نتیجهٔ بازتولید‌شده:
  صدا زدن `feed_utterance(speech=20, silence=32)` سپس `feed_utterance(speech=4, silence=32)`
  با ASR مسدود → فقط `response_skipped`، بدون `tts_start`، `state=listening` و
  `utterance.id=2` که هرگز بسته نمی‌شد.
- **رفع:**
  - `EnergyVAD.feed_event()` مرز صریح برمی‌گرداند: `onset` / `segment` / `rejected`
    (‏`feed()` قدیمی سازگار ماند). `rejected_utterances` هم شمرده می‌شود.
  - `Pipeline._reject_utterance()`: utterance لغو، partial دور ریخته، رویداد
    `utterance_rejected` منتشر، و `_settle_after_utterance()` به `IDLE` (یا `TRANSCRIBING`
    اگر final در صف است) برمی‌گردد.
  - `_latest_utterance_id` فقط در `_on_final` با متن معتبر جلو می‌رود؛ onset دیگر آن را
    بالا نمی‌برد. skip فقط وقتی رخ می‌دهد که نوبت جدیدتر **پاسخ گرفته باشد**
    (`response_skipped` با `reason=newer_final_answered`).
  - utterance بعدی که خالی/ناموفق بود در `_pending_answers` می‌ماند (کران‌دار) و
    `start_next_pending()` پاسخ آخرین پیام بی‌پاسخ را می‌دهد (`response_recovered`)،
    با تشخیص «قبلاً پاسخ گرفته» از خود `Conversation` تا پاسخ تکراری ساخته نشود.
- **تست:** `tests/test_short_noise_utterance.py` (۳ سناریوی الزامی: نویز کوتاه، نویز با ASR
  خالی، نویز بعد از final + دو حالت تکمیلی)، `tests/test_vad.py` (دو تست مرز صریح)،
  و به‌روزرسانی `test_final_asr_in_flight_survives_new_speech_onset` (که قبلاً رفتار باگ‌دار
  را assert می‌کرد).
- **وضعیت:** رفع‌شده + تست

### B2-01 — مسیر exception در `Pipeline.play` نوبت را در `SPEAKING` رها می‌کرد

- **فایل:** `server/streaming_voice.py` → `Pipeline.play`
- **ریشه:** شاخهٔ exception فقط `_commit_turn(..., failed)` می‌کرد، `token.set()` می‌زد و
  `continue` می‌کرد؛ state در `SPEAKING` می‌ماند و `turn.current` هم (اگر جمله نیمه‌کاره بود)
  پاک نمی‌شد. بازتولید: `state=speaking`، و گفتار بعدی رویداد `interrupted` با `turn_id=0`
  تولید می‌کرد.
- **ریشهٔ دوم (همان اجرا پیدا شد):** توکن سراسری `self.cancel` set می‌شد و جایگزین نمی‌شد؛
  هر پاسخ بعدی در اولین `token.is_set()` بی‌صدا abort می‌شد — یعنی یک خطای TTS دستیار را
  برای بقیهٔ نشست لال می‌کرد.
- **رفع:** مسیر TTS و مسیر LLM هر دو از `_fail_turn()` می‌گذرند: commit یک‌بار با وضعیت
  `failed`، پاک‌کردن `turn.current`، `turn.token.set()`، جایگزینی `self.cancel` اگر set باشد،
  حذف نشانگرهای مردهٔ همان نوبت از صف پخش، و `_release_after_failure()` برای گذار به
  `LISTENING`/`IDLE` وقتی صف خالی و `pending_turn is None` است.
- **تست:** `tests/test_turn_failures.py::test_tts_failure_leaves_speaking_and_is_not_a_barge_in`,
  `::test_conversation_recovers_after_a_tts_failure`
- **وضعیت:** رفع‌شده + تست

### B3-01 — `respond` با پیام‌های خالی: نه commit، نه خروج از `THINKING`

- **فایل:** `server/streaming_voice.py` → `Pipeline.respond`
- **ریشه:** `if not messages: return` قبل از هر commit/گذار. `pending_turn` می‌ماند، state روی
  `THINKING` قفل می‌شد و هیچ `response_end` برای نوبتی که `start_reply` اعلام کرده بود منتشر
  نمی‌شد. (مسیر `context_with_system` که exception بدهد هم چون خارج از `try` بود، task را با
  استثنای خام می‌کشت.)
- **رفع:** هر دو حالت (context خالی، خطای ساخت تاریخچه) به `_fail_turn()` با دلیل صریح
  (`empty_context` / `history_error`) می‌روند: `response_end` با وضعیت `failed`، پاک‌شدن
  `pending_turn`، خروج از `THINKING`، و رویداد `error` قابل‌فهم. هیچ درخواستی به مدل نمی‌رود.
- **تست:** `tests/test_turn_failures.py::test_empty_context_fails_the_turn_instead_of_wedging_thinking`,
  `::test_history_failure_is_a_failed_turn_not_a_stuck_state`,
  `::test_empty_context_turn_does_not_block_the_next_utterance`
- **وضعیت:** رفع‌شده + تست

### P0-04 — final پشت partial در حال decode معطل می‌ماند (بدون سنجه و بدون راه فرار)

- **فایل:** `server/streaming_voice.py` → `AsrScheduler`, `Pipeline.recognize`
- **ریشه:** یک actor تنها برای Whisper (الزام CPU دوهسته‌ای) یعنی partialی که شروع شده تا پایان
  decode خودش (تا `PVAI_ASR_PARTIAL_WINDOW_S` = ۶ ثانیه صوت) قابل توقف نبود؛ final پشت آن
  می‌ماند. تأخیر هم قابل انتساب نبود: `asr_final_latency_ms` انتظار صف، انتظار پشت partial و
  خود decode را در یک عدد جمع می‌کرد.
- **رفع:**
  - `AsrJob.started_at` + سه سنجه: `asr_final_dispatch_wait_ms`،
    `asr_final_wait_behind_partial_ms`، `asr_final_decode_ms` (به‌علاوهٔ
    `asr_partial_decode_ms`, `asr_partial_wasted_ms`).
  - `AsrScheduler.preempt()`/`begin()`/`end()`: به‌محض صف‌شدن final، توکن partial در حال اجرا
    set می‌شود؛ backendی که `transcribe_pcm(pcm, token, job)` را می‌پذیرد همان‌جا متوقف
    می‌شود (`asr_partial_preempted`/`asr_partial_cancelled`). backend قدیمی
    `transcribe_pcm(pcm)` سازگار می‌ماند: نتیجه‌اش دور ریخته می‌شود و همان تأخیر ثبت می‌شود.
  - partial منسوخ (final همان utterance یا جدیدتر در صف) در `submit_partial` رد می‌شود.
  - flag فقط با «همان jobی که preempt شده» تفسیر می‌شود، پس یک partial سالم که هم‌زمان با
    رسیدن partial جدید تمام شده، دور ریخته نمی‌شود.
- **تست:** `tests/test_asr_preemption.py` (۵ تست). عدد SIMULATED در sandbox: با partial
  ۲ ثانیه‌ای، پاسخ از **۱۹۶۵ms به ۳ms** رسید. عدد دستگاه واقعی **NOT MEASURED**.
- **وضعیت:** رفع‌شده + تست (SIMULATED)

---

## مرحلهٔ چهارم — بازتولید و رفع چهار باگ مسیر صوتی (۸ نوامبر ۲۰۲۶)

فایل‌ها: `server/audio_io.py` (`stream_forever`, `pump`)، `server/aec.py` (`DuplexAudio`)،
`server/streaming_voice.py` (`Pipeline.reset`). هر چهار مورد **اول بازتولید شد** (خروجی در متن)،
بعد رفع و تست شد. تست‌ها: `tests/test_audio_io.py` (+۱۱ تست جدید).

### A1-01 — حلقهٔ بازگشایی بی‌پایان برای دستگاهی که باز می‌شود ولی فریم نمی‌دهد

- **فایل:** `server/audio_io.py` → `stream_forever`
- **ریشه:** `attempts = 0` بلافاصله داخل `with sd.RawStream(...)` اجرا می‌شد، یعنی به‌محض
  موفقیت `__enter__` و **قبل** از آنکه معلوم شود دستگاه واقعاً صدا می‌دهد. میکروفون mute،
  دستگاه exclusive یا هدست بلوتوثی که کانال ورودی را واگذار کرده، هر بار `AudioStalled`
  می‌داد و هر بار شمارنده صفر می‌شد؛ پس `PVAI_AUDIO_REOPEN_ATTEMPTS` هرگز پر نمی‌شد.
- **بازتولید:** fake sd که باز می‌شود و ۳ فریم می‌دهد و بعد ساکت می‌شود، با سقف ۲ →
  در ۳ ثانیه **۱۲ بار** باز شد و همچنان ادامه داشت (اجرای مقایسه‌ای old/new هم همین را نشان داد:
  `old: STILL LOOPING (12 opens, cap 2)` در برابر `new: VoiceError after 3 opens`).
- **رفع:** سهمیهٔ تلاش‌ها فقط وقتی ریست می‌شود که همان تلاش دست‌کم
  `PVAI_AUDIO_REOPEN_STABLE_FRAMES` فریم **واقعی** تحویل داده باشد (پیش‌فرض ۵۰ = ۱ ثانیه).
  بعد از سقف، `VoiceError` فارسی و عملی می‌آید که دقیقاً همین سه علت را نام می‌برد
  (mute / exclusive / هدست) و به `PVAI_AUDIO_INPUT_DEVICE` ارجاع می‌دهد. تابع `_retry()`
  این تفکیک را انجام می‌دهد: «دستگاهی که کار می‌کرد و مرد» یک بازگشایی می‌گیرد،
  «دستگاهی که هرگز یک فریم هم نداد» سهمیه را مصرف می‌کند.
- **تست:** `test_silent_device_exhausts_the_reopen_budget_instead_of_looping_forever`,
  `test_a_short_burst_of_frames_does_not_refund_the_reopen_budget`,
  `test_a_device_that_delivered_real_frames_earns_a_fresh_budget`,
  `test_stable_frames_threshold_is_configurable`
- **وضعیت:** رفع‌شده + تست (hardware: NOT TESTED)

### A2-01 — دو ساعت متفاوت در سنجش stall (`monotonic` در برابر `perf_counter`)

- **فایل:** `server/audio_io.py` → `pump`، `server/aec.py` → `DuplexAudio._measure`
- **ریشه:** watchdog با `time.monotonic() - last_frame_at` حساب می‌کرد ولی
  `_measure` مقدار `last_frame_at` را با `time.perf_counter()` می‌نوشت. مبدأ `perf_counter`
  تعریف‌نشده است و روی Windows (سیستم هدف) با `monotonic` هم‌مبدأ نیست؛ نتیجه بسته به علامت
  اختلاف می‌تواند «stall کاذب روی stream سالم» یا «پذیرش بی‌صدای دستگاه مرده» باشد.
- **رفع:** همهٔ مُهرهای زمانی این مسیر از `time.monotonic` می‌آیند (`DuplexAudio._measure`,
  `opened()`, `pump`). `_stall_baseline()` تنها جایی است که watchdog ساعت می‌خواند.
- **تست:** `test_callback_timestamps_share_the_watchdogs_clock` (با `perf_counter` جابه‌جاشده،
  مهر زمانی باید داخل بازهٔ `monotonic` قبل/بعد بماند)
  و `test_healthy_stream_is_not_stalled_when_perf_counter_has_another_origin`.
- **وضعیت:** رفع‌شده + تست

### A3-01 — دستگاهی که هرگز callback نمی‌دهد هیچ‌وقت stall تشخیص داده نمی‌شد

- **فایل:** `server/audio_io.py` → `pump`، `server/aec.py` → `DuplexAudio.opened`
- **ریشه:** شرط `if last is not None and ...` باعث می‌شد تا اولین callback، `last_frame_at`
  همیشه `None` باشد؛ پس «میکروفونی که stream باز می‌کند و هیچ فریمی نمی‌دهد» و
  «دستگاه سالم» از نظر watchdog یکسان بودند. باگ A1-01 هم دقیقاً به همین دلیل بی‌پایان می‌شد.
- **بازتولید:** fake sd بدون هیچ callback با سقف ۳ → فقط ۱ بار باز شد و تا ابد منتظر ماند.
- **رفع:** دو مبنا: `DuplexAudio.opened()` لحظهٔ باز شدن stream را ثبت می‌کند و
  `stall_baseline()` بین «آخرین فریم» و «لحظهٔ باز شدن» انتخاب می‌کند؛ در `pump` هم اگر
  هیچ مبنایی نبود، لحظهٔ شروع خود watchdog مبنا می‌شود (هرگز بررسی غیرفعال نمی‌شود).
- **تست:** `test_pump_detects_a_stream_that_never_delivered_a_frame`,
  `test_stall_is_measured_from_stream_open_not_only_from_the_first_frame`
- **وضعیت:** رفع‌شده + تست

### A4-01 — بازگشایی stream، حالت VAD/Pipeline و AEC را ریست نمی‌کرد

- **فایل:** `server/audio_io.py` → `stream_forever`/`reset_stream_state`،
  `server/aec.py` → `DuplexAudio.reset_state`، `server/streaming_voice.py` → `Pipeline.reset`
- **ریشه:** فقط مسیر overflow ریست می‌کرد. بعد از reopen، detector ممکن بود نیمهٔ یک utterance
  را از قبل از قطعی نگه دارد و با صدای بعد از gap کاملش کند (ترنسکریپت ساختگی)، و فیلتر AEC
  مسیر پژواکِ دستگاهی را که دیگر وجود ندارد نگه می‌داشت.
- **بازتولید:** fake sd که یک فریم می‌دهد و می‌میرد → `detector.reset: 0 | aec.reset: 0`.
- **رفع:** `reset_stream_state()` روی هر reopen صدا زده می‌شود: `DuplexAudio.reset_state()`
  (خالی‌کردن صف capture و پاک‌کردن مهرهای زمانی)، `aec.reset()`، و `Pipeline.reset()`
  که utterance ناتمام و partial را رها می‌کند، رویداد `input_reset` می‌دهد، به `IDLE`
  برمی‌گردد — و **به صف final دست نمی‌زند** (همان قرارداد `overflow`).
- **تست:** `test_reopen_resets_detector_aec_and_buffered_audio`,
  `test_reset_stream_state_handles_plain_fakes`,
  `test_pipeline_reset_abandons_the_unfinished_utterance_but_keeps_finals`
- **وضعیت:** رفع‌شده + تست

سنجه‌های تازه: `audio_reopen_count`, `audio_reopen_attempts`, `audio_reopen_reset_count`.

## مرحلهٔ پنجم — بازتولید و رفع باگ «نشستِ resum‌شدهٔ خالی» (۸ نوامبر ۲۰۲۶)

### C1-01 — `create_conversation` هرگز فایل موجود را نمی‌خواند: context از دست می‌رفت و شناسه‌ها تکرار می‌شدند

- **فایل:** `server/conversation.py` → `create_conversation`, `Conversation.load`؛
  `server/streaming_voice.py` → `Pipeline.__init__`, `create_pipeline`؛
  `server/ws_voice.py` → `_open_pipeline`
- **ریشه:** فقط `Conversation.load` فایل JSONL را می‌خواند و تنها `/api/chat` از آن استفاده
  می‌کرد. `create_conversation(settings, session_id="demo")` یک `Conversation` **خالی** می‌ساخت
  و همان `session_id` را به فایل موجود می‌چسباند. نتیجه سه‌گانه بود:
  1. `messages` خالی ⇒ context مدل فقط نوبت جدید بود (الزام ۱۷ در `docs/STATUS.md` نقض می‌شد)،
  2. `_next_id`/`_next_turn` از ۱ شروع می‌شد ⇒ `id` و `turn_id` تکراری در همان فایل،
  3. مجموعهٔ dedupe خالی بود ⇒ `utterance_id`های ذخیره‌شده دوباره پذیرفته می‌شدند.
  علاوه بر این `Pipeline._next_utterance_id` همیشه از ۱ شروع می‌شد، پس در نشست resum‌شده
  **اولین utterance تازه** شناسه‌ای می‌گرفت که از قبل در فایل بود و بی‌صدا به‌عنوان «تکرار»
  دور انداخته می‌شد — رویداد `transcript_final` می‌آمد ولی نه پیام کاربر و نه پاسخی ثبت نمی‌شد.
- **بازتولید:** `/tmp/repro_conv.py` (دو بار `create_conversation(settings, session_id="demo")`)
  ⇒ نشست دوم `messages: []`، `next id 1`، فایل `ids: [1, 2, 1, 2]`، `turn_ids: [None, 1, None, 1]`،
  context فقط شامل دو پیام جدید. `/tmp/repro_utt.py` ⇒ `NEW UTTERANCE DROPPED`.
- **رفع:**
  - `create_conversation` هر نشستی را که قرار است در آن نوشته شود از `Conversation.load`
    می‌خواند (`resume_target` انتخاب می‌کند کدام نشست)؛ بنابراین `_next_id`، `_next_turn`،
    مجموعهٔ dedupe و `_next_utterance_id` همه از حداکثر موجود ادامه می‌یابند.
  - `Conversation.next_utterance_id()` افزوده شد و `Pipeline` شمارندهٔ utterance خود را از آن
    می‌گیرد (نشست تازه همان `1` می‌ماند؛ تست جداگانه تضمینش می‌کند).
  - **تصمیم دربارهٔ پیش‌فرض resume (مستندشده):** `PVAI_HISTORY_RESUME=on` پیش‌فرض است و فقط
    مسیر **زندهٔ محلی** را تحت تأثیر می‌گذارد: با نبود `session_id`، نشستِ
    `PVAI_VOICE_SESSION_ID` (اگر تنظیم شده باشد) وگرنه آخرین نشست نوشته‌شده ادامه می‌یابد.
    `PVAI_HISTORY_RESUME=off` رفتار قدیمی (هر اجرا نشست تازه) را برمی‌گرداند.
    `session_id` صریح همیشه resume می‌شود، مستقل از این کلید.
  - **استثنای لازم برای انزوا:** `ws_voice._open_pipeline` با `resume=False` صدا می‌زند، پس
    دستگاهی که `session_id` نمی‌فرستد هرگز به نشست محلی/دستگاه دیگر نمی‌چسبد
    (`test_device_without_a_session_id_never_joins_the_last_local_session`).
    `/api/chat` بدون `session_id` هم دست‌نخورده می‌ماند (همیشه RAM-only).
- **تست:** `test_create_conversation_resumes_an_explicit_session`,
  `test_resumed_conversation_deduplicates_stored_utterances`,
  `test_resume_defaults_to_the_last_session_for_the_local_loop`,
  `test_history_resume_off_keeps_every_start_fresh`,
  `test_voice_session_id_pins_the_local_session`,
  `test_resume_with_persistence_off_starts_fresh`,
  `test_streaming_voice.py::test_resumed_pipeline_continues_ids_and_keeps_the_first_new_utterance`,
  `test_streaming_voice.py::test_fresh_pipeline_still_starts_its_utterance_ids_at_one`,
  `test_ws_voice.py::test_device_resumes_a_session_with_its_earlier_context`,
  `test_ws_voice.py::test_resumed_device_session_context_reaches_the_model`
- **وضعیت:** رفع‌شده + تست (هر ۱۰ تست روی کد قدیم شکست می‌خورند)

### C1-02 — `ConversationStore.sanitize` حروف/ارقام یونیکد و نام‌های رزروشدهٔ ویندوز را می‌پذیرفت

- **فایل:** `server/conversation.py` → `sanitize`, `append`
- **ریشه:** `char.isalnum()` حروف فارسی/عربی و ارقام فارسی را قبول می‌کرد و آن‌ها داخل نام فایل
  می‌رفتند؛ روی ویندوز (case folding و نرمال‌سازی یونیکد) دو شناسهٔ متفاوت می‌توانند یک فایل
  شوند. نام‌های دستگاه (`CON`, `NUL`, `AUX`, `COM1`…) هم پذیرفته می‌شدند، در حالی که
  `<name>.jsonl` روی ویندوز ساخته نمی‌شود.
- **بازتولید:** `sanitize('CON')`, `sanitize('café')`, `sanitize('sess-١٢٣')` همه پذیرفته می‌شدند.
- **رفع:** الفبای مجاز فقط `[A-Za-z0-9_-]` (`SESSION_ID_ALPHABET`)؛ `WINDOWS_RESERVED_NAMES`
  (شامل `COM1`–`COM9`, `LPT1`–`LPT9`, `clock$`) رد می‌شود؛ و چون `S-DEMO`/`s-demo` روی
  فایل‌سیستم حساس‌نبودن به بزرگی/کوچکی یک فایل‌اند، `append` شناسه‌ای را که فقط در بزرگی/کوچکی
  با نشست موجود فرق دارد رد می‌کند (`store.error` توضیح می‌دهد) تا لاگ دو نشست قاطی نشود.
- **تست:** `test_session_id_rejects_non_ascii_letters_and_digits`,
  `test_session_id_rejects_windows_reserved_device_names`,
  `test_session_id_keeps_the_documented_alphabet`,
  `test_case_insensitive_collision_is_refused_instead_of_sharing_a_file`,
  `test_settings_reject_an_invalid_voice_session_id`
- **وضعیت:** رفع‌شده + تست. **سازگاری:** شناسه‌های غیر‑ASCII که قبلاً ذخیره شده‌اند دیگر از طریق
  API/CLI قابل خواندن نیستند؛ چنین نشستی فقط با تغییر نام فایل به الفبای مجاز بازیابی می‌شود.

### C1-03 — `sessions()` برای شمردن پیام‌ها کل فایل را JSON-parse می‌کرد و event loop را می‌بست

- **فایل:** `server/conversation.py` → `sessions`, `count_messages`؛ `server/main.py` → `GET /api/sessions`
- **ریشه:** در هندلر async، `sessions()` هر فایل را کامل می‌خواند و هر خط را `json.loads`
  می‌کرد فقط برای شمردن `kind == "message"`. یک فایل ۱۱ مگابایتی ۲۰٬۰۰۰ پیامی ۶۲٫۵ms طول
  می‌کشید و همان‌جا روی event loop اجرا می‌شد (صدای زنده و WebSocket پشت آن می‌ماند).
- **رفع:** `count_messages` (و `_count_messages`) با خواندن باینری خط‌به‌خط و تست پیشوند/پسوند
  `{"kind": "message"…}` می‌شمارد؛ هر خط غیرمنتظره (خط پارهٔ آخر فایل، فایل دست‌کاری‌شده) به
  مسیر دقیق `json.loads` می‌رود تا شمارش هرگز بیشتر از واقع نشود. هندلر هم با
  `asyncio.to_thread(store.sessions)` اجرا می‌شود.
- **اندازه‌گیری (همان فایل در sandbox):** ۶۲٫۵ms → ۱۳٫۹ms، با شمارش درست ۲۰٬۰۰۰.
- **تست:** `test_session_listing_counts_messages_without_parsing_the_whole_file`,
  `test_latest_session_follows_the_write_time`
- **وضعیت:** رفع‌شده + تست

### C1-04 — `history_persist=true` متن کامل گفتار را بی‌پایان نگه می‌داشت

- **فایل:** `server/config.py`, `server/conversation.py` → `purge_older_than`/`purge_expired`,
  `server/main.py` (startup), `server/sessions.py` → `purge`
- **ریشه:** `PVAI_HISTORY_PERSIST` پیش‌فرض روشن است و هر ترنسکریپت به‌صورت متن ساده در JSONL
  می‌ماند؛ هیچ سیاست نگه‌داری‌ای نبود، پس هم مسئلهٔ حریم خصوصی بود و هم دیسک، بدون آنکه کاربر
  بدون اجرای CLI ببیند.
- **رفع:** `PVAI_HISTORY_RETENTION_DAYS` (پیش‌فرض `0` = نگه‌داشتن همه، سازگار با رفتار قبلی).
  با مقدار مثبت، نشست‌هایی که mtime آن‌ها قدیمی‌تر است در startup API پاک می‌شوند
  (`purge_expired`) و با `python -m server.sessions purge [--days N]` دستی هم اجرا می‌شود.
- **تست:** `test_retention_purges_only_sessions_older_than_the_limit`,
  `test_retention_zero_keeps_everything_and_disabled_persistence_is_a_no_op`,
  `test_env_history_resume_and_retention_are_parsed`
- **وضعیت:** رفع‌شده + تست

## مرحلهٔ ششم — بازتولید و رفع باگ‌های `.env` (۸ نوامبر ۲۰۲۶)

### D1-01 — کامنت هم‌خط در `.env` جزو مقدار می‌شد و مقدار عددی بی‌صدا به پیش‌فرض برمی‌گشت

- **فایل:** `server/config.py` → `load_dotenv`, `_env_int/_env_float/_env_optional_bool/_env_choice`
- **ریشه:** `load_dotenv` خط را فقط با `strip()` تمیز می‌کرد و کل باقیمانده را مقدار می‌گذاشت، در حالی که
  خودِ `.env.example` چند متغیر را با توضیح هم‌خط مستند کرده است:
  `PVAI_N_THREADS=2          # 0 = auto…`. با `Copy-Item .env.example .env` (همان قدم اول README)
  مقدار رشتهٔ `'2          # 0 = auto…'` می‌شد؛ `int(...)` خطا می‌داد و `_env_int` **بی‌صدا**
  پیش‌فرض را برمی‌گرداند. نتیجه: `PVAI_N_THREADS=2` عملاً `0` بود. همین برای
  `PVAI_N_GPU_LAYERS`, `PVAI_VOICE_RECORD_SECONDS` و `PVAI_AEC_DELAY_MS` صادق بود — کسی که
  `PVAI_AEC_DELAY_MS` را کالیبره می‌کرد و کنارش کامنت می‌گذاشت، مقدارش را از دست می‌داد.
- **بازتولید:** کپی `.env.example` به `.env` و `Settings.from_env()` ⇒
  `n_threads=0` (فایل: 2)، `n_gpu_layers=0` (فایل: 7)، `voice_record_seconds=5.0` (فایل: 7)،
  `aec_delay_ms=40` (فایل: 55) — هر چهار مقدار نادیده.
- **رفع:** `strip_inline_comment()` کامنت را حذف می‌کند: `#` فقط در ابتدای مقدار یا بعد از فاصله
  کامنت است و هرگز داخل کوتیشن نیست، پس `PVAI_SYSTEM_PROMPT="یک # دو"` سالم می‌ماند و
  `abc#def` یک مقدار است. `.env.example` هم بازنویسی شد (توضیح‌ها به خط جدا) و
  `tests/test_env_config.py::test_env_example_has_no_inline_comments_left_to_bite_anyone`
  جلوگیری می‌کند دوباره برنگردد.
- **تست:** `test_copying_env_example_produces_the_values_it_documents` (هر ۱۳ متغیر عددی)،
  `test_strip_inline_comment` (۹ حالت)، `test_commented_values_survive_a_round_trip`
- **وضعیت:** رفع‌شده + تست

### D1-02 — مقدار نامعتبر یا کلمپ‌شده هیچ اثری از خود نمی‌گذاشت

- **فایل:** `server/config.py` → `ENV_ISSUES`, `Settings.env_issues`, `Settings.config_errors`
- **ریشه:** `_env_int/_env_float/_env_optional_bool` در هر سه حالت (غیرعددی، بولی نامفهوم، خارج از
  بازه) به مقدار پیش‌فرض یا حد مجاز برمی‌گشتند و **هیچ** لاگ/خطایی تولید نمی‌کردند. یعنی یک
  `.env` کاملاً غلط از یک `.env` درست قابل تشخیص نبود.
- **رفع:** هر رد/کلمپ با `logger.warning` (شامل نام متغیر و دلیل) لاگ می‌شود، در
  `ENV_ISSUES` ثبت و در `Settings.from_env()` به `Settings.env_issues` منتقل می‌شود (بعد از هر
  فراخوانی پاک می‌شود تا در یک پروسهٔ طولانی انبار نشود)، و `config_errors()` آن‌ها را برمی‌گرداند —
  بنابراین `GET /ready` (بخش `configuration`) و پیام startup مسیر زنده نشانش می‌دهند.
- **تست:** `test_invalid_number_is_reported_not_silently_defaulted`,
  `test_clamped_value_is_reported`, `test_invalid_boolean_and_choice_are_reported`,
  `test_a_clean_environment_reports_nothing`, `test_issues_do_not_accumulate_across_calls`,
  `test_ready_endpoint_surfaces_rejected_values`
- **وضعیت:** رفع‌شده + تست

### D1-03 — `.env` فقط از پوشهٔ جاری خوانده می‌شد

- **فایل:** `server/config.py` → `load_dotenv`, `dotenv_candidates`, `PROJECT_ROOT`
- **ریشه:** `env_path = Path.cwd() / ".env"`. اجرا از هر مسیری غیر از ریشهٔ پروژه (Task Scheduler،
  IDE، اسکریپت داخل `tools/`) یعنی `.env` **بی‌صدا** نادیده گرفته می‌شد و سرویس با پیش‌فرض‌ها
  بالا می‌آمد.
- **رفع:** `dotenv_candidates()` اول `.env` پوشهٔ جاری را می‌بیند و بعد `.env` ریشهٔ پروژه
  (`PROJECT_ROOT`) را؛ اولین فایل موجود برنده است، پس `cd` به یک پوشهٔ استقرار هنوز اولویت دارد.
  مسیر خوانده‌شده در `DOTENV_LOADED` ثبت می‌شود.
- **تست:** `test_dotenv_is_found_from_any_working_directory`,
  `test_current_directory_env_wins_over_the_project_root`, `test_explicit_path_is_used_verbatim`
- **وضعیت:** رفع‌شده + تست

### D1-04 — `PVAI_N_THREADS` / `PVAI_N_GPU_LAYERS` / `PVAI_N_CTX` هیچ‌جا استفاده نمی‌شدند

- **فایل:** `server/config.py` → `llama_server_command`, `_print_launch`؛ `tools/start_llama.ps1`
- **ریشه:** این سه فیلد فقط در dataclass تعریف شده بودند و هیچ مصرف‌کننده‌ای نداشتند؛ کلاینت HTTP
  آن‌ها را نمی‌فرستد (llama-server مسیر مدل و تعداد thread را خودش تعیین می‌کند). در همان حال
  README دستور دستی با `-t 4` داشت ولی `.env.example` می‌گفت ۲ — یعنی مستندات دو چیز متفاوت
  می‌گفتند و هیچ‌کدام از `.env` اثر نمی‌گرفت.
- **رفع:** `Settings.llama_server_command()` همین تنظیمات را به `-m/-c/--host/--port/-t/-ngl`
  تبدیل می‌کند (`-t` فقط وقتی `n_threads>0` باشد؛ `0` = خودِ llama-server) و
  `python -m server.config --launch [--launch-json]` آن را چاپ می‌کند.
  `tools/start_llama.ps1` همان خروجی JSON را می‌خواند و سرور را اجرا می‌کند (`-DryRun` برای
  دیدن دستور، `-Exe` برای مسیر باینری). مستندات یکسان شدند: `.env.example` روی
  `PVAI_N_THREADS=4` (۲ هستهٔ فیزیکی × ۲ رشتهٔ منطقی، همان پیکربندی `docs/SPEC.md`) و
  README/PC_VOICE دیگر `-t 4` را دستی تکرار نمی‌کنند.
- **تست:** `test_launch_command_uses_the_configured_flags`,
  `test_launch_command_omits_threads_when_auto`,
  `test_the_file_flags_and_the_documented_manual_command_agree`,
  `test_launch_cli_prints_the_command`, `test_launch_cli_warns_about_rejected_values`
- **وضعیت:** رفع‌شده + تست (اجرای واقعی PowerShell در sandbox تست نشده؛ **NOT MEASURED**)
