# پروتکل صوتی ESP32-S3 ↔ PC (transport سرور پیاده‌شده — firmware نشده)

> **وضعیت صادقانه:** قرارداد انتقال در `server/protocol.py` (تست: `tests/test_protocol.py`) و
> **transport سمت سرور** در `server/ws_voice.py` (تست: `tests/test_ws_voice.py`) پیاده شده است.
> `GET /api/protocol` این وضعیت را با `"implemented": true`, `"transport_implemented": true` و
> `"firmware_implemented": false` برمی‌گرداند.
> **هیچ firmware، رجیستری دستگاه یا OTA در این مخزن وجود ندارد** و هیچ تست سخت‌افزاری/شبکهٔ واقعی
> اجرا نشده است (به `docs/HARDWARE_VALIDATION.md` نگاه کنید). اولویت PC-first است: تا وقتی موتور
> مکالمهٔ PC تست پذیرش را پاس نکرده، firmware شروع نمی‌شود.

## ۱. اصول

- انتقال: **WebSocket روی LAN خصوصی**؛ فریم‌های باینری برای صدا و متن JSON برای کنترل.
- فقط IPv4/IPv6 خصوصی یا loopback؛ هیچ relay ابری، هیچ STUN/TURN عمومی.
- صدا **PCM16 تک‌کاناله 16kHz** (32 KB/s) در هر دو جهت. خروجی TTS روی PC به 16kHz نرمال می‌شود.
- بلوک مرجع ۲۰ms = ۳۲۰ نمونه = ۶۴۰ بایت.
- هر دستگاه `device_id`، هر مکالمه `session_id`، هر پاسخ دستیار `turn_id` و هر گفتار کاربر
  `utterance_id` جدا دارد. این چهار شناسه با هم اشتباه نمی‌شوند.
- احراز هویت با توکن مشترک (`token`) در پیام `hello`؛ مقایسه با `hmac.compare_digest`
  (`server.protocol.check_token`). قبل از `hello` هیچ پیام دیگری پذیرفته نمی‌شود.

## ۲. فریم صوتی باینری

هدر ۱۰ بایتی big-endian:

| فیلد | نوع | معنا |
|---|---|---|
| `version` | uint8 | نسخهٔ پروتکل (`PROTOCOL_VERSION`) |
| `kind` | uint8 | `1` = capture (دستگاه→سرور)، `2` = playback (سرور→دستگاه) |
| `sequence` | uint16 | شمارهٔ ترتیب، افزایشی و مستقل برای هر جهت |
| `samples` | uint16 | تعداد نمونه؛ `1..MAX_AUDIO_SAMPLES` |
| `timestamp_ms` | uint32 | زمان تولید نسبت به شروع session |

سپس `samples * 2` بایت PCM16 little-endian. پیاده‌سازی: `encode_audio` / `decode_audio`.
هر نقض قرارداد (`kind` ناشناخته، طول نامنطبق، نمونهٔ فرد، نسخهٔ دیگر) `ProtocolError` می‌دهد؛
گزارش آن به دستگاه با پیام `error` انجام می‌شود، نه با بستن بی‌صدا.

## ۳. پیام‌های کنترلی (JSON)

فرمت: `{"type": "...", "seq": n, ...}`. فیلدهای الزامی در `server/protocol.py::REQUIRED_FIELDS`
تعریف شده‌اند و تست `test_every_message_type_has_a_required_field_rule` کامل بودن آن را بررسی می‌کند.

