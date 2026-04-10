import logging
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone

from config.notificaciones.models import crear_notificacion_backoffice

from .models import Pedido, PedidoItem


logger = logging.getLogger(__name__)


def _to_decimal(value, default='0'):
    text = str(value if value is not None else default).strip().replace(',', '.')
    if not text:
        text = str(default)
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(str(default))


@transaction.atomic
def crear_pedido_desde_items(
    *,
    cliente,
    items_payload,
    origen,
    vendedor=None,
    cotizacion=None,
    nota_cliente='',
    acepta_terminos=False,
    canal_toma='',
):
    pedido = Pedido.objects.create(
        cliente=cliente,
        vendedor=vendedor if getattr(vendedor, 'role', '') == 'vendedor' else None,
        cotizacion=cotizacion,
        origen=origen,
        canal_toma=canal_toma,
        nota_cliente=nota_cliente,
        acepta_terminos=acepta_terminos,
        acepta_terminos_en=timezone.now() if acepta_terminos else None,
        total=Decimal('0'),
    )

    total = Decimal('0')

    for item in items_payload:
        presentacion = item['presentacion']
        cantidad = max(int(item['cantidad']), 1)
        precio = _to_decimal(item['precio'])
        subtotal = precio * cantidad

        PedidoItem.objects.create(
            pedido=pedido,
            presentacion=presentacion,
            cantidad=cantidad,
            precio=precio,
            subtotal=subtotal,
        )

        total += subtotal

    pedido.total = total
    pedido.save(update_fields=['total'])
    return pedido


def recalcular_pedido(pedido):
    total = Decimal('0')

    for item in pedido.items.all():
        item.subtotal = _to_decimal(item.precio) * item.cantidad
        item.save(update_fields=['subtotal'])
        total += item.subtotal

    pedido.total = total
    pedido.save(update_fields=['total', 'actualizada_en'])
    return total


def construir_contexto_pedido(pedido):
    items = pedido.items.select_related('presentacion__producto')
    return {
        'pedido': pedido,
        'cliente': pedido.cliente,
        'items': items,
    }


def notificar_backoffice_pedido(pedido):
    context = construir_contexto_pedido(pedido)
    html_content = render_to_string('emails/pedido_backoffice.html', context)

    email = EmailMultiAlternatives(
        subject=f"Nueva orden de compra #{pedido.id}",
        body='Se ha recibido una nueva orden de compra en el sistema.',
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[settings.ORDERS_NOTIFICATION_EMAIL],
    )

    email.attach_alternative(html_content, 'text/html')
    email.send(fail_silently=False)

    crear_notificacion_backoffice(
        titulo=f'Nueva orden de compra #{pedido.id}',
        mensaje=f'{pedido.cliente.nombre_empresa} envio una orden con total ${pedido.total}.',
        tipo='PEDIDO',
        url=f'/pedidos/backoffice/{pedido.id}/',
    )


def notificar_cliente_pedido(pedido):
    destinatario = pedido.cliente.usuario.email
    if not destinatario:
        return

    context = construir_contexto_pedido(pedido)
    html_content = render_to_string('emails/pedido_cliente_confirmado.html', context)

    email = EmailMultiAlternatives(
        subject=f"Orden de compra recibida #{pedido.id}",
        body='Tu orden de compra fue recibida correctamente.',
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[destinatario],
    )

    email.attach_alternative(html_content, 'text/html')
    email.send(fail_silently=False)