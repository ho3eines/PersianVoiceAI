# معماری PC Streaming — جایگزین مسیر «ضبط، سپس پردازش»

تاریخ: ۷ نوامبر ۲۰۲۶. مبنا: درخواست صریح کاربر برای تغییر معماری در صورت نیاز به اجرای لایو.

## تصمیم و گزینه‌ها

- **حفظ مسیر قبلی:** ساده‌تر، ولی ASR تا پایان صحبت منتظر می‌ماند و لغو SSE همگام ممکن است تا توکن بعدی عقب بیفتد؛ برای نیاز فعلی کافی نبود.
- **تصمیم اجراشده:** خط لولهٔ asynchronous و actorهای مستقل، adapter تشخیص افزایشی روی Whisper فارسی محلی.
  دریافت صوت، ASR، دریافت توکن و پخش، چرخه‌های مستقل دارند.
- **جایگزینی با ASR ذاتاً streaming:** در آینده ممکن است؛ بدون مدل فارسی ارزیابی‌شده و وزن/مجوز معتبر انتخاب قطعی نمی‌کنیم.
  رابط `transcribe_pcm` امکان تعویض adapter را بدون تغییر LLM/TTS فراهم می‌کند.

مزیت انتخاب فعلی: استفاده از مدل فارسی موجود و خروجی partial حین صحبت، عدم انتظار endpoint برای شروع ASR،
لغو مستقل شبکه، عدم ذخیرهٔ صوت زنده روی دیسک. هزینه: Whisper بازشناسی تکراری انجام می‌دهد و از ASR ذاتاً streaming سنگین‌تر است.
روی CPU قدیمی ممکن است تولید partial عقب بیفتد؛ «live» تضمین تأخیر hard realtime نیست.
FastAPI/llama-server و API متنی حفظ شده‌اند؛ معماری PC تغییر کرده، نه قرارداد ESP32 یا Command Broker.

## خط لوله

```text
PortAudio duplex callback — PCM16 mono / 16k / 20ms
    ↓ paired microphone + actual render reference → SpeexDSP AEC
    ↓ bounded queue (100 frames ≈ 2s)
Async coordinator + energy endpoint detector
    ├─ speech_start → generation++ → cancel previous HTTP/audio
    ├─ every ~0.8s → newest overlapping PCM snapshot
    └─ silence (~0.5s) or segment limit (15s) → final snapshot
                    ↓ latest-only mailbox (1)
Single ASR actor → Whisper CPU int8, in-memory float32
    ├─ transcript_partial + two-pass agreed_prefix
    └─ transcript_final → async LLM task
                          ↓ HTTP/SSE (FastAPI → llama-server)
                       response_delta → bounded phrase queue (8)
                                             ↓
                                      Single TTS actor
                                      Piper → resample 16k mono → bounded render queue → duplex callback
```

## قرارداد رویدادها

رویدادها JSON lines در ترمینال‌اند؛ هنوز WebSocket صوتی یا UI مرورگر پیاده نشده است.
هر رویداد `type` و `generation` دارد:

- `speech_start`, `speech_end`, `interrupted`
- `transcript_partial`: `text`, `agreed_prefix` (پیشوند مشترک دو decode؛ هنوز قابل اصلاح است)
- `transcript_final`: `text` نهایی بخش، نه الزاماً کل مکالمه
- `response_delta`: متن افزایشی مدل
- `first_token`: `endpoint_ms` از پایان بخش گفتار تا اولین متن مدل
- `llm_timings`: آمار برگشتی FastAPI
- `tts_start`, `tts_end`: شروع/پایان فراخوانی و پخش جمله؛ زمان اولین صدای واقعی اندازه‌گیری نشده
- `response_end`: بعد از مصرف صف پخش؛ تاریخچه در این نقطه ثبت می‌شود
- `error`: پیام فارسی بدون متن exception خام

