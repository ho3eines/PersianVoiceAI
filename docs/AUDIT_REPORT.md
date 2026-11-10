# گزارش نهایی بازبینی موتور مکالمهٔ PersianVoiceAI

مبنا: commit `9654a68` → شاخهٔ `arena/31347dde-persianvoiceai`. این سند همان خروجی‌های A تا F
راستی‌آزمایی است و **تنها** چیزهایی را «انجام‌شده» می‌نامد که کد و تست دارند.

---

## A. دفترچهٔ ایراد (خلاصهٔ اجرایی)

| شناسه | شدت | فایل / تابع | ریشه | رفع | تست |
|---|---|---|---|---|---|
| P0-01 | P0 | `streaming_voice.py` (`interrupt`, `recognize`, `offer`, `feed`) | صف مشترک partial/final، بررسی `generation` بعد از decode، خالی‌کردن صف در interrupt | تفکیک `Utterance`/`AssistantTurn`/`PartialASR`/`FinalASR`؛ صف final FIFO بدون‌افت؛ dedupe با `utterance_id` | `test_utterance_lifecycle.py` (۶ سناریو) |
| P0-02 | P0 | `streaming_voice.py`, `voice.py` | decode کل بخش در حال رشد هر ۰٫۸s، head-of-line blocking | پنجرهٔ partial محدود ۶s، اولویت مطلق final، سنجه‌های `asr_*` | `test_asr_scheduler.py` |
| P0-03 | P0 | `config.py`, `voice.py` | ۴ thread برای Whisper روی ۲ هستهٔ فیزیکی | `PVAI_ASR_THREADS` جدا، صف محدود، AEC خارج از callback | `test_resources.py`, `test_audio_io.py` |
| P1-01 | P1 | `respond` | system prompt در مسیر صوتی ارسال نمی‌شد | `context_with_system` در هر دو مسیر | `test_system_prompt_is_sent_to_the_model` |
| P1-02 | P1 | `feed` | لغو یکسان در پنج وضعیت مختلف | ماشین حالت + barge-in مقید | `test_state_machine.py` |
| P1-03 | P1 | `live_voice.Endpoint` | آستانهٔ ثابت، بدون هیسترزیس/پیش‌رول | `server/vad.py` | `test_vad.py` (۱۲ تست) |
| P1-04 | P1 | `live_voice.Speaker` | تک‌صدا، نبود سنجهٔ تأخیر | انتخاب male/female، callbackهای first chunk/frame/complete | `test_tts.py` |
| P1-05 | P1 | `streaming_voice` history | فقط RAM، برش با تعداد پیام، پاسخ قطع‌شده بدون وضعیت | `server/conversation.py` + JSONL | `test_conversation.py`, `test_sessions.py` |
| P1-06 | P1 | `main.py` | نبود readiness و جداسازی نشست | `/ready`, `/api/sessions`, `session_id` | `test_api.py` (+ جداسازی نشست) |
| P1-07 | P1 | `run_live` | دستگاه پیش‌فرض، بدون validation/recovery | `server/audio_io.py` | `test_audio_io.py` |
| P1-08 | P1 | `aec.py` | فریم نامعتبر `ValueError`، نبود وضعیت startup | `aec_status`, پیام فارسی، سنجهٔ `aec_process_ms` | `test_aec.py` |
| P1-09 | P1 | — | بدون سنجه | `server/metrics.py` | `test_metrics.py` |
| P1-10 | P1 | `AsrScheduler.next` | lost wakeup: `check → clear → wait` | clear قبل از بررسی مجدد | `test_submit_landing_between_check_and_wait_is_not_lost` |
| P1-11 | P1 | `Pipeline.play` | حالت پس از commit در `SPEAKING` گیر می‌کرد | گذار به LISTENING/IDLE | `test_pipeline_walks_the_documented_states_for_one_exchange` |
| P1-12 | P1 | `Speaker._chunk_pcm` | نرخ بومی Piper (22.05k) داخل stream 16k | `_to_mono_rate` | `test_speaker_only_path_resamples_piper_rate_to_the_output_stream` |
| P1-13 | P1 | `resources._LinuxSampler` | baseline CPU صفر ⇒ میلیون‌درصد | prime با tick فعلی | `test_fresh_sampler_first_reading_is_plausible` |
| P1-14 | P1 | `_commit_turn` | قطع در جملهٔ اول، کل متن تولیدشده را «شنیده‌شده» ثبت می‌کرد | `turn.current` و سه‌حالت ثبت | `test_bargein.py::test_sentences_queued_behind_the_spoken_one_never_play_after_barge_in` |
| P2-01…P2-08 | P2 | — | نبود سنجه/بنچمارک/پنجرهٔ partial/`pending_history`/`vad_filter`/برش ناهمگام/نرخ نمونه‌برداری/RAM نامحدود | همه رفع‌شده | `test_metrics`, `test_benchmark_tools`, `test_vad`, `test_conversation`, `test_long_run` |
| P3-01…P3-04 | P3 | — | نام‌گذاری، مستندات کهنه، نبود ارزیابی فارسی، گزارش آستانهٔ مؤثر | همه رفع‌شده | `test_persian`, `test_vad` |

