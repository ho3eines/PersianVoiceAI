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
| `asr_ms` / `asr_rtf` (whisper-small int8, 6s window) | MEASURED — ۴۶٫۲–۵۰٫۹s، RTF ۷٫۷–۱۷٫۰؛ روی گفتار Piper متن خالی (§۵٫۸) |
| `asr_final_latency_ms` (utterance 3s) | NOT MEASURED |
| `llm_ttft_ms` (Qwen3-4B-Q4_K_M) | 4.69 s CPU / 5.76 s GPU-auto (MEASURED) |
| `llm_tokens_per_second` (Qwen3-4B-Q4_K_M) | 4.64 CPU / 3.90 GPU-auto tok/s (MEASURED) |
| `tts_first_chunk_ms` (Piper fa) | MEASURED — ۳۰۳۹٫۶ms (§۵٫۸) |
| `first_audible_ms` | NOT MEASURED |
| `barge_in_latency_ms` (real device) | NOT MEASURED |
| `audio_callback_jitter_ms` | NOT MEASURED |
| CPU/RAM در ASR+LLM+TTS هم‌زمان | NOT MEASURED |

پس از اجرا، این جدول را با همان برچسب‌ها پر کنید و فایل JSON را در `benchmarks/results/` نگه دارید.

## ۴.۵. اجرای سناریوهای گفتار کامل با مدل‌های واقعی — ۱۳ نوامبر ۲۰۲۶

محیط: همان i7-6500U/Windows 11؛ Whisper-small int8 روی CPU، Piper `fa_IR`، و llama-server با
Qwen3.5-4B-Q4_K_M (`--jinja`، `-ngl 0`، `-t 4`، ctx=4096). دستور:

```text
python tools/benchmark_voice.py --scenarios duplex,bargein,combined \
    --json-out benchmarks/results/voice-hw-20261113.json
```

| سناریو | معیار | نتیجه | برچسب |
|---|---|---|---|
| duplex | `turns` | ۳ نوبت تغذیه شد | MEASURED |
| duplex | `turn_latency_mean_ms` / `turn_latency_max_ms` | — (۶۰s سقف انتظار بود، نه تأخیر) | NOT MEASURED |
| duplex | `messages_recorded` | ۰ | MEASURED |
| duplex | `interrupted_turns` | ۰ | MEASURED |
| duplex | `stale_completions_after_interrupt` | ۰ | MEASURED |
| bargein | `barge_in_latency_ms` | — (دستیار هرگز به `speaking` نرسید) | NOT MEASURED |
| combined | `asr_ms`, `asr_final_latency_ms`, `llm_ttft_ms`, `first_audible_ms`, `playback_duration_ms` | — (هیچ مقداری ثبت نشد) | NOT MEASURED |
| combined | `vad_start_ms` / `vad_end_ms` | ۶۰ / ۶۰۰ | MEASURED |
| combined | `turn_completed` | ۰ | MEASURED |

**ریشهٔ نبودِ عدد (نه بهانه):** ابزار ورودی گفتار واقعی ندارد و «گفتار» را با تُن مصنوعی
`frame()` می‌سازد (۲۴ فریم = ۴۸۰ms). Whisper روی این تُن متن فارسی تولید نمی‌کند؛ utterance
رد می‌شود، پس LLM/TTS هرگز شروع نمی‌شوند و نوبت کامل نمی‌شود. بنابراین `duplex`/`bargein`/
`combined` حتی با مدل‌های واقعی هنوز **قابل اندازه‌گیری نیستند** و عدد ۶۰ ثانیه‌ای سقف
انتظار `_one_turn` بود، نه تأخیر.

**نقص برچسب‌گذاری که در همین اجرا پیدا و رفع شد:** `Report.add` هر ردیف بدون مقدار (`None`)
را به `NOT MEASURED` با یادداشت `value missing` تنزل می‌دهد، و `duplex`/`combined` وقتی نوبتی
کامل نشده باشد عدد سقف انتظار را به‌عنوان تأخیر گزارش نمی‌کنند. تست‌ها:
`tests/test_benchmark_tools.py::test_a_missing_value_is_never_labelled_measured` و
`::test_a_timed_out_turn_reports_no_latency`.

**راه واقعی برای پر کردن این ردیف‌ها:** ورودی گفتار واقعی لازم است — ضبط ۴۰ جمله و اجرای
`benchmarks/persian_eval.py --audio-dir` برای کیفیت، و یک نشست واقعی با میکروفون برای تأخیر
نوبت و barge-in (چک‌لیست [HARDWARE_VALIDATION.md](HARDWARE_VALIDATION.md)). تا آن زمان این
معیارها `NOT MEASURED` می‌مانند و «تست‌شده» علامت نمی‌خورند.

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


