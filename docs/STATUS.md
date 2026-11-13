# وضعیت اجرایی پروژه — PersianVoiceAI Conversation Engine

آخرین به‌روزرسانی: ۹ نوامبر ۲۰۲۶ — رفع باگ‌های گزارش‌شده، بازبینی مدل‌فری و مستندسازی؛
مرحلهٔ ۱ تا ۶ پیشین: رفع P0، بهبود خط لولهٔ صوتی، ماندگاری مکالمه،
ابزار بنچمارک/ارزیابی و مستندسازی. بازبینی پیشین روی commit `1db2581` انجام شده بود؛ این متن با کد working tree فعلی هم‌گام شده است؛
commit `9654a68` و شاخهٔ `arena/31347dde-persianvoiceai` فقط سابقهٔ مراحل پیشین‌اند.

این سند **وضعیت واقعی کد** است و سه سطح را از هم جدا می‌کند:

- **پیاده‌شده + تست خودکار** — با fake یا داده‌ی ساختگی در `pytest` سبز است.
- **پیاده‌شده، تست واقعی لازم** — منطق کامل است ولی بدون مدل/میکروفون/بلندگو تأیید نشده.
- **پیاده‌نشده** — نباید به‌عنوان قابلیت موجود معرفی شود.

---

## ۱. جدول وضعیت

| بخش | وضعیت | مرجع |
|---|---|---|
| هستهٔ متنی: FastAPI + llama.cpp + CLI فارسی | پیاده‌شده + تست (fake مدل) | `server/main.py`, `server/llm.py` |
| API سازگار OpenAI، JSON و SSE، لغو استریم | پیاده‌شده + تست | `tests/test_api.py` |
| ورودی میکروفون پیوستهٔ ۲۰ms، انتخاب دستگاه، recovery | پیاده‌شده + تست fake؛ دستگاه واقعی **تست نشده** | `server/audio_io.py` |
| watchdog ضد‌گیر و سهمیهٔ بازگشایی محدود (بدون حلقهٔ بی‌پایان) | پیاده‌شده + تست fake | `tests/test_audio_io.py` |
| ساعت یکسان (`monotonic`) برای watchdog و مهر فریم | پیاده‌شده + تست (اختلاف مبدأ شبیه‌سازی‌شده) | `server/aec.py`, `server/audio_io.py` |
| ریست VAD/pipeline و AEC روی هر بازگشایی stream | پیاده‌شده + تست | `reset_stream_state` + `Pipeline.reset` |
| VAD انرژی با هیسترزیس، پیش‌رول، کف نویز تطبیقی | پیاده‌شده + تست؛ صدای واقعی **تست نشده** | `server/vad.py` |
| ASR افزایشی: صف final بدون‌افت + partial قابل‌جایگزینی | پیاده‌شده + تست کامل P0 | `server/streaming_voice.py` |
| تأخیر final پشت partial: سنجهٔ تفکیک‌شده + لغو partial در حال decode | پیاده‌شده + تست SIMULATED؛ عدد دستگاه **NOT MEASURED** | `tests/test_asr_preemption.py` |
| utterance ردشدهٔ VAD: رویداد صریح، حالت درست، پاسخ نوبت قبلی حفظ می‌شود | پیاده‌شده + تست | `tests/test_short_noise_utterance.py` |
| شکست TTS/LLM/context: `failed` + خروج از `THINKING`/`SPEAKING` | پیاده‌شده + تست | `tests/test_turn_failures.py` |
| جدا شدن طول عمر utterance و generation دستیار | پیاده‌شده + تست (۶ سناریوی الزامی) | `tests/test_utterance_lifecycle.py` |
| ماشین حالت صریح `idle/listening/transcribing/thinking/speaking` | پیاده‌شده + تست جدول گذار | `tests/test_state_machine.py` |
| barge-in: توقف LLM/TTS/پخش، بدون آسیب به صف final | پیاده‌شده + تست fake؛ تأخیر دستگاه **تست نشده** | `tests/test_tts.py`, `tests/test_long_run.py` |
| TTS Piper: انتخاب مرد/زن، جمله‌بندی، لغو، نرمال‌سازی نرخ | پیاده‌شده + تست؛ وزن فارسی **تست نشده** | `server/live_voice.py` |
| AEC SpeexDSP duplex پیش از VAD/ASR | پیاده‌شده؛ تست native مصنوعی **MEASURED** (۱۱/۱۱، ۱۲ نوامبر)؛ **تست اتاق واقعی روی همین لپ‌تاپ MEASURED (۱۳ نوامبر): با پیش‌فرض ۴۰ms هیچ حذفی ندارد، با تأخیر کالیبره ۶٫۸dB**؛ double-talk انسان و ESP32 **NOT TESTED** | `server/aec.py`, `tools/aec_room_measure.py`, `docs/AEC.md` |
| تاریخچه: نشست، وضعیت interrupted، برش توکنی، JSONL محلی | پیاده‌شده + تست | `server/conversation.py`, `server/sessions.py` |
| readiness و health (`/ready`, `/health`) | پیاده‌شده + تست | `server/readiness.py`, `tests/test_api.py` |
| سنجه‌های کامل خط لوله (شامل jitter callback، صف، CPU/RAM در اجرای زنده) | پیاده‌شده + تست | `server/metrics.py`, `server/audio_io.py` |
| بنچمارک کل زنجیرهٔ صوت | ابزار آماده (`tools/benchmark_voice.py`)؛ اعداد واقعی هدف **NOT MEASURED** | `docs/BENCHMARKS.md` |
| ارزیابی فارسی WER/CER | مجموعهٔ ۴۰ جمله + ۶ کلیپ `no_speech` + ابزار؛ اعداد واقعی **NOT MEASURED** | `benchmarks/persian_eval.py` |
| نرخ پذیرش کاذب روی سکوت/نویز | سنجه و ورودی‌ها آماده؛ عدد واقعی **NOT MEASURED** (بدون wav) | `benchmarks/persian_eval.py`, `server/persian.py` |
| پروتکل و **transport سمت سرور** WebSocket صوتی (`WS /ws/voice`) | پیاده‌شده + تست (fake)؛ توکن per-device، سقف اتصال، pacing، heartbeat، فریم ۲۰ms اجباری | `server/protocol.py`, `server/ws_voice.py`, `tests/test_ws_voice.py` |
| **میز تست وب یک‌نوبتی** (صحبت/تایپ → ASR → LLM → TTS در مرورگر) | پیاده‌شده + تست (fake)؛ یک چرخهٔ واقعی mic→Whisper→llama.cpp→Piper هم روی همین لپ‌تاپ اجرا شد | `server/webui.py`, `web/index.html`, `tests/test_webui.py`, `docs/WEB_UI.md` |
| امنیت REST (`PVAI_API_TOKEN`، rate limit، `/health` حداقلی) | پیاده‌شده + تست | `server/security.py`, `server/main.py`, `tests/test_ws_security.py` |
| firmware ESP32-S3، OTA، تست LAN واقعی | **پیاده‌نشده / NOT MEASURED** | — |
| Command Broker فاز اول (schema، policy، audit، فقط `FakeDevice`) | پیاده‌شده + تست؛ **SIMULATED** (هیچ GPIO/رله واقعی وصل نیست)؛ به ASR/LLM وصل نشده | `server/broker.py`, `tests/test_broker.py` |
| IoT واقعی (GPIO/رله/سنسور، اتصال به مسیر گفتار) | **پیاده‌نشده** | — |

## ۲. وضعیت تست

```text
$ python -m pytest
نتیجهٔ جاری را با اجرای python -m pytest ببینید (تعداد ثابت نیست)
```

