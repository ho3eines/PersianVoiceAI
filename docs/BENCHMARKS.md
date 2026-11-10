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

هفت گروه اندازه‌گیری که بازبینی الزام کرده است، به‌همراه دو سناریوی تکمیلی:

| گروه/سناریو | چه چیزی اندازه می‌گیرد | پیش‌نیاز واقعی |
|---|---|---|
| `idle` | pacing هماهنگ‌کننده (jitter p95/max)، CPU، RAM، VAD false positive | — (همیشه MEASURED) |
| `asr` (ASR only) | `asr_ms` و `asr_rtf` روی بافر ۱/۳/۶ ثانیه | مدل Whisper محلی |
| `llm` (LLM only) | `llm_ttft_ms`، `llm_tokens_per_second`، زمان کل استریم | llama-server |
| `tts` (TTS only) | `tts_first_chunk_ms`، `tts_first_frame_ms`، مدت پخش | مدل Piper + `.onnx.json` |
| `asr_llm` (ASR+LLM) | `asr_ms` + `llm_ttft_ms` + `turn_total_ms` بدون TTS | Whisper + llama-server |
| `asr_llm_tts` (ASR+LLM+TTS) | یک نوبت کامل گفتاری: VAD → final ASR → SSE → جمله → TTS → commit | هر سه |
| `duplex` (مکالمهٔ کامل دوطرفه) | چند نوبت پشت‌سرهم، یک قطع، `turn_latency_mean/max_ms`، پیام‌های ثبت‌شده | هر سه |
| `bargein` | `barge_in_latency_ms`، شمارش پاسخ کهنه | همان |
| `combined` | نام تاریخی همان `asr_llm_tts` | هر سه |

سناریوهای چندنوبتی (`duplex`) با `--seconds` تعداد نوبت را هم تعیین می‌کنند (حداقل ۲).

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
| idle | `cpu_usage_percent` | ~۱ | % | MEASURED (بدون مدل) |
| idle | `ram_usage_mb` | ~۳۰ | MB | MEASURED |
| idle | `vad_false_positives` | ۰ | count | MEASURED |
| asr | `asr_ms`, `asr_rtf` | — | — | NOT MEASURED (مدل Whisper موجود نیست) |
| llm | `llm_ttft_ms`, `llm_tokens_per_second` | — | — | NOT MEASURED (llama-server در دسترس نبود) |
| tts | `tts_first_chunk_ms` | — | — | NOT MEASURED (Piper محلی WAV ساخت؛ زمان first-chunk هنوز ثبت نشده) |
| asr_llm | `asr_ms` + `llm_ttft_ms` + `turn_total_ms` | ~۲۰۵ | ms | SIMULATED (ASR/LLM ساختگی) |
| asr_llm | `turn_completed` | ۱ | flag | MEASURED |
| asr_llm_tts | `first_audible_ms` / `turn_total_ms` | ~۲۵۳ / ~۶۶۴ | ms | SIMULATED |
| asr_llm_tts | `vad_start_ms` / `vad_end_ms` | ۶۰ / ۶۰۰ | ms | MEASURED (هم‌خوان با تنظیمات) |
| duplex | `turn_latency_mean_ms` (۲ نوبت) | ~۲۰۶ | ms | SIMULATED |
| duplex | `interrupted_turns` | ۱ | count | MEASURED |
| duplex | `stale_completions_after_interrupt` | ۰ | count | MEASURED |
| duplex | `messages_recorded` | ۴ | count | MEASURED (هیچ utterance گم نشد) |
| bargein | `barge_in_latency_ms` | <۱ | ms | SIMULATED (کامپوننت‌ها ساختگی) |
| bargein | `stale_completions_after_interrupt` | ۰ | count | MEASURED |

**تفسیر:** این جدول فقط ثابت می‌کند مسیر کامل بدون استثنا اجرا می‌شود و کرانه‌ها نگه داشته می‌شوند.
هیچ نتیجه‌ای دربارهٔ کارایی روی i7-6500U از آن گرفته نمی‌شود.

## ۴. نتایج target hardware

| معیار | i7-6500U (Windows 11) |
|---|---|
| `asr_ms` / `asr_rtf` (whisper-small int8, 6s window) | NOT MEASURED |
| `asr_final_latency_ms` (utterance 3s) | NOT MEASURED |
| `llm_ttft_ms` (Qwen3-4B-Q4_K_M) | 4.69 s CPU / 5.76 s GPU-auto (MEASURED) |
| `llm_tokens_per_second` (Qwen3-4B-Q4_K_M) | 4.64 CPU / 3.90 GPU-auto tok/s (MEASURED) |
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

