from datetime import timedelta

from django.utils import timezone

from config.pedidos.client_history import list_cliente_favorite_product_ids, list_cliente_purchase_orders
from config.productos.promotions import promociones_activas_queryset
from config.productos.models import Producto


def build_customer_success_summary(*, cliente, cart=None):
    """Compact, ownership-scoped DTO used by tools and the event engine."""
    from config.cotizaciones.models import Cotizacion
    from config.facturacion.models import Invoice

    now = timezone.now()
    recent_orders = list(list_cliente_purchase_orders(cliente=cliente)[:1])
    ready_quotes = list(
        Cotizacion.objects.filter(cliente=cliente, estado='LISTA_PARA_CONFIRMACION')
        .order_by('-fecha')
        .values('id', 'token_cliente', 'fecha')[:3]
    )
    pending_quotes = list(
        Cotizacion.objects.filter(cliente=cliente, estado='ENVIADA')
        .order_by('-fecha')
        .values('id', 'fecha')[:3]
    )
    invoices = list(
        Invoice.objects.filter(
            cliente=cliente,
            estado='GENERADA',
            qb_payment_status__in=['OPEN', 'DUE', 'DUE_TODAY', 'OVERDUE'],
        ).order_by('qb_due_date', '-creada_en')[:5]
    )
    due_soon = [
        invoice for invoice in invoices
        if invoice.qb_due_date and invoice.qb_due_date <= timezone.localdate() + timedelta(days=7)
    ]
    promotion_count = promociones_activas_queryset(cliente=cliente).count()
    cart = cart or {}
    cart_lines = sum(int(item.get('cantidad') or 0) for item in cart.values() if isinstance(item, dict))
    last_order = recent_orders[0] if recent_orders else None
    favorites = list_cliente_favorite_product_ids(cliente=cliente, limit=5)
    favorite_product_map = {
        product.id: product
        for product in Producto.objects.filter(
            id__in=[item['product_id'] for item in favorites],
            activo=True,
        ).select_related('marca')
    }
    return {
        'ready_quotes': ready_quotes,
        'pending_quotes': pending_quotes,
        'open_invoices': [
            {
                'id': invoice.id,
                'number': invoice.numero,
                'status': invoice.qb_payment_status,
                'due_date': invoice.qb_due_date.isoformat() if invoice.qb_due_date else None,
                'balance': str(invoice.saldo_cliente),
            }
            for invoice in invoices
        ],
        'invoices_due_soon': [
            {'id': invoice.id, 'number': invoice.numero, 'due_date': invoice.qb_due_date.isoformat()}
            for invoice in due_soon
        ],
        'last_order': (
            {
                'id': last_order.id,
                'status': last_order.estado,
                'created_at': last_order.creada_en.isoformat(),
            }
            if last_order else None
        ),
        'favorite_products': [
            {
                **favorite,
                'name': favorite_product_map[favorite['product_id']].nombre,
                'brand': favorite_product_map[favorite['product_id']].marca.nombre if favorite_product_map[favorite['product_id']].marca_id else '',
            }
            for favorite in favorites
            if favorite['product_id'] in favorite_product_map
        ],
        'active_promotion_count': promotion_count,
        'cart_line_count': cart_lines,
        'generated_at': now.isoformat(),
    }
