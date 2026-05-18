# شروع سریع (راهنمای کامل)

این صفحه یک **راهنمای کامل و ساده** برای شروع است.
اگر مرحله‌به‌مرحله جلو بروید، در پایان پراکسی شما کاملا کار خواهد کرد.

---

## نتیجه نهایی

اگر همه چیز درست انجام شود:
- پراکسی HTTP روی `127.0.0.1:8085` فعال می‌شود
- پراکسی SOCKS5 روی `127.0.0.1:1080` فعال می‌شود
- سایت‌های HTTPS بدون خطای گواهی باز می‌شوند
- می‌توانید با `--scan` و `--adaptive-scan` کیفیت مسیر را بررسی کنید

---

## پیش‌نیازها

- Python نسخه `3.10+`
- یک اکانت Google (برای Apps Script)
- Git (اختیاری؛ دانلود ZIP هم کافی است)
- مرورگری که تنظیم پراکسی دستی داشته باشد

---

## 1) دریافت پروژه

### روش A — ZIP

دانلود و extract:
- <https://github.com/masterking32/MasterHttpRelayVPN/archive/refs/heads/python_testing.zip>

بعد داخل پوشه پروژه terminal باز کنید.

### روش B — Git

```bash
git clone https://github.com/masterking32/MasterHttpRelayVPN.git
cd MasterHttpRelayVPN
```

---

## 2) ساخت رله Google Apps Script

1. وارد <https://script.google.com/> شوید.
2. روی **New project** کلیک کنید.
3. کد پیش‌فرض را پاک کنید.
4. فایل [`apps_script/Code.gs`](../../apps_script/Code.gs) را باز کنید، کل محتوا را کپی و در Apps Script paste کنید.
5. این مقدار را تغییر دهید:

   ```javascript
   const AUTH_KEY = "your-secret-password-here";
   ```

   و یک رمز طولانی و تصادفی خودتان بگذارید.

6. مسیر **Deploy** → **New deployment** را بزنید.
7. نوع **Web app** را انتخاب کنید.
8. **Execute as** را روی **Me** بگذارید.
9. **Who has access** را روی **Anyone** بگذارید.
10. Deploy کنید، دسترسی را تایید کنید، و **Deployment ID** را کپی کنید.

دو مقدار مهم برای سیستم محلی:
- `Deployment ID`
- `AUTH_KEY`

---

## 3) اجرای برنامه (روش پیشنهادی)

### Windows

```cmd
start.bat
```

### Linux / macOS

```bash
chmod +x start.sh
./start.sh
```

لانچر کارهای زیر را انجام می‌دهد:
- ساخت `.venv`
- نصب وابستگی‌ها
- اجرای setup wizard اگر `config.json` موجود نباشد
- اجرای پراکسی

---

## 4) تکمیل Setup Wizard

در wizard:
1. `auth_key` دقیقا برابر `AUTH_KEY` در Apps Script باشد
2. `script_id` همان Deployment ID شما باشد
3. پورت HTTP را `8085` بگذارید (مگر اینکه اشغال باشد)
4. LAN sharing را فقط وقتی لازم دارید روشن کنید

در پایان فایل `config.json` ساخته می‌شود.

---

## 5) تنظیم پراکسی در مرورگر

| گزینه | مقدار |
|---|---|
| نوع پراکسی | HTTP |
| آدرس | `127.0.0.1` |
| پورت | `8085` |

در Firefox: Settings → General → Network Settings → Manual proxy
و برای HTTPS هم فعال کنید.

---

## 6) نصب CA محلی (برای HTTPS ضروری)

فایل گواهی در `ca/ca.crt` ساخته می‌شود.
اگر نصب خودکار انجام نشد، دستی نصب کنید.

### Windows
1. فایل `ca/ca.crt` را باز کنید
2. Install Certificate
3. Current User
4. ذخیره در **Trusted Root Certification Authorities**
5. مرورگر را کامل ببندید و دوباره باز کنید

### macOS
1. `ca/ca.crt` را در Keychain Access باز کنید
2. بخش Trust را باز کنید
3. روی **Always Trust** بگذارید
4. مرورگر را ری‌استارت کنید

### Ubuntu / Debian

```bash
sudo cp ca/ca.crt /usr/local/share/ca-certificates/masterhttp-relay.crt
sudo update-ca-certificates
```

مرورگر را ری‌استارت کنید.

### Firefox (در صورت نیاز)
ممکن است Firefox store جداگانه داشته باشد:
- Settings → Privacy & Security → Certificates → View Certificates → Authorities → Import `ca/ca.crt`
- گزینه اعتماد برای شناسایی وب‌سایت را فعال کنید

---

## 7) تست عملکرد

- چند سایت عادی باز کنید.
- اگر `unauthorized` دیدید: `AUTH_KEY` ناهماهنگ است.
- اگر خطای گواهی HTTPS دیدید: CA درست نصب نشده.

---

## 8) دستورهای بررسی کیفیت مسیر

### اسکن سریع دسترسی IP

```bash
python main.py --scan
```

IP پیشنهادی را در `config.json` قرار دهید.

### اسکن تطبیقیِ پایداری‌محور (پیشنهادی برای شبکه ناپایدار)

```bash
python main.py --adaptive-scan
```

این اسکن فقط روی کمترین پینگ تصمیم نمی‌گیرد و پایداری را هم در نظر می‌گیرد.

---

## 9) اجرای دستی (بدون لانچر)

### Windows

```cmd
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python setup.py
.venv\Scripts\python main.py
```

### Linux / macOS

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python setup.py
.venv/bin/python main.py
```

---

## 10) مشکلات رایج (خلاصه)

- `unauthorized`: عدم تطابق auth key
- اتصال پراکسی برقرار است ولی سایت‌ها باز نمی‌شوند: Deployment ID اشتباه یا دسترسی Web app درست نیست
- خطای HTTPS: گواهی CA نصب/Trusted نشده
- برخی سرویس‌ها خروجی Google را می‌بندند: از Exit Node استفاده کنید

---

## صفحه‌های بعدی

- [رفع مشکل](TROUBLESHOOTING.md)
- [مرجع تنظیمات](CONFIGURATION.md)
- [راهنمای Exit Node](../exit-node/EXIT_NODE_DEPLOYMENT_FA.md)
- [معماری](ARCHITECTURE.md)