| نوع | جهت | فیلدهای الزامی | کار |
|---|---|---|---|
| `hello` | دستگاه→سرور | `device_id`, `token` | احراز هویت و شناسایی دستگاه |
| `hello_ok` | سرور→دستگاه | `session_id` | پذیرش، اعلام قالب صدا و نسخه |
| `session_start` | دستگاه→سرور | `session_id` | آغاز مکالمهٔ جدید (پاک‌شدن context قبلی) |
| `session_ack` | سرور→دستگاه | `session_id` | تأیید آغاز نشست |
| `speech_start` | دستگاه→سرور | `utterance_id` | VAD سمت دستگاه: شروع گفتار |
| `speech_end` | دستگاه→سرور | `utterance_id`, `reason` | پایان گفتار (`endpoint`/`max_segment`/`forced`/`overflow`/`device`) |
| `interrupt` | دستگاه→سرور | `reason` | barge-in (`user_speech`/`manual`/`timeout`/`shutdown`/`superseded`) |
| `play_start` | سرور→دستگاه | `turn_id`, `sample_rate` | آغاز ارسال صدای پاسخ |
| `play_end` | سرور→دستگاه | `turn_id`, `played_ms` | پایان پخش واقعی روی دستگاه |
| `play_stop` | سرور→دستگاه | `turn_id`, `reason` | قطع پخش (`barge_in`/`new_turn`/`shutdown`/`error`) |
| `set_voice` | هر دو | `voice` (`male`/`female`) | انتخاب صدا؛ سرور و دستگاه هم‌گام می‌شوند |
| `asr_partial` | سرور→دستگاه | `utterance_id`, `text` | متن نهایی‌نشده برای نمایش/ردیابی |
| `asr_final` | سرور→دستگاه | `utterance_id`, `text` | متن نهایی همان utterance |
| `ping` / `pong` | هر دو | `nonce` | اندازه‌گیری RTT و تشخیص قطعی |
| `error` | هر دو | `code`, `detail` | خطای قابل‌فهم (فارسی برای انسان، `code` انگلیسی) |

### قواعد رفتاری

1. **`asr_partial` هرگز فرمان نیست.** فقط `asr_final` به LLM می‌رود و می‌تواند به `Command Broker` منتهی شود.
2. `speech_start` سمت سرور، پاسخ در حال پخش را قطع می‌کند (barge-in) اما **ریال‌های ASR نهایی قبلی را
   هرگز باطل نمی‌کند** — همان قاعدهٔ `docs/STREAMING_ARCHITECTURE.md`.
3. `play_stop` باید در دستگاه هم اجرا شود: بافر پخش پاک و صدای قدیمی خاموش؛ بایت‌های در راه نباید
   بعد از `play_stop` پخش شوند (شمارهٔ `sequence` برای تشخیص).
4. `ping` بدون `pong` در چند دوره = قطع اتصال؛ دستگاه باید reconnect کند و `session_id` تازه بگیرد.
5. خطای احراز هویت → `error` با کد `auth_failed` و بستن اتصال.
6. NaN/`inf` در صدا پذیرفته نمی‌شود؛ PCM16 دقیقاً ۲ بایت هر نمونه.

## ۴. جریان نمونه (یک نوبت کامل)

```text
device            server(PC)                llama-server      piper
  | hello(token) -->|
  |<- hello_ok ------|
  | session_start ->|
  |<- session_ack ---|
  | capture seq=1 -->|  (20ms blocks, continuous)
  | speech_start --->|  VAD onset
  | capture ... ---->|  partial ASR windows every 0.8s
  |<- asr_partial ---|
  | speech_end ----->|  endpoint
  |<- asr_final -----|
  |                  |--- POST /v1/chat/completions (SSE) -->|
  |                  |<-- sentence streams ------------------|
  |<- play_start ----|
  |<- playback seq=1-|  PCM 16k
  |  (user speaks)   |
  | interrupt ------>|  barge-in: cancel generation + queued speech
  |<- play_stop -----|
  | capture ... ---->|  utterance جدید؛ context مکالمه حفظ می‌شود
```

## ۵. بازپخش، reconnect و شناسه‌ها

- `sequence` هر جهت مستقل و ۱۶ بیتی است؛ سرریز → `sequence = 0` و ادامه.
- پس از reconnect، دستگاه `hello` می‌فرستد؛ اگر `session_id` قبلی را بخواهد، با `session_start`
  همان شناسه را اعلام می‌کند و سرور context محلی را از `data/conversations/<session_id>.jsonl`
  بازیابی می‌کند (فقط اگر `PVAI_HISTORY_PERSIST=true`).
