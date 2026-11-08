# وضعیت اجرایی پروژه — PersianVoiceAI Conversation Engine

آخرین به‌روزرسانی: ۸ نوامبر ۲۰۲۶ — مرحلهٔ ۱ تا ۶ بازبینی: رفع P0، بهبود خط لولهٔ صوتی، ماندگاری مکالمه،
ابزار بنچمارک/ارزیابی و مستندسازی. مبنای تغییر: commit `9654a68`، شاخهٔ `arena/31347dde-persianvoiceai`.

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
| VAD انرژی با هیسترزیس، پیش‌رول، کف نویز تطبیقی | پیاده‌شده + تست؛ صدای واقعی **تست نشده** | `server/vad.py` |
| ASR افزایشی: صف final بدون‌افت + partial قابل‌جایگزینی | پیاده‌شده + تست کامل P0 | `server/streaming_voice.py` |
| تأخیر final پشت partial: سنجهٔ تفکیک‌شده + لغو partial در حال decode | پیاده‌شده + تست SIMULATED؛ عدد دستگاه **NOT MEASURED** | `tests/test_asr_preemption.py` |
| utterance ردشدهٔ VAD: رویداد صریح، حالت درست، پاسخ نوبت قبلی حفظ می‌شود | پیاده‌شده + تست | `tests/test_short_noise_utterance.py` |
| شکست TTS/LLM/context: `failed` + خروج از `THINKING`/`SPEAKING` | پیاده‌شده + تست | `tests/test_turn_failures.py` |
| جدا شدن طول عمر utterance و generation دستیار | پیاده‌شده + تست (۶ سناریوی الزامی) | `tests/test_utterance_lifecycle.py` |
| ماشین حالت صریح `idle/listening/transcribing/thinking/speaking` | پیاده‌شده + تست جدول گذار | `tests/test_state_machine.py` |
| barge-in: توقف LLM/TTS/پخش، بدون آسیب به صف final | پیاده‌شده + تست fake؛ تأخیر دستگاه **تست نشده** | `tests/test_tts.py`, `tests/test_long_run.py` |
| TTS Piper: انتخاب مرد/زن، جمله‌بندی، لغو، نرمال‌سازی نرخ | پیاده‌شده + تست؛ وزن فارسی **تست نشده** | `server/live_voice.py` |
| AEC SpeexDSP duplex پیش از VAD/ASR | پیاده‌شده + تست مصنوعی؛ اتاق واقعی **تست نشده** | `server/aec.py`, `docs/AEC.md` |
| تاریخچه: نشست، وضعیت interrupted، برش توکنی، JSONL محلی | پیاده‌شده + تست | `server/conversation.py`, `server/sessions.py` |
| readiness و health (`/ready`, `/health`) | پیاده‌شده + تست | `server/readiness.py`, `tests/test_api.py` |
| سنجه‌های کامل خط لوله (شامل jitter callback، صف، CPU/RAM در اجرای زنده) | پیاده‌شده + تست | `server/metrics.py`, `server/audio_io.py` |
| بنچمارک کل زنجیرهٔ صوت | ابزار آماده (`tools/benchmark_voice.py`)؛ اعداد واقعی هدف **NOT MEASURED** | `docs/BENCHMARKS.md` |
| ارزیابی فارسی WER/CER | مجموعهٔ ۴۰ جمله + ابزار؛ اعداد واقعی **NOT MEASURED** | `benchmarks/persian_eval.py` |
| پروتکل و **transport سمت سرور** WebSocket صوتی (`WS /ws/voice`) | پیاده‌شده + تست (fake)؛ احراز هویت اجباری با توکن دستگاه | `server/protocol.py`, `server/ws_voice.py`, `tests/test_ws_voice.py` |
| firmware ESP32-S3، رجیستری per-device، OTA، تست LAN واقعی | **پیاده‌نشده / NOT MEASURED** | — |
| Command Broker / IoT | **پیاده‌نشده** | — |

## ۲. وضعیت تست

```text
$ python -m pytest
269 passed, 5 skipped in 4.9s
```

- دو تست skip فقط تست‌های native SpeexDSP هستند: در sandbox کتابخانهٔ native ساخته نشده و تست
  به‌جای شکست، صریحاً skip می‌شود. اگر `libspeexdsp` نصب باشد اجرا می‌شوند (در اجرای قبلی همین
  محیط: ۵۴ تست موفق با کتابخانهٔ ساخته‌شده).
