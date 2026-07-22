"""Active product promotions and per-line discount resolution.

Architecture
------------
A ``Promocion`` is the "header" (product/presentation, customer types,
validity window). The actual discount rules are ``PromocionEscala`` rows
(quantity tiers such as "buy 12 -> 5%", "buy 24 -> 10%", "buy 10 -> 1 free
unit"). Every resolver below works in two layers:

- ``resolver_escala_*`` functions return ``(promocion, escala, monto)`` and
  are the source of truth (they know which tier matched and why).
- ``resolver_promocion_para_linea`` / ``resolver_promocion_disponible_para_linea``
  are thin ``(promocion, monto)`` wrappers kept for the call sites (cart,
  quotes, orders) that only care about the money, not which tier applied.

Customer-type scoping is optional and additive: every public resolver takes
an optional ``cliente`` kwarg. When it is omitted (internal/BackOffice
contexts that don't have a shopping customer) promotions are not filtered by
customer type, matching the previous behaviour.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Count, Q
from django.utils import timezone

from config.productos.models import Promocion, PromocionEscala, PromocionProducto


DESCUENTO_ORIGEN_PROMOCION = 'promocion'
DESCUENTO_ORIGEN_MANUAL = 'manual'

# Preset percentage options for Promotions admin (Benefit Type = Percentage).
# Labels mirror the Orders "Discount N" wording so ops can map them consistently.
DEFAULT_PROMO_PERCENTAGE_PRESETS = (
    Decimal('5.00'),
    Decimal('10.00'),
    Decimal('15.00'),
    Decimal('20.00'),
    Decimal('25.00'),
    Decimal('30.00'),
    Decimal('35.00'),
    Decimal('40.00'),
    Decimal('45.00'),
    Decimal('50.00'),
)


def opciones_porcentaje_promocion(extra_value=None):
    """Return [{value, label, key}, ...] for the Percentage benefit dropdown."""
    from django.utils.translation import gettext as _

    values = []
    seen = set()
    for amount in DEFAULT_PROMO_PERCENTAGE_PRESETS:
        quantized = _quantize_money(amount)
        key = format(quantized, '.2f')
        if key in seen:
            continue
        seen.add(key)
        values.append(quantized)

    if extra_value is not None:
        extra = _quantize_money(extra_value)
        if extra > 0:
            key = format(extra, '.2f')
            if key not in seen:
                values.append(extra)
                seen.add(key)

    options = []
    for index, amount in enumerate(values, start=1):
        options.append({
            'key': f'descuento_{index}',
            'value': format(amount, '.2f'),
            'label': str(_('Discount %(number)s – %(amount)s%%') % {
                'number': index,
                'amount': format(amount, '.0f') if amount == amount.to_integral_value() else format(amount, '.2f'),
            }),
        })
    return options


def opciones_monto_fijo_promocion(extra_value=None):
    """Return Orders preset dollar discounts for the Fixed Dollars benefit dropdown."""
    from django.utils.translation import gettext as _
    from config.productos.models import ConfiguracionDescuentos

    options = list(ConfiguracionDescuentos.obtener().opciones_activas())
    if extra_value is None:
        return options

    extra = _quantize_money(extra_value)
    if extra <= 0:
        return options
    extra_key = format(extra, '.2f')
    if any(option['value'] == extra_key for option in options):
        return options

    options.append({
        'key': 'custom',
        'value': extra_key,
        'label': str(_('Custom – $%(amount)s') % {'amount': extra_key}),
    })
    return options


def _to_decimal(value, default='0'):
    try:
        return Decimal(str(value if value is not None else default))
    except Exception:
        return Decimal(str(default))


def _quantize_money(value):
    return _to_decimal(value, '0').quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _filtrar_por_tipo_cliente(queryset, cliente):
    """
    Scope a Promocion queryset to a customer's type.

    A promotion with no ``tipos_cliente`` configured applies to every
    customer type (this is also what keeps promotions created before this
    feature existed working unchanged). When ``cliente`` is None (no
    shopping customer in context, e.g. BackOffice/admin previews) no
    filtering is applied at all.
    """
    if cliente is None:
        return queryset

    # Count is more reliable than tipos_cliente__isnull for empty M2M sets.
    queryset = queryset.annotate(_tipos_cliente_count=Count('tipos_cliente', distinct=True))
    tipo_cliente_id = getattr(cliente, 'tipo_cliente_id', None)
    if tipo_cliente_id:
        return queryset.filter(
            Q(_tipos_cliente_count=0) | Q(tipos_cliente__id=tipo_cliente_id)
        ).distinct()
    # Customer without a type only receives unrestricted promotions.
    return queryset.filter(_tipos_cliente_count=0)


def promociones_activas_queryset(now=None, cliente=None):
    now = now or timezone.now()
    queryset = (
        Promocion.objects.filter(activa=True)
        .filter(Q(fecha_inicio__isnull=True) | Q(fecha_inicio__lte=now))
        .filter(Q(fecha_fin__isnull=True) | Q(fecha_fin__gte=now))
        .select_related('producto', 'presentacion')
        .prefetch_related('productos_grupo', 'productos_grupo__producto', 'productos_grupo__presentacion')
    )
    return _filtrar_por_tipo_cliente(queryset, cliente)


def _normalizar_linea_promocion(item):
    if item is None:
        return None
    if isinstance(item, dict):
        if item.get('es_regalo'):
            return None
        producto_id = item.get('producto_id')
        presentacion_id = item.get('presentacion_id')
        cantidad = item.get('cantidad', 0)
    else:
        if getattr(item, 'es_regalo', False):
            return None
        presentacion = getattr(item, 'presentacion', None)
        producto_id = getattr(presentacion, 'producto_id', None) if presentacion is not None else None
        presentacion_id = getattr(item, 'presentacion_id', None)
        cantidad = getattr(item, 'cantidad', 0)
    try:
        qty = int(cantidad or 0)
    except (TypeError, ValueError):
        qty = 0
    if not producto_id:
        return None
    return {
        'producto_id': int(producto_id),
        'presentacion_id': int(presentacion_id) if presentacion_id else None,
        'cantidad': max(qty, 0),
    }


def _lineas_promocion_desde_contexto(lineas_context):
    lineas = []
    for item in lineas_context or []:
        normalizada = _normalizar_linea_promocion(item)
        if normalizada is not None:
            lineas.append(normalizada)
    return lineas


def _linea_coincide_alcance_individual(promo, producto_id, presentacion_id):
    if promo.producto_id != int(producto_id):
        return False
    if promo.presentacion_id and promo.presentacion_id != int(presentacion_id or 0):
        return False
    return True


def _linea_coincide_alcance_grupo(promo, producto_id, presentacion_id):
    presentacion_id = int(presentacion_id) if presentacion_id else None
    for alcance in promo.productos_grupo.all():
        if alcance.producto_id != int(producto_id):
            continue
        if alcance.presentacion_id is None or alcance.presentacion_id == presentacion_id:
            return True
    return False


def _linea_coincide_promocion(promo, producto_id, presentacion_id):
    if promo.alcance == Promocion.ALCANCE_GRUPO:
        return _linea_coincide_alcance_grupo(promo, producto_id, presentacion_id)
    return _linea_coincide_alcance_individual(promo, producto_id, presentacion_id)


def _cantidad_agregada_promocion(promo, lineas):
    total = 0
    for linea in lineas:
        if _linea_coincide_promocion(promo, linea['producto_id'], linea['presentacion_id']):
            total += linea['cantidad']
    return total


def _promociones_para_linea(*, producto_id, presentacion_id, now=None, cliente=None):
    if not producto_id:
        return Promocion.objects.none()

    producto_id = int(producto_id)
    presentacion_id = int(presentacion_id) if presentacion_id else None
    base = promociones_activas_queryset(now=now, cliente=cliente)

    individual = base.filter(alcance=Promocion.ALCANCE_INDIVIDUAL, producto_id=producto_id).filter(
        Q(presentacion__isnull=True) | Q(presentacion_id=presentacion_id)
    )

    grupo = base.filter(alcance=Promocion.ALCANCE_GRUPO, productos_grupo__producto_id=producto_id).filter(
        Q(productos_grupo__presentacion__isnull=True)
        | Q(productos_grupo__presentacion_id=presentacion_id)
    ).distinct()

    promo_ids = set(individual.values_list('id', flat=True)) | set(grupo.values_list('id', flat=True))
    if not promo_ids:
        return Promocion.objects.none()

    return (
        base.filter(id__in=promo_ids)
        .prefetch_related(
            'escalas',
            'escalas__presentacion_regalo',
            'escalas__presentacion_regalo__producto',
            'productos_grupo',
            'productos_grupo__producto',
            'productos_grupo__presentacion',
        )
        .order_by('id')
    )


def _cantidad_evaluacion_promocion(promo, *, cantidad_linea, lineas):
    if promo.alcance == Promocion.ALCANCE_GRUPO and lineas:
        return _cantidad_agregada_promocion(promo, lineas)
    return int(cantidad_linea or 0)


def _resolver_mejor_escala_linea(
    *,
    producto_id,
    presentacion_id,
    cantidad_linea,
    precio_unitario,
    now=None,
    presentacion=None,
    cliente=None,
    lineas_context=None,
    requiere_cantidad_minima=True,
    preferir_menor_cantidad_minima=False,
):
    if not producto_id:
        return None, None, Decimal('0.00')

    presentacion_id = int(presentacion_id) if presentacion_id else None
    precio = _quantize_money(precio_unitario)
    if presentacion is None and presentacion_id:
        from config.productos.models import Presentacion
        presentacion = Presentacion.objects.filter(id=presentacion_id).first()

    lineas = _lineas_promocion_desde_contexto(lineas_context)
    promos = list(_promociones_para_linea(
        producto_id=producto_id,
        presentacion_id=presentacion_id,
        now=now,
        cliente=cliente,
    ))

    best_promo = None
    best_escala = None
    best_monto = Decimal('0.00')
    best_compare = Decimal('0.00')

    for promo in promos:
        if not _linea_coincide_promocion(promo, producto_id, presentacion_id):
            continue
        qty_eval = _cantidad_evaluacion_promocion(promo, cantidad_linea=cantidad_linea, lineas=lineas)
        if requiere_cantidad_minima and qty_eval < 1:
            continue

        escalas = list(promo.escalas.all())
        if requiere_cantidad_minima:
            escalas = [escala for escala in escalas if escala.cantidad_minima <= qty_eval]
        if not escalas:
            continue

        promo_result, escala_result, monto = _mejor_escala(
            escalas,
            precio=precio,
            presentacion=presentacion,
            cantidad=qty_eval,
            preferir_menor_cantidad_minima=preferir_menor_cantidad_minima,
        )
        if escala_result is None:
            continue
        # Cross-product Free Units grant a separate FREE line ($0/unit on the
        # trigger SKU). Compare total savings so gifts compete fairly with
        # per-unit discounts (monto * qty vs estimated gift list value).
        is_cross_gift = (
            escala_result.tipo_beneficio == PromocionEscala.TIPO_FREE_UNITS
            and getattr(escala_result, 'presentacion_regalo_id', None)
        )
        if is_cross_gift:
            compare = _valor_comparacion_escala(
                escala_result,
                precio=precio,
                presentacion=presentacion,
                cantidad=qty_eval,
            )
        else:
            compare = _quantize_money(monto * Decimal(str(max(int(qty_eval or 0), 1))))
        if compare <= 0:
            continue
        if best_escala is None or compare > best_compare:
            best_promo, best_escala, best_monto, best_compare = (
                promo_result, escala_result, monto, compare
            )
            continue
        if compare != best_compare:
            continue
        if preferir_menor_cantidad_minima and escala_result.cantidad_minima != best_escala.cantidad_minima:
            if escala_result.cantidad_minima < best_escala.cantidad_minima:
                best_promo, best_escala, best_monto, best_compare = (
                    promo_result, escala_result, monto, compare
                )
            continue
        if promo.id < best_promo.id:
            best_promo, best_escala, best_monto, best_compare = (
                promo_result, escala_result, monto, compare
            )

    return best_promo, best_escala, best_monto


def precio_referencia_presentacion(presentacion, precio_unitario=None):
    """Prefer the line price; otherwise the first positive list price on the presentation."""
    precio = _quantize_money(precio_unitario)
    if precio > 0:
        return precio
    if presentacion is None:
        return Decimal('0.00')
    for attr in ('precio_1', 'precio_2', 'precio_3', 'precio_4', 'precio_5'):
        candidate = _quantize_money(getattr(presentacion, attr, 0))
        if candidate > 0:
            return candidate
    qb = getattr(presentacion, 'qb_price', None)
    if qb is not None:
        candidate = _quantize_money(qb)
        if candidate > 0:
            return candidate
    return Decimal('0.00')


def calcular_descuento_monto_escala(escala, precio_unitario, *, presentacion=None, cantidad=None):
    """
    Return the per-unit discount dollars granted by one PromocionEscala tier.

    Fixed-dollar and special-price tiers apply even when the line price is
    still $0 (BackOffice often sets the selling price later); percentage and
    free-unit tiers use the line price, or a list price fallback from the
    presentation when the line is still unpriced.
    """
    precio = _quantize_money(precio_unitario)
    valor = _quantize_money(escala.valor_beneficio or 0)

    if escala.tipo_beneficio == PromocionEscala.TIPO_PERCENT:
        base = precio_referencia_presentacion(presentacion, precio)
        if base <= 0:
            return Decimal('0.00')
        monto = _quantize_money(base * valor / Decimal('100'))
        if precio > 0 and monto > precio:
            monto = precio

    elif escala.tipo_beneficio == PromocionEscala.TIPO_PRECIO_ESPECIAL:
        base = precio_referencia_presentacion(presentacion, precio)
        if base <= 0:
            return Decimal('0.00')
        monto = _quantize_money(base - valor)

    elif escala.tipo_beneficio == PromocionEscala.TIPO_FREE_UNITS:
        # Cross-product free goods are separate FREE lines (no $/unit discount on A).
        if getattr(escala, 'presentacion_regalo_id', None):
            monto = Decimal('0.00')
        else:
            base = precio_referencia_presentacion(presentacion, precio)
            unidades = int(escala.unidades_gratis or 0)
            try:
                qty = int(cantidad) if cantidad else int(escala.cantidad_minima)
            except (TypeError, ValueError):
                qty = int(escala.cantidad_minima)
            qty = max(qty, 1)
            if base <= 0 or unidades <= 0:
                return Decimal('0.00')
            # Same-product free units: equivalent per-unit discount on purchased qty.
            monto = _quantize_money((base * unidades) / Decimal(qty))
            if precio > 0 and monto > precio:
                monto = precio

    else:  # TIPO_FIXED
        monto = valor
        if precio > 0 and monto > precio:
            monto = precio

    if monto < 0:
        monto = Decimal('0.00')
    return monto


def calcular_unidades_regalo_escala(escala, cantidad):
    """How many free gift units apply for a purchased quantity."""
    if escala is None or escala.tipo_beneficio != PromocionEscala.TIPO_FREE_UNITS:
        return 0
    if not getattr(escala, 'presentacion_regalo_id', None):
        return 0
    unidades = int(escala.unidades_gratis or 0)
    minimo = max(int(escala.cantidad_minima or 1), 1)
    try:
        qty = int(cantidad or 0)
    except (TypeError, ValueError):
        qty = 0
    if unidades <= 0 or qty < minimo:
        return 0
    return (qty // minimo) * unidades


def _valor_comparacion_escala(escala, *, precio, presentacion, cantidad):
    """Score used to pick the best scale (discount $ or estimated gift value)."""
    if (
        escala.tipo_beneficio == PromocionEscala.TIPO_FREE_UNITS
        and getattr(escala, 'presentacion_regalo_id', None)
    ):
        gift_units = calcular_unidades_regalo_escala(escala, cantidad)
        if gift_units <= 0:
            return Decimal('0.00')
        gift = getattr(escala, 'presentacion_regalo', None)
        gift_price = Decimal('0.00')
        if gift is not None:
            gift_price = _quantize_money(getattr(gift, 'precio_1', 0) or 0)
        # Prefer catalog gift value; if the gift SKU has no list price yet, still
        # treat the free-goods award as a winning benefit so the FREE line is created.
        estimated = _quantize_money(gift_price * Decimal(str(gift_units)))
        return estimated if estimated > 0 else Decimal(str(gift_units))
    return calcular_descuento_monto_escala(escala, precio, presentacion=presentacion, cantidad=cantidad)

def _mejor_escala(candidatos, *, precio, presentacion, cantidad, preferir_menor_cantidad_minima):
    best_escala = None
    best_promo = None
    best_monto = Decimal('0.00')
    best_score = Decimal('0.00')
    for escala in candidatos:
        score = _valor_comparacion_escala(escala, precio=precio, presentacion=presentacion, cantidad=cantidad)
        if score <= 0:
            continue
        monto = calcular_descuento_monto_escala(escala, precio, presentacion=presentacion, cantidad=cantidad)
        if best_escala is None or score > best_score:
            best_escala, best_promo, best_monto, best_score = escala, escala.promocion, monto, score
            continue
        if score != best_score:
            continue
        if preferir_menor_cantidad_minima and escala.cantidad_minima != best_escala.cantidad_minima:
            if escala.cantidad_minima < best_escala.cantidad_minima:
                best_escala, best_promo, best_monto, best_score = escala, escala.promocion, monto, score
            continue
        if escala.promocion_id < best_promo.id:
            best_escala, best_promo, best_monto, best_score = escala, escala.promocion, monto, score
    return best_promo, best_escala, best_monto

def resolver_escala_disponible_para_linea(
    *,
    producto_id,
    presentacion_id,
    precio_unitario,
    now=None,
    presentacion=None,
    cliente=None,
    lineas_context=None,
):
    """Return (promocion, escala, monto) for the most attractive tier even before its minimum is met."""
    return _resolver_mejor_escala_linea(
        producto_id=producto_id,
        presentacion_id=presentacion_id,
        cantidad_linea=1,
        precio_unitario=precio_unitario,
        now=now,
        presentacion=presentacion,
        cliente=cliente,
        lineas_context=lineas_context,
        requiere_cantidad_minima=False,
        preferir_menor_cantidad_minima=True,
    )


def resolver_escala_para_linea(
    *,
    producto_id,
    presentacion_id,
    cantidad,
    precio_unitario,
    now=None,
    presentacion=None,
    cliente=None,
    lineas_context=None,
):
    """
    Return (promocion, escala, descuento_monto_per_unit) for the best qualifying tier.
    Best = greatest per-unit savings among every tier whose minimum quantity is met.
    """
    try:
        qty = int(cantidad or 0)
    except (TypeError, ValueError):
        qty = 0
    if qty < 1 or not producto_id:
        return None, None, Decimal('0.00')

    return _resolver_mejor_escala_linea(
        producto_id=producto_id,
        presentacion_id=presentacion_id,
        cantidad_linea=qty,
        precio_unitario=precio_unitario,
        now=now,
        presentacion=presentacion,
        cliente=cliente,
        lineas_context=lineas_context,
        requiere_cantidad_minima=True,
        preferir_menor_cantidad_minima=False,
    )


def resolver_promocion_disponible_para_linea(
    *,
    producto_id,
    presentacion_id,
    precio_unitario,
    now=None,
    presentacion=None,
    cliente=None,
    lineas_context=None,
):
    """Return (promocion_or_None, descuento_monto_per_unit) for the most attractive active promo."""
    promo, _escala, monto = resolver_escala_disponible_para_linea(
        producto_id=producto_id,
        presentacion_id=presentacion_id,
        precio_unitario=precio_unitario,
        now=now,
        presentacion=presentacion,
        cliente=cliente,
        lineas_context=lineas_context,
    )
    return promo, monto


def resolver_promocion_para_linea(
    *,
    producto_id,
    presentacion_id,
    cantidad,
    precio_unitario,
    now=None,
    presentacion=None,
    cliente=None,
    lineas_context=None,
):
    """Return (promocion_or_None, descuento_monto_per_unit) for the best qualifying promo/tier."""
    promo, _escala, monto = resolver_escala_para_linea(
        producto_id=producto_id,
        presentacion_id=presentacion_id,
        cantidad=cantidad,
        precio_unitario=precio_unitario,
        now=now,
        presentacion=presentacion,
        cliente=cliente,
        lineas_context=lineas_context,
    )
    return promo, monto


def producto_ids_con_promocion_individual_activa(now=None, cliente=None):
    """
    Product IDs that should sort first in "View promotional products" mode.

    Only individual promotions count here; combo members stay in the normal
    catalog order because combos are shown in their own section.
    """
    return set(
        promociones_activas_queryset(now=now, cliente=cliente)
        .filter(alcance=Promocion.ALCANCE_INDIVIDUAL)
        .exclude(producto_id__isnull=True)
        .values_list('producto_id', flat=True)
    )


def promociones_por_producto_ids(producto_ids, now=None, cliente=None):
    """
    Return {producto_id: best display Promocion} for catalog badges.

    Only INDIVIDUAL promotions are attached to product cards. Combo (GROUP)
    promotions are intentionally excluded here so that each member product keeps
    its normal, standalone card (the customer can still order it below the combo
    threshold). Combos are surfaced separately as their own catalog cards via
    ``combos_para_catalogo``.
    """
    ids = {int(pid) for pid in (producto_ids or []) if pid}
    if not ids:
        return {}

    mapping = {}
    base = promociones_activas_queryset(now=now, cliente=cliente).prefetch_related('escalas')

    for promo in base.filter(
        alcance=Promocion.ALCANCE_INDIVIDUAL, producto_id__in=ids
    ).distinct().order_by('id'):
        producto_id = promo.producto_id
        current = mapping.get(producto_id)
        if current is None:
            mapping[producto_id] = promo
            continue
        if current.presentacion_id and not promo.presentacion_id:
            mapping[producto_id] = promo
    return mapping


def combos_para_catalogo(now=None, cliente=None):
    """
    Active combo (GROUP) promotions rendered as their own catalog cards.

    Each entry carries the promotion name, description, quantity threshold,
    benefit text and the list of member products so the catalog can show a
    dedicated, self-explanatory combo card with the "build combo" action.
    """
    now = now or timezone.now()
    combos = []
    queryset = (
        promociones_activas_queryset(now=now, cliente=cliente)
        .filter(alcance=Promocion.ALCANCE_GRUPO)
        .prefetch_related('escalas')
        .distinct()
        .order_by('id')
    )
    for promo in queryset:
        escalas = sorted(promo.escalas.all(), key=lambda escala: escala.cantidad_minima)
        if not escalas:
            continue
        miembros = []
        for pp in promo.productos_grupo.all():
            if pp.producto_id:
                nombre = getattr(pp.producto, 'nombre_traducido', None) or pp.producto.nombre
                miembros.append(nombre)
        if len(miembros) < 2:
            continue
        escala_min = escalas[0]
        combos.append({
            'id': promo.id,
            'nombre': promo.nombre,
            'descripcion': promo.texto_catalogo(),
            'minimo': escala_min.cantidad_minima,
            'beneficio': escala_min.texto_beneficio(),
            'miembros': miembros,
            'total_miembros': len(miembros),
            'fecha_fin_iso': promo.fecha_fin.isoformat() if promo.fecha_fin else '',
        })
    return combos


def adjuntar_promociones_a_productos(productos, now=None, cliente=None):
    productos = list(productos or [])
    mapping = promociones_por_producto_ids([p.id for p in productos], now=now, cliente=cliente)
    for producto in productos:
        promo = mapping.get(producto.id)
        escalas = list(promo.escalas.all()) if promo else []
        escala_minima = escalas[0] if escalas else None
        producto.promocion_activa = promo
        producto.promocion_texto = promo.texto_catalogo() if promo else ''
        producto.promocion_escalas = escalas
        producto.promocion_escala_minima = escala_minima
        producto.promocion_cantidad_minima = escala_minima.cantidad_minima if escala_minima else None
        producto.promocion_presentacion_id = promo.presentacion_id if promo else None
        producto.promocion_presentacion_nombre = (
            promo.presentacion.nombre if promo and promo.presentacion_id else ''
        )
        es_grupo = bool(promo and promo.alcance == Promocion.ALCANCE_GRUPO)
        producto.promocion_es_grupo = es_grupo
        combo_nombres = []
        if es_grupo:
            for pp in promo.productos_grupo.all():
                if pp.producto_id:
                    nombre = getattr(pp.producto, 'nombre_traducido', None) or pp.producto.nombre
                    combo_nombres.append(nombre)
        producto.promocion_combo_productos = combo_nombres
        producto.promocion_combo_total = len(combo_nombres)
        producto.promocion_fecha_fin_iso = (
            promo.fecha_fin.isoformat() if promo and promo.fecha_fin else ''
        )
    return productos


def estado_promocion_para_linea(
    *,
    producto_id,
    presentacion_id,
    cantidad,
    precio_unitario,
    now=None,
    presentacion=None,
    cliente=None,
    lineas_context=None,
):
    """Build serializable UI state for an active promotion on a cart line."""
    try:
        qty = int(cantidad or 0)
    except (TypeError, ValueError):
        qty = 0

    applied_promo, applied_escala, applied_amount = resolver_escala_para_linea(
        producto_id=producto_id,
        presentacion_id=presentacion_id,
        cantidad=qty,
        precio_unitario=precio_unitario,
        now=now,
        presentacion=presentacion,
        cliente=cliente,
        lineas_context=lineas_context,
    )
    available_promo, available_escala, available_amount = resolver_escala_disponible_para_linea(
        producto_id=producto_id,
        presentacion_id=presentacion_id,
        precio_unitario=precio_unitario,
        now=now,
        presentacion=presentacion,
        cliente=cliente,
        lineas_context=lineas_context,
    )
    promo = applied_promo or available_promo
    escala = applied_escala if applied_promo else available_escala
    amount = applied_amount if applied_promo else available_amount
    if promo is None or escala is None:
        return {
            'available': False,
            'applied': False,
            'minimum': 0,
            'current': qty,
            'missing': 0,
            'name': '',
            'description': '',
            'discount_amount': '0.00',
            'grouped': False,
            'group_total': qty,
        }

    minimum = int(escala.cantidad_minima)
    lineas = _lineas_promocion_desde_contexto(lineas_context)
    if promo.alcance == Promocion.ALCANCE_GRUPO and lineas:
        current_qty = _cantidad_agregada_promocion(promo, lineas)
    else:
        current_qty = qty
    return {
        'available': True,
        'applied': applied_promo is not None,
        'minimum': minimum,
        'current': current_qty,
        'missing': max(0, minimum - current_qty),
        'name': promo.nombre,
        'description': promo.texto_catalogo(),
        'discount_amount': format(amount, '.2f'),
        'grouped': promo.alcance == Promocion.ALCANCE_GRUPO,
        'group_total': current_qty,
    }


def _clear_promo_fields(item):
    item['descuento_aplicado'] = False
    item['descuento_monto'] = 0
    item['descuento_origen'] = ''
    item.pop('promocion_id', None)
    item.pop('promocion_nombre', None)
    item.pop('promocion_descripcion', None)
    return item


def _clear_gift_fields(item):
    item.pop('promocion_regalo_presentacion_id', None)
    item.pop('promocion_regalo_cantidad', None)
    item.pop('promocion_escala_id', None)
    return item


def aplicar_promocion_en_item_sesion(
    item,
    *,
    precio_unitario=None,
    respect_manual=True,
    presentacion=None,
    cliente=None,
    lineas_context=None,
):
    """
    Mutate a session cart line with the best active promotion discount.

    - Writes descuento_aplicado / descuento_monto used by CotizacionItem / PedidoItem.
    - If respect_manual and the line was edited manually, leave discount alone.
    - If quantity falls below the threshold, remove only auto-applied promo discounts.
    """
    if not isinstance(item, dict):
        return item
    if item.get('es_regalo'):
        return item

    origen = str(item.get('descuento_origen') or '').strip().lower()
    if respect_manual and origen == DESCUENTO_ORIGEN_MANUAL:
        return item

    precio = precio_unitario if precio_unitario is not None else item.get('precio', 0)
    promo, escala, monto = resolver_escala_para_linea(
        producto_id=item.get('producto_id'),
        presentacion_id=item.get('presentacion_id'),
        cantidad=item.get('cantidad'),
        precio_unitario=precio,
        presentacion=presentacion,
        cliente=cliente,
        lineas_context=lineas_context,
    )
    if promo is None:
        _clear_gift_fields(item)
        if origen == DESCUENTO_ORIGEN_PROMOCION or not item.get('descuento_aplicado'):
            return _clear_promo_fields(item)
        return item

    gift_units = calcular_unidades_regalo_escala(escala, item.get('cantidad'))
    if gift_units > 0 and getattr(escala, 'presentacion_regalo_id', None):
        item['descuento_aplicado'] = False
        item['descuento_monto'] = 0
        item['descuento_origen'] = DESCUENTO_ORIGEN_PROMOCION
        item['promocion_id'] = promo.id
        item['promocion_nombre'] = promo.nombre
        item['promocion_descripcion'] = promo.texto_catalogo()
        item['promocion_escala_id'] = escala.id
        item['promocion_regalo_presentacion_id'] = escala.presentacion_regalo_id
        item['promocion_regalo_cantidad'] = gift_units
        return item

    _clear_gift_fields(item)
    item['descuento_aplicado'] = True
    item['descuento_monto'] = float(monto)
    item['descuento_origen'] = DESCUENTO_ORIGEN_PROMOCION
    item['promocion_id'] = promo.id
    item['promocion_nombre'] = promo.nombre
    item['promocion_descripcion'] = promo.texto_catalogo()
    item['promocion_escala_id'] = escala.id if escala is not None else None
    return item


def sincronizar_regalos_en_sesion(lineas, *, cliente=None):
    """Ensure session cart contains FREE gift lines for cross-product Free units."""
    if not isinstance(lineas, dict):
        return lineas

    from config.productos.models import Presentacion

    desired = {}
    paid_keys = []
    for key, item in list(lineas.items()):
        if item.get('es_regalo'):
            continue
        paid_keys.append(key)
        gift_presentation_id = item.get('promocion_regalo_presentacion_id')
        gift_qty = int(item.get('promocion_regalo_cantidad') or 0)
        if not gift_presentation_id or gift_qty <= 0:
            continue
        desired[str(key)] = {
            'trigger_key': str(key),
            'presentacion_id': int(gift_presentation_id),
            'cantidad': gift_qty,
            'promocion_id': item.get('promocion_id'),
            'promocion_nombre': item.get('promocion_nombre'),
            'promocion_descripcion': item.get('promocion_descripcion'),
        }

    for key in list(lineas.keys()):
        item = lineas[key]
        if not item.get('es_regalo'):
            continue
        trigger_key = str(item.get('regalo_de_presentacion_key') or '')
        plan = desired.get(trigger_key)
        if not plan:
            del lineas[key]
            continue
        if int(item.get('presentacion_id') or 0) != plan['presentacion_id'] or int(item.get('cantidad') or 0) != plan['cantidad']:
            del lineas[key]
            continue
        desired.pop(trigger_key, None)

    for trigger_key, plan in desired.items():
        presentacion = Presentacion.objects.select_related('producto').filter(id=plan['presentacion_id']).first()
        if presentacion is None:
            continue
        gift_key = f'gift:{trigger_key}:{presentacion.id}'
        lineas[gift_key] = {
            'presentacion_id': presentacion.id,
            'producto_id': presentacion.producto_id,
            'nombre': presentacion.producto.nombre,
            'presentacion_nombre': presentacion.nombre_empaque_cliente,
            'precio': 0,
            'cantidad': plan['cantidad'],
            'descuento_aplicado': True,
            'descuento_monto': 0,
            'descuento_origen': DESCUENTO_ORIGEN_PROMOCION,
            'promocion_id': plan.get('promocion_id'),
            'promocion_nombre': plan.get('promocion_nombre'),
            'promocion_descripcion': plan.get('promocion_descripcion') or 'FREE',
            'es_regalo': True,
            'regalo_de_presentacion_key': trigger_key,
            'precio_key': '',
        }
    return lineas


def reaplicar_promociones_en_lineas_sesion(lineas, *, cliente=None):
    """Re-evaluate promotions for every session/cart line sharing combo quantity totals."""
    if not isinstance(lineas, dict):
        return lineas
    contexto = [item for item in lineas.values() if not item.get('es_regalo')]
    for item in lineas.values():
        if item.get('es_regalo'):
            continue
        if str(item.get('descuento_origen') or '').strip().lower() == DESCUENTO_ORIGEN_MANUAL:
            continue
        aplicar_promocion_en_item_sesion(item, cliente=cliente, lineas_context=contexto)
    sincronizar_regalos_en_sesion(lineas, cliente=cliente)
    return lineas

def aplicar_promocion_a_item_persistido(item, *, only_if_missing=True, cliente=None, lineas_context=None):
    """
    Apply the best qualifying promotion onto a CotizacionItem or PedidoItem.

    When only_if_missing is True, lines that already have a discount are left alone
    (BackOffice may have set a manual discount).
    Returns True when the item was modified.
    """
    if item is None:
        return False
    if getattr(item, 'es_regalo', False):
        return False
    if only_if_missing and item.descuento_aplicado and _quantize_money(item.descuento_monto) > 0:
        return False

    presentacion = getattr(item, 'presentacion', None)
    producto_id = getattr(presentacion, 'producto_id', None) if presentacion is not None else None
    if not producto_id:
        return False

    promo, escala, monto = resolver_escala_para_linea(
        producto_id=producto_id,
        presentacion_id=getattr(item, 'presentacion_id', None),
        cantidad=item.cantidad,
        precio_unitario=item.precio,
        presentacion=presentacion,
        cliente=cliente,
        lineas_context=lineas_context,
    )
    if promo is None:
        return False

    # Cross-product free goods are materialized as separate FREE lines.
    if calcular_unidades_regalo_escala(escala, item.cantidad) > 0:
        return False

    item.descuento_aplicado = True
    item.descuento_monto = monto
    item.subtotal = _quantize_money(
        max(Decimal('0.00'), _quantize_money(item.precio) - monto) * Decimal(str(item.cantidad or 0))
    )
    return True

def asegurar_promociones_en_cotizacion(cotizacion, *, only_if_missing=True):
    """Persist missing promotion discounts onto quote lines that already qualify."""
    from config.cotizaciones.models import CotizacionItem

    cliente = getattr(cotizacion, 'cliente', None)
    changed = False
    items = list(
        CotizacionItem.objects.filter(cotizacion=cotizacion)
        .select_related('presentacion__producto')
    )
    for item in items:
        if aplicar_promocion_a_item_persistido(
            item,
            only_if_missing=only_if_missing,
            cliente=cliente,
            lineas_context=items,
        ):
            item.save(update_fields=['descuento_aplicado', 'descuento_monto', 'subtotal'])
            changed = True

    if changed:
        total = sum((_quantize_money(row.subtotal) for row in items), Decimal('0.00'))
        cotizacion.total = _quantize_money(total)
        cotizacion.save(update_fields=['total'])
    return changed


def sincronizar_regalos_promocion_en_pedido(pedido):
    """Create/update/remove FREE PedidoItem gift lines for cross-product Free units."""
    from config.pedidos.models import PedidoItem
    from config.productos.models import Presentacion

    cliente = getattr(pedido, 'cliente', None)
    paid_items = list(
        PedidoItem.objects.filter(pedido=pedido, es_regalo=False)
        .select_related('presentacion__producto')
        .prefetch_related('lineas_regalo')
    )
    desired = []
    for item in paid_items:
        promo, escala, _monto = resolver_escala_para_linea(
            producto_id=item.presentacion.producto_id,
            presentacion_id=item.presentacion_id,
            cantidad=item.cantidad,
            precio_unitario=item.precio,
            presentacion=item.presentacion,
            cliente=cliente,
            lineas_context=paid_items,
        )
        if promo is None or escala is None:
            continue
        gift_units = calcular_unidades_regalo_escala(escala, item.cantidad)
        if gift_units <= 0 or not escala.presentacion_regalo_id:
            continue
        desired.append({
            'origen': item,
            'presentacion_id': escala.presentacion_regalo_id,
            'cantidad': gift_units,
        })

    existing_gifts = list(PedidoItem.objects.filter(pedido=pedido, es_regalo=True).select_related('presentacion'))
    keep_ids = set()
    created = []
    for plan in desired:
        match = next(
            (
                gift for gift in existing_gifts
                if gift.regalo_origen_item_id == plan['origen'].id
                and gift.presentacion_id == plan['presentacion_id']
            ),
            None,
        )
        if match:
            if match.cantidad != plan['cantidad'] or match.cantidad_solicitada != plan['cantidad']:
                match.cantidad = plan['cantidad']
                match.cantidad_solicitada = plan['cantidad']
                match.precio = Decimal('0.00')
                match.descuento_aplicado = True
                match.descuento_monto = Decimal('0.00')
                match.subtotal = Decimal('0.00')
                match.save(update_fields=[
                    'cantidad', 'cantidad_solicitada', 'precio',
                    'descuento_aplicado', 'descuento_monto', 'subtotal',
                ])
            keep_ids.add(match.id)
            continue
        presentacion = Presentacion.objects.filter(id=plan['presentacion_id']).first()
        if presentacion is None:
            continue
        gift = PedidoItem.objects.create(
            pedido=pedido,
            presentacion=presentacion,
            cantidad_solicitada=plan['cantidad'],
            cantidad=plan['cantidad'],
            precio=Decimal('0.00'),
            descuento_aplicado=True,
            descuento_monto=Decimal('0.00'),
            subtotal=Decimal('0.00'),
            es_regalo=True,
            regalo_origen_item=plan['origen'],
        )
        created.append(gift)
        keep_ids.add(gift.id)

    for gift in existing_gifts:
        if gift.id not in keep_ids:
            gift.delete()

    items = list(PedidoItem.objects.filter(pedido=pedido))
    total = sum((_quantize_money(row.subtotal) for row in items), Decimal('0.00'))
    pedido.total = _quantize_money(total)
    update_fields = ['total']
    if hasattr(pedido, 'actualizada_en'):
        update_fields.append('actualizada_en')
    pedido.save(update_fields=update_fields)
    return created


def asegurar_promociones_en_pedido(pedido, *, only_if_missing=True):
    """Persist missing promotion discounts onto order lines that already qualify."""
    from config.pedidos.models import PedidoItem

    cliente = getattr(pedido, 'cliente', None)
    changed = False
    items = list(
        PedidoItem.objects.filter(pedido=pedido, es_regalo=False)
        .select_related('presentacion__producto')
    )
    for item in items:
        if aplicar_promocion_a_item_persistido(
            item,
            only_if_missing=only_if_missing,
            cliente=cliente,
            lineas_context=items,
        ):
            item.save(update_fields=['descuento_aplicado', 'descuento_monto', 'subtotal'])
            changed = True

    sincronizar_regalos_promocion_en_pedido(pedido)
    changed = True

    if changed:
        items = list(PedidoItem.objects.filter(pedido=pedido))
        total = sum((_quantize_money(row.subtotal) for row in items), Decimal('0.00'))
        pedido.total = _quantize_money(total)
        update_fields = ['total']
        if hasattr(pedido, 'actualizada_en'):
            update_fields.append('actualizada_en')
        pedido.save(update_fields=update_fields)
    return changed

def marcar_descuento_manual_en_item(item):
    if not isinstance(item, dict):
        return item
    if item.get('descuento_aplicado'):
        item['descuento_origen'] = DESCUENTO_ORIGEN_MANUAL
        item.pop('promocion_id', None)
        item.pop('promocion_nombre', None)
        item.pop('promocion_descripcion', None)
    else:
        _clear_promo_fields(item)
    return item
