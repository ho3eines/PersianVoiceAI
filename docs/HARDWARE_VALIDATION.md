# چک‌لیست اعتبارسنجی روی سخت‌افزار واقعی

> هیچ مورد این سند در sandbox اجرا نشده است. تا زمانی که هر ردیف با تاریخ و نتیجه پر نشود،
> وضعیت آن **NOT TESTED** است. تست‌های خودکار `pytest` با fake اجرا می‌شوند و جای این سند را نمی‌گیرند.

ماشین هدف: Intel i7-6500U (۲ هسته/۴ رشته)، ۱۶GB RAM، GeForce 940MX (۲GB، پیش‌فرض استفاده نمی‌شود)،
Windows 11 64-bit، SSD+HDD.

## ۰. آماده‌سازی

- [ ] Python 3.12 در venv، `pip install -r requirements.txt -r requirements-voice.txt`
- [ ] SpeexDSP DLL ۶۴بیتی طبق [AEC.md](AEC.md) (یا تصمیم صریح برای تست با هدفون)
- [ ] وزن‌ها: `models/Qwen3-4B-Q4_K_M.gguf`، `models/whisper-small/`، `models/piper/fa_*`
- [ ] `.env` طبق [CONFIGURATION.md](CONFIGURATION.md)
- [ ] llama-server روی `127.0.0.1:8080` بالا
- [ ] `GET /ready` → `"status": "ready"` (اگر 503 است، اول همان را رفع کنید)

## ۱. دستگاه و کلاک

- [ ] `python -m server.voice --list-devices` دستگاه‌ها را نشان می‌دهد
- [ ] انتخاب ورودی/خروجی با نام یا index کار می‌کند (پیام «دستگاه صوتی آماده است» با نام درست)
- [ ] `tools/benchmark_voice.py --scenarios idle --seconds 30` بدون خطا؛ jitter را ثبت کنید
- [ ] قطع فیزیکی میکروفون در حین اجرا: پس از حداکثر `PVAI_AUDIO_REOPEN_ATTEMPTS` پیام
      «جریان صوتی بیش از … فریم نداد» و خروج تمیز (بدون traceback)
- [ ] وصل دوباره و اجرای مجدد: برنامه دوباره صدا می‌شنود

## ۲. VAD و تشخیص گفتار

| آزمایش | انتظار | نتیجه |
|---|---|---|
| ۱۰ ثانیه سکوت | هیچ `speech_start` | |
| پنکه/کولر روشن | بدون utterance، فقط `noise_floor` بالاتر | |
| جملهٔ آرام فارسی | `speech_start` با `rms` پایین، متن کامل | |
| «سلام» (حرف «س») | ابتدای کلمه بریده نشود | |
| جمله با مکث کوتاه وسط | یک utterance، نه دو | |
| ۲۰ ثانیه صحبت پیوسته | دو بخش (سقف ۱۵s)، هر دو متن در تاریخچه | |

## ۳. ASR و دقت فارسی

- [ ] `tools/benchmark_voice.py --scenarios asr` اعداد `asr_ms`/`asr_rtf` را MEASURED می‌دهد
- [ ] ضبط ۴۰ جملهٔ `benchmarks/persian_eval_set.json` و اجرای
      `benchmarks/persian_eval.py --audio-dir recordings\fa --strict`
- [ ] WER/CER را در `docs/BENCHMARKS.md` ثبت کنید (هیچ عددی قبل از این مرحله ادعا نمی‌شود)
- [ ] متن فارسی: نیم‌فاصله، ارقام، واژهٔ انگلیسی داخل جمله، نام خاص

## ۴. LLM

- [ ] `tools/benchmark_voice.py --scenarios llm` → TTFT و tok/s (MEASURED)
- [ ] جملهٔ اول پاسخ قبل از پایان متن کامل شنیده می‌شود
- [ ] قطع llama-server وسط پاسخ → پیام فارسی روی ترمینال، نشست زنده می‌ماند
- [ ] `reasoning_content` به صدا تبدیل نمی‌شود (`PVAI_LLM_ENABLE_THINKING=false`)

## ۵. TTS

- [ ] صدای زن و مرد جدا: `PVAI_TTS_VOICE=male/female` و مسیرهای `.onnx`
- [ ] `tools/benchmark_voice.py --scenarios tts` → `tts_first_chunk_ms` (MEASURED)
- [ ] سرعت و زیروبمی طبیعی است (نه سریع/نازک) — این همان باگ نرخ ۲۲٫۰۵k در مسیر بدون duplex است
- [ ] اعداد و تاریخ به‌صورت قابل‌فهم خوانده می‌شوند؛ کلمات انگلیسی داخل جمله فارسی
- [ ] قطع دستگاه خروجی وسط پخش → پیام روشن، بدون crash

## ۶. barge-in و AEC

- [ ] با هدفون و `PVAI_AEC_ENABLED=false`: حین پخش صحبت کنید → صدا در <۳۰۰ms قطع شود
- [ ] با بلندگو و AEC فعال: هیچ قطع ناخواسته در سکوت (false barge-in) رخ ندهد
- [ ] کالیبراسیون `PVAI_AEC_DELAY_MS`: مقادیر ۰/۲۰/۴۰/۶۰/۸۰ را امتحان و بهترین را ثبت کنید
- [ ] double-talk: کاربر و دستیار همزمان → صدای کاربر ادامه پیدا کند، پخش قطع شود
- [ ] `tools/benchmark_voice.py --scenarios bargein` → `stale_completions_after_interrupt=0`
- [ ] هیچ صدای کهنه‌ای بعد از قطع پخش شنیده نشود

## ۷. مکالمهٔ پیوسته (سناریوی پذیرش نهایی)

اجرا با اینترنت **قطع**:

1. [ ] برنامه offline شروع می‌شود (`/ready` سبز)
2. [ ] فارسی صحبت می‌کنید؛ Enter لازم نیست
3. [ ] گفتار خودکار تشخیص داده می‌شود
4. [ ] کل utterance رونویسی می‌شود (شامل کلمهٔ آخر)
5. [ ] LLM شروع به تولید می‌کند
6. [ ] TTS قبل از پایان متن کامل صحبت می‌کند
7. [ ] وسط صحبت دستیار حرف می‌زنید
8. [ ] دستیار سریع متوقف می‌شود
9. [ ] utterance جدید درست تشخیص داده می‌شود
10. [ ] context مکالمه درست است (دستیار جملهٔ قبلی را به یاد دارد)
11. [ ] **هیچ جملهٔ قبلی گم نشده** (تاریخچه را با `python -m server.sessions show <id>` ببینید)
12. [ ] صدای کهنه پخش نمی‌شود
13. [ ] ۲۰ نوبت پشت‌سرهم بدون crash و بدون افت حافظه (RAM را قبل/بعد ثبت کنید)
14. [ ] کل مسیر آفلاین می‌ماند (هیچ درخواست بیرونی)

## ۸. ثبت نتیجه

پس از اجرا، این بلوک را در `docs/STATUS.md` به‌روز کنید:

```text
تاریخ:
دستگاه/ویندوز:
مدل‌ها:
pytest: … passed, … skipped
benchmark (MEASURED): asr_ms=…  asr_rtf=…  llm_ttft_ms=…  tok/s=…  tts_first_chunk_ms=…
                      first_audible_ms=…  barge_in_latency_ms=…  cpu=…%  ram=…MB
WER/CER فارسی: …
مشکلات باقی‌مانده:
```

بدون این بلوک، وضعیت پروژه همچنان «تست خودکار سبز، سخت‌افزار NOT TESTED» است.
