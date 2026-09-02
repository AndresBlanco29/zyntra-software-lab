# Deploy Zyntra Software Lab (DEMO) on Railway

**Never** connect this to the La Tortilla Grocery production Railway project or DB.

## Prerequisites

- GitHub repo: `andres29dev-hub/zyntra-software-lab`
- Branch: `main` (Railway-friendly; also available as `demo/zyntra-software-lab`)
- New Railway project (empty)

## 1. Create project

1. [railway.app/new](https://railway.app/new) → Deploy from GitHub
2. Select `zyntra-software-lab` → branch **`main`**
3. Keep **only the `web` service**. Delete `worker` / `beat` / `scheduler` if Railway created them (DEMO uses eager Celery; no LTG scheduler).
4. Add plugin: **MySQL** (new empty database — no LTG dump)

Build uses `nixpacks.toml` (`python -m pip`). Start uses `railway.toml` / `Procfile` (DEMO web only).

## 2. Variables (service → Variables)

Copy from `.env.demo.example`, then set:

```
DEMO_MODE=1
SHOWCASE_MODE=1
DEMO_ENVIRONMENT_LABEL=SOFTWARE LAB
DEMO_BRAND_NAME=Zyntra
DEMO_BRAND_LEGAL_NAME=Zyntra
DEMO_BRAND_TAGLINE=B2B distribution operations platform
DEMO_DISABLE_OUTBOUND_EMAIL=1
DEMO_ALLOW_CLOUDINARY=0
DEMO_USE_SQLITE=0

DEBUG=False
SECRET_KEY=<generate a long random string>

# After Railway assigns *.up.railway.app:
DEMO_RAILWAY_DOMAIN=<your-service>.up.railway.app
ALLOWED_HOSTS=<your-service>.up.railway.app
CSRF_TRUSTED_ORIGINS=https://<your-service>.up.railway.app
APP_BASE_URL=https://<your-service>.up.railway.app

QUICKBOOKS_PROVIDER=mock
QUICKBOOKS_ENVIRONMENT=sandbox
QUICKBOOKS_CLIENT_ID=
QUICKBOOKS_CLIENT_SECRET=
QUICKBOOKS_CATALOG_ONLY_MODE=1
QUICKBOOKS_IMPORT_ACCOUNTING_DOCUMENTS=0

CORS_ALLOW_ALL_ORIGINS=0
USE_CLOUDINARY_MEDIA=0
AI_ASSISTANT_ENABLED=1
AI_ASSISTANT_PROVIDER=mock
CELERY_TASK_ALWAYS_EAGER=1

DEFAULT_FROM_EMAIL=Demo Software Lab <noreply@example.com>
ORDER_NOTIFICATION_EMAILS=demo-orders@example.com
ORDERS_NOTIFICATION_EMAIL=demo-orders@example.com
```

MySQL: link the Railway MySQL plugin so `MYSQLHOST` / `MYSQLUSER` / `MYSQLPASSWORD` / `MYSQLDATABASE` / `MYSQLPORT` are injected (or map Railway’s `MYSQL*` / `MYSQL_*` vars to what Django expects — check `settings.py` DB block).

**Do not set** LTG Resend, Cloudinary, OpenAI, Twilio, or production QuickBooks secrets.

## 3. First deploy + seed

1. Deploy / wait until healthcheck `/login/` is green.
2. One-off shell / Railway CLI:

```bash
python manage.py seed_demo_showcase --reset
```

3. Login: `demo@demo-system.com` / `DemoShowcase2026!`

## 4. Module URLs

| Lab tab   | Path |
|-----------|------|
| Login     | `/login/` |
| Pedidos   | `/pedidos/backoffice/ordenes/` |
| Catálogo  | `/productos/catalogo/` |
| Reportes  | `/reportes/` |

## 5. Wire marketing site (Hostinger)

In `zyntra-web` before `npm run build`:

```
VITE_SOFTWARE_LAB_URL=https://<your-service>.up.railway.app
```

Rebuild and upload full `dist/` to `public_html`.

## Isolation checklist

See [ISOLATION_CHECKLIST.md](./ISOLATION_CHECKLIST.md). Boot must fail if `QUICKBOOKS_ENVIRONMENT=production` while `DEMO_MODE=1`.