- تست پایه پیش از این مرحله: `53 passed, 2 skipped`. یعنی ۲۱۶ تست جدید یا گسترش‌یافته؛
  آخرین افزوده: ۱۴ تست transport وب‌سوکت + ۱ تست قطع کلاینت SSE + ۲ تست پیکربندی/readiness +
  ۱۶ تست مرحلهٔ سوم (۴ باگ: نویز کوتاه/utterance ردشده، شکست TTS، context خالی، preemption partial).
- ۵ skip این محیط: ۳ تست native SpeexDSP (کتابخانهٔ native ساخته نشده) + ۲ تستی که به numpy نیاز
  دارند و در sandbox نصب نیست. هیچ‌کدام به منطق موتور مکالمه مربوط نیست.
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
| ۱۷ | ماندگاری context پس از restart | `test_conversation.py::test_session_is_persisted_and_reloaded`, `test_api.py::test_chat_with_session_id_persists_and_reuses_context` |
| ۱۸ | برش توکنی | `test_conversation.py::test_context_is_truncated_by_tokens_not_only_message_count` |
| ۱۹ | کار آفلاین بدون API خارجی | `test_voice.py::test_whisper_never_downloads`, `test_public_api_rejected` |
| ۲۰ | نبود صف/حافظهٔ نامحدود | `test_long_run.py` (۲ تست) + `test_conversation.py` (۲ تست bounded-RAM) |

## ۳. اعداد اندازه‌گیری‌شده در sandbox

| معیار | مقدار | معنا |
|---|---|---|
| طول عمر خط لوله در حالت سکوت (jitter p95) | ۰٫۳۹ ms | فقط pacing هماهنگ‌کننده؛ **jitter واقعی callback دستگاه نیست** |
| RAM پروسه در حالت idle | ۲۹٫۵ MB | بدون مدل لود شده |
| CPU حالت idle | ~۰٪ | بدون مدل لود شده |
| VAD false positive روی سکوت | ۰ | باورپذیر برای آستانهٔ انرژی |
| سنجه‌های زنده: jitter/صف/CPU/RAM | ثبت می‌شوند | `audio_io.pump` هر ۱ ثانیه (`test_pump_publishes_system_and_queue_metrics`) |

هیچ عدد ASR/LLM/TTS روی این sandbox تولید نشد: مدل‌ها نصب نیستند و llama-server اجرا نبود.
جدول کامل با برچسب `MEASURED`/`SIMULATED`/`NOT MEASURED` در [BENCHMARKS.md](BENCHMARKS.md).

## ۴. کارهایی که فقط روی سخت‌افزار هدف معنا دارند (NOT TESTED)

- کیفیت و سرعت Whisper فارسی با وزن واقعی روی i7-6500U؛ RTF و تأخیر final.
- کیفیت تلفظ Piper فارسی (اعداد، واژهٔ انگلیسی داخل جمله، نیم‌فاصله) با گوش انسان.
- حذف اکوی اتاق واقعی و کالیبراسیون `PVAI_AEC_DELAY_MS`؛ رفتار double-talk در بلندگو.
- jitter واقعی callback و dropout در همزمانی ASR+LLM+TTS.
- انتخاب صدای مرد/زن با فایل‌های واقعی `.onnx` و مجوز آن‌ها.
- قطع/وصل فیزیکی میکروفون و بلندگو در Windows 11.

چک‌لیست اجرای این موارد: [HARDWARE_VALIDATION.md](HARDWARE_VALIDATION.md).

## ۵. گزارش کامل بازبینی

خروجی A تا F (دفترچهٔ ایراد، فایل‌ها، تست، کارایی، موارد باز، چک‌لیست پذیرش) در
[AUDIT_REPORT.md](AUDIT_REPORT.md) است.

## ۶. اولویت بعدی

1. تهیهٔ وزن‌ها و اجرای `tools/benchmark_voice.py` روی هدف؛ ثبت اعداد در BENCHMARKS.
2. اجرای چک‌لیست سخت‌افزاری، مخصوصاً barge-in و بدون هدفون.
3. کلکسیون صدای ارزیابی (۴۰ جمله) و اجرای `benchmarks/persian_eval.py --audio-dir` برای WER واقعی.
4. WebSocket صوتی روی `server/protocol.py` برای ESP32-S3 (پس از پاس‌شدن تست‌های PC).
