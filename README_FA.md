# MasterHttpRelayVPN

**[English README](README.md)**

یک ابزار رایگان برای عبور از فیلترینگ و DPI که ترافیک شما را پشت دامنه‌های قابل اعتماد مثل Google پنهان می‌کند. برای حالت ساده، به VPS یا سرور نیاز ندارید و فقط یک اکانت Google کافی است.

> **توضیح ساده:** مرورگر شما به این ابزار روی کامپیوتر خودتان وصل می‌شود. این ابزار ترافیک را شبیه ترافیک عادی Google نشان می‌دهد. فیلتر فقط `google.com` را می‌بیند و اجازه عبور می‌دهد. در پشت صحنه، یک Google Apps Script رایگان سایت واقعی را برای شما دریافت می‌کند.

---

## نحوه کار

```
مرورگر -> پراکسی محلی -> Google/CDN -> رله شما -> سایت مقصد
           |
           +-> فیلتر فقط google.com را می‌بیند
```

مرورگر، درخواست‌ها را به پراکسی محلی می‌فرستد. پراکسی این درخواست‌ها را از مسیر Google عبور می‌دهد تا برای فیلتر شبیه ترافیک عادی به نظر برسد. سپس رله‌ای که شما deploy کرده‌اید، سایت اصلی را دریافت می‌کند و پاسخ را برمی‌گرداند.

---

## راه‌اندازی مرحله‌به‌مرحله

### مرحله 1: دریافت پروژه

```bash
git clone -b python_testing https://github.com/masterking32/MasterHttpRelayVPN.git
cd MasterHttpRelayVPN
pip install -r requirements.txt
```

اگر نخواستید با Git کار کنید، می‌توانید فایل ZIP پروژه را از GitHub دانلود و extract کنید.

### مرحله 2: راه‌اندازی رله Google با `Code.gs`

این بخش همان رله‌ای است که روی سرورهای Google اجرا می‌شود و سایت‌ها را برای شما دریافت می‌کند.