- سرور در `hello_ok` نسخهٔ پروتکل و قالب صدا را اعلام می‌کند؛ دستگاه ناسازگار باید صریحاً
  `error` بدهد، نه اینکه با نرخ اشتباه ادامه دهد.

## ۶. transport سمت سرور (`WS /ws/voice`)

پیاده‌سازی: `server/ws_voice.py`؛ فقط وقتی `PVAI_WS_ENABLED=true` باشد در برنامه ثبت می‌شود.

- **احراز هویت اجباری است.** اگر `PVAI_WS_TOKEN` خالی باشد، هر اتصال با `error` کد
  `auth_not_configured` و بستن `1008` رد می‌شود؛ API صوتی بی‌احراز هویت باز نمی‌شود.
  `PVAI_WS_ENABLED=true` بدون توکن خطای پیکربندی است (`Settings.config_errors`).
- نخستین پیام باید `hello` باشد؛ پیام دیگر → `error` کد `handshake_required`. مهلت `hello` ده ثانیه
  است (`handshake_timeout`). پیام خطا **قبل از** بستن اتصال فرستاده می‌شود تا دستگاه دلیل را لاگ کند.
- هر اتصال **Pipeline و Conversation مستقل** دارد؛ ASR، TTS و صف پخش بین دستگاه‌ها مشترک نیست.
- capture باینری به همان `Pipeline` مسیر میکروفن داده می‌شود؛ event‌های آن یک‌به‌یک به پیام‌های
  `speech_start`/`speech_end`/`asr_partial`/`asr_final`/`play_start`/`play_end`/`play_stop` تبدیل می‌شوند.
- پخش: هر بلوک ۲۰ms در یک فریم باینری `kind=2` با `sequence` صعودی ۰..۶۵۵۳۵. صف پخش محدود است
  (۲۵۰ فریم ≈ ۵ ثانیه)؛ دستگاه کند باعث backpressure روی سنتز می‌شود، نه رشد حافظه.
- `play_end` مرز **نوبت** است، نه جمله؛ و فقط پس از ارسال همهٔ فریم‌های آن نوبت فرستاده می‌شود
  (نوبت چندجمله‌ای یک `play_end` دارد).
- `interrupt` از دستگاه: `Pipeline.barge_in` اجرا می‌شود، صف پخش همان لحظه خالی می‌شود و سپس
  `play_stop` می‌رود؛ هیچ فریمی از نوبت لغوشده بعد از `play_stop` ارسال نمی‌شود.
- `speech_start`/`speech_end` از دستگاه فقط **اطلاعی** هستند؛ endpointing مال سرور است.
- `set_voice` صدای جمله‌های بعدی را عوض می‌کند؛ اگر مدل آن صدا نباشد `error` کد `voice_unavailable`.
- متریک‌ها: `ws_connections`, `ws_frames_in`, `ws_frames_out`, `ws_auth_failures`.
- `/ready` کلید `ws_voice` را برمی‌گرداند (خاموش/بدون‌توکن/آماده).

## ۷. امنیت LAN

- API صوتی **بدون احراز هویت نباید عمومی شود**. `PVAI_HOST=0.0.0.0` فقط در شبکهٔ مورد اعتماد.
- توکن در `.env` نگه داشته می‌شود؛ در مخزن commit نشود (`.gitignore`).
- پیشنهاد آینده: توکن per-device، rate limit و لاگ اتصال. هیچ‌کدام **پیاده نشده‌اند** و نباید
  به‌عنوان قابلیت موجود مستند شوند.

## ۸. آنچه هنوز لازم است (به ترتیب)

1. firmware ESP-IDF: I2S capture/playback، VAD سبک سمت دستگاه، reconnect (شروع نشده).
2. رجیستری دستگاه per-device token، rate limit و لاگ اتصال (شروع نشده).
3. OTA و مدیریت نسخهٔ firmware (شروع نشده).
4. سنجش RTT/LAN jitter واقعی با `ping/pong` روی سخت‌افزار — **NOT MEASURED**.
