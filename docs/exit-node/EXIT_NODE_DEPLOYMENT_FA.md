# راهنمای نصب نود خروجی (Cloudflare / Deno / VPS)

این راهنما توضیح می‌دهد چطور یک نود خروجی رایگان برای MasterHttpRelayVPN راه‌اندازی کنید.

مسیر ترافیک:

```
مرورگر -> پراکسی محلی -> Apps Script -> نود خروجی -> سایت مقصد
```

از این قابلیت زمانی استفاده کنید که سایت‌های مقصد آی‌پی‌های دیتاسنتر Google را مسدود می‌کنند.

## ۱) یک Provider انتخاب کنید

- Cloudflare Workers
- Deno Deploy
- VPS شخصی

فقط به یکی از این‌ها نیاز دارید.

## ۲) PSK را در کد تنظیم کنید

هر template شامل این خط است:

```js
const PSK = "CHANGE_ME_TO_A_STRONG_SECRET";
```

آن مقدار را با یک secret قوی و تصادفی جایگزین کنید.

نکته مهم:
- همین PSK را در `config.json` زیر `exit_node.psk` وارد کنید.
- URL عمومی را هرگز همراه با PSK معتبر به اشتراک نگذارید.

## ۳) نصب روی Cloudflare Workers

فایل: `apps_script/cloudflare_worker.js`

مراحل:
1. در [https://dash.cloudflare.com](https://dash.cloudflare.com) وارد شوید.
2. به Compute -> Workers & Pages بروید.
3. گزینه Create Application -> Start with Hello World -> Deploy -> Edit Code را انتخاب کنید.
4. کد را با محتوای `apps_script/cloudflare_worker.js` جایگزین کنید.
5. مقدار PSK را در کد تنظیم کنید.
6. Deploy کنید.
7. URL را کپی کنید؛ معمولاً به شکل `https://YOUR-WORKER.YOUR-SUBDOMAIN.workers.dev`

## ۴) نصب روی Deno Deploy (هنوز تست نشده)

فایل: `apps_script/deno_deploy.ts`

مراحل:
1. در [https://dash.deno.com](https://dash.deno.com) وارد شوید.
2. یک app جدید بسازید.
3. گزینه Basic HTML -> Clone Repository را انتخاب کنید.
4. محتوای `apps_script/deno_deploy.ts` را آپلود یا paste کنید.
5. مقدار PSK را در کد تنظیم کنید.
6. Deploy کنید.
7. URL را کپی کنید؛ معمولاً به شکل `https://YOUR-PROJECT.deno.dev`

## ۵) نصب روی VPS شخصی (فقط Linux)

فایل‌ها:
- `apps_script/vps_exit_node.py` (سرور نود خروجی)
- `apps_script/setup_vps_exit_node.sh` (نصب خودکار - پیشنهادی)

نیازمندی‌ها:
- یک VPS لینوکسی با دسترسی root/sudo
- Python 3.10+

دستور نصب سریع:

```bash
curl -fsSL https://raw.githubusercontent.com/masterking32/MasterHttpRelayVPN/python_testing/apps_script/setup_vps_exit_node.sh | sudo bash
```

یا:

```bash
wget -qO- https://raw.githubusercontent.com/masterking32/MasterHttpRelayVPN/python_testing/apps_script/setup_vps_exit_node.sh | sudo bash
```

## ۶) تنظیم MasterHttpRelayVPN

فایل `config.json` را ویرایش کنید:

```json
"exit_node": {
  "enabled": true,
  "provider": "cloudflare",
  "url": "https://YOUR-WORKER.YOUR-SUBDOMAIN.workers.dev",
  "psk": "CHANGE_ME_TO_A_STRONG_SECRET",
  "mode": "full",
  "hosts": [
    "chatgpt.com",
    "openai.com",
    "claude.ai",
    "anthropic.com"
  ]
}
```

مقادیر provider:
- `cloudflare`
- `deno`
- `vps`

اگر `mode` برابر `selective` باشد، فقط دامنه‌های داخل `hosts` از نود خروجی عبور می‌کنند.
اگر `mode` برابر `full` باشد، تمام ترافیک relay‌شده از نود خروجی عبور می‌کند.
