# شروع سریع

این راهنما مسیر ساده راه‌اندازی را نشان می‌دهد: یک رله Google Apps Script، یک فایل `config.json`، و پراکسی محلی روی سیستم شما.

## 1. دریافت پروژه

**با Git:**

```bash
git clone https://github.com/masterking32/MasterHttpRelayVPN.git
cd MasterHttpRelayVPN
```

**با ZIP:**

- صفحه [GitHub پروژه](https://github.com/masterking32/MasterHttpRelayVPN) را باز کنید.
- روی **Code** -> **Download ZIP** کلیک کنید.
- فایل ZIP را extract کنید.
- داخل پوشه `MasterHttpRelayVPN` یک terminal باز کنید.

## 2. ساخت رله Google

- به [Google Apps Script](https://script.google.com/) بروید و یک پروژه جدید بسازید.
- محتوای [apps_script/Code.gs](../../apps_script/Code.gs) را داخل فایل `Code.gs` کپی کنید.
- مقدار `AUTH_KEY` را به یک رمز طولانی و تصادفی تغییر دهید.
- از مسیر **Deploy** -> **New deployment** نوع **Web app** را انتخاب کنید.
- گزینه **Execute as** را روی **Me** و گزینه دسترسی را روی **Anyone** بگذارید.
- Deploy کنید و `Deployment ID` را نگه دارید.

بعد از هر تغییر در `Code.gs` باید deployment جدید بسازید.

## 3. اجرای لانچر

**Windows:**

```cmd
start.bat
```

**Linux / macOS:**

```bash
chmod +x start.sh
./start.sh
```

لانچر محیط مجازی می‌سازد، وابستگی‌ها را نصب می‌کند، اگر `config.json` وجود نداشته باشد setup wizard را اجرا می‌کند، و سپس پراکسی را بالا می‌آورد.

## 4. تنظیم مرورگر

مرورگر را روی پراکسی زیر تنظیم کنید:

| گزینه | مقدار |
|-------|-------|
| نوع پراکسی | HTTP |
| آدرس | `127.0.0.1` |
| پورت | `8085` |
| SOCKS5، اختیاری | `127.0.0.1:1080` |

برای HTTPS اگر مرورگر خطای گواهی داد، فایل `ca/ca.crt` را به عنوان trusted root نصب کنید و مرورگر را کامل ببندید و دوباره باز کنید.

## 5. بررسی سریع

- اگر `unauthorized` دیدید، مقدار `AUTH_KEY` در Apps Script باید دقیقا با `auth_key` در `config.json` یکی باشد.
- اگر صفحه‌ها باز نمی‌شوند، [رفع مشکل](TROUBLESHOOTING.md) را ببینید.
- اگر سرعت پایین است، دستور `python main.py --scan` را اجرا کنید و IP پیشنهادی را در `config.json` بگذارید.

## قدم بعدی

برای همه گزینه‌های تنظیمات، [مرجع تنظیمات](CONFIGURATION.md) را بخوانید. برای مسیرهای خاص مثل ChatGPT یا Turnstile، [راهنمای Exit Node](../exit-node/EXIT_NODE_DEPLOYMENT_FA.md) را ببینید.
