# روش‌شناسی بنچمارک و نتایج

## ۱. چرا برچسب‌گذاری اجباری است

هر عدد در این پروژه یکی از سه برچسب زیر را دارد و **هیچ عددی بدون برچسب نقل نمی‌شود**:

| برچسب | معنا |
|---|---|
| `MEASURED` | همین حالا روی همین ماشین با کامپوننت واقعی اندازه‌گیری شده |
| `SIMULATED` | کامپوننت واقعی (وزن مدل/سرور) موجود نبوده؛ تأخیر ساختگی و فقط اثبات اجرای مسیر |
| `NOT MEASURED` | برای اندازه‌گیری نیاز به سخت‌افزار/وزن/اتصال است و انجام نشده |

`SIMULATED` هرگز نباید به‌عنوان «کارایی» نقل شود. جدول target (i7-6500U) تا اجرای واقعی
`NOT MEASURED` است.

## ۲. اجرا

```powershell
# همهٔ سناریوها (آنچه در دسترس باشد واقعی، بقیه برچسب‌گذاری می‌شوند)
.\.venv\Scripts\python.exe tools\benchmark_voice.py

# فقط idle و یک نوبت کامل
.\.venv\Scripts\python.exe tools\benchmark_voice.py --scenarios idle,combined

# ذخیرهٔ گزارش
.\.venv\Scripts\python.exe tools\benchmark_voice.py --json-out benchmarks\results\voice-pc.json
```

| سناریو | چه چیزی اندازه می‌گیرد | پیش‌نیاز واقعی |
|---|---|---|
| `idle` | pacing هماهنگ‌کننده (jitter p95/max)، CPU، RAM، VAD false positive | — (همیشه MEASURED) |
| `asr` | `asr_ms` و `asr_rtf` روی بافر ۱/۳/۶ ثانیه | مدل Whisper محلی |
| `llm` | `llm_ttft_ms`، `llm_tokens_per_second`، زمان کل استریم | llama-server در `PVAI_LLAMA_SERVER_URL` |
| `tts` | `tts_first_chunk_ms`، `tts_first_frame_ms`، مدت پخش | مدل Piper + `.onnx.json` |
| `combined` | یک نوبت کامل: VAD → final ASR → SSE → جمله → TTS → commit | هر سه |
| `bargein` | `barge_in_latency_ms`، شمارش پاسخ کهنه | همان |

### قواعد اندازه‌گیری

1. هر سناریو را **حداقل ۵ بار** اجرا و **میانه** را گزارش کنید؛ اولین اجرا warmup است.
2. همهٔ برنامه‌های سنگین دیگر (مرورگر، CPU-boost) را ببندید؛ روی باتری اجرا نکنید.
3. برای CPU/RAM سنجه‌های `server/resources.py` استفاده می‌شود (psutil اگر نصب باشد، وگرنه
   `/proc` در Linux و `psapi` در Windows). نبود ابزار = `NOT MEASURED`، نه صفر.
4. `asr_rtf < 1` یعنی decode سریع‌تر از زمان واقعی؛ روی i7-6500U با whisper-small/int8
   انتظار می‌رود نزدیک ۱ یا بیشتر باشد، اما **تا اندازه‌گیری، ادعا نمی‌شود**.
5. `first_audible_ms` از پایان utterance تا اولین بلوک تحویل‌شده به دستگاه است؛
   تأخیر DAC/بلندگو از پایتون قابل اندازه‌گیری نیست و باید با گوش/اسکوپ سنجیده شود.
6. `barge_in_latency_ms` زمان منطقی teardown است (cancel task + set token + پاک‌کردن صف).
   قطع واقعی صدا یک بلوک ۲۰ms بعد از آن رخ می‌دهد؛ تست سخت‌افزاری جدا لازم است.

## ۳. نتایج sandbox (۸ نوامبر ۲۰۲۶)

محیط: Linux sandbox، Python 3.11، **بدون** مدل Whisper/Piper، **بدون** llama-server، بدون دستگاه صوتی.
دستور: `tools/benchmark_voice.py --quick --json-out benchmarks/results/voice-sandbox-20261108.json`

