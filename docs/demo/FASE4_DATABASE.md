# FASE 4 — Base de datos DEMO vacía

**Producción LTG no se toca.** Esta fase prepara un schema vacío (o local) solo para el DEMO.

## Opción A — Local (recomendada para desarrollo)

1. Copia [`.env.demo.example`](../../.env.demo.example) → `.env.demo` (o exporta las vars).
2. Deja **sin** `MYSQL*` para usar SQLite en `config/db.sqlite3`, **o** apunta a un schema MySQL llamado p.ej. `demo_b2b_db` (vacío).
3. Arranca solo con variables DEMO:

```bash
# PowerShell ejemplo
$env:DEMO_MODE="1"
$env:QUICKBOOKS_PROVIDER="mock"
$env:QUICKBOOKS_ENVIRONMENT="sandbox"
$env:DEMO_DISABLE_OUTBOUND_EMAIL="1"
# Opcional: no cargar .env de LTG — usa un .env.demo aislado

python manage.py migrate --noinput
python manage.py check_demo_isolation --require-demo
python manage.py seed_demo_showcase
```

## Opción B — Railway DEMO (infra; sin publicar aún)

1. Proyecto Railway **nuevo**.
2. Plugin MySQL **nuevo** (vacío).
3. Variables de [`.env.demo.example`](../../.env.demo.example) + `SECRET_KEY` fuerte.
4. Start command basado en [`Procfile.demo`](../../Procfile.demo).
5. Tras el primer deploy interno: `migrate` (ya en Procfile) + one-off `seed_demo_showcase`.

## Reglas

| Debe | No debe |
|------|---------|
| DB vacía + migraciones del repo | Restore / dump de producción LTG |
| `DEMO_MODE=1` | Misma `MYSQLDATABASE` que LTG |
| Seed ficticio (Fase 5) | Copiar clientes/productos reales |

## Verificación

```bash
python manage.py check_demo_isolation --require-demo
python manage.py shell -c "from config.clientes.models import Cliente; print(Cliente.objects.count())"
```

Tras `seed_demo_showcase` el conteo debe ser > 0 y los nombres de empresa deben ser ficticios (Harborline, Plaza Fresh, etc.).
