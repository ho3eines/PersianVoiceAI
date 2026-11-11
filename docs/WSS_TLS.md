# wss و TLS برای دستگاه LAN — Caddy، nginx و TLS مستقیم

این سند فقط چیزی را می‌گوید که در همین مخزن قابل بررسی است. جایی که به سخت‌افزار
واقعی (ESP32-S3)، نصب واقعی Caddy/nginx یا گواهی معتبر دامنه نیاز است، صریحاً
**NOT MEASURED** نوشته شده است.

## چرا wss لازم است

توکن دستگاه (`PVAI_WS_TOKENS`) و متن مکالمه روی `ws://` به‌صورت plaintext در شبکه
می‌روند. روی یک LAN مورد‌اعتماد این انتخاب آگاهانه است؛ روی Wi-Fi مشترک، هتل یا هر
شبکه‌ای که خودتان کنترل نمی‌کنید نیست: هر کسی که ترافیک را ببیند هم توکن را دارد و
هم می‌تواند صدای کاربر را بازپخش کند.

دو راه برای `wss://` وجود دارد:

| راه | چه‌کسی TLS را تمام می‌کند | مناسب برای |
|---|---|---|
| **TLS مستقیم uvicorn** | خود سرور، با `PVAI_SSL_CERTFILE` + `PVAI_SSL_KEYFILE` | یک گواهی ساده، بدون نصب نرم‌افزار اضافه |
| **reverse proxy** (Caddy/nginx) | proxy؛ سرور روی `127.0.0.1` بدون TLS | چند سرویس روی یک ماشین، گواهی خودکار (Caddy)، خاتمهٔ TLS متمرکز |

هر دو مسیر با احراز هویت توکن سازگارند و **TLS جای توکن را نمی‌گیرد**؛ هم
`PVAI_API_TOKEN` (REST) و هم توکن per-device (WS) همچنان اجباری‌اند.

## ۱) TLS مستقیم روی uvicorn

```bash
# .env
PVAI_HOST=0.0.0.0
PVAI_API_PORT=8443
PVAI_API_TOKEN=<token>
PVAI_WS_ENABLED=true
PVAI_WS_TOKENS="esp32-a:<token-a>"
PVAI_SSL_CERTFILE=/etc/pvai/fullchain.pem
PVAI_SSL_KEYFILE=/etc/pvai/privkey.pem
```

سپس `tools/start_server.sh` (یا `python -m server`). آدرس‌ها `https://` و `wss://`
می‌شوند: `wss://<host>:8443/ws/voice`.

- هر دو فایل با هم تنظیم می‌شوند؛ یکی‌بدون‌دیگری در `config_errors()` رد می‌شود و
  وجود فایل‌ها هم بررسی می‌شود.
- کلید خصوصی را در git نگذارید (`.gitignore` پوشهٔ `models/` و `data/` را می‌گیرد،
  کلید را جای دیگری نگه دارید).
- `server.voice.local_api_url` وقتی همین دو تنظیم باشند خودش `https://` می‌سازد تا
  کلاینت داخلی هم به همان سرور TLS‌دار وصل شود.

تست خودکار: `tests/test_tls_wss.py::test_uvicorn_serves_https_when_the_certificate_is_configured`
با یک گواهی self-signed واقعی، uvicorn را بالا می‌آورد و `GET /health` را روی
`https://` می‌خواند. یعنی مسیر «گواهی تنظیم شد ⇒ سرور با TLS بالا آمد» **اندازه‌گیری
شده** است (روی Linux sandbox؛ Windows/macOS در این نشست اجرا نشده).

## ۲) Caddy (گواهی خودکار، ساده‌ترین مسیر با دامنه)

سرور را فقط روی loopback بالا بیاورید (بدون `PVAI_SSL_*`) و Caddy را جلوی آن بگذارید:

```caddyfile
voice.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

Caddy به‌طور پیش‌فرض ارتقای WebSocket را انجام می‌دهد و `wss://` روی همان پورت ۴۴۳
کار می‌کند: `wss://voice.example.com/ws/voice`.

اگر گواهی را از قبل دارید و DNS ندارید:

```caddyfile
:8443 {
    tls /etc/pvai/fullchain.pem /etc/pvai/privkey.pem
    reverse_proxy 127.0.0.1:8000
}
```

