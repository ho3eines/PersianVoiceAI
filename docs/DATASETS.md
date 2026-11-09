# آماده‌سازی دادهٔ سفارشی و قرارداد آموزش (آفلاین)

این سند دو کار می‌کند: (۱) می‌گوید چگونه دادهٔ فارسی خودتان را برای آموزش آماده و
اعتبارسنجی کنید، (۲) قرارداد hyper-parameterها را برای **trainer خارجی** تعریف می‌کند.

## دامنهٔ فعلی (واقعیت کد)

این مخزن **حلقهٔ آموزش یا fine-tuning ندارد**. Whisper/CTranslate2، Piper و
llama.cpp فقط برای inference محلی استفاده می‌شوند و فایل `model.bin` یک checkpoint
قابل آموزش نیست. ابزارهای این مخزن:

| ابزار | کار |
|---|---|
| `tools/prepare_dataset.py` | اعتبارسنجی manifest/WAV، گزارش نشت split، export قابل‌حمل JSONL |
| `tools/train_plan.py` | اعتبارسنجی پیکربندی آموزش و تولید plan ماشین‌خوان برای trainer خارجی |
| `tools/training_config.py` | لایهٔ مشترک JSON/YAML، مسیرها، و اعتبارسنجی انواع |
| `configs/dataset.example.yaml` | نمونهٔ پیکربندی داده (سه split) |
| `configs/training.example.yaml` | نمونهٔ قرارداد آموزش (batch/lr/epochs/seed/…) |
| `benchmarks/persian_eval.py` | ارزیابی WER/CER پس از آموزش |

هیچ‌کدام شبکه استفاده نمی‌کنند و هیچ عدد کارایی/کیفیتی بدون اندازه‌گیری گزارش نمی‌شود.

## ۱. جمع‌آوری و جداسازی

- فقط صوت دارای رضایت و مجوز استفاده کنید؛ اطلاعات شخصی را حذف کنید.
- دادهٔ خصوصی را بیرون مخزن نگه دارید. صوت، manifest و `configs/*.yaml` شخصی را
  commit نکنید (فقط فایل‌های `*.example.*` در مخزن‌اند).
- train/validation/test را **پیش از آموزش** بر اساس گوینده و جلسهٔ ضبط جدا کنید.
  `--check` یک گزارش نشت (گوینده/فایل مشترک بین splitها) می‌دهد؛ آن را جدی بگیرید،
  چون یک گویندهٔ مشترک عدد held-out را بی‌اعتبار می‌کند.
- مجموعهٔ `benchmarks/persian_eval_set.json` برای ارزیابی است؛ آن را وارد train نکنید.
- متن باید رونویسی واقعی همان قطعه باشد. نرمال‌سازی WER در
  `server.persian.normalize_for_eval` سیاست ارزیابی است، نه الزام تغییر متن آموزش.

## ۲. قرارداد manifest

هر split یک فایل UTF-8 با پسوند `.json`، `.jsonl` یا `.csv` دارد، یا یک پوشهٔ
Hugging Face `save_to_disk` است (بخش ۵). JSON یک آرایهٔ غیرخالی از objectهاست؛
JSONL هر خط یک object؛ CSV هدر الزامی دارد.