## ۵.۵. مقایسهٔ واقعی مدل‌های فارسی GGUF روی همین لپ‌تاپ (MEASURED)

ابزار `benchmarks/compare_models.py` روی i7-6500U با 16GB RAM، GPU 940MX (2GB VRAM)،
همان ۸ پرامپت فارسی، `temperature=0`، `max_tokens=160`، `ctx=4096`, `threads=4`.
دو حالت: CPU خالص (`-ngl 0`) و GPU auto-fit روی Vulkan (`--device Vulkan0 --fit on`).
گزارش‌های خام: `benchmarks/results/pc1-model-compare-20261110.json` (CPU) و
`benchmarks/results/pc1-model-compare-gpu-20261110.json` (GPU).

| مدل | حالت | TTFT میانگین | tok/s میانگین | RAM اوج | VRAM دلتا |
|---|---|---|---|---|---|
| MiniCPM5-1B-Q8_0 | CPU | ۲٫۰۵s | ۱۴٫۰۶ | 1119 MiB | 0 |
| MiniCPM5-1B-Q8_0 | GPU | ۱٫۶۰s | ۱۲٫۴۱ | 1302 MiB | 880 MiB |
| Qwen3-4B-Q4_K_M | CPU | ۴٫۶۹s | ۴٫۶۴ | 4806 MiB | 0 |
| Qwen3-4B-Q4_K_M | GPU | ۵٫۷۶s | ۳٫۹۰ | 3319 MiB | 860 MiB |
| Qwen3.5-4B-Q4_K_M | CPU | ۳٫۸۴s | ۳٫۸۴ | 4380 MiB | 0 |
| Qwen3.5-4B-Q4_K_M | GPU | ۴٫۶۳s | ۳٫۸۷ | 3577 MiB | 820 MiB |
| gemma-3-4b-persian-v0.Q4_K_M | CPU | ۱۴٫۲۲s | ۵٫۳۶ | 4264 MiB | 0 |
| gemma-3-4b-persian-v0.Q4_K_M | GPU | ۱۷٫۱۲s | ۵٫۱۰ | 3349 MiB | 890 MiB |

**نتیجهٔ کلیدی:** روی 940MX با ۲GB VRAM، بارگذاری لایه‌ها روی GPU برای مدل‌های ۴B
**سرعت تولید را بهتر نمی‌کند** (بررسی محلی: CPU ≥ GPU). دلیل: فقط بخشی از وزن در VRAM جا می‌شود
(Vulkan در لاگ `Flash Attention ... not supported` و تخصیص جزئی لایه‌ها گزارش می‌کند)، جابه‌جایی
مداوم CPU↔GPU سرباری می‌سازد که از سود GPU بیشتر است. پس `PVAI_N_GPU_LAYERS=0` است و
اجرای CPU گزینهٔ درست این لپ‌تاپ است. GPU مزیتش را فقط در صرفه‌جویی RAM نشان می‌دهد.

از نظر کیفیت (بازبینی دستی همان ۸ خروجی): Qwen3.5-4B و gemma-3-4b-persian فارسی روان و
مرتبط تولید کردند؛ MiniCPM5-1B خروجی نامفهوم و پراکنده داد. انتخاب مدل نهایی هستهٔ متنی:
**Qwen3.5-4B-Q4_K_M**. اعتبارسنجی آماری این مقایسه انجام نشده و مقایسهٔ کیفیت تک‌بازبین است.


## ۶. ارزیابی فارسی (WER/CER)

```powershell
# فهرست مجموعه
.\.venv\Scripts\python.exe benchmarks\persian_eval.py --list

# ارزیابی transcript‌های موجود (پس از تست واقعی)
.\.venv\Scripts\python.exe benchmarks\persian_eval.py --hypotheses my_transcripts.json --json-out eval.json

# ارزیابی مستقیم از فایل‌های WAV (فقط با مدل واقعی)
.\.venv\Scripts\python.exe benchmarks\persian_eval.py --audio-dir recordings\fa --strict
```

سیاست نرمال‌سازی (`server.persian.normalize_for_eval`)، به ترتیب اجرا:

1. **NFC** (نرمال‌سازی یونیکد).
2. **حذف نویسه‌های نامرئی**: `ZWNJ`‑نماها یعنی ZWSP `U+200B`، ZWJ `U+200D`، soft hyphen،
   BOM و نشانه‌های bidi (`U+200E/200F`, `U+202A–202E`, `U+2066–2069`). این‌ها از PDF و وب
   کپی می‌شوند، صدا ندارند و در diff دیده نمی‌شوند. **نیم‌فاصله (`U+200C`) حذف نمی‌شود.**
