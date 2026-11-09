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

- **احراز هویت اجباری است.** اگر هیچ توکنی تنظیم نشده باشد، هر اتصال با `error` کد
  `auth_not_configured` و بستن `1008` رد می‌شود؛ API صوتی بی‌احراز هویت باز نمی‌شود.
  `PVAI_WS_ENABLED=true` بدون توکن خطای پیکربندی است (`Settings.config_errors`).
- **توکن per-device.** `PVAI_WS_TOKENS="esp32-a:token-a,esp32-b:token-b"` هر دستگاه را به توکن
  خودش گره می‌زند؛ بستن دسترسی یک دستگاه یعنی حذف همان جفت، بدون تعویض توکن بقیه. اگر توکن با
  `device_id` دیگری در `hello` بیاید `error` کد `device_mismatch` است، و توکن ناشناخته
  `auth_failed`. توکن مشترک `PVAI_WS_TOKEN` هنوز کار می‌کند ولی باید صریحاً
  `PVAI_WS_ALLOW_SHARED_TOKENS=true` بگیرد: با یک توکن برای همه، بستن یک دستگاه یعنی بستن همه،
  و لاگ نمی‌تواند بگوید کدام دستگاه بوده.
- نخستین پیام باید `hello` باشد؛ پیام دیگر → `error` کد `handshake_required`. مهلت `hello` ده ثانیه
  است (`handshake_timeout`). پیام خطا **قبل از** بستن اتصال فرستاده می‌شود تا دستگاه دلیل را لاگ کند.
- هر اتصال **Pipeline و Conversation مستقل** دارد؛ ASR، TTS و صف پخش بین دستگاه‌ها مشترک نیست.
- capture باینری به همان `Pipeline` مسیر میکروفن داده می‌شود؛ event‌های آن یک‌به‌یک به پیام‌های
  `speech_start`/`speech_end`/`asr_partial`/`asr_final`/`play_start`/`play_end`/`play_stop` تبدیل می‌شوند.
- **فریم ورودی دقیقاً ۲۰ms.** capture باینری باید مضربی از ۳۲۰ نمونه باشد؛ بلوک‌های بزرگ‌تر
  به همان ۳۲۰ نمونه‌ای تقسیم می‌شوند و اندازهٔ نامعتبر `error` کد `protocol_error` می‌گیرد.
  دلیل: endpointing ناشی از VAD فریم‌ها را ۲۰ms فرض می‌کند، پس یک فریم ۱۰۰ms زمان سکوت را
  ۵ برابر اشتباه می‌شمارد.
- `seq` در capture بررسی می‌شود: پرش و تکرار در `ws_capture_gap_frames` و
  `ws_capture_out_of_order` ثبت می‌شود (WiFi بسته گم می‌کند؛ پیش‌تر هیچ اثری نمی‌ماند).
  نرخ فریم هم سقف دارد (`PVAI_WS_FRAME_RATE`، پیش‌فرض ۲۰۰/s = ۴ برابر نرخ اسمی): دستگاه معیوب
  نمی‌تواند CPU را با صدایی که خط لوله دور می‌ریزد اشغال کند. برست کوتاه (توقف WiFi و بعد
  تحویل دسته‌ای) جریمه نمی‌شود.
- پخش: هر بلوک ۲۰ms در یک فریم باینری `kind=2` با `sequence` صعودی ۰..۶۵۵۳۵. صف پخش محدود است
  (۲۵۰ فریم ≈ ۵ ثانیه)؛ دستگاه کند باعث backpressure روی سنتز می‌شود، نه رشد حافظه.
- **پخش pacing دارد.** فریم‌ها با نرخ واقعی (هر ۲۰ms) فرستاده می‌شوند، نه با سرعت سنتز.
  پیش از این سرور تا ۵ ثانیه جلوتر از دستگاه می‌رفت؛ دستگاه آن را بافر می‌کرد و پس از barge-in
  همان بافر تا رسیدن `play_stop` پخش می‌شد. اعتبار اولیهٔ بافر
  `PVAI_WS_PLAYBACK_CREDIT_MS` (پیش‌فرض ۲۰۰ms) است تا بین برست‌های سنتز underrun نشود؛
  با `PVAI_WS_PLAYBACK_PACING=false` می‌توان خاموشش کرد (فقط برای اشکال‌زدایی).
  از این رو `played_ms` یعنی «صدایی که واقعاً به دستگاه رسید»، نه «صدایی که در صف بود».