## ۵.۶. اجرای دوبارهٔ پنج وزن محلی با llama.cpp build 11040 — ۱۲ نوامبر ۲۰۲۶ (MEASURED)

همان ماشین (i7-6500U، ۱۵٫۸۷GB RAM، ۹۴۰MX که استفاده نشد)، ویندوز ۱۱.
موتور: `D:\Lama.cpp\bin\llama-server.exe` نسخهٔ `0.4.1-dev (build 11040, 5b335f413)`.
فرمان: `benchmarks/compare_models.py --gpu-layers 0 --ctx-size 4096 --threads 4 --max-tokens 48`
(CPU خالص، `--parallel 1`، `temperature=0`، همان ۸ پرامپت فارسی).
گزارش خام: `benchmarks/results/pc1-model-compare-20261112.json`.

| مدل | TTFT میانگین | tok/s میانگین | زمان کل میانگین | RAM اوج پروسه | VRAM delta |
|---|---|---|---|---|---|
| Qwen3.5-4B-Q4_K_M | ۴٫۶۴s | ۴٫۰۱ | ۱۶٫۷۰s | ۳۰۴۷MiB | ۷۲MiB |
| Qwen3-4B-Q4_K_M | ۶٫۵۱s | ۳٫۳۷ | ۲۰٫۶۷s | ۴۱۱۰MiB | ۱۲۴MiB |
| gemma-3-4b-persian-v0.Q4_K_M | ۱۵٫۲۷s | ۶٫۱۷ | ۲۱٫۹۷s | ۴۲۴۱MiB | ۷۸MiB |
| MiniCPM5-1B-Q8_0 | ۲٫۱۲s | ۱۵٫۸۴ | ۵٫۰۸s | ۱۰۹۱MiB | ۵۰MiB |
| qwen3-0.6b-Q4_K_M | ۰٫۸۲s | ۲۲٫۱۸ | ۲٫۸۴s | ۱۱۲۳MiB | ۴۵MiB |

**کیفیت (بازبینی دستی، تک‌بازبین؛ `max_tokens=48` یعنی همهٔ پاسخ‌ها بریده‌اند و این سنجش کامل نیست):**

- **Qwen3.5-4B**: فارسی روان و درست؛ پرسش ریاضی (۱۲۰ صفحه ÷ ۱۵ صفحه در روز) با پاسخ درست آغاز شد.
- **Qwen3-4B**: روان ولی موجزتر و کم‌دقت‌تر از Qwen3.5.
- **gemma-3-4b-persian**: متن روان، ولی **پاسخ ریاضی غلط** (ابتدا «چهل و پنج روز»، سپس «۴ روز»؛ پاسخ درست ۸ است). کندی prefill قبلی هم تکرار شد: TTFT ۱۵٫۳s.
- **MiniCPM5-1B و qwen3-0.6b**: فارسی شکسته/نامفهوم؛ برای دستیار فارسی قابل استفاده نیستند.

`token_limit_reached` برای هر سه مدل ۴B برابر ۸ از ۸ است، پس سرعت این جدول دست‌کم گرفته نشده
ولی مقایسهٔ کیفیت فقط یک بازبینی سطحی است. انتخاب قبلی (Qwen3.5-4B) با این اجرا نقض نشد.

### ۵.۶.۱ مسیر REST خود پروژه (MEASURED)

یک درخواست واقعی `POST /api/chat` با `max_tokens=64` روی همان سرور
(`-ngl 0 -c 4096`, چهار اسلات پیش‌فرض llama-server):

| معیار | مقدار |
|---|---|
| توکن تولیدشده | ۴۶ |
| زمان کل | ۲۱٫۵۵s |
| `prompt_eval` | ۳٫۶۱s (۲۷۳ توکن) |
| `generation` | ۱۷٫۹۳s |
| tok/s | ۲٫۵۱ |

این عدد از میانگین ۸ پرامپت جدول بالا کمتر است چون **اولین درخواست پس از لود مدل** و بدون
`--parallel 1` است؛ برای مقایسهٔ مدل‌ها از جدول ۵٫۶ استفاده کنید، نه این ردیف.

## ۵.۷. مقایسهٔ مدل‌ها با سقف ۱۶۰ توکن (بدون برش پاسخ) — ۱۲ نوامبر ۲۰۲۶ (MEASURED)

