from datetime import datetime, timedelta

from django.urls import reverse
from django.utils import timezone

from config.ai_assistant.services.customer_success_profile import mark_event


def _is_recently_marked(profile, key, hours):
    raw_value = (profile.event_marks or {}).get(key) if profile else None
    if not raw_value:
        return False
    try:
        timestamp = datetime.fromisoformat(raw_value)
        if timezone.is_naive(timestamp):
            timestamp = timezone.make_aware(timestamp)
        return timezone.now() - timestamp < timedelta(hours=hours)
    except (TypeError, ValueError):
        return False


def resolve_customer_event(*, cliente, profile, summary):
    """Return one prioritized, non-invasive customer success intervention."""
    candidates = []
    if summary['ready_quotes']:
        quote = summary['ready_quotes'][0]
        candidates.append({
            'priority': 100,
            'key': f'ready-quote:{quote["id"]}',
            'message': 'Tu cotización ya está lista para revisar.',
            'actions': [{'label': 'Ver cotización', 'url': reverse('cliente_cotizaciones_recibidas'), 'tour_id': 'quote-ready'}],
        })
    if summary['cart_line_count']:
        candidates.append({
            'priority': 90,
            'key': 'abandoned-cart',
            'message': 'Dejaste productos en tu pedido sin enviar. ¿Deseas continuarlo?',
            'actions': [{'label': 'Continuar pedido', 'url': reverse('ver_cotizacion')}],
        })
    if summary['invoices_due_soon']:
        invoice = summary['invoices_due_soon'][0]
        candidates.append({
            'priority': 80,
            'key': f'invoice-due:{invoice["id"]}',
            'message': f'La factura {invoice["number"] or invoice["id"]} tiene una fecha de vencimiento próxima.',
            'actions': [{'label': 'Hablar con un asesor', 'url': '#', 'kind': 'contact_handoff'}],
        })
    if summary['last_order']:
        candidates.append({
            'priority': 60,
            'key': f'repeat-order:{summary["last_order"]["id"]}',
            'message': '¿Deseas repetir tu último pedido? Puedes cargarlo y revisarlo antes de enviarlo.',
            'actions': [{'label': 'Repetir pedido', 'url': reverse('cliente_historial_ordenes'), 'tour_id': 'reorder'}],
        })
    if summary['favorite_products']:
        favorite = summary['favorite_products'][0]
        candidates.append({
            'priority': 50,
            'key': f'favorite:{favorite["product_id"]}',
            'message': f'Tu producto más comprado incluye {favorite["name"]}. ¿Deseas agregarlo nuevamente?',
            'actions': [{'label': f'Ver {favorite["name"]}', 'url': f'{reverse("catalogo")}?q={favorite["name"]}'}],
        })
    if summary['active_promotion_count'] and summary['favorite_products']:
        candidates.append({
            'priority': 40,
            'key': 'relevant-promotions',
            'message': 'Hay promociones activas que podrían interesarte según tus compras frecuentes.',
            'actions': [{'label': 'Ver promociones', 'url': f'{reverse("catalogo")}?promociones=1'}],
        })
    if not candidates:
        return None
    candidates.sort(key=lambda item: item['priority'], reverse=True)
    for candidate in candidates:
        cooldown_hours = 24 if candidate['priority'] >= 80 else 72
        if not _is_recently_marked(profile, candidate['key'], cooldown_hours):
            mark_event(profile, candidate['key'])
            return candidate
    return None
