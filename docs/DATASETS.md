# آماده‌سازی دادهٔ سفارشی (آفلاین)

## دامنهٔ فعلی

این مخزن **حلقهٔ آموزش یا fine-tuning ندارد**؛ Whisper/CTranslate2، Piper و
llama.cpp برای inference محلی استفاده می‌شوند. `benchmarks/persian_eval.py`
برای ارزیابی است و `tools/prepare_dataset.py` فقط اعتبارسنجی و خروجی manifest
انجام می‌دهد. batch size، learning rate و epochs در کد فعلی مصرف‌کننده ندارند؛
آن‌ها باید در argparse یا فایل پیکربندی trainer آینده تعریف شوند، نه در inference.
فایل CTranslate2 `model.bin` جایگزین checkpoint قابل آموزش Whisper نیست.

## ۱. جمع‌آوری و جداسازی

- فقط صوت دارای رضایت و مجوز مناسب استفاده کنید؛ اطلاعات شخصی را حذف کنید.
- دادهٔ خصوصی را بیرون مخزن نگه دارید. صوت و manifest را commit نکنید.
- train/validation/test را **پیش از آموزش** بر اساس گوینده و جلسهٔ ضبط جدا کنید؛
  قطعه‌های یک ضبط نباید در چند split باشند. validator هر manifest را مستقل بررسی
  می‌کند و عدم هم‌پوشانی بین splitها را تضمین نمی‌کند.
- مجموعهٔ `benchmarks/persian_eval_set.json` برای ارزیابی است؛ آن را وارد train نکنید.
- متن باید رونویسی واقعی همان قطعه باشد. نرمال‌سازی WER در
  `server.persian.normalize_for_eval` سیاست ارزیابی است، نه الزام تغییر متن آموزش.

## ۲. قرارداد manifest

هر split یک فایل UTF-8 با پسوند `.json`، `.jsonl` یا `.csv` دارد.
JSON یک آرایهٔ غیرخالی از objectهاست؛ JSONL هر خط یک object؛ CSV هدر الزامی دارد.
فیلدهای الزامی و رشته‌ای غیرخالی:

| فیلد | معنی |
|---|---|
| `id` | شناسهٔ یکتا در manifest |
| `audio` | مسیر محلی فایل WAV |
| `text` | رونویسی مرجع |

نمونهٔ JSONL (`train.jsonl`):

```json
{"id":"speaker01-001","audio":"audio/speaker01-001.wav","text":"سلام حال شما چطور است؟"}
```

نمونهٔ CSV:

```csv
id,audio,text
speaker01-001,audio/speaker01-001.wav,سلام حال شما چطور است؟
```

فیلد CSV دارای کاما باید داخل کوتیشن باشد. فیلدهای اضافی در export حفظ نمی‌شوند؛
نسخهٔ اصلی manifest و metadata گوینده/مجوز را جداگانه نگه دارید.

## ۳. مسیرها و اعتبارسنجی

مسیرهای نسبی `audio` به **پوشهٔ manifest** وابسته‌اند، نه پوشهٔ اجرای فرمان.
`--audio-root` این مبنا را تغییر می‌دهد؛ مقدار نسبی آن نسبت به پوشهٔ اجرای فرمان است.
مسیر مطلق، `~` و متغیر محیطی مانند `${CORPUS_ROOT}` نیز پشتیبانی می‌شوند.
خروجی مسیر صوت را نسبت به پوشهٔ manifest خروجی ذخیره می‌کند؛ برای انتقال، ساختار
نسبی پوشه‌های صوت و manifest را با هم حفظ کنید. متغیر محیطی تعریف‌نشده در نهایت
به‌صورت مسیر ناموجود رد می‌شود. هیچ دانلود یا تماس شبکه‌ای انجام نمی‌شود.

```bash
python tools/prepare_dataset.py --manifest /path/to/corpus/train.jsonl
python tools/prepare_dataset.py --manifest /path/to/corpus/train.csv \
  --audio-root /path/to/recordings --sample-rate 16000 \
  --out /path/to/corpus/validated/train.jsonl
```

همهٔ پارامترهای preprocessing از argparse می‌آیند:
`--manifest` (اجباری)، `--audio-root` (پیش‌فرض پوشهٔ manifest)،
`--sample-rate` (پیش‌فرض 16000)، `--out` (اختیاری).

validator با کتابخانهٔ استاندارد پایتون فایل را واقعاً باز می‌کند و WAV PCM16،
mono، نرخ انتخاب‌شده، غیرخالی بودن و کامل بودن فریم‌ها را بررسی می‌کند.
شناسه یا recording تکراری، نوع فیلد نامعتبر، JSON خراب و صوت ناخوانا با پیام شامل
مسیر/شمارهٔ رکورد و exit code 2 رد می‌شوند. فایل ورودی بازنویسی نمی‌شود.
این ابزار صوت را تغییر نمی‌دهد؛ تبدیل را آگاهانه روی یک کپی انجام دهید، مثلاً با
FFmpeg نصب‌شدهٔ محلی:

```bash
ffmpeg -i original.wav -ar 16000 -ac 1 -c:a pcm_s16le normalized.wav
```

## ۴. استفاده با trainer خارجی یا Hugging Face

این ابزار `DatasetDict`، مسیر `save_to_disk` یا نام dataset روی Hub را نمی‌خواند.
برای دادهٔ Hugging Face، هر split را **محلی** به قرارداد JSONL بالا export کنید؛
ستون audio باید مسیر فایل باشد، نه object، آرایهٔ نمونه‌ها یا URL.
اگر داده فقط آرایه دارد، ابتدا آن را با نرخ صحیح به WAV محلی ذخیره کنید.
شناسهٔ پایدار به هر ردیف بدهید و splitها را جدا اعتبارسنجی کنید.
پس از validation، trainer خارجی باید manifest را بخواند و مسیرهای نسبی audio را
نسبت به خود manifest resolve کند. هایپرپارامترها، seed، نسخهٔ مدل/داده و وابستگی‌ها
را در پیکربندی همان trainer ثبت کنید. این مخزن اجرای آموزش را ارائه نمی‌دهد.

## ۵. ارزیابی پس از آموزش

فرمت ارزیابی با فرمت train متفاوت است: JSON دارای کلید `sentences`، با `id` و
`text` و فیلدهای اختیاری `category` و `kind`؛ شناسه فقط حروف لاتین، رقم، `_` و `-`
دارد. `kind=no_speech` باید متن خالی داشته باشد. برای حالت صوت فایل
`<id>.wav` با PCM16 mono 16kHz بسازید.

```bash
python benchmarks/persian_eval.py --set /path/to/eval.json --list
python benchmarks/persian_eval.py --set /path/to/eval.json \
  --hypotheses /path/to/transcripts.json --strict
```

hypotheses یک object از `id: text` یا لیست objectهای `id` و `text` است؛ شناسهٔ
تکراری در لیست و مقدار غیررشته‌ای رد می‌شود. معیارها روی دادهٔ واقعی گزارش شوند؛
validation manifest به‌تنهایی تأیید کیفیت برچسب‌ها یا نتیجهٔ آموزش نیست.
