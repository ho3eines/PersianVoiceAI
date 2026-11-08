# معماری موتور مکالمهٔ PC — utterance، generation، صف‌ها و لغو

این سند رفتار **پیاده‌شده** در `server/streaming_voice.py` را توصیف می‌کند. هر ادعای اینجا با
نام تست مشخص پشتیبانی می‌شود؛ اگر چیزی تست نشده، همان‌جا نوشته شده است.

## ۱. مسئله‌ای که حل شد

پیاده‌سازی قبلی یک شمارندهٔ `generation` و یک صف یک‌تایی (`snapshots`) برای همهٔ کارها داشت.
نتیجه: با شروع گفتار جدید، `interrupt()` صف را خالی می‌کرد و `recognize()` هم نتیجهٔ final را
در صورت تغییر `generation` دور می‌ریخت. یعنی جملهٔ A می‌توانست بی‌صدا از مکالمه حذف شود:

```text
کاربر A را می‌گوید → سکوت → final(A) به صف میرود → Whisper در حال decode
کاربر B را شروع می‌کند → onset → interrupt() → generation+1
decode تمام میشود → «generation قدیمی» → final(A) دور ریخته میشود ← باگ P0-01
```

سه مسیر مستقل این از دست رفتن را می‌ساختند: خالی‌کردن صف در `interrupt()`، بررسی `generation`
پس از decode، و mailbox مشترک partial/final. هر سه در طراحی جدید حذف شده‌اند.

## ۲. چهار مفهوم مستقل

| مفهوم | مالک | عمر | لغو می‌شود؟ |
|---|---|---|---|
| `Utterance` | VAD + Pipeline | onset تا commit متن | خیر (شناسهٔ یکتا و افزایشی) |
| `FinalASR` (صف FIFO) | `AsrScheduler.finals` | submit تا decode و ثبت | **فقط** در shutdown، با اعلام |
| `PartialASR` (mailbox یک‌جا) | `AsrScheduler.partial` | تا جایگزینی یا رسیدن final | بله، بدون اثر |
| `AssistantTurn` | `Pipeline.pending_turn` | تا commit (complete/interrupted/failed) | بله، مستقل از ASR |

قاعدهٔ طلایی: **لغو پاسخ دستیار هیچ‌وقت به صف final دست نمی‌زند.** barge-in فقط
`AssistantTurn`، task پاسخ و صف پخش را متوقف می‌کند.

### شناسه‌ها

- `utterance_id`: ۱، ۲، ۳…؛ هرگز تکرار نمی‌شود. کلید dedupe در `Conversation.add_user`.
- `turn_id`: هر تلاش پاسخ؛ کلید dedupe در `Conversation.finish_assistant`.
- `generation`: شمارندهٔ barge-in برای بی‌اعتبار کردن taskهای async قدیمی (شبکه/TTS)،
  و **ملاک اعتبار ASR نیست**.

## ۳. خط لوله

```text
PortAudio duplex callback  (PCM16 mono / 16 kHz / 20 ms)
   │  capture+reference تازه، شماره‌گذاری‌شده
   ▼
Queue محدود  ──►  AEC (SpeexDSP)  ──►  EnergyVAD  ──►  Pipeline.feed()
                                                       │
                        ┌──────────────────────────────┼──────────────────────────────┐
                        ▼                              ▼                              ▼
                  onset: Utterance(id)         partial هر 0.8s               endpoint/max_segment
                  barge-in فقط اگر            پنجرهٔ هم‌پوشان ۶s             audio کامل بخش
                  THINKING/SPEAKING           → mailbox یک‌جا               → صف FIFO final
                        │                              │                              │
                        └──────────────► AsrScheduler ◄┘                              │
                                             │                                        │
                                     یک actor Whisper (thread)                        │
                                             │                                        │
                              partial → رویداد transcript_partial                      │
                              final   → transcript_final → Conversation.add_user        │
                                             │                                        │
                                    AssistantTurn (SSE به llama-server) ◄──────────────┘
                                             │
                                    جمله‌های آماده → صف محدود (۸) → actor پخش
                                             │
                                     Piper → mono 16k → duplex → بلندگو
```