| سناریو | معیار | مقدار | واحد | برچسب |
|---|---|---|---|---|
| idle | `audio_loop_jitter_p95_ms` | ۰٫۳۹ | ms | MEASURED |
| idle | `audio_loop_jitter_max_ms` | ۰٫۴۲ | ms | MEASURED |
| idle | `cpu_usage_percent` | ~۰ | % | MEASURED (بدون مدل) |
| idle | `ram_usage_mb` | ۲۹٫۵ | MB | MEASURED |
| idle | `vad_false_positives` | ۰ | count | MEASURED |
| asr | `asr_ms`, `asr_rtf` | — | — | NOT MEASURED (مدل Whisper موجود نیست) |
| llm | `llm_ttft_ms`, `llm_tokens_per_second` | — | — | NOT MEASURED (llama-server در دسترس نبود) |
| tts | `tts_first_chunk_ms` | — | — | NOT MEASURED (مدل Piper موجود نیست) |
| combined | `turn_total_ms` و بقیه | ~۶۶۳ | ms | SIMULATED (ASR ۲۰۰ms / LLM ۳۰۰ms / TTS ۵۰ms ساختگی) |
| combined | `vad_start_ms` / `vad_end_ms` | ۶۰ / ۶۰۰ | ms | MEASURED (هم‌خوان با تنظیمات) |
| combined | `turn_completed` | ۱ | flag | MEASURED |
| bargein | `barge_in_latency_ms` | <۱ | ms | SIMULATED (کامپوننت‌ها ساختگی) |
| bargein | `stale_completions_after_interrupt` | ۰ | count | MEASURED |

**تفسیر:** این جدول فقط ثابت می‌کند مسیر کامل بدون استثنا اجرا می‌شود و کرانه‌ها نگه داشته می‌شوند.
هیچ نتیجه‌ای دربارهٔ کارایی روی i7-6500U از آن گرفته نمی‌شود.

## ۴. نتایج target hardware

| معیار | i7-6500U (Windows 11) |
|---|---|
| `asr_ms` / `asr_rtf` (whisper-small int8, 6s window) | NOT MEASURED |
| `asr_final_latency_ms` (utterance 3s) | NOT MEASURED |
| `llm_ttft_ms` (Qwen3-4B-Q4_K_M) | NOT MEASURED |
| `llm_tokens_per_second` | NOT MEASURED |
| `tts_first_chunk_ms` (Piper fa) | NOT MEASURED |
| `first_audible_ms` | NOT MEASURED |
| `barge_in_latency_ms` (real device) | NOT MEASURED |
| `audio_callback_jitter_ms` | NOT MEASURED |
| CPU/RAM در ASR+LLM+TTS هم‌زمان | NOT MEASURED |

پس از اجرا، این جدول را با همان برچسب‌ها پر کنید و فایل JSON را در `benchmarks/results/` نگه دارید.

## ۵. بنچمارک متنی (سابقه)

`benchmarks/bench_chat.py` و `tools/benchmark.ps1` فقط مسیر متنی را می‌سنجند. نتیجهٔ تاریخی
درس ۱ روی MiniCPM5-1B-Q8_0 (i7-6500U): TTFT ≈ ۱٫۴۵s، کل ≈ ۱۱٫۴۵s، ≈ ۱۹٫۹ tok/s.
این اعداد **به Qwen3-4B و به مسیر صوتی تعمیم داده نمی‌شوند** و Qwen با وزن واقعی هنوز سنجیده نشده است.

## ۶. ارزیابی فارسی (WER/CER)

```powershell
# فهرست مجموعه
.\.venv\Scripts\python.exe benchmarks\persian_eval.py --list

# ارزیابی transcript‌های موجود (پس از تست واقعی)
.\.venv\Scripts\python.exe benchmarks\persian_eval.py --hypotheses my_transcripts.json --json-out eval.json

# ارزیابی مستقیم از فایل‌های WAV (فقط با مدل واقعی)
.\.venv\Scripts\python.exe benchmarks\persian_eval.py --audio-dir recordings\fa --strict
```

سیاست نرمال‌سازی (`server.persian.normalize_for_eval`): NFC، `ي/ك→ی/ک`، حذف اعراب و کشیده،
یکسان‌سازی ارقام به ASCII، نیم‌فاصله → فاصله، حذف علائم، جمع‌کردن فاصله‌ها.
دو نکتهٔ مهم:

- **تفاوت فقط در قالب** (حروف عربی، ارقام شرقی، نیم‌فاصله به‌صورت فاصله) خطا شمرده نمی‌شود.
- **حذف نیم‌فاصله** (`میروم` به‌جای `میروم`) یک خطای مرز واژه شمرده می‌شود؛ این عمدی است و
  دستهٔ `zwnj` مجموعه دقیقاً همین را می‌سنجد. اعداد به‌صورت رقم و به‌صورت واژه معادل نیستند
  (`24` با `بیست و چهار` یکسان شمرده نمی‌شود) — این محدودیت سیاست است، نه اشتباه ابزار.

نتیجهٔ فعلی روی دادهٔ واقعی: **NOT MEASURED** (بدون ضبط و وزن مدل).
