# Probar Zyntra DEMO en local

Carpeta del Software Lab: `D:\ZYNTRA SOFTWARE LAB` (rama `demo/zyntra-software-lab`).  
Trabajamos **solo aquí** para Zyntra; Tortilla queda aparte en el Escritorio.

Entorno **aislado** (SQLite `db_demo.sqlite3`). No usa la MySQL de La Tortilla Grocery aunque exista en `.env`.

## Requisitos

- Python del proyecto con dependencias instaladas
- Trabajar desde `D:\ZYNTRA SOFTWARE LAB` (donde está `manage.py`)

## Arranque rápido (Windows PowerShell)

```powershell
cd "D:\ZYNTRA SOFTWARE LAB"
.\scripts\run_demo_local.ps1
```

Opciones:

```powershell
.\scripts\run_demo_local.ps1 -SkipSeed          # solo servidor (DB ya sembrada)
.\scripts\run_demo_local.ps1 -Port 8001         # otro puerto
.\scripts\run_demo_local.ps1 -FreshDb           # borra db_demo.sqlite3 y vuelve a migrar/seed
```

## Manual

```powershell
cd "Proyecto App Tortilla"

$env:DEMO_MODE = "1"
$env:SHOWCASE_MODE = "1"
$env:DEMO_USE_SQLITE = "1"
$env:QUICKBOOKS_PROVIDER = "mock"
$env:QUICKBOOKS_ENVIRONMENT = "sandbox"
$env:DEMO_DISABLE_OUTBOUND_EMAIL = "1"
$env:AI_ASSISTANT_ENABLED = "1"
$env:AI_ASSISTANT_PROVIDER = "mock"
$env:USE_CLOUDINARY_MEDIA = "0"
$env:CELERY_TASK_ALWAYS_EAGER = "1"
$env:DEBUG = "True"

python manage.py check_demo_isolation --require-demo
python manage.py migrate --noinput
python manage.py seed_demo_showcase --reset
python manage.py runserver 127.0.0.1:8000
```

## Login

| Campo | Valor |
|-------|--------|
| Email | `demo@demo-system.com` |
| Password | `DemoShowcase2026!` |

Abre: http://127.0.0.1:8000/login/

## Qué probar (checklist)

1. Banner **SOFTWARE LAB** y marca **Zyntra** (no LTG).
2. Sidebar → **Guided tour** (recorre panel).
3. Pedidos / picking / facturas `INV-DEMO-*` / entregas.
4. **QuickBooks** → conectar / sync mock (sin red a Intuit).
5. **Reset Demo** (frase `RESET DEMO`) y vuelve a entrar con el mismo login.

## Seguridad

- `seed_demo_showcase --reset` y Reset Demo **borran datos de negocio** en la DB activa.
- Solo con `DEMO_MODE=1` y, en local, `DEMO_USE_SQLITE=1`.
- No desplegar ni apuntar a producción LTG.

## Si algo falla

```powershell
python manage.py check_demo_isolation --require-demo
```

Si dice que `DEMO_MODE` está off, asegúrate de exportar las variables **antes** de `manage.py` (el proceso `.env` de LTG no debe ganar: el script fija process env).