نکته‌ها:

- `PVAI_HOST=127.0.0.1` بمانید؛ سرور بدون توکن روی `0.0.0.0` بالا نمی‌آید و پشت
  proxy هم فقط loopback لازم است.
- طول عمر اتصال: سرور هر `PVAI_WS_HEARTBEAT_S` پینگ می‌فرستد و پس از
  `PVAI_WS_IDLE_TIMEOUT_S` سکوت، اتصال را می‌بندد. proxy نباید زودتر از این‌ها
  اتصال را قطع کند (پیش‌فرض Caddy برای WebSocket بی‌نهایت است، اما اگر
  `timeouts` را تغییر داده‌اید، بزرگ‌تر از idle timeout بگذارید).
- buffer کردن پاسخ‌ها را خاموش نگذارید؟ برای WS لازم نیست؛ Caddy در حالت upgrade
  بافر نمی‌کند. فقط مطمئن شوید هیچ `encode`/`gzip` اضافه‌ای روی `/ws/voice` نیست
  (فشرده‌سازی روی فریم‌های صوتی ۲۰ms تأخیر اضافه می‌کند).

## ۳) nginx

```nginx
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}

server {
    listen 443 ssl;
    server_name voice.example.com;

    ssl_certificate     /etc/pvai/fullchain.pem;
    ssl_certificate_key /etc/pvai/privkey.pem;

    # صدا ۲۰ms/بلوک است؛ بافر کردن یا تأخیر اضافه، مکالمه را می‌شکند.
    location /ws/voice {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_buffering off;
        proxy_read_timeout 3600s;   # باید > PVAI_WS_IDLE_TIMEOUT_S باشد
        proxy_send_timeout 3600s;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
    }
}
```

`Upgrade` و `Connection` **الزامی** هستند؛ بدون آن‌ها nginx درخواست را به‌عنوان HTTP
معمولی رد می‌کند و دستگاه فقط `400`/قطع اتصال می‌بیند.

## ۴) سمت دستگاه

- آدرس: `wss://<host>/ws/voice` (یا `wss://<host>:8443/ws/voice` در TLS مستقیم).
- اولین قاب `hello` با `token` دستگاه و `device_id`؛ توکن به همان دستگاه قفل است.
- گواهی self-signed: CA را روی firmware نصب کنید (`esp_crt_bundle` یا CA اختصاصی).
  **اعتبارسنجی گواهی را خاموش نکنید** — آن‌ها همان محافظتی است که wss را معنا می‌کند.
- اگر پشت proxy هستید، `X-Forwarded-For` را برای rate limit و لاگ نگه دارید؛
  محدودیت نرخ بر اساس آدرس کلاینت است.

## ۵) عیب‌یابی

| نشانه | علت محتمل |
|---|---|
| `426`/`400` هنگام upgrade | `Upgrade`/`Connection` در nginx تنظیم نشده |
| اتصال هر ۶۰ ثانیه قطع می‌شود | `proxy_read_timeout` کمتر از `PVAI_WS_IDLE_TIMEOUT_S` |
| `ssl_certificate` خطا در startup | `PVAI_SSL_CERTFILE` بدون `PVAI_SSL_KEYFILE` یا فایل ناموجود |
| صدا پخش می‌شود ولی متن نه | WS وصل است ولی `PVAI_WS_TOKENS` توکن دستگاه را ندارد |
| `session_in_use` بعد از قطع | برگردید به `tests/test_session_lease_release.py`؛ قفل نشست باید فوری آزاد شود |

## چه چیزی اندازه‌گیری نشده است

- اجرای واقعی Caddy/nginx و رفتارشان زیر بار: **NOT MEASURED** (در sandbox نصب
  نیستند؛ فقط پیکربندی استاندارد بالا مستند شده است).
- handshake TLS از ESP32-S3 با CA نصب‌شده: **NOT MEASURED** (firmware وجود ندارد).
- تأخیر افزودهٔ proxy روی مکالمهٔ واقعی: **NOT MEASURED**.
- اجرای `tests/test_tls_wss.py` روی Windows/macOS: **NOT MEASURED**؛ فقط Linux.