هر پایین‌دست **یک مالک** دارد: یک actor برای Whisper، یک actor برای پخش. این کار هم‌زمانی
دو decode یا دو سنتز را حذف می‌کند و روی CPU دوهسته‌ای قابل پیش‌بینی است.

## ۴. ASR: چرا partial «ارزان» شد

پیاده‌سازی قدیم هر ۰٫۸ ثانیه **کل بخش در حال رشد** را به Whisper می‌داد؛ هزینه تا سقف ۱۵ ثانیه
تقریباً درجه‌دوم رشد می‌کرد. اکنون:

- **final**: کل PCM بخش (بدون برش، فقط حذف سکوت پایانی اضافی) — دقت حفظ می‌شود.
- **partial**: پنجرهٔ هم‌پوشان با سقف `PVAI_ASR_PARTIAL_WINDOW_S` (پیش‌فرض ۶ ثانیه)؛
  هزینهٔ هر decode تقریباً ثابت است.
- ترتیب اولویت: صف final **همیشه** قبل از partial سرو می‌شود؛ partial معلق با رسیدن final
  همان utterance منسوخ می‌شود.
- `agreed_prefix` فقط وقتی معنا دارد که پنجره جابه‌جا نشده باشد؛ در غیر این صورت رشتهٔ خالی
  گزارش می‌شود تا «پیشوند پایدار» دروغ نباشد.
- سنجه‌ها: `asr_ms`, `asr_rtf`, `asr_final_latency_ms`, `asr_partial_latency_ms`,
  `asr_queue_depth`, `asr_dropped_partial_count`, `asr_final_job_count`, `asr_errors`.
- **backpressure**: صف final بی‌کران نیست در معنا که هیچ‌وقت drop نمی‌کند؛ فقط از
  `PVAI_ASR_MAX_FINAL_JOBS` عبور می‌کند و شمارندهٔ `backlog_warnings` بالا می‌رود. این انتخاب
  آگاهانه است: گم‌کردن متن کاربر بدتر از تأخیر است.

اصلاح مهم مرحلهٔ اخیر: در `AsrScheduler.next()` پرچم بیدارباش **بعد از** بررسی صف پاک می‌شد؛
یک final که در همان پنجره submit می‌شد می‌توانست تا submit بعدی خواب بماند (lost wakeup).
اکنون ابتدا `clear()` و سپس بررسی مجدد انجام می‌شود.
تست: `tests/test_asr_scheduler.py::test_submit_landing_between_check_and_wait_is_not_lost`.

## ۵. ماشین حالت

| حالت | معنا |
|---|---|
| `IDLE` | نه گفتاری، نه پاسخی |
| `LISTENING` | utterance باز است |
| `TRANSCRIBING` | final صف شده و منتظر decode |
| `THINKING` | پاسخ در حال دریافت از مدل |
| `SPEAKING` | سنتز/پخش |

گذار مجاز در `ALLOWED_TRANSITIONS`. هر گذار غیرمجاز **گزارش** می‌شود (`state_violation`) و
شمارندهٔ `states.violations` بالا می‌رود؛ هرگز باعث crash نمی‌شود. تست:
`tests/test_state_machine.py` (همهٔ ۲۵ جفت حالت + جریان واقعی یک نوبت + barge-in).

اصلاح مرحلهٔ اخیر: پس از پایان پخش، حالت در `SPEAKING` گیر می‌کرد چون نشانگر commit
(`SpeechItem` با متن خالی) آخرین آیتم صف است و شرط `reply.done()` در آن لحظه هنوز false بود.
اکنون هنگام مصرف نشانگر، اگر صف خالی و `pending_turn` پاک شده باشد، گذار به
`LISTENING`/`IDLE` انجام می‌شود. تست: `test_pipeline_walks_the_documented_states_for_one_exchange`.

## ۶. barge-in و لغو