موارد باز: **OPEN-01** توقف قطعی inference در حال اجرا (نیازمند process isolation)،
**OPEN-02** firmware ESP32/رجیستری/OTA (transport سرور و تست قطع SSE بسته شدند — بخش G)،
**OPEN-03** اکوی واقعی اتاق، **OPEN-04** VAD عصبی، **OPEN-05** Command Broker/IoT.

## B. فایل‌های تغییریافته

**افزوده (کد):** `server/audio_io.py`, `conversation.py`, `metrics.py`, `persian.py`, `protocol.py`,
`readiness.py`, `resources.py`, `sessions.py`, `vad.py`
**اصلاح (کد):** `streaming_voice.py`, `live_voice.py`, `config.py`, `aec.py`, `voice.py`, `main.py`
**افزوده (ابزار/داده):** `tools/benchmark_voice.py`, `benchmarks/persian_eval.py`,
`benchmarks/persian_eval_set.json`, `benchmarks/results/voice-sandbox-20261108.json`
**افزوده (تست):** `test_utterance_lifecycle`, `test_asr_scheduler`, `test_state_machine`,
`test_bargein`, `test_long_run`, `test_benchmark_tools`, `test_audio_io`, `test_aec`, `test_api`,
`test_conversation`, `test_metrics`, `test_persian`, `test_protocol`, `test_resources`,
`test_sessions`, `test_streaming_voice`, `test_tts`, `test_vad`, `test_voice`, `voicekit.py`
**مستندات:** `README`, `AGENTS`, `.env.example`, `docs/STATUS`, `docs/SPEC` (بخش ۱۷),
`docs/PC_VOICE`, `docs/STREAMING_ARCHITECTURE`, `docs/AEC`, `docs/ISSUE_REGISTER`,
`docs/CONFIGURATION`, `docs/PROTOCOL`, `docs/BENCHMARKS`, `docs/TROUBLESHOOTING`,
`docs/HARDWARE_VALIDATION`, `docs/AUDIT_REPORT` (همین فایل)

حذف فایل: ندارد. وابستگی جدید: ندارد (numpy/scipy از قبل در `requirements-voice.txt` بودند).

## C. گزارش تست

```text
$ .venv-audio/Scripts/python.exe -m pytest --tb=no -rA
258 passed, 3 skipped in 12.77s
```

- ۲ skip برای تست‌های native SpeexDSP (DLL در دسترس نیست) و ۱ skip برای sampler حافظهٔ پلتفرم Windows.
- تست پایه پیش از این کار: ۵۳ passed.
- تست‌های خودکار از fake استفاده می‌کنند؛ جداگانه Piper فارسی با مدل محلی WAV تولید کرد. تست دستگاه و مکالمهٔ end-to-end انجام نشده است.
- **تست سخت‌افزاری انجام نشده**؛ چک‌لیست آن در `docs/HARDWARE_VALIDATION.md` است.

نگاشت ۲۰ الزام به تست در `docs/STATUS.md` §۲ آمده است (به‌روزرسانی‌شده با تست‌های تازه).

## D. گزارش کارایی

