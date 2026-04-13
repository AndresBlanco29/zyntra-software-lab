import logging
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import gettext as _

from config.notificaciones.models import crear_notificacion_backoffice, crear_notificacion_usuario
from config.inventario.services import aplicar_verificacion_picking_inventario, reservar_stock_para_pedido_items, validar_disponibilidad_para_items

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
    if getattr(cliente, 'credit_hold', False):
        raise ValidationError(_('This customer is blocked for new purchases until BackOffice removes the hold.'))

    validar_disponibilidad_para_items(items_payload)

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
    created_items = []

    for item in items_payload:
        presentacion = item['presentacion']
        cantidad = max(int(item['cantidad']), 1)
        precio = _to_decimal(item['precio'])
        subtotal = precio * cantidad

        pedido_item = PedidoItem.objects.create(
            pedido=pedido,
            presentacion=presentacion,
            cantidad_solicitada=cantidad,
            cantidad=cantidad,
            precio=precio,
            subtotal=subtotal,
        )
        created_items.append(pedido_item)

        total += subtotal

    pedido.total = total
    pedido.save(update_fields=['total'])
    reservar_stock_para_pedido_items(pedido=pedido, pedido_items=created_items, creado_por=vendedor)
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


def _validate_selector_user(usuario):
    if not usuario or not getattr(usuario, 'is_active', False) or getattr(usuario, 'role', '') != 'seleccionador':
        raise ValidationError(_('The selected user is not a valid selector.'))


def validar_estado_backoffice_con_bloqueo(pedido, nuevo_estado):
    if pedido.estado == 'CANCELADO' and nuevo_estado != 'CANCELADO':
        raise ValidationError(_('Cancelled orders cannot be reactivated. Create a new order instead.'))
    if pedido.picking_bloqueado and nuevo_estado not in {'PARA_VERIFICAR', 'VERIFICADO_AJUSTADO'}:
        raise ValidationError(_('This order is locked by an unresolved selector note. Resolve it before moving to another status.'))


@transaction.atomic
def asignar_picking_a_seleccionador(*, pedido, seleccionador):
    _validate_selector_user(seleccionador)

    if pedido.estado in {'DESPACHADO', 'CANCELADO'}:
        raise ValidationError(_('Only active orders can be sent to selector verification.'))

    items = list(pedido.items.select_related('presentacion__producto').all())
    if not items:
        raise ValidationError(_('The order must contain at least one product before sending the picking ticket.'))

    timestamp = timezone.now()

    pedido.seleccionador = seleccionador
    pedido.estado = 'PARA_VERIFICAR'
    pedido.picking_asignado_en = timestamp
    pedido.picking_verificado_en = None
    pedido.nota_seleccionador = ''
    pedido.nota_seleccionador_resuelta = False
    pedido.picking_bloqueado = False
    pedido.save(update_fields=[
        'seleccionador',
        'estado',
        'picking_asignado_en',
        'picking_verificado_en',
        'nota_seleccionador',
        'nota_seleccionador_resuelta',
        'picking_bloqueado',
        'actualizada_en',
    ])

    for item in items:
        item.cantidad_solicitada = item.cantidad
        item.save(update_fields=['cantidad_solicitada'])

    crear_notificacion_usuario(
        usuario=seleccionador,
        titulo=f'{_("New picking ticket assigned")} #{pedido.id}',
        mensaje=_("You received a picking ticket for %(customer)s.") % {'customer': pedido.cliente.nombre_empresa},
        tipo='PEDIDO',
        url=f'/pedidos/seleccionador/picking/{pedido.id}/',
    )
    return pedido


@transaction.atomic
def guardar_verificacion_picking(*, pedido, seleccionador, cantidades_reales, nota, nota_resuelta):
    if pedido.seleccionador_id != getattr(seleccionador, 'id', None):
        raise PermissionDenied(_('You can only verify picking tickets assigned to you.'))

    if pedido.estado not in {'PARA_VERIFICAR', 'VERIFICADO_AJUSTADO'}:
        raise ValidationError(_('This picking ticket is not available for verification.'))

    nota_limpia = (nota or '').strip()
    if not nota_limpia:
        raise ValidationError(_('A selector note is required before saving the verification.'))

    total = Decimal('0')
    items = list(pedido.items.select_for_update().select_related('presentacion__producto'))
    for item in items:
        if item.id not in cantidades_reales:
            raise ValidationError(_('Every product must include a verified quantity.'))

        cantidad_real = cantidades_reales[item.id]
        if cantidad_real < 0:
            raise ValidationError(_('Verified quantities cannot be negative.'))

        if not item.cantidad_solicitada:
            item.cantidad_solicitada = item.cantidad

        item.cantidad = cantidad_real
        item.subtotal = _to_decimal(item.precio) * cantidad_real
        item.save(update_fields=['cantidad_solicitada', 'cantidad', 'subtotal'])
        total += item.subtotal

    pedido.total = total
    pedido.estado = 'VERIFICADO_AJUSTADO'
    pedido.nota_seleccionador = nota_limpia
    pedido.nota_seleccionador_resuelta = bool(nota_resuelta)
    pedido.picking_bloqueado = bool(nota_limpia and not nota_resuelta)
    pedido.picking_verificado_en = timezone.now()
    pedido.save(update_fields=[
        'total',
        'estado',
        'nota_seleccionador',
        'nota_seleccionador_resuelta',
        'picking_bloqueado',
        'picking_verificado_en',
        'actualizada_en',
    ])

    aplicar_verificacion_picking_inventario(
        pedido=pedido,
        pedido_item_ids=[item.id for item in items],
        creado_por=seleccionador,
    )

    crear_notificacion_backoffice(
        titulo=f'{_("Picking verification completed")} #{pedido.id}',
        mensaje=_("%(selector)s finished the picking verification for %(customer)s.") % {
            'selector': seleccionador.get_full_name() or seleccionador.username,
            'customer': pedido.cliente.nombre_empresa,
        },
        tipo='PEDIDO',
        url=f'/pedidos/backoffice/{pedido.id}/',
    )
    return pedido


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