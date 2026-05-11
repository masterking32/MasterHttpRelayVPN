# مرجع تنظیمات

بیشتر کاربران فقط به `script_id`، `auth_key`، و پورت‌های پیش‌فرض نیاز دارند. این صفحه برای زمانی است که می‌خواهید رفتار برنامه را دقیق‌تر تنظیم کنید.

## تنظیمات ضروری

| تنظیم | معنی |
|-------|------|
| `script_id` | Deployment ID مربوط به Google Apps Script. برای یک deployment استفاده می‌شود. |
| `script_ids` | آرایه‌ای از چند Deployment ID برای load balancing. به جای `script_id` استفاده می‌شود. |
| `auth_key` | رمز مشترک. باید دقیقا با `AUTH_KEY` داخل [apps_script/Code.gs](../../apps_script/Code.gs) یکی باشد. |

اگر از `script_ids` استفاده می‌کنید، همه deployment ها باید `AUTH_KEY` یکسان داشته باشند.

## اتصال پراکسی

| تنظیم | پیش‌فرض | معنی |
|-------|---------|------|
| `listen_host` | `127.0.0.1` | آدرسی که پراکسی روی آن گوش می‌دهد. برای فقط همین کامپیوتر، همین مقدار را نگه دارید. |
| `http_port` | `8085` | پورت HTTP proxy برای مرورگرها و بیشتر برنامه‌ها. |
| `socks5_port` | `1080` | پورت SOCKS5. بعضی برنامه‌ها hostname را محلی resolve می‌کنند، پس HTTP proxy معمولا قابل اعتمادتر است. |
| `lan_sharing` | `false` | وقتی true باشد، دستگاه‌های دیگر شبکه محلی هم می‌توانند از پراکسی استفاده کنند. |

قبل از فعال کردن، [اشتراک‌گذاری LAN](LAN_SHARING.md) را بخوانید.

## Domain Fronting

| تنظیم | پیش‌فرض | معنی |
|-------|---------|------|
| `google_ip` | `216.239.38.120` | IP فرانت Google که اتصال از آن مسیر می‌رود. |
| `front_domain` | `www.google.com` | دامنه‌ای که در اتصال TLS سمت Google دیده می‌شود. |
| `front_domains` | `www.google.com`, `mail.google.com`, `accounts.google.com` | لیست اختیاری برای چرخش SNI. |
| `verify_ssl` | `true` | اعتبار TLS اتصال سمت Google را بررسی می‌کند. در حالت عادی true بماند. |

اگر IP فعلی کند یا مسدود است، `python main.py --scan` را اجرا کنید و IP پیشنهادی را بگذارید.

## Timeout و کارایی

| تنظیم | پیش‌فرض | معنی |
|-------|---------|------|
| `relay_timeout` | `25` | حداکثر زمان برای یک درخواست relay. |
| `tls_connect_timeout` | `15` | timeout ساخت اتصال TLS به endpoint گوگل. |
| `tcp_connect_timeout` | `10` | timeout اتصال‌های TCP مستقیم و SNI-rewrite. |
| `h2_connections` | `2` | تعداد اتصال‌های HTTP/2 به relay. افزایش آن گاهی throughput را بهتر می‌کند. |
| `parallel_relay` | `1` | تعداد deployment هایی که برای درخواست‌های امن با هم race می‌شوند. |
| `enable_sub_batch` | `true` | اجازه می‌دهد batch ها بین اتصال‌های H2 تقسیم شوند. |

## دانلودها

| تنظیم | معنی |
|-------|------|
| `chunked_download_extensions` | پسوندهایی که می‌توانند از دانلود parallel range استفاده کنند. `".*"` همه دانلودهای GET را probe می‌کند. |
| `chunked_download_min_size` | حداقل اندازه فایل برای فعال ماندن دانلود موازی. |
| `chunked_download_chunk_size` | اندازه هر range request. |
| `chunked_download_max_parallel` | بیشترین تعداد range request همزمان برای یک دانلود. |
| `chunked_download_max_chunks` | سقف نرم تعداد chunk ها. برای فایل‌های بزرگ، اندازه chunk خودکار افزایش می‌یابد. |

## سیاست دامنه‌ها

