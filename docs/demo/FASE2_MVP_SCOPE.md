# FASE 2 — Alcance MVP del DEMO + video comercial

**Estado:** especificación. No implica deploy. Branding provisional pendiente de confirmación (ej. “Nexora Distribution”).

---

## 1. Objetivos del MVP

1. Grabar un video comercial profesional (Showcase Mode).
2. Permitir que visitantes prueben el flujo B2B en Software Lab.
3. Demostrar QuickBooks con **Mock** (showcase) y dejar lista la arquitectura **Sandbox** (live demo).
4. Mantener aislamiento total respecto a La Tortilla Grocery producción.

---

## 2. Personas y cuentas MVP

| Cuenta | Rol | Uso |
|--------|-----|-----|
| `demo@demo-system.com` (provisional) | backoffice + lecturas amplias | Exploración principal Software Lab |
| Usuario vendedor seed | `vendedor` | Video: tomar pedido |
| Usuario selector seed | `seleccionador` | Video: picking |
| Usuario driver seed | `driver` | Video: entrega |
| 1–2 clientes portal seed | `cliente` | Cotización / catálogo (opcional en video) |

**Prohibido en cuenta demo compartida:** `admin.users.manage`, editar variables/integraciones críticas, Django admin destructivo, conectar QuickBooks production, reset sin confirmación.

---

## 3. Módulos IN scope (MVP video + prueba)

| # | Módulo | Interactivo | Notas |
|---|--------|-------------|-------|
| 1 | Login + banner DEMO ENVIRONMENT | Sí | Label Software Lab |
| 2 | Dashboard / panel interno | Ver | KPIs del seed |
| 3 | Productos + presentaciones | Crear/editar básico | Datos ficticios |
| 4 | Inventario (stock / movimientos) | Consultar + ajuste demo | Coherente con pedidos |
| 5 | Clientes + asignación vendedor | Crear/editar | Nombres ficticios |
| 6 | Cotizaciones | Crear / enviar / confirmar | |
| 7 | Órdenes (pipeline estados) | Avanzar estados | Historia del video |
| 8 | Back Office comercial | Precios / confirmación | |
| 9 | Picking | Completar líneas | |
| 10 | Invoice | Generar / ver | Prefijo INV-DEMO-* |
| 11 | Driver / Delivery / Pickup / Customer Pickup | Asignar / completar | |
| 12 | Cuentas por cobrar (vista) | Consultar | |
| 13 | Reportes básicos | Consultar | |
| 14 | Promociones (lectura + 1 demo) | Consultar | |
| 15 | QuickBooks Center | Sync mock | Panel + historial |
| 16 | Guided Tour | Sí | 12 pasos |
| 17 | Reset Demo | Sí | Confirmación doble |

---

## 4. Módulos OUT / diferidos (post-MVP)

- Isabella AI con OpenAI de producción (off o key propia + rebrand).
- Backup/restore de sistema real hacia prod.
- Notas débito/crédito avanzadas (mostrar solo si el seed las incluye).
- GPS drivers en tiempo real (mock estático aceptable).
- Twilio SMS/WhatsApp outbound.
- Cloudinary de LTG.
- Live Demo Sandbox Intuit (Fase 10; arquitectura sí, conexión no hasta OK).
- Formulario “Solicitar acceso” corporativo (solo URL/placeholder).
- Tiers `demo_public` / `registered` / `approved` (diseño listo, implementación futura).

---

## 5. Dataset Showcase (mínimos coherentes)

Propuesta de marca de datos (ficticia):

**Empresa demo:** Zyntra (marca provisional confirmada).

**Clientes (ejemplos mejorados):**

- Harborline Market Group
- Plaza Fresh Wholesale
- Nova Pantry Supply
- Ridgeway Bodega Network
- Eastgate Cash & Carry

**Volúmenes sugeridos:**

- 40–80 productos / 80–150 presentaciones
- 12–20 clientes aprobados
- 25–40 pedidos repartidos en: RECIBIDO, EN_GESTION, LISTO_PARA_PICKING, PARA_VERIFICAR, VERIFICADO_AJUSTADO, INVOICE_GENERADA, DESPACHADO (+ cancelado 1–2)
- 15–25 invoices (OPEN / PAID / OVERDUE mix)
- 8–15 deliveries (ASIGNADA / EN_RUTA / ENTREGADA_*)
- 3 drivers, 2 vendedores, 2 selectors, 1 backoffice
- 5–8 cotizaciones en estados variados
- Inventario con stock positivo en SKUs del video
- 6–10 filas `QuickBooksSyncRun` seed (mock history)

Números del dashboard deben cuadrar con pedidos/invoices/AR del seed.

---

## 6. Guion del video (historia operativa)

1. Dashboard activo (ventas, órdenes, AR).
2. Entra pedido de Harborline Market Group.
3. Back Office gestiona precios / confirma.
4. Picking prepara mercancía.
5. Se genera invoice INV-DEMO-#####.
6. Se asigna driver → entrega.
7. Cierre / estado pagado (según flujo demo).
8. QuickBooks Center → Sync (mock animado) → historial Success.
9. Cierre: “Software Lab — pruébalo”.

Duración objetivo: 90–150 segundos de recorrido UI.

---

## 7. Showcase Mode vs visitante

| | Showcase (video) | Visitante Software Lab |
|--|------------------|------------------------|
| Dataset | Seed fresco “film-ready” | Mismo seed + Reset |
| QB | Mock obligatorio | Mock |
| Guided Tour | Opcional off-camera | On |
| Reset | Operador | Botón protegido |

---

## 8. Software Lab (página corporativa — contrato de enlace)

Botones previstos (corporativo, fuera de este repo por ahora):

- **VER VIDEO** → asset alojado en corporativo
- **PROBAR DEMO** → URL del Railway DEMO (`DEMO_PUBLIC_URL`)
- **SOLICITAR ACCESO** → formulario corporativo (placeholder)

Este repo solo documenta `DEMO_PUBLIC_URL` y deja el login demo listo.

---

## 9. Criterios de aceptación MVP

- [ ] Cero conexión a MySQL / QB / Cloudinary / Resend de LTG
- [ ] Banner DEMO visible
- [ ] Seed no vacío; video puede grabarse sin crear datos a mano
- [ ] Sync QuickBooks mock completa UX sin red externa
- [ ] Reset Demo restaura seed
- [ ] Branding distinto a LTG (tras confirmar nombre)
- [ ] Cuenta demo sin privilegios peligrosos
- [ ] Tests de guards `DEMO_MODE` en verde

---

## 10. Siguiente fase

**FASE 3:** `DEMO_MODE`, guards de boot, `.env.demo.example`, `Procfile.demo`, checklist de aislamiento. Sin deploy.
