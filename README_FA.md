# MasterHttpRelayVPN

**زبان:** [English](README.md) | فارسی

**کانال تلگرام 📣:** [https://t.me/masterdnsvpn](https://t.me/masterdnsvpn)

**تشکر ویژه ❤️:** [Abolix](https://github.com/abolix)

MasterHttpRelayVPN یک پراکسی محلی است که ترافیک مرورگر را از مسیر Google Apps Script و Domain Fronting عبور می‌دهد. برای مسیر ساده فقط همین پروژه و یک اکانت رایگان Google کافی است. اگر بعضی سایت‌ها خروجی Google را مسدود کنند، بعدا می‌توانید Exit Node اضافه کنید.

```text
مرورگر -> پراکسی محلی -> مسیر Google -> رله Apps Script شما -> سایت مقصد
                         فیلتر فقط اتصال شبیه Google را می‌بیند
```

## منوی سریع 🧭

[شروع سریع](docs/fa/GETTING_STARTED.md) | [Docker](docs/fa/DOCKER.md) | [اشتراک گذاری LAN](docs/fa/LAN_SHARING.md) | [راهنمای Exit Node](docs/exit-node/EXIT_NODE_DEPLOYMENT_FA.md)

[مرجع تنظیمات](docs/fa/CONFIGURATION.md) | [رفع مشکل](docs/fa/TROUBLESHOOTING.md) | [نکات امنیتی](docs/fa/SECURITY.md) | [معماری](docs/fa/ARCHITECTURE.md)

## شروع خیلی سریع ⚡

قبل از اجرای پراکسی، باید یک بار رله Google را deploy کنید. فقط یک اکانت Google لازم دارید و این کار حدود دو دقیقه زمان می‌برد.

## ساخت رله Google ☁️

- وارد [Google Apps Script](https://script.google.com/) شوید و روی **New project** کلیک کنید.
- محتوای پیش‌فرض ادیتور را کامل پاک کنید.
- فایل [apps_script/Code.gs](apps_script/Code.gs) را باز کنید، همه کد آن را کپی کنید، و داخل Apps Script قرار دهید.
- این خط را پیدا کنید و با یک رمز طولانی و مخصوص خودتان عوض کنید:

    ```javascript
    const AUTH_KEY = "your-secret-password-here";
    ```

- از مسیر **Deploy** -> **New deployment** -> **Web app** بروید.
- گزینه **Execute as** را روی **Me** بگذارید.
- گزینه **Who has access** را روی **Anyone** بگذارید.
- روی **Deploy** کلیک کنید، دسترسی‌ها را تایید کنید، و **Deployment ID** را کپی کنید.

این دو مقدار را برای setup wizard نگه دارید:

- `Deployment ID` از Google Apps Script
- `AUTH_KEY`، یک رمز طولانی که باید دقیقا با `auth_key` در کانفیگ محلی یکی باشد

اگر توضیح کامل‌تر می‌خواهید، [شروع سریع](docs/fa/GETTING_STARTED.md#2-ساخت-رله-google) را ببینید.

پروژه را با Git یا ZIP دریافت کنید، سپس لانچر یک‌کلیکی را اجرا کنید.

**گزینه A: Git**

```bash
git clone https://github.com/masterking32/MasterHttpRelayVPN.git
cd MasterHttpRelayVPN
```

**گزینه B: ZIP**

- [صفحه GitHub پروژه](https://github.com/masterking32/MasterHttpRelayVPN) را باز کنید.
- روی **Code** -> **Download ZIP** کلیک کنید.
- فایل ZIP را extract کنید.
- داخل پوشه extract شده `MasterHttpRelayVPN` یک terminal باز کنید.

بعد برنامه را اجرا کنید:

**Windows**

```cmd
start.bat
```

**Linux / macOS**

```bash
chmod +x start.sh
./start.sh
```

لانچر virtualenv می‌سازد، وابستگی‌ها را نصب می‌کند، اگر `config.json` وجود نداشته باشد setup wizard را باز می‌کند، و سپس پراکسی را اجرا می‌کند.

بعد از اجرا، مرورگر را روی این پراکسی تنظیم کنید:

| گزینه | مقدار |
|-------|-------|
| نوع پراکسی | HTTP |
| آدرس | `127.0.0.1` |
| پورت | `8085` |
| پورت SOCKS5، اختیاری | `1080` |

برای سایت‌های HTTPS، اگر برنامه نتوانست گواهی را خودکار نصب کند، فایل `ca/ca.crt` را نصب کنید. راهنمای کامل در [شروع سریع](docs/fa/GETTING_STARTED.md) است.

## قدم‌های بعدی رایج 🛠️

- اگر مرورگر خطای certificate نشان می‌دهد، [بخش خطای گواهی](docs/fa/TROUBLESHOOTING.md#خطاهای-certificate) را ببینید.
- اگر خطای `unauthorized` می‌بینید، مقدار `AUTH_KEY` در [apps_script/Code.gs](apps_script/Code.gs) باید دقیقا با `auth_key` در `config.json` یکی باشد.
- اگر سرعت پایین است یا timeout می‌گیرید، دستور `python main.py --scan` را اجرا کنید و [مرجع تنظیمات](docs/fa/CONFIGURATION.md#دستورهای-عیب‌یابی) را ببینید.
- اگر سایت‌هایی مثل ChatGPT یا Turnstile با خروجی Google مشکل دارند، [راهنمای Exit Node](docs/exit-node/EXIT_NODE_DEPLOYMENT_FA.md) را بخوانید.

## پشتیبانی و اطلاع‌رسانی 📣

- کانال Telegram: [https://t.me/masterdnsvpn](https://t.me/masterdnsvpn)
- کلاینت Windows: [MHRWindowsApp](https://github.com/AriPath/MHRWindowsApp)
- منبع فیلتر تبلیغات: [PersianBlocker](https://github.com/MasterKia/PersianBlocker/)

## امنیت 🔒

این پروژه برای آموزش، تست و پژوهش ارائه شده است. مسئولیت رعایت قوانین و شرایط سرویس‌ها با کاربر است. فایل `config.json`، مقدار `auth_key`، پوشه `ca/`، و آدرس Exit Node همراه با PSK معتبر را با کسی به اشتراک نگذارید. قبل از فعال کردن استفاده در شبکه محلی، [نکات امنیتی](docs/fa/SECURITY.md) را بخوانید.

## License

MIT
