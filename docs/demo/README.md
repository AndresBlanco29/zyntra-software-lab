# Software Lab — DEMO (La Tortilla Grocery codebase)

Documentación del entorno DEMO comercial. **Producción es intocable.**

| Documento | Fase | Contenido |
|-----------|------|-----------|
| [FASE2_MVP_SCOPE.md](FASE2_MVP_SCOPE.md) | 2 | Alcance MVP, historia del video, módulos, fuera de alcance |
| [ISOLATION_CHECKLIST.md](ISOLATION_CHECKLIST.md) | 3 | Checklist Railway / DB / secretos / QB |
| [FASE4_DATABASE.md](FASE4_DATABASE.md) | 4 | DB DEMO vacía + migrate |
| [FASE6_BRANDING.md](FASE6_BRANDING.md) | 6 | Identidad **Zyntra** (solo `DEMO_MODE`) |
| [TRY_LOCAL.md](TRY_LOCAL.md) | — | **Probar en local** (SQLite aislado + script) |
| [../.env.demo.example](../../.env.demo.example) | 3 | Variables de entorno DEMO (sin secretos reales) |
| [../../Procfile.demo](../../Procfile.demo) | 3 | Procesos Railway recomendados para DEMO |
| [../../scripts/run_demo_local.ps1](../../scripts/run_demo_local.ps1) | — | Arranque local Windows |

### Seed showcase (Fase 5)

```bash
# DEMO_MODE=1 on an isolated DB, then:
python manage.py migrate --noinput
python manage.py seed_demo_showcase --reset
```

Login: `demo@demo-system.com` / `DemoShowcase2026!`

## Regla de oro

- `DEMO_MODE=1` solo en el proyecto Railway / `.env` del DEMO.
- Nunca reutilizar `DATABASE_URL`, tokens QuickBooks, Cloudinary, Resend u OpenAI de producción LTG.
- No hacer deploy del DEMO hasta completar Fases 13–14 y aprobación explícita.