| وضعیت هنگام شروع گفتار کاربر | رفتار |
|---|---|
| `IDLE` | utterance جدید؛ هیچ لغوی |
| `LISTENING` | ادامهٔ همان utterance |
| `TRANSCRIBING` | utterance جدید ساخته می‌شود؛ **صف final دست‌نخورده**؛ پاسخ قدیمی اگر فعال باشد لغو می‌شود |
| `THINKING` | barge-in پس از `PVAI_VOICE_BARGE_IN_MS` گفتار: cancel task پاسخ، افزایش generation، پاک‌کردن صف جمله |
| `SPEAKING` | همان + set کردن token پخش؛ Speaker بین دو بلوک ۲۰ms متوقف می‌شود |

قواعد تکمیلی:

- پاسخ قطع‌شده با وضعیت `interrupted` ثبت می‌شود و فقط **آنچه شنیده شد** در تاریخچه می‌رود:
  اگر جمله‌ای کامل شده باشد همان‌ها (`turn.spoken`)، اگر فقط جملهٔ اول آغاز شده باشد همان جمله
  (`turn.current`)، و اگر هنوز هیچ صدایی نبود متن تولیدشده فقط برای بازبینی ثبت می‌شود و به‌طور
  پیش‌فرض وارد context مدل نمی‌شود. جمله‌های صف‌شدهٔ پخش‌نشده هرگز سنتز/پخش نمی‌شوند.
  تست: `tests/test_bargein.py`.
- `Stale audio` ممکن نیست: هر آیتم صف، `token` و `generation` خود را حمل می‌کند و actor پخش
  قبل از هر قطعه آن را بررسی می‌کند.
- توقف قطعی inference native در حال اجرا **تضمین نمی‌شود** (thread درجا kill نمی‌شود)؛
  نتیجهٔ دیررس با `generation`/`turn.token` دور ریخته می‌شود. رفع کامل = process isolation
  (مورد باز OPEN-01).

## ۷. صف‌ها و کرانه‌ها

| صف | کرانه | سیاست |
|---|---|---|
| capture (DuplexAudio) | ۱۰۰ فریم ≈ ۲s | overflow → `Pipeline.overflow()` نوبت جاری را حذف می‌کند، finalها می‌مانند |
| `AsrScheduler.finals` | بدون drop | FIFO؛ عمق فقط هشدار می‌دهد |
| `AsrScheduler.partial` | ۱ | latest-only |
| `Pipeline.speech` | `PVAI_VOICE_SPEECH_QUEUE` (۸) | پرشدن → backpressure روی SSE |
| `Conversation.messages` | `PVAI_HISTORY_MAX_STORED` (۵۰۰) | پنجرهٔ RAM؛ فایل JSONL کامل می‌ماند |
| `_finalized_ids` | ۵۱۲ | dedupe نتیجهٔ final تکراری |
| `Metrics` | ثابت | count/last/min/max/mean بدون نگه‌داشتن نمونه‌ها |

تست بار: `tests/test_long_run.py` با ۱۲۰ نوبت متوالی و بررسی همهٔ کرانه‌ها در هر نوبت.

## ۸. رویدادها (JSON lines روی stdout)

`speech_start`, `speech_end`, `transcript_partial`, `transcript_final`, `user_message`,
`first_token`, `response_delta`, `tts_enqueue`, `tts_start`, `tts_end`, `first_audible`,
`response_end`, `interrupted`, `response_skipped`, `state`, `state_violation`, `error`, `ready`,
`llm_timings`, `metrics` (در صورت فعال بودن).

هر رویداد `generation` و `state` دارد. `transcript_partial` هرگز فرمان اجرا نمی‌کند.

## ۹. معیار پذیرش (قابل اجرا)

1. `pytest -q` سبز (بدون مدل و بدون سخت‌افزار).
2. `python tools/benchmark_voice.py` بدون استثنا اجرا شود؛ ردیف‌های واقعی `MEASURED` و
   ردیف‌های بی‌مدل `SIMULATED`/`NOT MEASURED` باشند.
3. در اجرای واقعی (چک‌لیست سخت‌افزار): final(A) بعد از شروع B در تاریخچه بماند؛
   پیام تکراری ظاهر نشود؛ صدای کهنه پخش نشود؛ `barge_in_latency_ms` ثبت شود.