| فیلد | الزامی | معنی |
|---|---|---|
| `id` | ✅ | شناسهٔ یکتا و ایمن (بدون `..`, `/`, `\`)؛ در ارزیابی نام فایل `<id>.wav` می‌شود |
| `audio` | ✅ | مسیر فایل WAV (نسبی یا مطلق) |
| `text` | ✅ | رونویسی مرجع، غیرخالی |
| `speaker` | اختیاری | برای گزارش نشت split اکیداً توصیه می‌شود |
| `split` | اختیاری | اگر بیاید باید با split همان manifest یکی باشد |
| `duration_s`, `source` | اختیاری | حفظ می‌شوند؛ `duration_s` از خود WAV بازمحاسبه می‌شود |

نمونهٔ JSONL (`train.jsonl`):

```json
{"id":"speaker01-001","audio":"audio/speaker01-001.wav","text":"سلام حال شما چطور است؟","speaker":"speaker01"}
```

نمونهٔ CSV:

```csv
id,audio,text,speaker
speaker01-001,audio/speaker01-001.wav,سلام حال شما چطور است؟,speaker01
```

فیلد CSV دارای کاما باید داخل کوتیشن باشد. نسخهٔ اصلی manifest و metadata
گوینده/مجوز را جداگانه نگه دارید؛ export فقط فیلدهای شناخته‌شده را می‌نویسد.

## ۳. پیکربندی داده (توصیه‌شده) و مسیرها

همهٔ پارامترها هم از **فایل پیکربندی** و هم از **آرگومان خط فرمان** قابل تنظیم‌اند:

```bash
cp configs/dataset.example.yaml configs/dataset.yaml   # سپس مسیرها را ویرایش کنید
export PVAI_CORPUS_ROOT=/data/persian-corpus          # یا در خود فایل بگذارید
python tools/prepare_dataset.py --config configs/dataset.yaml --check --report reports/dataset.json
```

```bash
# یک manifest، با پرچم‌های صریح
python tools/prepare_dataset.py --manifest manifests/train.jsonl \
  --audio-root audio --sample-rate 16000 \
  --min-duration-s 0.3 --max-duration-s 30 \
  --out validated/train.jsonl
```

قواعد مسیر — همه در `tools/training_config.py` پیاده شده‌اند:

1. `${VAR}`، `$VAR`، `${VAR:-default}` و `~` باز می‌شوند. متغیر تعریف‌نشده
   **خطای پیکربندی** است، نه مسیر عجیب `${VAR}/...`.
2. مسیر نسبی در فایل پیکربندی **نسبت به پوشهٔ همان فایل** حل می‌شود، پس `cd` به
   پوشهٔ دیگر رفتار را عوض نمی‌کند. مسیر مطلق همان‌طور که هست استفاده می‌شود.
3. `PVAI_CORPUS_ROOT` مبنای مسیرهای `audio` است وقتی `--audio-root`/`audio_root`
   داده نشده باشد (پیش‌فرض: پوشهٔ manifest).
4. `PVAI_DATASET_CONFIG` و `PVAI_TRAIN_CONFIG` مسیر فایل پیکربندی را می‌دهند،
   `PVAI_BENCHMARK_OUTPUT_DIR` و `PVAI_EVAL_SET` مسیر خروجی/مجموعهٔ ارزیابی را.
5. پرچم خط فرمان بر مقدار فایل پیکربندی اولویت دارد.

کلید ناشناخته در فایل پیکربندی **رد** می‌شود (با پیشنهاد نزدیک‌ترین کلید درست)،
چون یک غلط تایپی مثل `learnig_rate` در سکوت به پیش‌فرض برمی‌گشت و آزمایش را عوض می‌کرد.

## ۴. اعتبارسنجی: چه چیزی بررسی می‌شود

| بررسی | رفتار |
|---|---|
| فرمت manifest | `.json`/`.jsonl`/`.csv`/HF dir؛ پسوند دیگر خطای صریح |
| فیلدهای رکورد | `id`/`audio`/`text` رشتهٔ غیرخالی؛ object بودن هر رکورد |
| `id` | یکتا و ایمن (بدون `..`, `/`, `\`, NUL) |
| recording | یکتا؛ WAV واقعاً باز می‌شود |
| فرمت WAV | PCM16 mono با نرخ پیکربندی (پیش‌فرض 16000) |
| خالی/بریده | `nframes>0` و طول داده = `nframes*2` |
| مدت | `min_duration_s`/`max_duration_s` روی طول واقعی |
| split | فیلد `split` نباید با manifest متناقض باشد؛ یک manifest = یک split |
| نشت | گزارش گوینده/فایل مشترک بین splitها |

خروجی: پیام خطا با مسیر manifest، شمارهٔ رکورد و مسیر فایل، و exit code `2`.
هیچ فایلی بازنویسی نمی‌شود و هیچ صوتی تغییر نمی‌کند.

```bash
# تبدیل آگاهانه روی یک کپی (FFmpeg سیستمی، نه پایتون)
ffmpeg -i original.wav -ar 16000 -ac 1 -c:a pcm_s16le normalized.wav
```

## ۵. ورودی Hugging Face (`save_to_disk`)

پوشه‌ای که `Dataset.save_to_disk`/`DatasetDict.save_to_disk` ساخته است مستقیماً
پذیرفته می‌شود:

```bash
python tools/prepare_dataset.py --manifest /data/hf/persian-tts --split train --check
```

- `dataset_dict.json` فهرست splitها را می‌دهد؛ `--split` برای انتخاب لازم است.
  یک `Dataset` تک‌split خودش نام split را تعیین می‌کند.
- شاردهای `.arrow` با PyArrow خوانده می‌شوند (`python -m pip install pyarrow`؛
  بخش توسعهٔ `requirements-dev.txt`). بدون PyArrow پیام صریح داده می‌شود.
- ستون `audio` **باید رشتهٔ مسیر** باشد. اگر ستون یک feature واکاوی‌شدهٔ `Audio` باشد
  (نمونه‌ها داخل arrow)، رد می‌شود: ابتدا نمونه‌ها را به WAV بنویسید و مسیر بدهید.
- هیچ دانلودی از Hub انجام نمی‌شود؛ `datasets` در زمان اعتبارسنجی لازم نیست.

## ۶. قرارداد آموزش برای trainer خارجی

`tools/train_plan.py` پیکربندی را اعتبارسنجی و یک plan ماشین‌خوان تولید می‌کند:

```bash
# فقط اعتبارسنجی طرح‌واره (بدون نیاز به corpus؛ همان چیزی که CI اجرا می‌کند)
python tools/train_plan.py --config configs/training.example.yaml --check --no-require-paths

# اعتبارسنجی واقعی و تحویل plan به trainer
PVAI_CORPUS_ROOT=/data/persian-corpus python tools/train_plan.py \
  --config configs/training.yaml --write runs/whisper-small-fa/plan.json
```

کلیدها و بازه‌های مجاز:

| کلید | پیش‌فرض | اعتبارسنجی |
|---|---|---|
| `model` | `openai/whisper-small` | رشتهٔ غیرخالی |
| `output_dir` | — (الزامی) | مسیر؛ نسبی = نسبت به فایل پیکربندی |
| `seed` | `13` | عدد صحیح ≥ 0 |
| `batch_size` | `8` | عدد صحیح ≥ 1 |
| `grad_accum` | `1` | عدد صحیح ≥ 1؛ `effective_batch_size = batch_size × grad_accum` |
| `learning_rate` | `1e-5` | عدد متناهی در `(0, 1]` |
| `epochs` | `3` | عدد صحیح ≥ 1 |
| `warmup_steps` | `100` | عدد صحیح ≥ 0 |
| `precision` | `fp32` | `fp32`/`fp16`/`bf16`/`int8` |
| `data.{train,validation,test}` | — (`train` الزامی) | فایل JSON/JSONL/CSV یا پوشهٔ HF |
| `data.sample_rate` | `16000` | 1000–192000 (Whisper pretrained = 16 kHz) |
| `data.min_duration_s`/`max_duration_s` | `0` / بی‌حد | `max > min` |

plan خروجی شامل `effective_batch_size`، مسیر مطلق هر split و صریحاً
`"trainer": "external (this repository performs no training)"` است. trainer باید
همان plan را بخواند تا اجرای مقایسه‌شده با hyper-parameter یکسان انجام شود.
seed، نسخهٔ مدل/داده و وابستگی‌ها را هم در لاگ همان trainer ثبت کنید.

## ۷. ارزیابی پس از آموزش

فرمت ارزیابی با فرمت train متفاوت است: JSON دارای کلید `sentences`، با `id` و
`text` و فیلدهای اختیاری `category` و `kind`؛ شناسه فقط حروف لاتین، رقم، `_` و `-`
دارد. `kind=no_speech` باید متن خالی داشته باشد. برای حالت صوت فایل
`<id>.wav` با PCM16 mono 16kHz بسازید.

```bash
python benchmarks/persian_eval.py --set /path/to/eval.json --list
python benchmarks/persian_eval.py --set /path/to/eval.json \
  --hypotheses /path/to/transcripts.json --strict \
  --json-out /path/to/report.json
```

مسیرها با `PVAI_EVAL_SET` هم قابل تنظیم‌اند. hypotheses یک object از `id: text`
یا لیست objectهای `id` و `text` است؛ شناسهٔ تکراری در لیست و مقدار غیررشته‌ای رد
می‌شود. معیارها روی دادهٔ واقعی گزارش شوند؛ اعتبارسنجی manifest به‌تنهایی تأیید
کیفیت برچسب‌ها یا نتیجهٔ آموزش نیست.