همان ماشین و همان موتور (`build 11040`)؛ فرمان:
`benchmarks/compare_models.py --gpu-layers 0 --ctx-size 4096 --threads 4 --max-tokens 160`.
گزارش خام: `benchmarks/results/pc1-model-compare-160-20261112.json`.

| مدل | TTFT | tok/s | زمان کل | RAM اوج | VRAM Δ | بریده‌شده |
|---|---|---|---|---|---|---|
| Qwen3.5-4B-Q4_K_M | ۴٫۰۵s | ۴٫۶۵ | ۲۶٫۲۲s | ۴۳۵۰MiB | ۱۷MiB | ۲/۸ |
| Qwen3-4B-Q4_K_M | ۴٫۲۸s | ۵٫۹۰ | ۲۷٫۰۵s | ۴۷۷۸MiB | ۶۰MiB | ۴/۸ |
| gemma-3-4b-persian-v0 | ۱۳٫۴۲s | ۶٫۷۴ | ۲۲٫۹۹s | ۴۲۴۰MiB | ۰ | ۰/۸ |
| MiniCPM5-1B-Q8_0 | ۱٫۸۰s | ۱۷٫۷۷ | ۶٫۰۱s | ۱۰۹۲MiB | ۰ | ۰/۸ |
| qwen3-0.6b-Q4_K_M | ۰٫۷۵s | ۲۴٫۷۱ | ۵٫۴۸s | ۱۱۲۳MiB | ۰ | ۴/۸ |

**داوری کیفیت (دستی، سه پرامپت دشوار: ریاضی، دستور ویندوز، صداقت):**

| مدل | ریاضی (۱۲۰÷۱۵) | دستور ویندوز | صداقت در ندانستن | حکم |
|---|---|---|---|---|
| Qwen3.5-4B | ✅ «۸ روز» با محاسبهٔ درست | قابل‌قبول ولی مراحل ساختگی (Win+E پنجرهٔ Explorer است، نه جستجو؛ «بزرگ‌تر از» فیلتر واقعی Explorer نیست) | ✅ صریحاً گفت اطلاعات ندارد و ادعای ساختگی را رد کرد | **بهترین گزینهٔ موجود** |
| Qwen3-4B | ✅ درست | کلی و مبهم | ❌ به‌جای پاسخ، یک الگو تکرار کرد («این را بگو: …») | قابل استفاده، ضعیف‌تر در پیروی از دستور |
| gemma-3-4b-persian | ❌ «چهل و پنج روز»، سپس «۴ روز» | قابل‌قبول | ✅ کوتاه و درست | روان ولی در حساب ناتوان |
| MiniCPM5-1B | ❌ نامفهوم | ❌ نامفهوم | ❌ نامفهوم | کنار گذاشته شد |
| qwen3-0.6b | ❌ حلقهٔ تکرار | ❌ بی‌معنا | ❌ بی‌معنا | کنار گذاشته شد |

مقایسه با اجرای ۴۸ توکنی (§۵٫۶) نشان می‌دهد TTFT و tok/s پایدارند (اختلاف <۱s در TTFT میانگین)
و تفاوت اصلی، کامل‌شدن پاسخ‌هاست. این داوری تک‌بازبین و روی ۸ پرامپت است، نه یک ارزیابی آماری.

## ۵.۸. زنجیرهٔ صوت با Whisper و Piper محلی — ۱۲ نوامبر ۲۰۲۶ (MEASURED)

فرمان: `tools/benchmark_voice.py --scenarios asr,llm,tts --json-out benchmarks/results/voice-pc-20261112.json`
روی همان لپ‌تاپ، با `models/whisper-small` (int8، CPU) و `models/piper/fa_IR.onnx`.

### ASR

| ورودی | زمان | RTF | متن |
|---|---|---|---|
| تُن مصنوعی ۱s | ۴۸۹۴٫۹ms | ۴٫۸۹ | — (زمان‌سنجی محض) |
| تُن مصنوعی ۳s | ۵۰۹۴۶٫۴ms | ۱۶٫۹۸ | — |
| تُن مصنوعی ۶s | ۴۶۲۲۲٫۹ms | ۷٫۷۰ | — |
| گفتار Piper `persian_synthetic.wav` (۶٫۴۳s) | ۵۴۴۴۳ms (میانهٔ ۳ اجرا) | ۸٫۴۶ | **خالی** (فیلتر توهم رد کرد) |
| همان فایل، فیلتر خاموش | ۴۹۲۶۰ms | ۷٫۶۶ | حلقهٔ تکرار: «ایندرگی کنیدی … دیدیدیدیدی…» |
| گفتار Piper `smoke-test.wav` (۵٫۰۶s) | ۴۶۹۹۸ms | ۹٫۲۸ | **خالی** |

