from datetime import datetime, timedelta

from django.urls import reverse
from django.utils import timezone

from config.ai_assistant.services.customer_success_profile import mark_event
from config.ai_assistant.services.language import normalize_assistant_language


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


def resolve_customer_event(*, cliente, profile, summary, language='es'):
    """Return one prioritized, non-invasive customer success intervention."""
    language = normalize_assistant_language(language)
    en = language == 'en'
    candidates = []
    if summary['ready_quotes']:
        quote = summary['ready_quotes'][0]
        candidates.append({
            'priority': 100,
            'key': f'ready-quote:{quote["id"]}',
            'message': (
                'Your quote is ready to review.'
                if en else
                'Tu cotización ya está lista para revisar.'
            ),
            'actions': [{
                'label': 'View quote' if en else 'Ver cotización',
                'url': reverse('cliente_cotizaciones_recibidas'),
                'tour_id': 'quote-ready',
            }],
        })
    if summary['cart_line_count']:
        candidates.append({
            'priority': 90,
            'key': 'abandoned-cart',
            'message': (
                'You left products in your order without sending it. Want to continue?'
                if en else
                'Dejaste productos en tu pedido sin enviar. ¿Deseas continuarlo?'
            ),
            'actions': [{
                'label': 'Continue order' if en else 'Continuar pedido',
                'url': reverse('ver_cotizacion'),
            }],
        })
    if summary['invoices_due_soon']:
        invoice = summary['invoices_due_soon'][0]
        invoice_label = invoice['number'] or invoice['id']
        candidates.append({
            'priority': 80,
            'key': f'invoice-due:{invoice["id"]}',
            'message': (
                f'Invoice {invoice_label} has an upcoming due date.'
                if en else
                f'La factura {invoice_label} tiene una fecha de vencimiento próxima.'
            ),
            'actions': [{
                'label': 'Talk with sales manager' if en else 'Hablar con el gerente de ventas',
                'url': '#',
                'kind': 'contact_handoff',
            }],
        })
    if summary['last_order']:
        candidates.append({
            'priority': 60,
            'key': f'repeat-order:{summary["last_order"]["id"]}',
            'message': (
                'Want to repeat your last order? You can load it and review it before sending.'
                if en else
                '¿Deseas repetir tu último pedido? Puedes cargarlo y revisarlo antes de enviarlo.'
            ),
            'actions': [{
                'label': 'Reorder' if en else 'Repetir pedido',
                'url': reverse('cliente_historial_ordenes'),
                'tour_id': 'reorder',
            }],
        })
    if summary['favorite_products']:
        favorite = summary['favorite_products'][0]
        candidates.append({
            'priority': 50,
            'key': f'favorite:{favorite["product_id"]}',
            'message': (
                f'Your most purchased product includes {favorite["name"]}. Want to add it again?'
                if en else
                f'Tu producto más comprado incluye {favorite["name"]}. ¿Deseas agregarlo nuevamente?'
            ),
            'actions': [{
                'label': f'View {favorite["name"]}' if en else f'Ver {favorite["name"]}',
                'url': f'{reverse("catalogo")}?q={favorite["name"]}',
            }],
        })
    if summary['active_promotion_count'] and summary['favorite_products']:
        candidates.append({
            'priority': 40,
            'key': 'relevant-promotions',
            'message': (
                'There are active promotions that may interest you based on your usual purchases.'
                if en else
                'Hay promociones activas que podrían interesarte según tus compras frecuentes.'
            ),
            'actions': [{
                'label': 'View promotions' if en else 'Ver promociones',
                'url': f'{reverse("catalogo")}?promociones=1',
            }],
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