- آخرین اجرای کامل `pytest -q` در این مخزن: exit 0، بدون شکست (MEASURED). تست‌های `test_ws_security.py` که `reset()` را با warmup واقعی Whisper در پس‌زمینه معطل می‌کردند، با `asr_warmup=False` بی‌خطر شدند.
- تعداد تست‌های مجموعه (pytest --collect-only): **1227** تست — این تنها عدد
  مجاز است و `tests/test_docs_consistency.py` آن را با خروجی واقعی
  `pytest --collect-only` مقایسه می‌کند؛ عدد کهنه ⇒ تست قرمز. بقیهٔ اعداد این
  سند **تاریخی** و مربوط به همان مرحله‌اند.

- ۵۳ تست امنیتی/منابع در `tests/test_ws_security.py` و ۲۰۲ تست آماده‌سازی متن TTS در
  `tests/test_tts_text_preparation.py`: هر تست یک نقص گزارش‌شده را می‌پوشاند و
  روی کد پیش از رفع شکست می‌خورد (برای بارگذاری مدل، pacing، صف حیاتی و نرخ فریم دستی بررسی شد).
  (تصحیح: پیش‌تر «۳۶ امنیتی» و «متن/ASR» نوشته شده بود؛ فیلتر توهم ASR در
  `tests/test_voice.py` تست می‌شود، نه در آن فایل. تعداد فعلی از `pytest --collect-only`
  همان اجراست، نه شمارش دستی.)

- تست‌های native SpeexDSP در sandbox بدون کتابخانهٔ ساخته‌شده صریحاً skip می‌شوند؛ اگر
  `libspeexdsp` نصب باشد اجرا می‌شوند (در اجرای قبلی همین محیط: ۵۴ تست موفق با کتابخانه).
- ۱۹ تست تطابق مستندات در `tests/test_docs_consistency.py` برای متغیرهای محیطی، مسیرها، ارجاع تست‌ها، نقشهٔ مخزن و لینک‌ها افزوده شد.
- تست پایه پیش از این مرحله: گزارش تاریخی (تعداد تست‌ها تغییر کرده است). یعنی ۵۵۳ تست جدید یا گسترش‌یافته
  آخرین افزوده: ۱۴ تست transport وب‌سوکت + ۱ تست قطع کلاینت SSE + ۲ تست پیکربندی/readiness +
  ۲۷ تست موتور مکالمه و مسیر صوتی + ۲۳ تست مرحلهٔ resume (ادامهٔ نشست از مسیر
  `create_conversation`/`Pipeline`/`WS`، سخت‌گیری شناسهٔ نشست، شمارش سبک فهرست نشست‌ها و
  سیاست نگه‌داری) + ۲۸ تست پیکربندی `.env` (کامنت هم‌خط، مقدار رد/کلمپ‌شده، محل `.env` و
  هم‌خوانی پرچم‌های llama-server).
- در اجرای فعلی با numpy/scipy/librosa/soundfile نصب‌شده، دو تست native SpeexDSP
  به‌علت نبود کتابخانه skip شدند. تعداد وابسته به محیط است؛ `python -m pytest -rs`
  مرجع تعداد و علت جاری است. اجرای Windows/macOS در این نشست انجام نشده است.
- **همهٔ تست‌ها fake/ساختگی‌اند**: بدون مدل Whisper/Piper/Qwen، بدون میکروفون و بلندگو، بدون شبکهٔ
  واقعی. این تست‌ها «صحت منطق و کنترل جریان» را ثابت می‌کنند، نه کیفیت صوتی.

نگاشت تست‌ها به ۲۰ الزام گزارش بازبینی:

| # | الزام | تست |
|---|---|---|
| ۱ | final ASR با شروع گفتار جدید زنده می‌ماند | `test_utterance_lifecycle.py::test_final_asr_in_flight_survives_new_speech_onset` |
| ۱ب | utterance ردشده (نویز کوتاه) پاسخ نوبت قبلی را حذف نمی‌کند و state را رها می‌کند | `test_short_noise_utterance.py` (۳ سناریو) |
| ۱پ | شکست TTS/LLM/context نوبت را `failed` می‌کند و از `THINKING`/`SPEAKING` خارج می‌شود | `test_turn_failures.py` |
| ۱ت | final پشت partial در حال decode معطل نمی‌ماند و تأخیرش اندازه‌گیری می‌شود | `test_asr_preemption.py` |
| ۲ | چند utterance پشت‌سرهم حفظ می‌شوند | `…::test_multiple_consecutive_utterances_are_all_preserved` |
| ۳ | جایگزینی partial، final را از بین نمی‌برد | `…::test_partial_replacement_never_loses_the_final` |
| ۴ | گفتار بلند (>15s) همهٔ بخش‌ها را حفظ می‌کند | `…::test_long_speech_beyond_segment_limit_keeps_every_segment` + `test_vad.py::test_long_speech_is_segmented_without_losing_audio` |
| ۵ | barge-in تولید LLM را لغو می‌کند | `test_streaming_voice.py::test_async_sse_stream_cancelled_without_waiting_for_next_token` |
| ۶ | barge-in پخش TTS را متوقف می‌کند | `test_tts.py::test_playback_stops_within_one_block_and_aborts_the_stream` |
| ۶ب | اکوی پخش، VAD را مکرر فعال نمی‌کند | `test_bargein.py::test_moderate_playback_echo_does_not_retrigger_vad_but_real_speech_does` |
| ۷ | هیچ صدای کهنه‌ای بعد از قطع پخش نمی‌شود | `test_bargein.py::test_sentences_queued_behind_the_spoken_one_never_play_after_barge_in` + `test_streaming_voice.py::test_incremental_playback_before_remaining_tokens` |
| ۸ | پیام کاربر تکراری ثبت نمی‌شود | `test_utterance_lifecycle.py::test_duplicate_final_result_is_not_committed_twice` |
| ۹ | پاسخ دستیار تکراری ثبت نمی‌شود | `test_conversation.py::test_assistant_turn_is_committed_once` |
| ۱۰ | وضعیت صریح interrupted/failed | `test_conversation.py::test_interrupted_reply_has_explicit_status_and_is_excluded_by_default` |
| ۱۱ | نبود AEC = رفتار ایمن و پیام صریح | `test_aec.py::test_missing_library_is_explicit_not_silent_bypass` |
| ۱۲ | حالت AEC خاموش | `test_aec.py::test_aec_config` |
| ۱۳ | قطع دستگاه | `test_audio_io.py::test_pump_detects_a_stalled_device` |
| ۱۴ | وصل دوباره/بازگشایی دستگاه | `test_audio_io.py::test_stream_forever_reopens_after_portaudio_failure_then_gives_up` |
| ۱۵ | قطع llama-server و بازیابی نوبت بعد | `test_api.py::test_backend_unavailable_returns_503`, `test_stream_backend_failure_is_sse_error`, `test_streaming_voice.py::test_llm_failure_releases_the_state_and_the_next_turn_recovers` |
| ۱۶ | خطای init مدل به‌صورت پیام | `test_voice.py::test_module_startup_missing_model_has_no_traceback`, `test_tts.py::test_missing_piper_model_is_a_user_facing_error` |
| ۱۷ | ماندگاری context پس از restart | `test_conversation.py::test_session_is_persisted_and_reloaded`, `test_create_conversation_resumes_an_explicit_session`, `test_resume_defaults_to_the_last_session_for_the_local_loop`, `test_api.py::test_chat_with_session_id_persists_and_reuses_context`, `test_streaming_voice.py::test_resumed_pipeline_continues_ids_and_keeps_the_first_new_utterance`, `test_ws_voice.py::test_device_resumes_a_session_with_its_earlier_context`, `test_resumed_device_session_context_reaches_the_model` |
| ۱۸ | برش توکنی | `test_conversation.py::test_context_is_truncated_by_tokens_not_only_message_count` |
| ۱۹ | کار آفلاین بدون API خارجی | `test_voice.py::test_whisper_never_downloads`, `test_public_api_rejected` |
| ۲۰ | نبود صف/حافظهٔ نامحدود | `test_long_run.py` (۲ تست) + `test_conversation.py` (۲ تست bounded-RAM) |
| ۲۱ | پیکربندی `.env` واقعاً اعمال می‌شود (کامنت هم‌خط، مقدار رد/کلمپ‌شده، محل فایل) | `test_env_config.py::test_copying_env_example_produces_the_values_it_documents`, `test_invalid_number_is_reported_not_silently_defaulted`, `test_ready_endpoint_surfaces_rejected_values`, `test_dotenv_is_found_from_any_working_directory`, `test_the_file_flags_and_the_documented_manual_command_agree` |