- لود/گرم‌کردن مدل: ۵۲٫۰–۶۰٫۴s (تکرار عدد ۴۵٫۹–۶۳٫۷s در `HARDWARE_VALIDATION.md` §۹٫۳).
- **نتیجهٔ صریح:** با این وزن و این CPU، ASR برای مکالمهٔ زندهٔ فارسی قابل استفاده نیست:
  RTF ۸–۱۷ (یعنی ۵–۹ برابر کندتر از زمان واقعی) و روی گفتار واقعی خروجی خالی یا حلقهٔ تکرار.
  خالی بودن خروجی با فیلترهای پیش‌فرض **رفتار درست فیلتر** است، نه خرابی آن؛ مشکل خودِ decode است.
- **دقت خودِ فیلتر توهم روی دادهٔ فارسی برچسب‌دار `NOT MEASURED` است**؛ آستانه‌ها پیش‌فرض‌اند و
  کالیبراسیون در انتهای همین سند (بخش «کالیبراسیون فیلتر توهم Whisper») توضیح داده شده است.
- این با ادعای «WER ۰٫۲۱ روی Piper→Whisper» در §۹٫۳ هم‌خوان نیست. تفاوت‌ها: آن اجرا از مسیر
  دیگری (با پرامپت اولیه/تنظیمات متفاوت) بود و اعدادش هم در همان سند «غیرقابل استناد» علامت خورده‌اند.
  **عدد معتبر امروز همین جدول است** و کالیبراسیون یا تعویض مدل ASR لازم است.

### LLM (از مسیر `benchmark_voice`، llama-server روی ۸۰۸۰ با Qwen3.5-4B)

| معیار | مقدار |
|---|---|
| `llm_ttft_ms` | ۳۲۱۱٫۳ |
| `llm_tokens_per_second` | ۴٫۰۱ |
| `llm_stream_total_ms` | ۱۹۱۵۸٫۰ |

### TTS (Piper فارسی، صدای زن)

| معیار | مقدار |
|---|---|
| `tts_first_chunk_ms` | ۳۰۳۹٫۶ |
| `tts_first_frame_ms` | ۵۹۵۸٫۲ (تقریب تحویل به دستگاه؛ تأخیر DAC اندازه‌گیری‌شدنی نیست) |
| `tts_total_ms` | ۶۰۰۵٫۳ |
| `tts_played_ms` | ۱۲۶۳۱۸۰٫۰ ⚠️ |

`tts_played_ms` با `tts_total_ms` (۶s) و مدت متن ناسازگار است؛ این یک **ایراد حساب‌داری پخش**
(یا پخش بدون دستگاه واقعی) است و به‌عنوان عدد کارایی نقل نمی‌شود. برای رفع، باید مدت پخش
از بلوک‌های تحویل‌شده به دستگاه شمارش شود، نه از مسیر تولید. تا آن زمان فقط سه ردیف اول معتبرند.

## ۵.۹. AEC با SpeexDSP — مصنوعی MEASURED — ۱۲ نوامبر ۲۰۲۶ (اتاق واقعی: §۵٫۱۰)

AEC **پیاده‌سازی شده** است (`server/aec.py`، مسیر duplex پیش از VAD/ASR) و کتابخانهٔ native
روی همین رایانه موجود است: `models/speexdsp/Library/bin/speexdsp.dll` (۱۲۵۵۱۲ بایت، SpeexDSP 1.2.1
از conda-forge). تست‌های `tests/test_aec.py` با `PVAI_TEST_AEC_LIBRARY` روی این ماشین **۱۱/۱۱ سبز**
شدند؛ دو تست native هم اجرا شدند (skip نشدند).

اندازه‌گیری تازهٔ همان سناریوهای تست، این بار با ثبت عدد
(`benchmarks/results/aec-pc-20261112.json`، `logs/aec-measure-20261112.log`):