| تنظیم | معنی |
|-------|------|
| `block_hosts` | دامنه‌هایی که باید 403 بگیرند و هرگز tunnel نشوند. از نام دقیق و الگوی `.suffix` پشتیبانی می‌کند. |
| `direct_hosts` | دامنه‌هایی که همیشه مستقیم می‌روند، بدون MITM و بدون relay. |
| `bypass_hosts` | دامنه‌های محلی یا خاص که از MITM و relay عبور نمی‌کنند. برای `.lan` و `.local` مفید است. |
| `hosts` | نگاشت دستی DNS برای تست یا split-DNS. |
| `direct_google_exclude` | سرویس‌های Google که به جای tunnel مستقیم باید از relay استفاده کنند. |
| `youtube_via_relay` | YouTube را از مسیر Apps Script relay عبور می‌دهد. اگر مسیر مستقیم Google باعث مشکل پخش شود مفید است. |

نمونه:

```json
{
  "block_hosts": ["ads.example.com", ".doubleclick.net"],
  "direct_hosts": ["chat.openai.com", ".openai.com"],
  "hosts": {
    "example.org": "93.184.216.34",
    ".internal.lan": "192.168.1.10"
  }
}
```

## Exit Node

وقتی مقصد، خروجی IPهای Google را مسدود می‌کند، Exit Node کمک می‌کند.

```json
"exit_node": {
  "enabled": true,
  "provider": "cloudflare",
  "url": "https://YOUR-WORKER.YOUR-SUBDOMAIN.workers.dev",
  "psk": "CHANGE_ME_TO_A_STRONG_SECRET",
  "mode": "full",
  "hosts": ["chatgpt.com", "openai.com"]
}
```

| تنظیم | معنی |
|-------|------|
| `exit_node.enabled` | فعال یا غیرفعال کردن مسیر Exit Node. |
| `exit_node.provider` | یکی از `cloudflare`، `deno`، `vps`، یا `custom`. |
| `exit_node.url` | آدرس provider انتخاب‌شده. |
| `exit_node.psk` | رمز مشترک Exit Node. باید با کد deploy شده یکی باشد. |
| `exit_node.mode` | مقدار `full` برای همه ترافیک relay شده، یا `selective` فقط برای host های لیست‌شده. |
| `exit_node.hosts` | لیست host ها در حالت selective. |

مراحل deploy در [راهنمای Exit Node](../exit-node/EXIT_NODE_DEPLOYMENT_FA.md) است.

## Adblock

`adblock_lists` لیست URLهای فیلتر host/domain را می‌گیرد. کانفیگ پیش‌فرض از PersianBlocker استفاده می‌کند. اگر این رفتار را نمی‌خواهید، لیست را خالی کنید.

## وابستگی‌های اختیاری

برای همه امکانات، dependency های [requirements.txt](../../requirements.txt) را نصب کنید.

| بسته | کاربرد |
|------|--------|
| `cryptography` | ساخت certificate محلی و MITM برای HTTPS. |
| `h2` | ارتباط HTTP/2 با Apps Script. |
| `brotli` | decode کردن `Content-Encoding: br`. |
| `zstandard` | decode کردن `Content-Encoding: zstd`. |

## دستورهای اجرا

```bash
python main.py                          # اجرای عادی
python main.py -p 9090                  # تغییر پورت HTTP
python main.py --socks5-port 1081       # تغییر پورت SOCKS5
python main.py --host 0.0.0.0           # تغییر listen host
python main.py --log-level DEBUG        # لاگ بیشتر
python main.py -c path/to/config.json   # استفاده از config دیگر
python main.py --install-cert           # نصب CA و خروج
python main.py --uninstall-cert         # حذف CA و خروج
python main.py --no-cert-check          # رد شدن از بررسی خودکار CA
python main.py --scan                   # پیدا کردن IP سریع‌تر Google
```

متغیرهای محیطی پشتیبانی‌شده: `DFT_CONFIG`, `DFT_AUTH_KEY`, `DFT_SCRIPT_ID`, `DFT_HTTP_PORT`, `DFT_PORT`, `DFT_HOST`, `DFT_SOCKS5_PORT`, و `DFT_LOG_LEVEL`.

## دستورهای عیب‌یابی

اسکن IPهای Google:

```bash
python main.py --scan
```

نصب یا حذف CA محلی:

```bash
python main.py --install-cert
python main.py --uninstall-cert
```

لاگ کامل‌تر:

```bash
python main.py --log-level DEBUG
```
