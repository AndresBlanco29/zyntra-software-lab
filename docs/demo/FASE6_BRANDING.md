# FASE 6 — Branding DEMO: Zyntra

Marca provisional confirmada: **Zyntra**.

Solo se aplica cuando `DEMO_MODE=1`. Producción LTG no cambia.

## Identidad

| Token | Valor |
|-------|--------|
| Nombre | Zyntra |
| Legal | Zyntra |
| Tagline | B2B distribution operations platform |
| Ink | `#07111F` |
| Primary | `#0E7490` |
| Accent | `#2DD4BF` |
| Tipografía | Sora + Manrope |

## Archivos

- [`config/core/demo_branding.py`](../../config/core/demo_branding.py)
- [`config/static/css/zyntra-demo.css`](../../config/static/css/zyntra-demo.css)
- [`config/static/img/zyntra-mark.svg`](../../config/static/img/zyntra-mark.svg)
- Context: `DEMO_BRAND_*` via `demo_environment`
- Overrides en home, panel interno, login/registro, catálogo, emails (nombre)

## Override por env

```
DEMO_BRAND_NAME=Zyntra
DEMO_BRAND_LEGAL_NAME=Zyntra
DEMO_BRAND_TAGLINE=B2B distribution operations platform
```