## ۳. اعداد اندازه‌گیری‌شده در sandbox

| معیار | مقدار | معنا |
|---|---|---|
| طول عمر خط لوله در حالت سکوت (jitter p95) | ۰٫۳۹ ms | فقط pacing هماهنگ‌کننده؛ **jitter واقعی callback دستگاه نیست** |
| RAM پروسه در حالت idle | ۲۹٫۵ MB | بدون مدل لود شده |
| CPU حالت idle | ~۰٪ | بدون مدل لود شده |
| VAD false positive روی سکوت | ۰ | باورپذیر برای آستانهٔ انرژی |
| سنجه‌های زنده: jitter/صف/CPU/RAM | ثبت می‌شوند | `audio_io.pump` هر ۱ ثانیه (`test_pump_publishes_system_and_queue_metrics`) |

اعداد LLM روی همین لپ‌تاپ (i7-6500U + 940MX) اندازه‌گیری شد (MEASURED: Qwen3-4B TTFT 4.69s/4.64 tok/s روی CPU؛ جدول کامل در BENCHMARKS.md). یک اجرای تک‌جهتهٔ ASR/TTS هم روی میکروفون همین لپ‌تاپ انجام شد (MEASURED، ۲۰۲۶-۱۱-۱۲)؛ نتیجه‌اش در بخش ۹.۳
[`HARDWARE_VALIDATION.md`](HARDWARE_VALIDATION.md) است: مدل سالم، سیگنال سالم، ولی رونویسی گفتار
انسان ضعیف. آن اجرا **به `BENCHMARKS.md` منتقل نشده** چون زمان‌هایش پایدار نبود و دامنه‌اش
فقط همین ماشین است.
جدول کامل با برچسب `MEASURED`/`SIMULATED`/`NOT MEASURED` در [BENCHMARKS.md](BENCHMARKS.md).

اجرای دوبارهٔ پنج وزن محلی با llama.cpp build 11040 در ۱۲ نوامبر ۲۰۲۶ انجام شد (MEASURED):
Qwen3.5-4B با ۴٫۰۱ tok/s و TTFT ۴٫۶۴s بهترین تعادل را داشت؛ gemma-3-4b-persian با
TTFT ۱۵٫۲۷s و پاسخ ریاضی غلط، و دو مدل کوچک (MiniCPM5-1B و qwen3-0.6b) با فارسی
شکسته کنار گذاشته شدند. جزئیات در BENCHMARKS §۵٫۶؛ گزارش خام در
`benchmarks/results/pc1-model-compare-20261112.json`. مقایسه با سقف ۱۶۰ توکن هم تکرار شد و
داوری کیفیت سه پرامپت (ریاضی/دستور ویندوز/صداقت) در BENCHMARKS §۵٫۷ ثبت است.

سناریوهای `asr,llm,tts` با Whisper و Piper محلی هم در ۱۲ نوامبر اجرا شد (MEASURED):
`llm_ttft_ms=3211.3`، `4.01 tok/s`، `tts_first_chunk_ms=3039.6`، ولی ASR با RTF
۷٫۷–۱۷٫۰ روی همین CPU عملاً غیرقابل‌استفاده است (روی گفتار Piper متن خالی یا حلقهٔ تکرار).
جزئیات و هشدار نقص `tts_played_ms` در BENCHMARKS §۵٫۸؛ جایگزینی/کالیبراسیون مدل ASR
اولویت بعدی است.

## ۴. کارهایی که فقط روی سخت‌افزار هدف معنا دارند (NOT TESTED)

- کیفیت و سرعت Whisper فارسی با وزن واقعی روی i7-6500U؛ RTF و تأخیر final.
  یک اجرای تک‌جهته انجام شد (بخش ۹.۳ `HARDWARE_VALIDATION.md`) و رونویسی ضعیف بود؛
  ارزیابی WER/CER روی مجموعهٔ جمله‌ها هنوز **NOT TESTED** است.
- کیفیت تلفظ Piper فارسی (اعداد، واژهٔ انگلیسی داخل جمله، نیم‌فاصله) با گوش انسان.
- حذف اکوی اتاق واقعی و کالیبراسیون `PVAI_AEC_DELAY_MS`؛ رفتار double-talk در بلندگو.
- jitter واقعی callback و dropout در همزمانی ASR+LLM+TTS.
- انتخاب صدای مرد/زن با فایل‌های واقعی `.onnx` و مجوز آن‌ها.
- قطع/وصل فیزیکی میکروفون و بلندگو در Windows 11.

چک‌لیست اجرای این موارد: [HARDWARE_VALIDATION.md](HARDWARE_VALIDATION.md).
بخش ۹ همان سند («مرز شواهد») مشخص می‌کند چه چیزی در محیط توسعه بررسی شده (SIMULATED) و
چه چیزی هنوز NOT TESTED است؛ سبز بودن `pytest` جای آن چک‌لیست را نمی‌گیرد.

## ۵. گزارش کامل بازبینی

خروجی A تا F (دفترچهٔ ایراد، فایل‌ها، تست، کارایی، موارد باز، چک‌لیست پذیرش) در
[AUDIT_REPORT.md](AUDIT_REPORT.md) است.

## ۶. اولویت بعدی

> transport سرور WebSocket **انجام شده** است (بند ۴ پیشین، ردیف جدول بالا، و
> `tests/test_ws_voice.py`). آنچه باقی مانده firmware دستگاه و اجرای واقعی روی LAN است،
> نه سرور. این بند قبلاً «کار بعدی» شمرده می‌شد و با وضعیت واقعی نمی‌خواند.

1. تهیهٔ وزن‌ها و اجرای `tools/benchmark_voice.py` روی هدف؛ ثبت اعداد در BENCHMARKS.
2. اجرای چک‌لیست سخت‌افزاری، مخصوصاً barge-in و بدون هدفون.
3. کلکسیون صدای ارزیابی (۴۰ جمله) و اجرای `benchmarks/persian_eval.py --audio-dir` برای WER واقعی.
4. firmware ESP32-S3 که با `WS /ws/voice` سخن بگوید، و تست LAN واقعی با دستگاه
   (سرور سمت خودش آماده و تست‌شده است).
5. ضبط واقعی سکوت/نویز و اجرای `benchmarks/persian_eval.py --no-speech-dir` برای
   «نرخ پذیرش کاذب»؛ تا آن زمان عدد آن `NOT MEASURED` است.

## بازبینی نوامبر ۲۰۲۶ (این شاخه)

- تست‌های بازتولیدکننده با ASGITransport، WebSocket درون‌فرایندی، مولد ساختگی کند Whisper،
  و ۴۷ نمونهٔ TTS اجرا شدند؛ `python -m pytest -q` ملاک نتیجهٔ جاری است، نه عدد قدیمی این سند.
- هدر Bearer برای کلاینت داخلی، محدودیت handshake مستقل از slot مدل، اصلاح overflow/pacing،
  لغو partial بین segmentهای Whisper و پنجرهٔ پیش‌فرض ۳ ثانیه، زبان auto، فیلتر ترکیبی،
  پیوند utterance و تلفظ اعداد/تاریخ/شماره پیاده‌سازی و با fake تأیید شدند.
- ماندگاری پیش‌فرض ۳۰ روز و purge روزانهٔ API؛ نشست‌های حلقهٔ محلی پیشوند `pc-` دارند
  و تنها از همان پیشوند برای resume بدون شناسه استفاده می‌کنند. شمارش فهرست نشست‌ها
  با cache اندازه/mtime فایل سریع‌تر شده؛ برخورد حروف بزرگ/کوچک قبلاً رد می‌شد.
