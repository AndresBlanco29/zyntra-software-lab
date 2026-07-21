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

from django.db.models import Q
from django.utils import timezone

from config.productos.models import Promocion, PromocionEscala


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

    tipo_cliente_id = getattr(cliente, 'tipo_cliente_id', None)
    if tipo_cliente_id:
        return queryset.filter(
            Q(tipos_cliente__isnull=True) | Q(tipos_cliente_id=tipo_cliente_id)
        ).distinct()
    return queryset.filter(tipos_cliente__isnull=True).distinct()


def promociones_activas_queryset(now=None, cliente=None):
    now = now or timezone.now()
    queryset = (
        Promocion.objects.filter(activa=True)
        .filter(Q(fecha_inicio__isnull=True) | Q(fecha_inicio__lte=now))
        .filter(Q(fecha_fin__isnull=True) | Q(fecha_fin__gte=now))
        .select_related('producto', 'presentacion')
    )
    return _filtrar_por_tipo_cliente(queryset, cliente)


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
        base = precio_referencia_presentacion(presentacion, precio)
        unidades = int(escala.unidades_gratis or 0)
        try:
            qty = int(cantidad) if cantidad else int(escala.cantidad_minima)
        except (TypeError, ValueError):
            qty = int(escala.cantidad_minima)
        qty = max(qty, 1)
        if base <= 0 or unidades <= 0:
            return Decimal('0.00')
        # Free units are modelled as an equivalent per-unit discount spread across the
        # purchased quantity, rather than physically adding extra units to the order.
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


def _mejor_escala(candidatos, *, precio, presentacion, cantidad, preferir_menor_cantidad_minima):
    best_escala = None
    best_promo = None
    best_monto = Decimal('0.00')
    for escala in candidatos:
        monto = calcular_descuento_monto_escala(escala, precio, presentacion=presentacion, cantidad=cantidad)
        if monto <= 0:
            continue
        if best_escala is None or monto > best_monto:
            best_escala, best_promo, best_monto = escala, escala.promocion, monto
            continue
        if monto != best_monto:
            continue
        if preferir_menor_cantidad_minima and escala.cantidad_minima != best_escala.cantidad_minima:
            if escala.cantidad_minima < best_escala.cantidad_minima:
                best_escala, best_promo, best_monto = escala, escala.promocion, monto
            continue
        if escala.promocion_id < best_promo.id:
            best_escala, best_promo, best_monto = escala, escala.promocion, monto
    return best_promo, best_escala, best_monto


def resolver_escala_disponible_para_linea(
    *,
    producto_id,
    presentacion_id,
    precio_unitario,
    now=None,
    presentacion=None,
    cliente=None,
):
    """Return (promocion, escala, monto) for the most attractive tier even before its minimum is met."""
    if not producto_id:
        return None, None, Decimal('0.00')

    presentacion_id = int(presentacion_id) if presentacion_id else None
    precio = _quantize_money(precio_unitario)
    if presentacion is None and presentacion_id:
        from config.productos.models import Presentacion
        presentacion = Presentacion.objects.filter(id=presentacion_id).first()

    promos = promociones_activas_queryset(now=now, cliente=cliente).filter(
        producto_id=int(producto_id),
    ).filter(
        Q(presentacion__isnull=True) | Q(presentacion_id=presentacion_id)
    )
    candidatos = PromocionEscala.objects.filter(promocion__in=promos).select_related('promocion')

    return _mejor_escala(
        candidatos,
        precio=precio,
        presentacion=presentacion,
        cantidad=None,
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

    presentacion_id = int(presentacion_id) if presentacion_id else None
    precio = _quantize_money(precio_unitario)
    if presentacion is None and presentacion_id:
        from config.productos.models import Presentacion
        presentacion = Presentacion.objects.filter(id=presentacion_id).first()

    promos = promociones_activas_queryset(now=now, cliente=cliente).filter(
        producto_id=int(producto_id),
    ).filter(
        Q(presentacion__isnull=True) | Q(presentacion_id=presentacion_id)
    )
    candidatos = PromocionEscala.objects.filter(
        promocion__in=promos,
        cantidad_minima__lte=qty,
    ).select_related('promocion')

    return _mejor_escala(
        candidatos,
        precio=precio,
        presentacion=presentacion,
        cantidad=qty,
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
):
    """Return (promocion_or_None, descuento_monto_per_unit) for the most attractive active promo."""
    promo, _escala, monto = resolver_escala_disponible_para_linea(
        producto_id=producto_id,
        presentacion_id=presentacion_id,
        precio_unitario=precio_unitario,
        now=now,
        presentacion=presentacion,
        cliente=cliente,
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
    )
    return promo, monto


def promociones_por_producto_ids(producto_ids, now=None, cliente=None):
    """Return {producto_id: best display Promocion} for catalog badges."""
    ids = {int(pid) for pid in (producto_ids or []) if pid}
    if not ids:
        return {}

    mapping = {}
    for promo in (
        promociones_activas_queryset(now=now, cliente=cliente)
        .filter(producto_id__in=ids)
        .prefetch_related('escalas')
        .order_by('id')
    ):
        current = mapping.get(promo.producto_id)
        if current is None:
            mapping[promo.producto_id] = promo
            continue
        # Prefer product-wide promo for badge, else keep first seen.
        if current.presentacion_id and not promo.presentacion_id:
            mapping[promo.producto_id] = promo
    return mapping


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
    )
    available_promo, available_escala, available_amount = resolver_escala_disponible_para_linea(
        producto_id=producto_id,
        presentacion_id=presentacion_id,
        precio_unitario=precio_unitario,
        now=now,
        presentacion=presentacion,
        cliente=cliente,
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
        }

    minimum = int(escala.cantidad_minima)
    return {
        'available': True,
        'applied': applied_promo is not None,
        'minimum': minimum,
        'current': qty,
        'missing': max(0, minimum - qty),
        'name': promo.nombre,
        'description': promo.texto_catalogo(),
        'discount_amount': format(amount, '.2f'),
    }


