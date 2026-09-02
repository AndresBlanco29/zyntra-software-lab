# Checklist de aislamiento DEMO (FASE 3)

Completar **antes** de cualquier deploy público. Producción LTG no debe aparecer en ninguna fila.

## A. Infraestructura

- [ ] Proyecto Railway **nuevo** (no el de `tortilla-erp-production`)
- [ ] Plugin MySQL **nuevo** (nombre distinto, sin restore desde dump LTG)
- [ ] Dominio DEMO distinto (`DEMO_CANONICAL_DOMAIN` / `ALLOWED_HOSTS`)
- [ ] `Procfile.demo` (o start command equivalente) — sin scheduler QB de producción
- [ ] Redis DEMO propio si se usa Celery (o `CELERY_TASK_ALWAYS_EAGER=True`)

## B. Secretos (nunca copiar de LTG)

- [ ] `SECRET_KEY` nuevo
- [ ] `MYSQL*` / `DB_*` solo DEMO
- [ ] `QUICKBOOKS_CLIENT_ID` / `SECRET` vacíos en Showcase, o App Intuit **propia** en Live Demo
- [ ] Sin fila `QuickBooksConnection` con tokens de producción
- [ ] Sin `CLOUDINARY_*` de LTG (`USE_CLOUDINARY_MEDIA=False` salvo cuenta DEMO)
- [ ] Sin `RESEND_API_KEY` de LTG (usar console email)
- [ ] Sin `OPENAI_API_KEY` de LTG (`AI_ASSISTANT_PROVIDER=mock` + Zyntra Guide; nunca `live` en demo)
- [ ] Sin `TWILIO_*` activos

## C. Flags obligatorios

```
DEMO_MODE=1
QUICKBOOKS_PROVIDER=mock
QUICKBOOKS_ENVIRONMENT=sandbox
DEMO_DISABLE_OUTBOUND_EMAIL=1
AI_ASSISTANT_ENABLED=1
AI_ASSISTANT_PROVIDER=mock
CORS_ALLOW_ALL_ORIGINS=0
USE_CLOUDINARY_MEDIA=0
```

El boot **debe fallar** si `DEMO_MODE=1` y `QUICKBOOKS_ENVIRONMENT=production`.

## D. Datos

- [ ] Migraciones sobre DB vacía
- [ ] `seed_demo_showcase` (Fase 5) — nunca `dumpdata` de producción
- [ ] Teléfonos / WhatsApp / emails del seed son ficticios
- [ ] Prefijos documento `INV-DEMO-`, `ORD-DEMO-`, etc.

## E. Verificación local

```bash
# Debe arrancar
set DEMO_MODE=1
set QUICKBOOKS_PROVIDER=mock
python manage.py check_demo_isolation
python manage.py check

# Debe fallar el import de settings
set QUICKBOOKS_ENVIRONMENT=production
python -c "import django; django.setup()"  # con DJANGO_SETTINGS_MODULE
```

## F. Go-live (Fase 14 — no ahora)

- [ ] Security audit Fase 13
- [ ] Aprobación explícita para deploy
- [ ] Enlace Software Lab → URL DEMO