- مالکیت انحصاری JSONL در مسیر نویسندهٔ local pipeline، دستگاه WS و `/api/chat`
  با قفل سیستم‌عامل روی lockfile دارای PID تضمین می‌شود؛ رقیب با `session_in_use`
  (REST 409) رد می‌شود. تست thread/process و API هم‌زمان با fake اجرا شد؛
  رفتار Windows و قطع ناگهانی فرایند روی سخت‌افزار هدف **NOT MEASURED** است.
  پیشوند `pc-` جلوی resume تصادفی نشست دستگاه را می‌گیرد؛ نشست‌های محلی قدیمی
  بدون این پیشوند خودکار resume نمی‌شوند و باید صریحاً مهاجرت/انتخاب شوند.

### NOT MEASURED (نیازمند مدل/دستگاه واقعی)

- کیفیت فارسی Whisper: WER/CER، false-reject گفتار، false-accept سکوت/نویز؛ آستانه‌های
  no_speech/logprob هنوز با فایل wav همسان کالیبره نشده‌اند.
- کیفیت پاسخ فارسی Qwen با وزن/سرور واقعی و تلفظ فارسی Piper با صدای واقعی.
- AEC و double-talk در اتاق واقعی: echo-only و پروکسی double-talk در بخش «تست واقعی اتاق AEC»
  اندازه‌گیری شد؛ double-talk با گفتار انسان، فاصلهٔ متغیر و تأخیر/قطع دستگاه واقعی همچنان NOT TESTED.
- بار CPU روی لپ‌تاپ دوهسته‌ای هدف و `asr_final_wait_behind_partial_ms` با مدل واقعی.
  آزمون مقایسه‌ای با همان backend ساختگیِ کند کاهش انتظار final را تأیید می‌کند،
  ولی فقط **SIMULATED** است، نه اندازه‌گیری سخت‌افزاری.
- firmware ESP32-S3، برقراری LAN/wss واقعی، jitter شبکه و VAD عصبی (پیاده‌نشده).


## بازبینی ایمنی inference — ۹ نوامبر ۲۰۲۶

- تشخیص CUDA با CTranslate2 برای `PVAI_ASR_DEVICE=auto` یا `cuda`؛ در نبود
  CUDA یا شکست بارگذاری GPU، fallback صریح با لاگ به CPU/int8. مقدار پیش‌فرض CPU
  حفظ شده است. MPS در CTranslate2 پشتیبانی نمی‌شود و به CPU برمی‌گردد.
- فایل ورودی ASR با librosa محلی به mono/16kHz/float32 تبدیل می‌شود؛ مسیر نامعتبر،
  فایل خراب/خالی و NaN/Inf با `AudioValidationError` رد می‌شوند. PCM زنده همچنان
  قرارداد PCM16 mono/16kHz دارد؛ دادهٔ خالی یا نمونهٔ ناقص رد می‌شود.
- `ModelLoadError` برای وزن‌های Whisper/Piper؛ بارگذاری Piper یک مسیر مشترک دارد.
  اسکریپت‌های llama پیش از اجرای واقعی نبود فایل وزن (حتی مسیر مطلق) را رد می‌کنند؛
  dry-run همچنان بدون وزن قابل استفاده است.
- generator تنبل ASR در success/error/preemption بسته می‌شود؛ مدل مشترک بین
  اتصال‌ها حفظ می‌شود و cache سراسری GPU در هر utterance تخلیه نمی‌شود.
- تست‌های مدل‌فری و تبدیل WAV مصنوعی: `tests/test_inference_safety.py`؛
  GPU، MPS، وزن‌های واقعی و اجرای PowerShell روی دستگاه واقعی **NOT MEASURED**.

## بازبینی MLOps و داده — ۹ نوامبر ۲۰۲۶

- trainer یا fine-tuning در مخزن موجود نیست؛ هایپرپارامتر آموزش بلااستفاده اضافه نشد.
- `tools/prepare_dataset.py`: CLI آفلاین با مسیرهای قابل تنظیم، ورودی JSON/JSONL/CSV،
  اعتبارسنجی schema/شناسه و recording تکراری/PCM16 mono WAV و export مسیر نسبی.
- loader ارزیابی: رد نوع نامعتبر، شناسهٔ ناامن برای مسیر صوت، no_speech با متن،
  hypothesis تکراری یا غیررشته‌ای؛ خطای ورودی CLI بدون traceback.
- راهنمای `docs/DATASETS.md` شامل جداسازی split، حریم خصوصی، قرارداد trainer خارجی
  و export محلی Hugging Face است. پشتیبانی آموزش یا دریافت dataset از Hub اضافه نشده.
- تست‌های ساختگی: `tests/test_dataset_preparation.py`؛ کیفیت دادهٔ واقعی و آموزش
  **NOT MEASURED**.

## بازبینی NLP: نرمال‌سازی متن، نیم‌فاصله و جمله‌بندی — ۹ نوامبر ۲۰۲۶

این گذر ماژول متن (`server/persian.py`، `server/live_voice.py`) را بازبینی و باگ‌های
واقعیِ بازتولیدشدنی را رفع کرد. هیچ عدد WER/CER جدیدی ادعا نمی‌شود؛ همه‌چیز با تست
ساختگی سنجیده شده است و کیفیت تلفظ واقعی **NOT MEASURED** است.

- **حروف عربی**: `ك/ي/ى/ة` از قبل نگاشت می‌شد، ولی `أ`/`إ`/`ٱ` نه. `normalize("إيران")`
  خروجی `إیران` می‌داد: نیمی از واژه فارسی و نیمی عربی، یعنی یک خطای بی‌دلیل در WER و
  یک نویسهٔ نادیده برای Piper. اکنون به `ا` نگاشت می‌شوند؛ `ؤ`/`ئ` عمداً دست‌نخورده‌اند
  (در فارسی حرف جدا هستند) و `ۀ` (`U+06C0`) برای ارزیابی `ه` و برای صدا `ه`+`U+0654`.
- **نیم‌فاصله**: سیاست `keep`/`space`/`remove` درست بود و `ZWNJ` حفظ می‌شود، اما `ZWNJ`
  سرگردان (کنار فاصله/نشانه یا تکراری) پاک نمی‌شد و چون `ZWNJ.isspace() == False` است،
  داخل توکن می‌ماند (`" ".join(s.split())` آن را جدا نمی‌کند). اکنون `_clean_zwnj`
  فقط نیم‌فاصلهٔ واقعی (بین دو حرف) را نگه می‌دارد. نویسه‌های نامرئی دیگر (ZWSP، ZWJ،
  soft hyphen، BOM، bidi) هم حذف می‌شوند — از PDF/وب کپی می‌شوند و در diff دیده نمی‌شوند.
- **سیاست ارزیابی (تغییر مستند)**: `normalize_for_eval` قبلاً علائم را **بدون جایگزین**
  پاک می‌کرد، پس `۳.۵ متر` → `35 متر` و `ساعت ۳:۳۰` → `330`، و `کتاب، دفتر` →
  `کتابدفتر`. اکنون (۱) جداکنندهٔ عدد پیش از هر چیز با `_normalise_separators`
  استاندارد می‌شود (`۳٫۵`/`3,5`/`3.5` → `3.5`؛ `۱٬۲۰۰`/`1,200`/`1.200` → `1200`)،
  (۲) علائم به **فاصله** تبدیل می‌شوند نه حذف. `٪`/`%` و `؟` عربی/فارسی هم یکی شدند.
  این یک تغییر سیاست است، نه تنظیم خاموش؛ در `docs/BENCHMARKS.md` بند‌به‌بند ثبت شده و
  `tests/test_benchmark_tools.py::test_policy_limits_are_documented_not_hidden`
  محدودیت‌های باقی‌مانده (رقم ≠ واژه، نیم‌فاصلهٔ اضافه) را پین می‌کند.
