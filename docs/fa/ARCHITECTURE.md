# معماری

MasterHttpRelayVPN از یک پراکسی محلی و یک relay که کاربر deploy می‌کند ساخته شده است.

## مسیر ساده

```text
Browser یا app
  -> HTTP/SOCKS5 proxy محلی
  -> اتصال TLS fronted به سمت Google
  -> Apps Script relay
  -> سایت مقصد
```

شبکه یک اتصال شبیه Google می‌بیند. URL واقعی مقصد داخل ترافیک رمزگذاری‌شده به relay فرستاده می‌شود.

## بخش‌های اصلی

| فایل یا پوشه | کاربرد |
|--------------|--------|
| [main.py](../../main.py) | نقطه ورود CLI. config را می‌خواند، دستورهای certificate را اجرا می‌کند، و proxy را شروع می‌کند. |
| [setup.py](../../setup.py) | wizard تعاملی که `config.json` می‌سازد. |
| [start.bat](../../start.bat) | لانچر Windows. venv می‌سازد، dependency نصب می‌کند، setup را اجرا می‌کند، و proxy را بالا می‌آورد. |
| [start.sh](../../start.sh) | لانچر Linux/macOS با همین نقش. |
| [config.example.json](../../config.example.json) | نمونه کانفیگ و پیش‌فرض‌ها. |
| [apps_script/Code.gs](../../apps_script/Code.gs) | رله Google Apps Script که کاربر deploy می‌کند. |
| [src/proxy/proxy_server.py](../../src/proxy/proxy_server.py) | HTTP CONNECT، مسیرهای MITM، SOCKS5، و تصمیم‌های مربوط به host policy. |
| [src/proxy/mitm.py](../../src/proxy/mitm.py) | CA محلی و certificate های ساخته‌شده برای سایت‌ها. |
| [src/relay/domain_fronter.py](../../src/relay/domain_fronter.py) | کلاینت Apps Script relay، batch، retry، و انتخاب transport H1/H2. |
| [src/relay/h2_transport.py](../../src/relay/h2_transport.py) | transport اختیاری HTTP/2 برای multiplexing. |
| [src/core/cert_installer.py](../../src/core/cert_installer.py) | نصب و حذف CA برای سیستم‌عامل و Firefox. |
| [src/core/google_ip_scanner.py](../../src/core/google_ip_scanner.py) | اسکنر IPهای Google برای `python main.py --scan`. |

## پردازش درخواست

- مرورگر ترافیک HTTP یا HTTPS proxy را به `127.0.0.1:8085` می‌فرستد.
- برای HTTPS، proxy می‌تواند با CA تولیدشده MITM محلی انجام دهد.
- قوانین host مشخص می‌کنند درخواست مستقیم، blocked، bypass، یا relayed باشد.
- درخواست‌های relayed به JSON برای Apps Script تبدیل می‌شوند.
- Apps Script مقصد را fetch می‌کند و پاسخ HTTP سریال‌شده برمی‌گرداند.
- پراکسی محلی پاسخ HTTP را برای مرورگر بازسازی می‌کند.

## امکانات کارایی

- pool گرم اتصال TLS برای fallback H1.
- HTTP/2 multiplexing وقتی package `h2` نصب باشد.
- batch کردن درخواست‌های static در burst ها.
- چند `script_ids` اختیاری برای load balancing.
- دانلود موازی range برای فایل‌های بزرگ.
- Exit Node اختیاری برای مقصدهایی که خروجی Google را مسدود می‌کنند.

## مسیر Exit Node

```text
Browser -> Local proxy -> Apps Script -> Exit node -> Target website
```

Exit Node می‌تواند روی Cloudflare Workers، Deno Deploy، یا VPS اجرا شود. [راهنمای Exit Node](../exit-node/EXIT_NODE_DEPLOYMENT_FA.md) را ببینید.