- `play_start` و `play_end` هر دو مرز **نوبت** هستند، نه جمله: `tts_start` برای هر جمله می‌آید،
  ولی سرور یک `play_start` در آغاز نوبت و یک `play_end` پس از ارسال همهٔ فریم‌های آن می‌فرستد
  (نوبت چندجمله‌ای دقیقاً یک جفت دارد).
- **پیام‌های حیاتی هرگز دور ریخته نمی‌شوند.** صف کنترل کران‌دار است (۶۴)؛ پیش‌تر هر چه جا
  نمی‌شد حذف می‌شد — از جمله `play_stop`. اکنون فقط `asr_partial` قابل‌حذف است (شمارندهٔ
  `ws_control_dropped`، چون partial تازه‌تر جایش را می‌گیرد)؛ `play_stop`/`play_end`/`asr_final`/
  `error` در صف سرریز می‌مانند و **پیش از** بقیهٔ صف فرستاده می‌شوند. شمارندهٔ `rejected` فقط
  برای رد handshake/ظرفیت است.
- **heartbeat و idle timeout.** سرور هر `PVAI_WS_HEARTBEAT` (پیش‌فرض ۱۵s) یک `ping` می‌فرستد و
  اتصالی که `PVAI_WS_IDLE_TIMEOUT` (پیش‌فرض ۶۰s) هیچ پیامی نفرستد بسته می‌شود؛ پیش از این
  دستگاه مرده فقط با timeout چنددقیقه‌ای TCP آزاد می‌شد و مدل‌ها اشغال می‌ماند. دستگاه باید
  به `ping` با `pong` پاسخ دهد.
- اتصال همزمان سقف دارد (`PVAI_WS_MAX_CONNECTIONS`، پیش‌فرض ۴) و نرخ اتصال هم
  (`PVAI_WS_CONNECT_RATE`). مدل‌ها (Whisper و Piper) **یک‌بار** در کل فرایند بار می‌شوند و بین
  اتصال‌ها مشترک‌اند؛ بارگذاری در `asyncio.to_thread` انجام می‌شود تا loop و REST بلاک نشود.
  چون هیچ‌کدام thread-safe نیستند، decode در `LocalAudio._decode_lock` و هر گام مدل Piper در
  قفل همان صدا (`ModelPool.piper_lock`) سریال می‌شود — پخش دو دستگاه هم‌زمان هم‌پوشانی دارد،
  ولی مدل مشترک هم‌زمان پیش برده نمی‌شود.
- یک `session_id` در هر لحظه یک نویسنده دارد؛ دستگاه دوم `error` کد `session_in_use` می‌گیرد
  (دو نویسنده روی یک JSONL رکوردها را درهم می‌کردند).
- `interrupt` از دستگاه: `Pipeline.barge_in` اجرا می‌شود، صف پخش همان لحظه خالی می‌شود و سپس
  `play_stop` می‌رود؛ هیچ فریمی از نوبت لغوشده بعد از `play_stop` ارسال نمی‌شود.
- `speech_start`/`speech_end` از دستگاه فقط **اطلاعی** هستند؛ endpointing مال سرور است.
- `set_voice` صدای جمله‌های بعدی را عوض می‌کند؛ اگر مدل آن صدا نباشد `error` کد `voice_unavailable`.
- متریک‌ها: `ws_connections`, `ws_frames_in`, `ws_frames_out`, `ws_auth_failures`,
  `ws_control_dropped`, `ws_capture_gap_frames`, `ws_capture_out_of_order`,
  `ws_capture_bad_size`, `ws_capture_rate_limited`, `ws_voice_rejected`, `ws_heartbeats`,
  `ws_idle_timeouts`. سمت REST: `api_auth_failures`, `api_rate_limited`.
- `/ready` (پشت توکن) کلید `ws_voice` را برمی‌گرداند (خاموش/بدون‌توکن/آماده).

## ۷. امنیت LAN

