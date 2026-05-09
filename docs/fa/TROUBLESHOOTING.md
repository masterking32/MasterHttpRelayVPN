# رفع مشکل

از نشانه‌ای شروع کنید که می‌بینید. بیشتر مشکل‌ها از تنظیمات، اعتماد گواهی، یا deployment قدیمی Apps Script می‌آیند.

## خطاهای Certificate

نشانه‌ها:

- مرورگر می‌گوید اتصال امن نیست.
- بعضی برنامه‌ها کار می‌کنند اما سایت‌های HTTPS در مرورگر باز نمی‌شوند.
- بعد از نصب گواهی هنوز Chrome یا Edge خطا می‌دهد.

راه‌حل:

- یک بار پراکسی را اجرا کنید تا فایل `ca/ca.crt` ساخته شود.
- فایل `ca/ca.crt` را به عنوان trusted root certificate نصب کنید.
- مرورگر را کامل ببندید و دوباره باز کنید. در Windows، Task Manager را هم چک کنید.
- در Firefox گواهی را جداگانه از مسیر **Settings** -> **Privacy & Security** -> **Certificates** وارد کنید.

می‌توانید این دستور را هم اجرا کنید:

```bash
python main.py --install-cert
```

## `unauthorized`

رمز مشترک یکی نیست.

راه‌حل:

- فایل [apps_script/Code.gs](../../apps_script/Code.gs) را باز کنید.
- مقدار `const AUTH_KEY = "...";` را پیدا کنید.
- مطمئن شوید دقیقا با `auth_key` در `config.json` یکی است.
- بعد از تغییر [apps_script/Code.gs](../../apps_script/Code.gs)، یک deployment جدید بسازید.

## `Config not found`

setup wizard را اجرا کنید:

```bash
python setup.py
```

یا فایل [config.example.json](../../config.example.json) را به `config.json` کپی کنید و مقدارهای `script_id` و `auth_key` را پر کنید.

## `502 Bad JSON`

Google به جای JSON رله، HTML یا پاسخ غیرمنتظره برگردانده است.

علت‌های رایج:

- `Deployment ID` اشتباه است.
- quota روزانه Apps Script تمام شده است.
- `Code.gs` را تغییر داده‌اید اما deployment جدید نساخته‌اید.
- دسترسی Web App روی **Anyone** نیست.

راه‌حل:

- یک deployment جدید Apps Script بسازید.
- `Deployment ID` جدید را داخل `config.json` بگذارید.
- مطمئن شوید Web App با **Execute as: Me** و **Who has access: Anyone** deploy شده است.
- اگر quota تمام شده، صبر کنید تا reset شود یا چند deployment دیگر با `script_ids` اضافه کنید.

## صفحه به شکل کاراکترهای عجیب باز می‌شود

نشانه‌ها:

- صفحه با متن‌هایی مثل `�` و علامت‌های تصادفی باز می‌شود.
- مشکل فقط برای بعضی کاربران یا بعضی سایت‌ها دیده می‌شود.
- HTML، JavaScript، یا JSON شبیه خروجی باینری نمایش داده می‌شود.

علت احتمالی:

سایت مقصد پاسخ فشرده فرستاده، اما مرورگر آن را بدون header درست `Content-Encoding` دریافت کرده است. این معمولا وقتی رخ می‌دهد که deployment قدیمی Apps Script یا یک Exit Node هنوز `Accept-Encoding` را به سایت مقصد پاس می‌دهد.

راه‌حل:

- پروژه را به‌روز کنید و وابستگی‌ها را دوباره با `pip install -r requirements.txt` نصب کنید.
- فایل [apps_script/Code.gs](../../apps_script/Code.gs) را دوباره به عنوان deployment جدید Apps Script منتشر کنید.
- اگر `Deployment ID` عوض شد، آن را در `config.json` جایگزین کنید.
- اگر از Deno Exit Node استفاده می‌کنید، [apps_script/deno_deploy.ts](../../apps_script/deno_deploy.ts) را دوباره deploy کنید.
- پراکسی و مرورگر را کامل restart کنید.

## Timeout اتصال

ممکن است `google_ip` فعلی روی شبکه شما کند یا مسدود باشد.

اجرا کنید:

```bash
python main.py --scan
```

سپس IP پیشنهادی را در `config.json` بگذارید و پراکسی را restart کنید.

## مرورگر روی پراکسی است اما سایت‌ها باز نمی‌شوند

چک کنید:

- terminal نشان می‌دهد HTTP proxy روی `127.0.0.1:8085` فعال است.
- نوع پراکسی مرورگر **HTTP** است، نه HTTPS.
- ترافیک HTTPS هم از همان HTTP proxy عبور می‌کند.
- گواهی نصب شده و مرورگر کامل restart شده است.