1. وارد [Google Apps Script](https://script.google.com/) شوید.
2. روی **New project** کلیک کنید.
3. کد پیش‌فرض را کامل حذف کنید.
4. فایل `Code.gs` همین پروژه را باز کنید، همه محتوای آن را کپی کنید و داخل Apps Script قرار دهید.
5. این خط را به یک رمز دلخواه و امن تغییر دهید:
   ```javascript
   const AUTH_KEY = "your-secret-password-here";
   ```
6. روی **Deploy -> New deployment** کلیک کنید.
7. نوع deployment را **Web app** بگذارید.
8. این تنظیمات را انتخاب کنید:
   - **Execute as:** Me
   - **Who has access:** Anyone
9. روی **Deploy** بزنید.
10. مقدار **Deployment ID** را کپی کنید. در مرحله بعد به آن نیاز دارید.

نکته: مقداری که برای `AUTH_KEY` می‌گذارید باید دقیقا با `auth_key` در فایل `config.json` یکی باشد.

### مرحله 3: تنظیم `config.json`

ابتدا فایل نمونه را کپی کنید:

```bash
cp config.example.json config.json
```

در ویندوز می‌توانید فایل را دستی کپی و rename کنید.

سپس `config.json` را باز کنید و مقادیر را وارد کنید:

```json
{
  "mode": "apps_script",
  "google_ip": "216.239.38.120",
  "front_domain": "www.google.com",
  "script_id": "PASTE_YOUR_DEPLOYMENT_ID_HERE",
  "auth_key": "your-secret-password-here",
  "listen_host": "127.0.0.1",
  "listen_port": 8085,
  "log_level": "INFO",
  "verify_ssl": true
}
```

- `script_id` : همان Deployment ID مرحله 2
- `auth_key` : همان رمزی که در `Code.gs` گذاشته‌اید

### مرحله 4: اجرا

```bash
python main.py
```

اگر همه‌چیز درست باشد، پراکسی روی `127.0.0.1:8085` بالا می‌آید.

### مرحله 5: تنظیم مرورگر

مرورگر را روی این پراکسی تنظیم کنید:

- **Proxy Address:** `127.0.0.1`
- **Proxy Port:** `8085`
- **Type:** HTTP

نمونه تنظیم مرورگرها:

- **Firefox:** Settings -> General -> Network Settings -> Manual proxy
- **Chrome / Edge:** از تنظیمات پراکسی سیستم استفاده می‌کنند
- یا از افزونه‌هایی مثل FoxyProxy استفاده کنید

### مرحله 6: نصب گواهی CA برای HTTPS

در حالت `apps_script`، برنامه برای مدیریت HTTPS یک گواهی محلی می‌سازد. اگر آن را نصب نکنید، مرورگر برای سایت‌ها خطای امنیتی می‌دهد.

فایل گواهی بعد از اولین اجرا در این مسیر ساخته می‌شود:

`ca/ca.crt`

#### ویندوز
1. روی `ca/ca.crt` دوبار کلیک کنید.
2. گزینه **Install Certificate** را بزنید.
3. گزینه **Current User** را انتخاب کنید.
4. گزینه **Place all certificates in the following store** را بزنید.
5. از بخش **Browse**، گزینه **Trusted Root Certification Authorities** را انتخاب کنید.
6. مراحل را تا پایان ادامه دهید.
7. مرورگر را یک بار ببندید و دوباره باز کنید.

#### Firefox
Firefox معمولا certificate store جداگانه دارد:

1. به **Settings -> Privacy & Security -> Certificates** بروید.
2. روی **View Certificates** کلیک کنید.
3. در تب **Authorities**، روی **Import** بزنید.
4. فایل `ca/ca.crt` را انتخاب کنید.
5. گزینه **Trust this CA to identify websites** را فعال کنید.

نکته امنیتی: پوشه `ca/` را با کسی به اشتراک نگذارید. اگر خواستید از اول گواهی جدید بسازید، این پوشه را حذف کنید تا دوباره ساخته شود.

---

## حالت‌های موجود

| حالت | نیازمندی | توضیح |
|------|----------|-------|
| `apps_script` | اکانت رایگان Google | ساده‌ترین حالت، بدون نیاز به سرور |
| `google_fronting` | Google Cloud Run | استفاده از سرویس Cloud Run خودتان |
| `domain_fronting` | Cloudflare Worker | استفاده از Worker روی Cloudflare |
| `custom_domain` | دامنه شخصی روی Cloudflare | اتصال مستقیم به دامنه خودتان |

برای اکثر کاربران، `apps_script` بهترین انتخاب است.

---

## تنظیمات مهم

| تنظیم | توضیح |
|------|-------|
| `mode` | نوع رله |
| `auth_key` | رمز مشترک بین برنامه و رله |
| `script_id` | Deployment ID مربوط به Apps Script |
| `listen_host` | آدرس محلی برای اجرا |
| `listen_port` | پورت پراکسی |
| `log_level` | میزان جزئیات لاگ |

### تنظیمات پیشرفته

| تنظیم | مقدار پیش‌فرض | توضیح |
|------|---------------|-------|
| `google_ip` | `216.239.38.120` | IP مورد استفاده برای مسیر Google |
| `front_domain` | `www.google.com` | دامنه‌ای که فیلتر می‌بیند |
| `verify_ssl` | `true` | بررسی اعتبار TLS |
| `worker_host` | - | برای حالت‌های Cloudflare/Cloud Run |
| `custom_domain` | - | دامنه شخصی شما |
| `script_ids` | - | چند Deployment ID برای load balancing |

### استفاده از چند Script ID

اگر چند نسخه از `Code.gs` را deploy کنید، می‌توانید همه Deployment ID ها را در آرایه `script_ids` بگذارید:

```json
{
  "script_ids": [
    "DEPLOYMENT_ID_1",
    "DEPLOYMENT_ID_2",
    "DEPLOYMENT_ID_3"
  ]
}
```

---

## به‌روزرسانی `Code.gs`

اگر فایل `Code.gs` را تغییر دادید، باید دوباره **Deploy -> New deployment** بزنید و `script_id` جدید را داخل `config.json` قرار دهید. صرفا ذخیره کردن کد، نسخه فعال را عوض نمی‌کند.

---

## دستورهای اجرا

```bash
python main.py
python main.py -p 9090
python main.py --log-level DEBUG
python main.py -c /path/to/config.json
```

---

## معماری

```
┌─────────┐     ┌──────────────┐     ┌─────────────┐     ┌──────────┐
│ Browser │────►│ Local Proxy  │────►│ CDN / Google │────►│  Relay   │──► Internet
│         │◄────│ (this tool)  │◄────│  (fronted)   │◄────│ Endpoint │◄──
└─────────┘     └──────────────┘     └─────────────┘     └──────────┘
```

---

## فایل‌های پروژه

| فایل | کاربرد |
|------|--------|
| `main.py` | اجرای برنامه |
| `proxy_server.py` | مدیریت اتصال مرورگر |
| `domain_fronter.py` | انجام domain fronting |
| `h2_transport.py` | ارتباط سریع‌تر با HTTP/2 |
| `mitm.py` | ساخت و مدیریت certificate |
| `ws.py` | پشتیبانی WebSocket |
| `Code.gs` | رله Apps Script |
| `config.example.json` | فایل نمونه تنظیمات |

---

## رفع مشکل

| مشکل | راه‌حل |
|------|--------|
| `Config not found` | فایل `config.example.json` را به `config.json` کپی کنید |
| خطای certificate در مرورگر | گواهی CA را نصب کنید |
| خطای `unauthorized` | مقدار `auth_key` و `AUTH_KEY` باید یکسان باشند |
| timeout | IP دیگری برای Google امتحان کنید |
| سرعت کم | از چند `script_id` برای load balancing استفاده کنید |

---

## نکات امنیتی

- فایل `config.json` را با کسی به اشتراک نگذارید.
- مقدار پیش‌فرض `AUTH_KEY` را قبل از deploy عوض کنید.
- پوشه `ca/` را منتشر نکنید.
- بهتر است `listen_host` روی `127.0.0.1` بماند.

---

## License

MIT