3. **حروف عربی → فارسی**: `ي/ى→ی`، `ك→ک`، `ة→ه` و **`أ/إ/ٱ→ا`** (همزهٔ روی الف/زیر الف و
   الف وصل، همان واکهٔ فارسی‌اند). `ؤ`/`ئ` دست‌نخورده می‌مانند چون در فارسی حرف جدا هستند
   (`مؤمن`، `مسئول`). `ۀ` (`U+06C0`) به `ه` می‌رود تا با `ه`+`U+0654` یکسان شود.
4. **حذف اعراب و کشیده** (`U+0640`)، و یکسان‌سازی `٪`/`%` و `؟` عربی/فارسی.
5. **ارقام شرقی → ASCII** و **یکسان‌سازی جداکنندهٔ عدد** (`_unify_numeric_separators`):
   هر رشتهٔ رقمی با `_normalise_separators` به یک شکل استاندارد می‌رود — `۳٫۵`/`3,5`/`3.5`
   همه `3.5` و `۱٬۲۰۰`/`1,200`/`1.200` همه `1200`. علت: پیش‌تر «حذف علائم» جداکننده را
   **بدون جایگزین** پاک می‌کرد و `۳٫۵` را به `35` تبدیل می‌کرد؛ یعنی یک پاسخ درست به‌عنوان
   خطا شمرده می‌شد.
6. **نیم‌فاصله → فاصله**؛ و `زنج`ی که بین دو حرف نیست (کنار فاصله/نشانه یا تکراری) دور
   ریخته می‌شود، چون `ZWNJ.isspace() == False` است و `split()` آن را جدا نمی‌کند.
7. **علائم → فاصله، نه حذف**: `،` و `؛` و `.` و `:` و `«»` به فاصله تبدیل می‌شوند. حذف
   بدون جایگزین `کتاب، دفتر` را به یک توکن `کتابدفتر` می‌چسباند.
8. جمع‌کردن فاصله‌ها.

نکته‌های مهم:

- **تفاوت فقط در قالب** (حروف عربی، ارقام شرقی، `٪`/`%`، `٫`/`.`، نیم‌فاصله به‌صورت فاصله)
  خطا شمرده نمی‌شود.
- **حذف نیم‌فاصله** (`میروم` به‌جای `میروم`) یک خطای مرز واژه شمرده می‌شود؛ این عمدی است و
  دستهٔ `zwnj` مجموعه دقیقاً همین را می‌سنجد.
- **رقم و واژه معادل نیستند**: `۲۴` با «بیست و چهار» یکسان شمرده نمی‌شود.
- **همزهٔ اضافه (`U+0654`) در چسبیدن به `ه`**: «کتابخانهٔ» با نیم‌فاصله‌نویسی «کتابخانه‌ی» دو
  توکن می‌شود (`کتابخانه ی`) و یک خطای مرز واژه است؛ سیاست `zwnj` را عمداً یکسان نمی‌کند.
  این‌ها محدودیت مستند سیاست‌اند، نه اشتباه ابزار، و در
  `tests/test_benchmark_tools.py::test_policy_limits_are_documented_not_hidden` پین شده‌اند.

نتیجهٔ فعلی روی دادهٔ واقعی: **NOT MEASURED** (بدون ضبط و وزن مدل).

## کالیبراسیون فیلتر توهم Whisper (NOT MEASURED)

`benchmarks/persian_eval.py --audio-dir DIR` روی همان مجموعهٔ wav گفتار واقعی
`speech_rejections.false_reject_rate` را (تعداد گفتارهای ثبت‌شده که transcript تهی
داشتند / تعداد گفتارها) کنار WER/CER می‌دهد. سکوت/نویز با `kind=no_speech`
و فرضیه‌های همان شناسه‌ها `no_speech.false_accept_rate` را می‌دهد؛ ورودی غایب
در مخرج هیچ‌کدام شمرده نمی‌شود و در `missing_ids` گزارش می‌شود.

آستانه‌های فعلی `PVAI_ASR_MAX_NO_SPEECH_PROB=0.75` و
`PVAI_ASR_LOG_PROB_THRESHOLD=-1.0` فقط اگر **هر دو** عبور کنند segment را حذف
می‌کنند؛ compression_ratio و عبارات ثابت مستقل‌اند. برای تنظیم آستانه‌ها
گفتار/سکوت را با همان میکروفون، محل، وزن Whisper و تنظیمات decode جمع کنید؛
روی یک بخش آستانه را انتخاب و روی بخش نگه‌داشته‌شده هر دو نرخ را گزارش کنید.
هیچ فایل wav/مدل واقعی برای این بازبینی فراهم نبود: هر دو نرخ و آستانهٔ بهینه
**NOT MEASURED** هستند؛ تست‌های stub معیار کیفیت فارسی نیستند.