تصمیم/فرمان از متن partial اجرا نمی‌شود. فقط final به LLM می‌رود. پاسخ‌دادن قبل از پایان صحبت کاربر
می‌تواند باعث پاسخ نامرتبط شود؛ «تشخیص زنده» را با «پاسخ حدسی حین حرف‌زدن» یکی نمی‌گیریم.

## لغو، فشار صف و طول عمر

- شروع گفتار جدید، token پاسخ قبلی را لغو و task مربوط به SSE را cancel می‌کند؛ context شبکه بسته می‌شود، حتی اگر توکن بعدی هنوز نرسیده باشد.
- generation قدیمی اجازهٔ انتشار متن ASR، پخش جملهٔ صف‌شده یا ثبت تاریخچه ندارد.
- تنها یک فراخوانی Whisper و یک فراخوانی Piper هم‌زمان مجاز است. محاسبهٔ native در thread درجا قابل کشتن نیست؛ نتیجهٔ قدیمی حذف می‌شود.
- pending snapshot جدید جای قبلی را می‌گیرد؛ صف صوت ورودی/خروجی نامحدود نیست. پرشدن صف جمله‌ها دریافت متن را backpressure می‌کند.
- overflow میکروفون کل نوبت و partialهای قدیمی را حذف می‌کند؛ صوت شکسته به‌عنوان نوبت سالم پردازش نمی‌شود.
- خروج taskهای async را لغو می‌کند؛ خروج executor می‌تواند منتظر بازگشت native inference بماند. توقف قطعی مدل گیرکرده نیازمند process isolation در مرحلهٔ بعد است.
- waveform فقط RAM است. حالت عیب‌یابی `--turn-based` هنوز فایل موقت می‌سازد.

## مرز تکمیل

**پیاده‌شده:** capture و تشخیص افزایشی، partial حین صحبت، SSE async قابل لغو، پخش تدریجی و قطع پاسخ منطقی، صف محدود و حذف دادهٔ قدیمی.

**AEC PC اضافه شده:** SpeexDSP با مرجع خروجی duplex پیش از VAD/ASR؛ بدون mute ورودی.
قرارداد، وابستگی native و معیار سنجش در [AEC.md](AEC.md).
**پیاده‌نشده/تأییدنشده:** VAD تخصصی، مدل ASR ذاتاً streaming، UI، ESP32 صوتی، تضمین تأخیر، کیفیت وزن‌ها و پایداری روی سخت‌افزار واقعی.
بدون AEC هدفون لازم است؛ با AEC نیز استفادهٔ بلندگو نیازمند کالیبراسیون و تست echo-only/double-talk است.

## معیار پذیرش واقعی

1. هنگام ادامهٔ صحبت، قبل از سکوت رویداد `transcript_partial` دیده شود.
2. با ادامهٔ متن بلند مدل، جملهٔ اول پخش شود.
3. با شروع صحبت جدید، پخش قبلی قطع شود و هیچ متن/صوت متعلق به generation قدیمی منتشر نشود.
4. قطع SSE، خطای Piper و overflow ورودی تاریخچهٔ ناقص ثبت نکنند.
5. پس از نصب اولیه و تهیهٔ وزن‌ها، با اینترنت قطع مسیر کامل کار کند.
6. تأخیر و RAM/CPU روی لپ‌تاپ هدف ثبت شوند؛ اگر ASR از زمان واقعی عقب افتاد interval، اندازهٔ مدل یا adapter باید بر اساس اندازه‌گیری تغییر کند.

## وابستگی صوتی جدید

SpeexDSP DLL/SO لازم است و به‌صورت پیش‌فرض AEC فعال است. نبود آن خطا می‌دهد؛ bypass فقط با
`PVAI_AEC_ENABLED=false` صریح انجام می‌شود. صف مرجع تأخیر و state فیلتر در overflow reset می‌شوند.
فیلتر در callback اجرا نمی‌شود؛ DSP در هماهنگ‌کننده و پیش از detector قرار دارد.