| سنجه | مقدار | شرایط |
|---|---|---|
| ERLE (کاهش توان اکو) | **۵۷٫۸۲ dB** | نویز مرجع ۱۲s، اکو با تأخیر ۴۰ms و gain=۰٫۵۵، ۲s پایانی |
| RMS اکو / باقی‌مانده | ۱۶۵۳٫۴ / ۲٫۱ | همان |
| هزینهٔ پردازش | ۰٫۰۸۸s برای ۱۲s صوت (RTF ۰٫۰۰۷۴) | حلقهٔ تک‌رشته‌ای، بدون callback |
| double-talk: correlation | **۰٫۹۱۸** | سیگنال نزدیک مستقل ۲۲۰+۳۷۰Hz، ۴s پایانی |
| double-talk: RMS retention | **۰٫۹۹۳** | همان |
| کنترل منفی: تأخیر مرجع ۰ms | ۳۳٫۴۱ dB | نشان می‌دهد تنظیم `PVAI_AEC_DELAY_MS` واقعاً اثر دارد |

این اعداد عیناً همان مقادیر مستند در `docs/AEC.md` هستند؛ بازتولیدپذیری با seed ثابت تأیید شد.
RTF پایین AEC یعنی هزینهٔ DSP در بودجهٔ نرم‌افزاری مسئله نیست؛ گلوگاه همان ASR است (§۵٫۸).

تست اتاق واقعی با میکروفون و بلندگوی همین لپ‌تاپ در §۵٫۱۰ اندازه‌گیری شد؛ آنچه همچنان
`NOT MEASURED` است double-talk با گفتار انسان، قطع/وصل فیزیکی دستگاه، فاصلهٔ متغیر و
ESP32-S3 است. «AEC پیاده شده و در تأخیر کالیبره اثر می‌کند» درست است؛ «مکالمهٔ بدون هدفون
روی PC تأیید شده» همچنان درست نیست (تأخیر ناپایدار و SNR محدود).

## ۵.۱۰. AEC در مسیر آکوستیک واقعی — MEASURED، ۱۳ نوامبر ۲۰۲۶

ابزار: `tools/aec_room_measure.py`. یک `RawStream` دوطرفه (همان مسیر زنده)، محرک **گفتار
Piper** (نه نویز ایستا؛ پردازش میکروفون ویندوز نویز ایستا را در چند ثانیه حذف می‌کند)، و برای
هر فریم `(میکروفون، همان PCM نوشته‌شده در outdata)`. دستگاه: Realtek HD Audio توکار همان
لپ‌تاپ، بلندگو و میکروفون داخلی، ولوم ۱۰۰٪.

| سنجه | MME (پیش‌فرض) | DirectSound |
|---|---|---|
| تأخیر صوتی مؤثر (cross-correlation) | ۳۵۸ ms | ۲۳۱ و ۴۴۸ ms (دو نشست) |
| SNR اکو ۳۰۰–۳۰۰۰Hz | ۱۹٫۷ dB | ۳٫۱ dB |
| ERLE با `PVAI_AEC_DELAY_MS=40` | **۰٫۰۱ dB** | **۰٫۰۱ dB** |
| ERLE با تأخیر کالیبره | **۶٫۸۲ dB** @۳۴۰ms | **۵٫۸۸ dB** @۴۴۰ms |
| ERLE کل ۶۰–۷۰۰۰Hz | ۳٫۵۶ dB | ۳٫۰۴ dB |
| `PVAI_AEC_FILTER_MS=500` | ۶٫۸۵ dB | ۵٫۸۹ dB |
| CPU / RTF | ۰٫۱۵ms / ۰٫۰۰۸ | ۰٫۱۵ms / ۰٫۰۰۸ |
| onset خام VAD → بعد از AEC | ۲ → ۲ | ۱ → ۲ |
| double-talk: نزدیک / اکو | −۰٫۱۷ dB / ۳٫۲۶ dB | −۰٫۰ dB / ۰٫۰۱ dB |

گزارش خام: `benchmarks/results/aec-room-mme-20261113.json` و
`benchmarks/results/aec-room-ds-20261113.json`؛ لاگ: `logs/aec-room-*-20261113.log`.

**تفسیر:** عدد ۰٫۰۱dB یعنی پیش‌فرض ۴۰ms با تأخیر واقعی هم‌تراز نیست و AEC کاری نمی‌کند؛
در تأخیر کالیبره به ۵٫۹–۶٫۸dB می‌رسد که تقریباً سقفِ همین SNR است. ناپایداری تأخیر بین
نشست‌ها (DirectSound) یعنی عدد ثابت `.env` برای اتاق واقعی کافی نیست و تخمین خودکار تأخیر
لازم است. این‌ها `MEASURED` روی همین ماشین‌اند، نه ادعای کیفیت برای ESP32-S3 یا اتاق دیگر.


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