- **TTS**: `_LIST_MARKER_RE` و `_TIME_RE`/`_NUMBER_RE`/regexهای درصد و ارز فقط ارقام و
  جداکنندهٔ ASCII را می‌شناختند، پس `۱. اول` با صدای «یک.» خوانده می‌شد، `۱٬۲۰۰٬۰۰۰` به
  «یک٬دویست٬صفر» تبدیل می‌شد و `۳٫۴۵` (اعشار) «سه و چهل و پنج دقیقه» خوانده می‌شد.
  هر سه رفع شد؛ `٪۲۵` هم «بیست و پنج درصد» می‌شود. `spoken_numbers` روی `²` کرش می‌کرد
  (`isdigit` true ولی `int()` خطا) — حالا امن است.
- **جمله‌بندی streaming**: `_NOT_A_BOUNDARY` فقط ASCII را می‌شناخت، پس فهرست فارسی در
  حالت streaming شکسته می‌شد (`['۱.', 'اول ۲.', 'دوم']`). همچنین اگر پنجرهٔ ۱۸۰ نویسه‌ای
  هیچ فاصله‌ای نداشت، `rfind(" ")` منفی برمی‌گرداند و **هیچ چیز** آزاد نمی‌شد: تأخیر
  شروع گفتار تا پایان نوبت رشد می‌کرد. اکنون در همان حد بریده می‌شود.
- **کتابخانه‌ها**: `hazm`/`parsivar` یکپارچه **نشده‌اند** و این عمدی است (آفلاین، بدون
  وابستگی سنگین، یک سیاست نرمال‌سازی واحد). G2P واقعی وجود ندارد؛ تنها «G2P» تبدیل رقم
  به واژه است و Piper خودش واج‌سازی می‌کند. در `docs/DATASETS.md` بخش ۸ مستند شده، همراه
  با شرط پذیرش اگر روزی وابستگی اضافه شود.
- تست‌ها: `tests/test_persian.py`، `tests/test_tts_text_preparation.py` و
  `tests/test_benchmark_tools.py` (حدود چهل تست جدید، همه با fake/دادهٔ ساختگی).
  `python -m pytest` و `python tools/build_docs.py --check` باید سبز باشند. هیچ
  مدل/میکروفون/شبکه‌ای استفاده نشد؛ کیفیت تلفظ Whisper/Piper **NOT MEASURED**.

## بازبینی MLOps: پیکربندی، مسیرها و اعتبارسنجی داده — ۹ نوامبر ۲۰۲۶

این گذر خواستهٔ «هیچ hyper-parameter و هیچ مسیر محلی داخل اسکریپت نباشد» را برای
ابزارهای داده/آموزش پیاده کرد. **حلقهٔ آموزش همچنان در مخزن نیست** و هیچ عدد
کیفیتی ادعا نمی‌شود.

- `tools/training_config.py` (جدید): لایهٔ مشترک پیکربندی JSON/YAML با
  اعتبارسنجی نوع/بازه، رد کلید ناشناخته با پیشنهاد، و بازکردن `${VAR}`،
  `${VAR:-default}` و `~`. متغیر تعریف‌نشده **خطای پیکربندی** است (قبلاً به
  «manifest not found: .../${CORPUS_ROOT}/...» تبدیل می‌شد).
- مسیرهای نسبی در فایل پیکربندی نسبت به **پوشهٔ همان فایل** حل می‌شوند، نه `cwd`.
  `PVAI_CORPUS_ROOT` مبنای مسیر صوت است وقتی `--audio-root`/`audio_root` نباشد.
- `configs/`: `dataset.example.{yaml,json}` و `training.example.{yaml,json}`؛
  هیچ مسیر مطلق محلی در آن‌ها نیست و تست، برابری YAML و JSON را تضمین می‌کند.
- `tools/train_plan.py` (جدید): قرارداد trainer خارجی — `batch_size`، `grad_accum`
  (با `effective_batch_size`)، `learning_rate` در `(0,1]`، `epochs`، `seed`،
  `warmup_steps`، `precision` و مسیر هر split؛ `--write` یک plan ماشین‌خوان
  می‌نویسد و `--check --no-require-paths` طرح‌واره را بدون corpus بررسی می‌کند.
  پرچم خط فرمان بر فایل پیکربندی اولویت دارد.
- `tools/prepare_dataset.py`: `--config` چند split را یک‌جا اعتبارسنجی می‌کند،
  ورودی Hugging Face `save_to_disk` (DatasetDict و Dataset تک‌split، خواندن
  محلی `.arrow` با PyArrow و صراحت در نبودش) می‌پذیرد، `--check` گزارش نشت
  گوینده/فایل بین splitها می‌دهد و `--report` خروجی JSON ماشین‌خوان می‌نویسد.
  بررسی تازه: `id` ایمن، `min/max_duration_s`، تناقض فیلد `split`، و پیام خطای
  حاوی نام فایل برای WAV نامعتبر. هیچ فایلی بازنویسی نمی‌شود.
- مسیرهای خروجی ابزارهای بنچمارک/ارزیابی از محیط قابل تنظیم شدند:
  `PVAI_BENCHMARK_OUTPUT_DIR`، `PVAI_EVAL_SET` — گزارش دیگر به اجبار داخل checkout
  نوشته نمی‌شود.
- مستندات: `docs/DATASETS.md` بازنویسی شد (پیکربندی، قاعدهٔ مسیر، جدول بررسی‌ها،
  قرارداد آموزش، ورودی Hugging Face) و README/AGENTS/THIRD_PARTY هم‌گام شدند.
  `PyYAML` و `pyarrow` فقط dependency توسعه/ابزارند و در requirements-dev آمده‌اند.
- تست‌ها: `tests/test_training_config.py` (۵۳ تست، بدون مدل/شبکه؛ خواندن
  `save_to_disk` فقط وقتی `datasets`/`pyarrow` نصب باشند skip می‌شود). کیفیت دادهٔ
  واقعی، آموزش و رفتار روی Windows **NOT MEASURED**.

## بازبینی چرخهٔ عمر منابع و پیکربندی — ۹ نوامبر ۲۰۲۶

هدف این گذر «حافظهٔ نامحدود و مقدار نامعتبرِ خاموش» بود: هر جا عددی رشد می‌کرد
بدون سقف، یا مقداری از محیط بدون اعتبارسنجی به کتابخانه می‌رسید، همان‌جا بسته شد.

- `PVAI_ASR_DEVICE` فقط `cpu`/`auto`/`cuda`/`mps` را می‌پذیرد؛ مقدار دیگر
  (`gpu`، فاصله‌های اضافی، رشتهٔ خالی) به پیش‌فرض `cpu` برمی‌گردد و در
  `env_issues` + `/ready` با نام همان متغیر گزارش می‌شود، نه با خطای دیرهنگام CTranslate2.
- `LocalAudio.close()` و `Speaker.close()` اضافه شدند و `ModelPool.reset()`،
  `run_live` و `benchmarks/persian_eval.py`/`tools/benchmark_voice.py` در `finally:`
  آن‌ها را صدا می‌زنند؛ مدل Whisper/Piper پس از پایان کار یا بازنشانی pool باقی نمی‌ماند.
- `RateLimiter` سقف `max_keys` دارد (پیش‌فرض ۱۰۲۴) و قدیمی‌ترین bucket را دور می‌ریزد؛
  پویش شبکه دیگر دیکشنری را بی‌کران بزرگ نمی‌کند. `VoiceWebSocket._frame_stamps`
  از list به `deque` با پنجرهٔ یک‌ثانیه‌ای تبدیل شد و سقف نرخ **پیش از** append
  بررسی می‌شود، پس سیل فریم حافظهٔ آشکارساز را بزرگ نمی‌کند.
- `Pipeline.close()` صف پخش، صف final و utterance نیمه‌کاره را خالی می‌کند ولی
  `conversation.messages` و `reply` را دست نمی‌زند (تست‌های shutdown و
  `test_turn_failures.py` هر دو باید سبز بمانند). حلقهٔ ASR ارجاع job/PCM را در هر
  تکرار آزاد می‌کند و `respond()` فهرست بلااستفادهٔ `pieces` را نگه نمی‌دارد.
