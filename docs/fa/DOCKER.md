# راهنمای Docker

Docker زمانی مفید است که می‌خواهید پروژه را بدون نصب مستقیم Python اجرا کنید.

## پیش‌نیاز

- Docker یا Docker Desktop
- فایل `config.json` آماده
- رله Apps Script که از [apps_script/Code.gs](../../apps_script/Code.gs) deploy شده باشد

## اجرای سریع

در پوشه پروژه اجرا کنید:

```bash
docker compose up --build
```

پورت‌های پیش‌فرض:

| سرویس | آدرس |
|-------|------|
| HTTP proxy | `127.0.0.1:8085` |
| SOCKS5 proxy | `127.0.0.1:1080` |

## تنظیم مرورگر

مرورگر را روی HTTP proxy با آدرس `127.0.0.1` و پورت `8085` تنظیم کنید.

اگر از HTTPS استفاده می‌کنید، باید گواهی ساخته‌شده در `ca/ca.crt` را روی سیستم یا مرورگر trust کنید.

## توقف

```bash
docker compose down
```

## نکته‌ها

- مقدارهای محرمانه مثل `auth_key` را داخل تصویر Docker منتشر نکنید.
- اگر `config.json` را تغییر دادید، container را restart کنید.
- اگر پورت‌ها اشغال هستند، پورت‌های `docker-compose.yml` را تغییر دهید.

برای تنظیمات کامل‌تر، [مرجع تنظیمات](CONFIGURATION.md) را بخوانید.