- **هر مسیر REST توکن می‌خواهد.** `PVAI_API_TOKEN` به‌صورت
  `Authorization: Bearer <token>` (یا `X-API-Key`) روی همهٔ مسیرها جز `/health` بررسی می‌شود.
  پیش از این `GET /api/sessions/{id}` متن کامل گفتگو را بی‌احراز هویت برمی‌گرداند و
  `/v1/chat/completions` هم آزاد بود — با `PVAI_HOST=0.0.0.0`، که برای دستگاه LAN لازم است،
  هر کسی در شبکه این‌ها را می‌دید. `/health` فقط `{"status": "ok"}` است: بدون نام مدل، بدون
  مسیر داده. جزئیات پشت توکن در `/ready` است.
- **اتصال روی نشانی غیر-loopback بدون توکن بالا نمی‌آید.** `PVAI_HOST=0.0.0.0` یا `::` بدون
  `PVAI_API_TOKEN` خطای پیکربندی است، پس حالت «امن نیست ولی کار می‌کند» وجود ندارد.
  روی `127.0.0.1` توکن اختیاری است.
- نرخ درخواست REST محدود است (`PVAI_API_RATE_LIMIT`/`PVAI_API_RATE_BURST`).
- توکن در `.env` نگه داشته می‌شود؛ در مخزن commit نشود (`.gitignore`).
- **TLS لازم است اگر شبکه مورد اعتماد نیست.** توکن روی WS و REST به‌صورت plaintext روی LAN
  می‌رود؛ در شبکهٔ مشترک (WiFi عمومی، خوابگاه، مهمان) هر کسی می‌تواند آن را بخواند.
  جلوی سرور یک reverse proxy با `wss://`/`https://` بگذارید (Caddy یا nginx) و دستگاه را به
  آن وصل کنید. سرور خودش TLS terminate نمی‌کند.
- این وضعیت **هشدار** است نه خطا: در لاگ startup و در `/ready` (کلید
  `checks.configuration.warnings`) دیده می‌شود. `config_errors` عمداً چیزی نمی‌گوید، چون آن
  یکی سخت‌گیر است (`run_live` را متوقف می‌کند و `/ready` را ناآماده می‌کند) و نباید راه‌اندازی
  خانگی مستند را غیرممکن کند.
- توکن per-device است (بخش ۶): هر توکن فقط با یک `device_id` کار می‌کند، پس لو رفتن توکن
  یک دستگاه بقیه را باز نمی‌کند و بستن آن دستگاه بقیه را قطع نمی‌کند.

## ۸. آنچه هنوز لازم است (به ترتیب)

1. firmware ESP-IDF: I2S capture/playback، VAD سبک سمت دستگاه، reconnect (شروع نشده).
2. رجیستری دستگاه، توکن per-device، rate limit و لاگ اتصال — **پیاده شد** (بخش ۶ و ۷)؛
   تنها حلقهٔ باقی‌مانده `pong` در firmware است.
3. OTA و مدیریت نسخهٔ firmware (شروع نشده).
4. سنجش RTT/LAN jitter واقعی با `ping/pong` روی سخت‌افزار — **NOT MEASURED**.

### HTTPS / WSS در LAN

با `PVAI_SSL_CERTFILE` و `PVAI_SSL_KEYFILE` روی uvicorn، دستگاه به
`wss://host:port/ws/voice` وصل می‌شود. اگر گواهی را در reverse proxy پایان
می‌دهید، API را فقط روی `127.0.0.1:8000` نگه دارید و توکن REST و دستگاه را
حفظ کنید. نمونهٔ Caddy (دامنه و گواهی معتبر خودتان):

```caddy
voice.example.internal {
    reverse_proxy 127.0.0.1:8000
}
```

برای nginx نیز `proxy_pass http://127.0.0.1:8000`،
`proxy_set_header Upgrade $http_upgrade` و `proxy_set_header Connection "upgrade"`
در location `/ws/voice` ضروری است. اتصال دستگاه `wss://` باشد؛ در شبکهٔ
غیرمطمئن `ws://` توکن hello و صوت را آشکار می‌کند. گواهی خودامضا نیاز به
نصب CA روی firmware دارد؛ بدون آن اعتبارسنجی TLS را خاموش نکنید.