- `check_tts` و `LocalAudio.validate()` فایل `.onnx.json` را واقعاً parse می‌کنند
  (شیء JSON لازم است)؛ `read_wav_mono16k` فایل خالی/بریده/غیر-WAV را با
  `ValueError` و نام همان فایل رد می‌کند. سیاست سخت‌گیرانهٔ WAV (بدون resample خودکار)
  تغییر نکرده است.
- `_chunk_pcm`/`_to_mono_rate` در `server/live_voice.py` نرخ/کانال نامعتبر، بافر
  غیربایتی، طول ناهم‌تراز و NaN/Inf را با `VoiceError` رد می‌کنند و generator تنبل
  Piper در لغو barge-in صریحاً `close()` می‌شود.
- تست‌های جدید در `tests/test_inference_safety.py`، `test_streaming_voice.py`،
  `test_tts.py`، `test_ws_security.py`، `test_env_config.py` و
  `test_benchmark_tools.py`؛ همه بدون مدل/دستگاه واقعی. GPU، MPS، Piper واقعی و
  رفتار Windows **NOT MEASURED**.

## یکپارچه‌سازی مستندات — ۹ نوامبر ۲۰۲۶

`docs/PROJECT_GUIDE.md` نسخهٔ تک‌فایلی همهٔ راهنماهای فعال و پیوست‌های تاریخی است؛
با `tools/build_docs.py` از منبع تولید می‌شود و تست مانع کهنه‌شدن آن می‌شود.
ناسازگاری‌های پیش‌فرض partial (۳ ثانیه)، endpoint (۶۰۰ms)، thread مدل متنی
(کد: صفر/خودکار، نمونهٔ env: چهار)، نگه‌داری تاریخچه (۳۰ روز)، شرط توکن روی loopback،
HTTP 503 مدل بارنشده و ادعای اجرای تست Windows/macOS اصلاح شدند.
جدول مجوز با librosa هم‌گام شد؛ هیچ تأیید سخت‌افزاری تازه‌ای ادعا نشده است.

## مرحلهٔ ۷ — قفل نشست، jinja، فیلتر توهم، نام‌های رزرو‌شده، دانلود و wss (۱۱ نوامبر ۲۰۲۶)

هر مورد با تستِ اول‌نوشته‌شده (قرمز) و سپس رفع بسته شد؛ هیچ عدد کیفیتی ادعا نشده است.

| مورد | ادعا | تست |
|---|---|---|
| ۱ | آزادسازی **فوری** قفل نشست (OS lease + registry) پیش از teardown کند، و آزادسازی در حالت خطا/لغو/تأخیر | `tests/test_session_lease_release.py` (۱۰ تست) + `tests/test_ws_voice.py::test_device_resumes_a_session_with_its_earlier_context` |
| ۲ | `--jinja` در launcherها و هشدار `/ready` وقتی `chat_template_kwargs.enable_thinking` بی‌اثر است | `tests/test_llama_jinja.py` (۱۰ تست) |
| ۳ | فیلتر توهم فقط با ترکیب `no_speech_prob`+`avg_logprob` یا `compression_ratio` | `tests/test_asr_hallucination_filter.py` (۱۰ تست) |
| ۴ | رد نام‌های رزرو‌شدهٔ Windows با هر پسوند | `tests/test_reserved_session_names.py` |
| ۵ | رد `http` غیرمحلی، اجباری‌بودن sha256 برای مدل‌های شناخته‌شده، تأیید پیش از rename | `tests/test_download_parallel.py` |
| ۶ | راهنمای wss (Caddy/nginx/TLS مستقیم) و برخاستن واقعی uvicorn با TLS | `tests/test_tls_wss.py` + `docs/WSS_TLS.md` |
| ۷ | عدد تست از `pytest --collect-only` خوانده می‌شود، نه دستی | `tests/test_docs_consistency.py` |

### NOT MEASURED در این مرحله

- بهینهٔ آستانه‌های فیلتر توهم روی **دادهٔ فارسی برچسب‌دار**: هیچ مجموعهٔ سکوت/نویز
  فارسی با transcript مرجع در مخزن نیست ⇒ آستانه‌ها پیش‌فرض‌اند، نه کالیبره‌شده.
- اجرای واقعی Caddy/nginx زیر بار و handshake `wss://` از ESP32-S3.
- ASR/TTS/LLM واقعی برای هر تغییر این مرحله (همهٔ تست‌ها fake هستند).
- رفتار قفل فایل روی Windows (`msvcrt.locking`) در این نشست اجرا نشد؛ فقط Linux.

## پیش‌بارگذاری مدل مشترک ASR در startup — ۱۲ نوامبر ۲۰۲۶

بدون این تغییر، اولین اتصال ESP32 در `ModelPool` مدل Whisper را از صفر بار می‌کرد.
روی این لپ‌تاپ دو‌هسته‌ای، `asr_model_load_ms` در `tools/mic_probe.py` برابر
**۴۵٫۹ تا ۶۳٫۷ ثانیه** اندازه‌گیری شد (MEASURED، بخش ۹.۳ `docs/HARDWARE_VALIDATION.md`) —
یعنی اولین utterance دستگاه عملاً به timeout می‌خورد.

| مورد | رفتار | تست |
|---|---|---|
| `PVAI_ASR_WARMUP=true` + `PVAI_WS_ENABLED=true` | `MODEL_POOL.preload()` در thread daemon: `audio()` (لود) و `warmup()` (decode ۲۰۰ms سکوت) | `test_ws_security.py::test_preload_warms_the_shared_model_once` |
| بدون transport صوتی (`ws_enabled=False`) | هیچ مدلی بار نمی‌شود؛ سرور فقط REST هزینهٔ ASR را نمی‌پردازد | `test_ws_security.py::test_startup_preload_needs_the_voice_transport` |
| `voice_bundle` تزریق‌شده (تست/اپ میزبان) | preload اجرا نمی‌شود؛ مالکیت مدل دست فراخوان است | همان تست |
| شکست لود (وزن غایب/خراب) | فقط `logger.warning` + traceback؛ startup و `/ready` سالم می‌مانند | پوشش با همان مسیر `_preload_voice_models` |
| `ModelPool.reset()` | حالا `telemetry` را هم صفر می‌کند تا شمارش بارگذاری بین تست‌ها/ری‌ست‌ها تجمع نکند | `test_resetting_the_model_pool_releases_the_whisper_model` |

startup **بلاک نمی‌شود**: thread جدا و daemon است، پس `/health` و `/ready` بلافاصله پاسخ می‌دهند.
این عدد صرفه‌جویی (چند ده ثانیه) روی دستگاه واقعی اندازه‌گیری نشده است؛ فقط می‌دانیم لود مدل
چقدر طول می‌کشد. صرفه‌جویی خودکار = همان مدت لود که به startup منتقل شده است (SIMULATED).

### NOT MEASURED در این مرحله

- زمان «اولین utterance» روی ESP32-S3 واقعی قبل و بعد از preload.
- مقدار حافظهٔ بلندمدت اشغال‌شده توسط مدل پیش‌بارگذاری‌شده در سروری که هیچ دستگاه وصل نمی‌کند
  (با `PVAI_WS_ENABLED=false` این مسیر کلاً اجرا نمی‌شود، پس مورد بحث نیست).

## بازبینی نقص‌یابی — ۱۳ نوامبر ۲۰۲۶

بازبینی کد (نه اجرای سخت‌افزار) برای یافتن مسیرهای نیمه‌کاره و اشتباه‌های خاموش.
هر مورد با تستِ افزوده‌شده بسته شد؛ هیچ ادعای سخت‌افزاری تازه‌ای اضافه نشده است.

