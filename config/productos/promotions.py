"""Active product promotions and per-line discount resolution."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Q
from django.utils import timezone

from config.productos.models import Promocion


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


def promociones_activas_queryset(now=None):
    now = now or timezone.now()
    return (
        Promocion.objects.filter(activa=True)
        .filter(Q(fecha_inicio__isnull=True) | Q(fecha_inicio__lte=now))
        .filter(Q(fecha_fin__isnull=True) | Q(fecha_fin__gte=now))
        .select_related('producto', 'presentacion')
    )


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


def calcular_descuento_monto_promocion(promocion, precio_unitario, *, presentacion=None):
    """
    Return per-unit discount dollars for a promotion.

    Fixed-dollar promos apply even when the line price is still $0 (BackOffice often
    sets the selling price later). Percentage promos use the line price, or a list
    price fallback from the presentation when the line is still unpriced.
    """
    precio = _quantize_money(precio_unitario)
    valor = _quantize_money(promocion.valor_beneficio)
    if promocion.tipo_beneficio == Promocion.TIPO_PERCENT:
        base = precio_referencia_presentacion(presentacion, precio)
        if base <= 0:
            return Decimal('0.00')
        monto = _quantize_money(base * valor / Decimal('100'))
        # Cap only against the real line price when it is already known.
        if precio > 0 and monto > precio:
            monto = precio
    else:
        monto = valor
        if precio > 0 and monto > precio:
            monto = precio
    if monto < 0:
        monto = Decimal('0.00')
    return monto


def promociones_por_producto_ids(producto_ids, now=None):
    """Return {producto_id: best display Promocion} for catalog badges."""
    ids = {int(pid) for pid in (producto_ids or []) if pid}
    if not ids:
        return {}

    mapping = {}
    for promo in promociones_activas_queryset(now=now).filter(producto_id__in=ids).order_by('id'):
        current = mapping.get(promo.producto_id)
        if current is None:
            mapping[promo.producto_id] = promo
            continue
        # Prefer product-wide promo for badge, else keep first seen.
        if current.presentacion_id and not promo.presentacion_id:
            mapping[promo.producto_id] = promo
    return mapping


def adjuntar_promociones_a_productos(productos, now=None):
    productos = list(productos or [])
    mapping = promociones_por_producto_ids([p.id for p in productos], now=now)
    for producto in productos:
        promo = mapping.get(producto.id)
        producto.promocion_activa = promo
        producto.promocion_texto = promo.texto_catalogo() if promo else ''
        producto.promocion_cantidad_minima = promo.cantidad_minima if promo else None
        producto.promocion_presentacion_id = promo.presentacion_id if promo else None
        producto.promocion_presentacion_nombre = (
            promo.presentacion.nombre if promo and promo.presentacion_id else ''
        )
    return productos


def resolver_promocion_disponible_para_linea(
    *,
    producto_id,
    presentacion_id,
    precio_unitario,
    now=None,
    presentacion=None,
):
    """Return the most attractive active promo even before its minimum is met."""
    if not producto_id:
        return None, Decimal('0.00')

    presentacion_id = int(presentacion_id) if presentacion_id else None
    precio = _quantize_money(precio_unitario)
    if presentacion is None and presentacion_id:
        from config.productos.models import Presentacion
        presentacion = Presentacion.objects.filter(id=presentacion_id).first()

    candidates = promociones_activas_queryset(now=now).filter(
        producto_id=int(producto_id),
    ).filter(
        Q(presentacion__isnull=True) | Q(presentacion_id=presentacion_id)
    )

    best_promo = None
    best_monto = Decimal('0.00')
    for promo in candidates:
        monto = calcular_descuento_monto_promocion(promo, precio, presentacion=presentacion)
        if monto <= 0:
            continue
        if best_promo is None or monto > best_monto:
            best_promo = promo
            best_monto = monto
        elif monto == best_monto:
            if promo.cantidad_minima < best_promo.cantidad_minima:
                best_promo = promo
            elif promo.cantidad_minima == best_promo.cantidad_minima and promo.id < best_promo.id:
                best_promo = promo

    return best_promo, best_monto


def estado_promocion_para_linea(
    *,
    producto_id,
    presentacion_id,
    cantidad,
    precio_unitario,
    now=None,
    presentacion=None,
):
    """Build serializable UI state for an active promotion on a cart line."""
    try:
        qty = int(cantidad or 0)
    except (TypeError, ValueError):
        qty = 0

    applied_promo, applied_amount = resolver_promocion_para_linea(
        producto_id=producto_id,
        presentacion_id=presentacion_id,
        cantidad=qty,
        precio_unitario=precio_unitario,
        now=now,
        presentacion=presentacion,
    )
    available_promo, available_amount = resolver_promocion_disponible_para_linea(
        producto_id=producto_id,
        presentacion_id=presentacion_id,
        precio_unitario=precio_unitario,
        now=now,
        presentacion=presentacion,
    )
    promo = applied_promo or available_promo
    amount = applied_amount if applied_promo else available_amount
    if promo is None:
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

    minimum = int(promo.cantidad_minima)
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


def resolver_promocion_para_linea(
    *,
    producto_id,
    presentacion_id,
    cantidad,
    precio_unitario,
    now=None,
    presentacion=None,
):
    """
    Return (promocion_or_None, descuento_monto_per_unit) for the best qualifying promo.
    Best = greatest per-unit savings.
    """
    try:
        qty = int(cantidad or 0)
    except (TypeError, ValueError):
        qty = 0
    if qty < 1 or not producto_id:
        return None, Decimal('0.00')

    presentacion_id = int(presentacion_id) if presentacion_id else None
    precio = _quantize_money(precio_unitario)
    if presentacion is None and presentacion_id:
        from config.productos.models import Presentacion
        presentacion = Presentacion.objects.filter(id=presentacion_id).first()

    candidates = promociones_activas_queryset(now=now).filter(
        producto_id=int(producto_id),
        cantidad_minima__lte=qty,
    ).filter(
        Q(presentacion__isnull=True) | Q(presentacion_id=presentacion_id)
    )

    best_promo = None
    best_monto = Decimal('0.00')
    for promo in candidates:
        monto = calcular_descuento_monto_promocion(promo, precio, presentacion=presentacion)
        if monto <= 0:
            continue
        if best_promo is None or monto > best_monto:
            best_promo = promo
            best_monto = monto
        elif monto == best_monto and promo.id < best_promo.id:
            best_promo = promo

    if best_promo is None:
        return None, Decimal('0.00')
    return best_promo, best_monto


def _clear_promo_fields(item):
    item['descuento_aplicado'] = False
    item['descuento_monto'] = 0
    item['descuento_origen'] = ''
    item.pop('promocion_id', None)
    item.pop('promocion_nombre', None)
    item.pop('promocion_descripcion', None)
    return item


def aplicar_promocion_en_item_sesion(item, *, precio_unitario=None, respect_manual=True, presentacion=None):
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


def aplicar_promocion_a_item_persistido(item, *, only_if_missing=True):
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

    changed = False
    items = list(
        CotizacionItem.objects.filter(cotizacion=cotizacion)
        .select_related('presentacion__producto')
    )
    for item in items:
        if aplicar_promocion_a_item_persistido(item, only_if_missing=only_if_missing):
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

    changed = False
    items = list(
        PedidoItem.objects.filter(pedido=pedido)
        .select_related('presentacion__producto')
    )
    for item in items:
        if aplicar_promocion_a_item_persistido(item, only_if_missing=only_if_missing):
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
