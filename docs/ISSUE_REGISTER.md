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
| OPEN-02 | P1 | WebSocket صوتی سرور/ESP32 و احراز هویت دستگاه | فقط پروتکل طراحی و تست واحد شده؛ پیاده‌سازی انتقال نشده |
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
| `docs/PROTOCOL.md` (قرارداد WebSocket/ESP32) | افزوده شد؛ همچنان design-only |
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