def _clear_promo_fields(item):
    item['descuento_aplicado'] = False
    item['descuento_monto'] = 0
    item['descuento_origen'] = ''
    item.pop('promocion_id', None)
    item.pop('promocion_nombre', None)
    item.pop('promocion_descripcion', None)
    return item


def aplicar_promocion_en_item_sesion(item, *, precio_unitario=None, respect_manual=True, presentacion=None, cliente=None):
    """
    Mutate a session cart line with the best active promotion discount.

    - Writes descuento_aplicado / descuento_monto used by CotizacionItem / PedidoItem.
    - If respect_manual and the line was edited manually, leave discount alone.
    - If quantity falls below the threshold, remove only auto-applied promo discounts.
    """
    if not isinstance(item, dict):
        return item

    origen = str(item.get('descuento_origen') or '').strip().lower()
    if respect_manual and origen == DESCUENTO_ORIGEN_MANUAL:
        return item

    precio = precio_unitario if precio_unitario is not None else item.get('precio', 0)
    promo, monto = resolver_promocion_para_linea(
        producto_id=item.get('producto_id'),
        presentacion_id=item.get('presentacion_id'),
        cantidad=item.get('cantidad'),
        precio_unitario=precio,
        presentacion=presentacion,
        cliente=cliente,
    )
    if promo is None:
        if origen == DESCUENTO_ORIGEN_PROMOCION or not item.get('descuento_aplicado'):
            return _clear_promo_fields(item)
        return item

    item['descuento_aplicado'] = True
    item['descuento_monto'] = float(monto)
    item['descuento_origen'] = DESCUENTO_ORIGEN_PROMOCION
    item['promocion_id'] = promo.id
    item['promocion_nombre'] = promo.nombre
    item['promocion_descripcion'] = promo.texto_catalogo()
    return item


def aplicar_promocion_a_item_persistido(item, *, only_if_missing=True, cliente=None):
    """
    Apply the best qualifying promotion onto a CotizacionItem or PedidoItem.

    When only_if_missing is True, lines that already have a discount are left alone
    (BackOffice may have set a manual discount).
    Returns True when the item was modified.
    """
    if item is None:
        return False
    if only_if_missing and item.descuento_aplicado and _quantize_money(item.descuento_monto) > 0:
        return False

    presentacion = getattr(item, 'presentacion', None)
    producto_id = getattr(presentacion, 'producto_id', None) if presentacion is not None else None
    if not producto_id:
        return False

    promo, monto = resolver_promocion_para_linea(
        producto_id=producto_id,
        presentacion_id=getattr(item, 'presentacion_id', None),
        cantidad=item.cantidad,
        precio_unitario=item.precio,
        presentacion=presentacion,
        cliente=cliente,
    )
    if promo is None:
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
        if aplicar_promocion_a_item_persistido(item, only_if_missing=only_if_missing, cliente=cliente):
            item.save(update_fields=['descuento_aplicado', 'descuento_monto', 'subtotal'])
            changed = True

    if changed:
        total = sum((_quantize_money(row.subtotal) for row in items), Decimal('0.00'))
        cotizacion.total = _quantize_money(total)
        cotizacion.save(update_fields=['total'])
    return changed


def asegurar_promociones_en_pedido(pedido, *, only_if_missing=True):
    """Persist missing promotion discounts onto order lines that already qualify."""
    from config.pedidos.models import PedidoItem

    cliente = getattr(pedido, 'cliente', None)
    changed = False
    items = list(
        PedidoItem.objects.filter(pedido=pedido)
        .select_related('presentacion__producto')
    )
    for item in items:
        if aplicar_promocion_a_item_persistido(item, only_if_missing=only_if_missing, cliente=cliente):
            item.save(update_fields=['descuento_aplicado', 'descuento_monto', 'subtotal'])
            changed = True

    if changed:
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
