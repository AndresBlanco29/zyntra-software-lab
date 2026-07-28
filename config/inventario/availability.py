"""Dual-ledger inventory availability.

Quick Inventory (QI) is the quantity imported from QuickBooks (stock_fisico).
Active Manual Adjustments are temporary Emergency Inventory Adjustments.
Sales Pending Sync is sold on local invoices not yet exported to QuickBooks.
In orders is quantity reserved on open sales orders (after picking verification)
that do not yet have an invoice. Creating an order alone does not reserve stock.
Available is always computed and never stored as source of truth:

    Available = Quick Inventory
                + Active Manual Adjustments
                - Sales Pending Sync
                - In orders
"""

from __future__ import annotations

from django.db.models import Q, Sum

from config.facturacion.models import InvoiceItem
from config.inventario.models import InventarioMovimiento, StockPresentacion
from config.pedidos.models import PedidoItem

# Orders that still consume available stock until invoiced/cancelled.
CLOSED_ORDER_ESTADOS = frozenset({'CANCELADO', 'INVOICE_GENERADA', 'DESPACHADO'})
EMERGENCY_ADJUSTMENT_TIPO = 'AJUSTE_EMERGENCIA'


def _normalize_presentacion_ids(presentacion_ids):
    ids = []
    for value in presentacion_ids or []:
        try:
            presentacion_id = int(value)
        except (TypeError, ValueError):
            continue
        if presentacion_id > 0:
            ids.append(presentacion_id)
    return sorted(set(ids))


def quick_inventory_map(presentacion_ids):
    """Return {presentacion_id: Quick Inventory packages} from stock_fisico."""
    ids = _normalize_presentacion_ids(presentacion_ids)
    if not ids:
        return {}
    rows = StockPresentacion.objects.filter(presentacion_id__in=ids).values_list(
        'presentacion_id',
        'stock_fisico',
    )
    result = {presentacion_id: 0 for presentacion_id in ids}
    for presentacion_id, stock_fisico in rows:
        result[presentacion_id] = int(stock_fisico or 0)
    return result


def active_manual_adjustments_map(presentacion_ids):
    """Sum Active Emergency Inventory Adjustment deltas (never mutate QI)."""
    ids = _normalize_presentacion_ids(presentacion_ids)
    if not ids:
        return {}
    rows = (
        InventarioMovimiento.objects.filter(
            presentacion_id__in=ids,
            tipo=EMERGENCY_ADJUSTMENT_TIPO,
            estado=InventarioMovimiento.ESTADO_ACTIVE,
        )
        .values('presentacion_id')
        .annotate(qty=Sum('delta_fisico'))
    )
    result = {presentacion_id: 0 for presentacion_id in ids}
    for row in rows:
        result[int(row['presentacion_id'])] = int(row['qty'] or 0)
    return result


def presentations_with_active_adjustments(presentacion_ids):
    """Return set of presentacion_ids that have at least one Active emergency adjustment."""
    ids = _normalize_presentacion_ids(presentacion_ids)
    if not ids:
        return set()
    return set(
        InventarioMovimiento.objects.filter(
            presentacion_id__in=ids,
            tipo=EMERGENCY_ADJUSTMENT_TIPO,
            estado=InventarioMovimiento.ESTADO_ACTIVE,
        ).values_list('presentacion_id', flat=True)
    )


def sales_pending_sync_map(presentacion_ids):
    """Sum InvoiceItem.cantidad_facturada for GENERADA invoices not yet in QuickBooks."""
    ids = _normalize_presentacion_ids(presentacion_ids)
    if not ids:
        return {}
    rows = (
        InvoiceItem.objects.filter(
            presentacion_id__in=ids,
            invoice__estado='GENERADA',
        )
        .filter(Q(invoice__quickbooks_id__isnull=True) | Q(invoice__quickbooks_id=''))
        .values('presentacion_id')
        .annotate(qty=Sum('cantidad_facturada'))
    )
    result = {presentacion_id: 0 for presentacion_id in ids}
    for row in rows:
        result[int(row['presentacion_id'])] = int(row['qty'] or 0)
    return result


def in_orders_map(presentacion_ids, *, exclude_pedido_ids=None):
    """Sum reserved packages on open orders that do not yet have an invoice.

    Reservation happens at picking verification via
    PedidoItem.cantidad_reservada_inventario. Requested/open line qty alone
    does not reduce Available.
    """
    ids = _normalize_presentacion_ids(presentacion_ids)
    if not ids:
        return {}
    queryset = PedidoItem.objects.filter(
        presentacion_id__in=ids,
        pedido__invoice__isnull=True,
        cantidad_reservada_inventario__gt=0,
    ).exclude(pedido__estado__in=CLOSED_ORDER_ESTADOS)
    if exclude_pedido_ids:
        queryset = queryset.exclude(pedido_id__in=list(exclude_pedido_ids))
    rows = queryset.values('presentacion_id').annotate(qty=Sum('cantidad_reservada_inventario'))
    result = {presentacion_id: 0 for presentacion_id in ids}
    for row in rows:
        result[int(row['presentacion_id'])] = int(row['qty'] or 0)
    return result


def availability_snapshot(presentacion_ids, *, exclude_pedido_ids=None):
    """
    Build per-presentation dual-ledger snapshot.

    Returns dict[presentacion_id] = {
        'quick_inventory', 'active_manual_adjustments', 'sales_pending_sync',
        'in_orders', 'available', 'has_active_adjustments'
    }
    """
    ids = _normalize_presentacion_ids(presentacion_ids)
    qi = quick_inventory_map(ids)
    adjustments = active_manual_adjustments_map(ids)
    pending = sales_pending_sync_map(ids)
    in_orders = in_orders_map(ids, exclude_pedido_ids=exclude_pedido_ids)
    with_active = presentations_with_active_adjustments(ids)
    snapshot = {}
    for presentacion_id in ids:
        quick_inventory = int(qi.get(presentacion_id, 0) or 0)
        active_manual_adjustments = int(adjustments.get(presentacion_id, 0) or 0)
        sales_pending_sync = int(pending.get(presentacion_id, 0) or 0)
        in_orders_qty = int(in_orders.get(presentacion_id, 0) or 0)
        snapshot[presentacion_id] = {
            'quick_inventory': quick_inventory,
            'active_manual_adjustments': active_manual_adjustments,
            'sales_pending_sync': sales_pending_sync,
            'in_orders': in_orders_qty,
            'available': (
                quick_inventory
                + active_manual_adjustments
                - sales_pending_sync
                - in_orders_qty
            ),
            'has_active_adjustments': presentacion_id in with_active,
        }
    return snapshot


def available_for_presentacion(presentacion_id, *, exclude_pedido_ids=None):
    snapshot = availability_snapshot([presentacion_id], exclude_pedido_ids=exclude_pedido_ids)
    return int(snapshot.get(int(presentacion_id), {}).get('available', 0) or 0)


def presentacion_is_quickbooks_linked(presentacion):
    qb_id = str(getattr(presentacion, 'quickbooks_id', '') or '').strip()
    if qb_id:
        return True
    producto = getattr(presentacion, 'producto', None)
    return bool(str(getattr(producto, 'quickbooks_id', '') or '').strip())
