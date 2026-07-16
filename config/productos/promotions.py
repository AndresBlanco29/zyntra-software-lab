"""Active product promotions and per-line discount resolution."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Q
from django.utils import timezone

from config.productos.models import Promocion


DESCUENTO_ORIGEN_PROMOCION = 'promocion'
DESCUENTO_ORIGEN_MANUAL = 'manual'


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


def calcular_descuento_monto_promocion(promocion, precio_unitario):
    precio = _quantize_money(precio_unitario)
    if precio <= 0:
        return Decimal('0.00')
    valor = _quantize_money(promocion.valor_beneficio)
    if promocion.tipo_beneficio == Promocion.TIPO_PERCENT:
        monto = _quantize_money(precio * valor / Decimal('100'))
    else:
        monto = valor
    if monto > precio:
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
    return productos


def resolver_promocion_para_linea(
    *,
    producto_id,
    presentacion_id,
    cantidad,
    precio_unitario,
    now=None,
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
    candidates = promociones_activas_queryset(now=now).filter(
        producto_id=int(producto_id),
        cantidad_minima__lte=qty,
    ).filter(
        Q(presentacion__isnull=True) | Q(presentacion_id=presentacion_id)
    )

    best_promo = None
    best_monto = Decimal('0.00')
    for promo in candidates:
        monto = calcular_descuento_monto_promocion(promo, precio)
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


def aplicar_promocion_en_item_sesion(item, *, precio_unitario=None, respect_manual=True):
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