| معیار | sandbox | i7-6500U |
|---|---|---|
| coordinator jitter p95 | ۰٫۳۹ ms (MEASURED) | NOT MEASURED |
| RAM idle (بدون مدل) | ۲۹٫۵ MB (MEASURED) | NOT MEASURED |
| `asr_ms` / `asr_rtf` | — | **NOT MEASURED** |
| `asr_final_latency_ms` | — | **NOT MEASURED** |
| `llm_ttft_ms` / `tok/s` | — | **NOT MEASURED** |
| `tts_first_chunk_ms` / `first_audible_ms` | — | **NOT MEASURED** |
| `barge_in_latency_ms` | <۱ms (SIMULATED) | **NOT MEASURED** |
| `audio_callback_jitter_ms` (دستگاه) | — | **NOT MEASURED** |

گزارش کامل با برچسب‌ها: `docs/BENCHMARKS.md`.

## E. کارهای باقی‌مانده

1. تهیهٔ وزن‌ها و اجرای `tools/benchmark_voice.py` روی هدف (۷ گروه اندازه‌گیری).
2. چک‌لیست `HARDWARE_VALIDATION.md` (barge-in با گوش، false barge-in، کالیبراسیون AEC).
3. ضبط ۴۰ جمله و `benchmarks/persian_eval.py --audio-dir` برای WER/CER واقعی.
4. OPEN-01: process isolation برای توقف قطعی inference.
5. OPEN-02 (باقی‌مانده): firmware ESP32 پس از پاس‌شدن تست‌های PC؛ transport سرور انجام شد.

## F. چک‌لیست پذیرش

| الزام | پیاده | تست واحد | تست یکپارچه | تست سخت‌افزار | مستند |
|---|---|---|---|---|---|
| گم نشدن final ASR / چند utterance | ✅ | ✅ | ✅ | ⛔ | ✅ |
| barge-in + عدم پخش صدای کهنه | ✅ | ✅ | ✅ | ⛔ | ✅ |
| VAD (آرام/نویز/پیش‌رول/بخش‌بندی) | ✅ | ✅ | ✅ | ⛔ | ✅ |
| AEC + fallback صریح + عدم false barge-in در سکوت | ✅ | ✅ (مصنوعی) | ✅ | ⛔ | ✅ |
| TTS زن/مرد + لغو + نرخ درست | ✅ | ✅ | ✅ | ⛔ | ✅ |
| تاریخچهٔ ماندگار + برش توکنی + ثبت فقط شنیده‌شده | ✅ | ✅ | ✅ | — | ✅ |
| readiness / سنجه‌های زنده (jitter، صف، CPU/RAM) | ✅ | ✅ | ✅ | ⛔ | ✅ |
| آفلاین بودن | ✅ | ✅ | ✅ | ⛔ | ✅ |
| پروتکل ESP32 | قرارداد ✅ | ✅ | ⛔ | ⛔ | ✅ |

علامت ⛔ به معنی «انجام نشده»، نه «شکست‌خورده» است. هیچ ردیفی «تأییدشده روی سخت‌افزار» نیست.

## G. تکمیل — transport صوتی LAN و لغو استریم با قطع کلاینت (۸ نوامبر ۲۰۲۶)

| مورد | وضعیت | شاهد |
|---|---|---|
| `WS /ws/voice` با احراز هویت توکن، pipeline مستقل برای هر دستگاه | انجام شد (تست fake) | `server/ws_voice.py`, `tests/test_ws_voice.py` (۱۴ تست) |
| `play_end` مرز نوبت و پس از همهٔ فریم‌ها | انجام شد | `test_playback_frames_are_valid_protocol_frames`, `test_one_play_end_per_turn_not_per_sentence` |
| barge-in دستگاه: drain صف پخش + `play_stop` با `turn_id` | انجام شد | `test_cancelled_playback_frames_are_dropped_before_play_stop` |
| لغو تولید پس از قطع کلاینت SSE | انجام شد | `tests/test_api.py::test_sse_client_disconnect_cancels_upstream_generation` |
| firmware / LAN واقعی / رجیستری / OTA | **انجام نشده — NOT MEASURED** | `docs/HARDWARE_VALIDATION.md` |