| مورد | نقص | رفع | تست |
|---|---|---|---|
| ۱ | `NameError` بالقوه در `_open_pipeline`: `httpx` فقط داخل یک تابع دیگر import شده بود، پس مسیر خطای probe به استثنای خام می‌رسید | `import httpx` به سطح ماژول و انتقال probe به `_probe_local_api()` (timeout ۲ ثانیه، فقط `HTTPError/TimeoutError/OSError`، پاسخ ۴۰۱ ⇒ `api_auth_failed`) | `test_ws_security.py::test_a_dead_ready_probe_does_not_kill_a_device_connection`, `::test_a_401_from_the_local_api_is_reported_as_api_auth_failed` |
| ۲ | `readiness.check_ws` توکن‌های per-device را نمی‌دید؛ `/ready` با `PVAI_WS_TOKENS` تنها، پیام گمراه‌کننده می‌داد | بررسی `device_tokens()` + اشارهٔ پیام فارسی به `PVAI_WS_TOKENS` | `test_api.py::test_ready_accepts_a_per_device_token_list_without_a_shared_token` |
| ۳ | **گزارش غلط تأخیر VAD:** `onset_delay_ms`/`endpoint_delay_ms` شمارش فریم را با ثابت ۲۰ms تبدیل می‌کردند، پس با `PVAI_AUDIO_BLOCK_MS=10/40` عدد `vad_start_ms`/`vad_end_ms` دو برابر/نصف واقعیت بود | `EnergyVAD(block_ms=...)`؛ `build_vad` مقدار `settings.audio_block_ms` را می‌دهد و `describe()` هم `block_ms`/تأخیرها را برمی‌گرداند | `test_vad.py::test_delay_reporting_tracks_the_configured_block_length`, `::test_a_zero_block_length_is_refused` |
| ۴ | `tools/mic_probe.py` با `--block-ms` مستقل از `PVAI_AUDIO_BLOCK_MS` کار می‌کرد؛ طول segment و تأخیرها با برچسب MEASURED غلط می‌شدند | پرچم فقط ۱۰/۲۰/۴۰ را می‌پذیرد و `Settings` با همان مقدار بازساخته می‌شود | `test_mic_probe.py::test_probe_refuses_a_block_length_the_service_would_not_use` |
| ۵ | مهلت `HELLO` سه ثانیه بود، خلاف «ده ثانیه»ی `docs/PROTOCOL.md`؛ handshake TLS روی ESP32-S3 در آن پنجره جا نمی‌شد | `HANDSHAKE_TIMEOUT_S = 10.0`؛ محدودیت نیمه‌باز همان‌جا با `WsCounters.handshake_enter` اعمال می‌شود | `test_ws_voice.py::test_the_hello_deadline_matches_the_documented_ten_seconds` |
| ۶ | تعریف‌های مردهٔ تکراری `_env_str`/`_env_choice` در `config.py` (نسخهٔ بی‌اثر که `_record` را صدا نمی‌زد) | حذف نسخهٔ مرده، نگه‌داشتن نسخهٔ فعال | پوشش غیرمستقیم با تست‌های پیکربندی |
| ۷ | flakiness در `test_a_device_that_delivered_real_frames_earns_a_fresh_budget` (پنجرهٔ ۱۰ms پیش از تخلیهٔ ۵۰ فریم صف) | پنجرهٔ همان تست به ۲۰۰ms رفت؛ **کد تولیدی تغییر نکرد** | همان تست، ۸ اجرای متوالی سبز |
| ۸ | **سنجهٔ مرده:** `vad_onset_events` در `METRIC_NAMES` اعلام شده بود ولی هیچ ماژولی آن را ثبت نمی‌کرد؛ نام دوم برای همان onset که `utterances` می‌شمارد | حذف از `METRIC_NAMES`؛ تست نگهبان هر نام اعلام‌شده را با متن `server/*.py` تطبیق می‌دهد | `test_metrics.py::test_every_declared_metric_is_actually_emitted_somewhere` |
| ۹ | **سنجه‌های ثبت‌شدهٔ اعلام‌نشده:** ۲۰ نام (`ws_capture_out_of_order`, `ws_heartbeats`, `asr_model_load_ms`, `api_rate_limited`, …) در کد ثبت می‌شدند ولی در `METRIC_NAMES` نبودند، پس هر مصرف‌کنندهٔ رجیستری آن‌ها را نمی‌دید | افزودن آن ۲۰ نام با گروه‌بندی موجود (ASR، تعامل، WS، HTTP) + تست نگهبان جهت معکوس | `test_metrics.py::test_every_recorded_metric_is_declared` |
| ۱۰ | **`/ready` دروغ می‌گفت:** `check_storage` فقط `is_dir()` را می‌دید، پس پوشهٔ read-only «آماده» گزارش می‌شد و اولین نوبت ذخیره با `OSError` می‌مرد؛ این بررسی هیچ تستی نداشت | `_can_write()` با فایل probe واقعی و حذف در `finally`؛ پیام فارسی شامل نوع و متن خطا | `test_readiness_storage.py` (۵ تست) |
| ۱۱ | **کد مردهٔ مکالمه:** `Conversation.record_turn`، `Conversation.last_user` و `load_conversations` بدون هیچ مصرف‌کننده‌ای بودند؛ `purge_older_than` عمداً نگه داشته شد چون مصرف زنده دارد | حذف سه تابع و import بی‌مصرف `Iterable` | مجموعهٔ کامل `pytest` سبز؛ بدون تغییر رفتار |
| ۱۲ | **بی‌تست:** `is_loopback_host`، `parse_device_tokens` (با `ENV_ISSUES`)، `StreamPlan.parameters`، `probe_aec` و `aec_status` فقط غیرمستقیم پوشش داشتند؛ import بی‌مصرف `StreamDone` در `server/main.py` و `re` در `server/streaming_voice.py` | تست مستقیم برای همهٔ توابع بالا؛ حذف دو import بی‌مصرف | `test_config_helpers.py`، `test_aec_status.py` (۲۵ تست)؛ مجموعهٔ کامل سبز |

مستندات هم‌گام: `AGENTS.md` §۶ و `docs/SPEC.md` §۶ (خروجی ۱۶kHz، فریم ۲۰ms در transport،
فهرست واقعی پیام‌ها، `turn_id`/`utterance_id`) و پیام‌های ناموجود (`DEVICE_INFO`/`INPUT_END`/
`SET_VOLUME`) صریحاً «پیاده‌نشده» علامت خوردند.

### NOT MEASURED در این بازبینی

- هیچ‌کدام از این رفع‌ها روی ESP32-S3 واقعی یا با میکروفون/بلندگوی واقعی اجرا نشد؛ همه با fake تست شده‌اند.
- مقدار واقعی `vad_start_ms`/`vad_end_ms` روی سخت‌افزار با `PVAI_AUDIO_BLOCK_MS` غیر از ۲۰ اندازه‌گیری نشد؛
  فقط درستی تبدیل فریم‌به‌میلی‌ثانیه تست شده است.

## تست واقعی اتاق AEC — ۱۳ نوامبر ۲۰۲۶

این مرحله فقط روی **لپ‌تاپ توسعه** اجرا شد (میکروفون و بلندگوی توکار Realtek، ویندوز،
ولوم ۱۰۰٪)؛ ESP32-S3 و اتاق با میکروفون دور همچنان NOT TESTED هستند. ابزار تازه
`tools/aec_room_measure.py` (تست‌شده با `tests/test_aec_room_measure.py`، ۹ تست) محرک
گفتار واقعی Piper پخش می‌کند و مرجع AEC را همان PCMی می‌گیرد که در `outdata` نوشته شده است.

| سنجه | MME (پیش‌فرض) | DirectSound |
|---|---|---|
| تأخیر صوتی مؤثر | ۳۵۸ ms | ۲۳۱ و ۴۴۸ ms در دو نشست |
| ERLE با `PVAI_AEC_DELAY_MS=40` | ۰٫۰۱ dB | ۰٫۰۱ dB |
| ERLE با تأخیر کالیبره | ۶٫۸۲ dB @ ۳۴۰ms | ۵٫۸۸ dB @ ۴۴۰ms |
| CPU هر فریم (RTF) | ۰٫۱۵ms (۰٫۰۰۸) | ۰٫۱۵ms (۰٫۰۰۸) |
| حفظ سیگنال نزدیک در double-talk | −۰٫۱۷ dB | −۰٫۰ dB |

- **نقص واقعی پیدا شد:** پیش‌فرض ۴۰ms با تأخیر مؤثر ۳۵۸ms (MME) هیچ هم‌ترازی ندارد و AEC
  عملاً خاموش است؛ فقط با `PVAI_AEC_DELAY_MS=340` به ۶٫۸۲dB می‌رسد. این در
  `docs/ISSUE_REGISTER.md` به‌عنوان مورد باز ثبت شد.
- تأخیر **بین نشست‌ها/بک‌اندها ناپایدار** است (DirectSound: ۲۳۱ و ۴۴۸ms) ⇒ یک عدد ثابت در
  `.env` برای اتاق واقعی کافی نیست؛ تخمین خودکار تأخیر در این نسخه پیاده نشده است.
- ERLE کالیبره نزدیک سقف مجاز همان SNR است؛ عدد پایین‌تر به معنی فیلتر خراب نیست.
- WASAPI/WDM-KS روی این دستگاه ۱۶kHz را قبول نمی‌کنند (فقط ۴۸kHz) و با قرارداد ۱۶k مسیر
  جایگزین کم‌تأخیر وجود ندارد.
- double-talk با گفتار انسان، قطع فیزیکی دستگاه و barge-in کامل با LLM زنده **NOT TESTED**؛
  فقط پروکسی آکوستیک با سیگنال مستقل اجرا شد.
- گزارش خام: `benchmarks/results/aec-room-mme-20261113.json` و
  `benchmarks/results/aec-room-ds-20261113.json`؛ جزئیات در `docs/AEC.md` و §۹.۵
  `docs/HARDWARE_VALIDATION.md`.

## پاک‌سازی پیکربندی — ۱۳ نوامبر ۲۰۲۶

در working tree، چند فیلد پیکربندی برای قابلیت‌هایی اضافه شده بود که هیچ مصرف‌کننده‌ای
نداشتند؛ یعنی تنظیمشان بی‌صدا هیچ اثری نمی‌گذاشت. این فیلدها حذف شدند تا `/ready`
و مستندات با رفتار واقعی یکی بمانند:

- `PVAI_VAD_BACKEND`/`PVAI_VAD_WEBRTC_MODE` — `build_vad` همیشه `EnergyVAD` می‌سازد؛
  VAD عصبی/WebRTC پیاده نشده و نیازمند ارزیابی اندازه‌گیری‌شده است (OPEN-04).
- `PVAI_MEMORY_*` (حافظهٔ بلندمدت) — هیچ ماژول حافظه‌ای وجود ندارد؛
  تصمیمش در `docs/SPEC.md` §۱۷ بند ۵ هنوز باز است.
- `PVAI_AEC_AUTO_DELAY*` — تخمین خودکار تأخیر پیاده نشده؛ `PVAI_AEC_DELAY_MS` دستی
  تنها راه کالیبره است (OPEN-06، `docs/AEC.md`).
- `PVAI_AUDIO_OUTPUT_RATE` — خروجی همچنان ۱۶kHz است؛ مسیر ۲۴kHz پیاده نشده.

همچنین `PVAI_AUDIO_SAMPLE_RATE` دوباره فقط `16000` را می‌پذیرد: نسخهٔ موقت، `48000`
را با ادعای «بازنمونه‌برداری خودکار» می‌پذیرفت در حالی که هیچ resampler ای وجود ندارد و
این مقدار VAD/ASR/AEC را بی‌صدا خراب می‌کرد (رجعت به رفع P2-07). تست:
`tests/test_tts.py::test_non_16k_capture_rate_is_a_configuration_error`.

این قابلیت‌ها هنوز **پیاده‌نشده** هستند و باید بعداً هرکدام جداگانه، با تست و اندازه‌گیری،
اضافه شوند. مجموعهٔ تست سبز است و تعداد تست‌ها همان عدد زندهٔ `pytest --collect-only` است.

## اجرای کامل بنچمارک با مدل‌های واقعی — ۱۳ نوامبر ۲۰۲۶

- `duplex,bargein,combined` با Whisper-small + Piper + Qwen3.5-4B روی همین لپ‌تاپ اجرا شد
  (گزارش خام: `benchmarks/results/voice-hw-20261113.json`؛ جزئیات: `docs/BENCHMARKS.md` §۴.۵).
  ابزار ورودی گفتار واقعی ندارد (تُن مصنوعی) و Whisper آن را رونویسی نمی‌کند، پس هیچ نوبتی
  کامل نشد و معیارهای تأخیر/`barge_in_latency_ms` همچنان `NOT MEASURED` ماندند.
- در همین اجرا دو نقص برچسب‌گذاری پیدا و رفع شد: ردیف بدون مقدار دیگر `MEASURED` برچسب
  نمی‌خورد، و سقف انتظار ۶۰ ثانیه‌ای به‌عنوان `turn_total_ms`/`turn_latency` گزارش نمی‌شود
  (`tests/test_benchmark_tools.py`).

## تست زندهٔ یک‌نوبتی و میز تست وب — ۱۳ نوامبر ۲۰۲۶

روی همین لپ‌تاپ (Realtek MME، بلندگوی توکار، AEC خاموش برای این تست) زنجیرهٔ واقعی
«میکروفون → VAD → Whisper-small → llama.cpp (Qwen3.5-4B) → Piper» یک‌بار کامل اجرا شد
(ابزار موقت probe، بعد با `server/webui.py` جایگزین شد):

| سنجه | مقدار | برچسب |
|---|---|---|
| طول utterance ضبط‌شده | ۷٫۶ s | MEASURED |
| زمان رونویسی Whisper (`asr_ms`) | ۳۵٫۶ s (RTF ۴٫۶۹) | MEASURED |
| کیفیت رونویسی | متن فارسی نادرست/نامفهوم | MEASURED (منفی) |
| پاسخ LLM | «متوجه نشدم…» (صداقت درست، ولی ورودی خراب بود) | MEASURED |
| زمان پاسخ LLM (`wall_ms`) | ~۳۹٫۹ s (بیشتر prompt-eval؛ ~۲۷ s) | MEASURED |
| پخش پاسخ Piper | ۲۱٫۵ s | MEASURED |

- **علت اصلی، ASR است نه LLM:** با متن تمیز و مستقیم، LLM فارسی را درست می‌فهمد
  (پایتخت ایران، پیشنهاد عملی؛ حساب وقتی «فقط عدد» خواسته شد: «چهار»، «نُه»؛ یک نمونه هم
  اشتباه «شش» داد). با `PVAI_LLM_ENABLE_THINKING=true` پاسخ ۱۴۱ s و ۵۱۲ توکن reasoning با
  `content` خالی بود ⇒ برای صدا خاموش می‌ماند.
- **حلقهٔ زندهٔ پیوسته روی این CPU قابل استفاده نیست:** در اجرای `python -m server.voice`،
  الگوهای گفتار پشت‌سرهم صف final را پر کرد؛ `dispatch_wait_ms` تا ۱۳۷ s رسید،
  پیام «ورودی صوت عقب افتاد؛ نوبت جاری حذف شد» آمد و رونویسی اولین utterance خالی بود.
  یعنی محدودیت RTF همان Whisper است، نه باگ صف.
- **راه‌حل موقت برای تست:** یک‌نوبت در هر لحظه + رونویسی قابل‌ویرایش در میز تست وب
  ([WEB_UI.md](WEB_UI.md)): صفحه روی loopback، push-to-talk، ارسال `POST /api/turn`،
  نمایش متن شنیده‌شده و پخش WAV برگشتی. تست‌های fake: `tests/test_webui.py` (۸ تست).
