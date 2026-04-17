from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from config.inventario.services import (
    aplicar_verificacion_picking_inventario,
    reservar_stock_para_pedido_items,
    validar_disponibilidad_para_items,
)
from config.notificaciones.models import crear_notificacion_backoffice, crear_notificacion_usuario

from .models import Pedido, PedidoItem


def _to_decimal(value, default='0'):
    try:
        return Decimal(str(value if value is not None else default))
    except Exception:
        return Decimal(str(default))


def _quantize_money(value):
    return _to_decimal(value, '0').quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def recalcular_pedido(pedido):
    total = Decimal('0.00')
    for item in pedido.items.all():
        item.subtotal = _quantize_money(_to_decimal(item.precio) * Decimal(str(item.cantidad or 0)))
        item.save(update_fields=['subtotal'])
        total += item.subtotal
    pedido.total = _quantize_money(total)
    pedido.save(update_fields=['total', 'actualizada_en'])
    return pedido


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
    bypass_stock_check=False,
    reservar_inventario=True,
):
    if not items_payload:
        raise ValidationError(_('You must add at least one item to create the purchase order.'))

    if reservar_inventario:
        validar_disponibilidad_para_items(items_payload, bypass_stock_check=bypass_stock_check)

    pedido = Pedido.objects.create(
        cliente=cliente,
        vendedor=vendedor,
        cotizacion=cotizacion,
        origen=origen,
        canal_toma=(canal_toma or '').strip(),
        estado='RECIBIDO',
        nota_cliente=(nota_cliente or '').strip(),
        acepta_terminos=bool(acepta_terminos),
        acepta_terminos_en=timezone.now() if acepta_terminos else None,
        total=Decimal('0.00'),
    )

    pedido_items = []
    total = Decimal('0.00')
    for payload in items_payload:
        presentacion = payload['presentacion']
        cantidad = max(int(payload.get('cantidad') or 1), 1)
        precio = _quantize_money(payload.get('precio', 0))
        subtotal = _quantize_money(precio * Decimal(str(cantidad)))
        pedido_items.append(
            PedidoItem(
                pedido=pedido,
                presentacion=presentacion,
                cantidad_solicitada=cantidad,
                cantidad=cantidad,
                precio=precio,
                subtotal=subtotal,
            )
        )
        total += subtotal

    created_items = list(PedidoItem.objects.bulk_create(pedido_items))
    pedido.total = _quantize_money(total)
    pedido.save(update_fields=['total', 'actualizada_en'])

    if reservar_inventario and created_items:
        reservar_stock_para_pedido_items(pedido=pedido, pedido_items=created_items, creado_por=vendedor)

    return pedido


def validar_estado_backoffice_con_bloqueo(pedido, nuevo_estado):
    if pedido.picking_bloqueado and nuevo_estado != pedido.estado:
        raise ValidationError(_('This purchase order is blocked by an unresolved picking note.'))


@transaction.atomic
def asignar_picking_a_seleccionador(*, pedido, seleccionador):
    if getattr(seleccionador, 'role', '') != 'seleccionador':
        raise ValidationError(_('Only selector users can be assigned to a picking ticket.'))
    if pedido.estado not in {'RECIBIDO', 'EN_GESTION', 'LISTO_PARA_PICKING', 'PARA_VERIFICAR'}:
        raise ValidationError(_('This purchase order cannot be assigned to picking in its current status.'))

    pedido.seleccionador = seleccionador
    pedido.estado = 'PARA_VERIFICAR'
    pedido.picking_asignado_en = timezone.now()
    pedido.save(update_fields=['seleccionador', 'estado', 'picking_asignado_en', 'actualizada_en'])

    crear_notificacion_usuario(
        usuario=seleccionador,
        titulo=_('Picking ticket assigned for PO #%(id)s') % {'id': pedido.id},
        mensaje=_('You have a new picking ticket assigned for %(client)s.') % {'client': pedido.cliente.nombre_empresa},
        tipo='PEDIDO',
        url=reverse('selector_picking_detail', args=[pedido.id]),
    )

    return pedido


@transaction.atomic
def guardar_verificacion_picking(*, pedido, seleccionador, cantidades_reales, nota, nota_resuelta):
    if pedido.seleccionador_id != seleccionador.id:
        raise PermissionDenied(_('You are not assigned to this picking ticket.'))

    nota_texto = (nota or '').strip()
    if not nota_texto:
        raise ValidationError(_('A picking note is required before saving the verification.'))

    items = list(pedido.items.select_for_update().select_related('presentacion__producto').all())
    for item in items:
        cantidad_real = max(int(cantidades_reales.get(item.id, item.cantidad) or 0), 0)
        item.cantidad = cantidad_real
        item.subtotal = _quantize_money(_to_decimal(item.precio) * Decimal(str(cantidad_real)))
        item.save(update_fields=['cantidad', 'subtotal'])

    aplicar_verificacion_picking_inventario(
        pedido=pedido,
        pedido_item_ids=[item.id for item in items],
        creado_por=seleccionador,
    )

    recalcular_pedido(pedido)
    pedido.estado = 'VERIFICADO_AJUSTADO'
    pedido.nota_seleccionador = nota_texto
    pedido.nota_seleccionador_resuelta = bool(nota_resuelta)
    pedido.picking_verificado_en = timezone.now()
    pedido.save(update_fields=[
        'estado',
        'nota_seleccionador',
        'nota_seleccionador_resuelta',
        'picking_verificado_en',
        'picking_bloqueado',
        'actualizada_en',
    ])

    crear_notificacion_backoffice(
        titulo=_('Picking verification completed for PO #%(id)s') % {'id': pedido.id},
        mensaje=_('%(selector)s completed the picking verification for %(client)s.') % {
            'selector': seleccionador.get_full_name() or seleccionador.username,
            'client': pedido.cliente.nombre_empresa,
        },
        tipo='PEDIDO',
        url=reverse('backoffice_pedido_detalle', args=[pedido.id]),
    )

    return pedido


def notificar_backoffice_pedido(pedido):
    crear_notificacion_backoffice(
        titulo=_('New purchase order #%(id)s') % {'id': pedido.id},
        mensaje=_('%(client)s submitted a new purchase order.') % {'client': pedido.cliente.nombre_empresa},
        tipo='PEDIDO',
        url=reverse('backoffice_pedido_detalle', args=[pedido.id]),
    )

    user_model = get_user_model()
    backoffice_emails = list(
        user_model.objects.filter(role__in=['admin', 'backoffice'], is_active=True)
        .exclude(email='')
        .values_list('email', flat=True)
        .distinct()
    )
    if not backoffice_emails:
        return

    html_content = render_to_string(
        'emails/pedido_backoffice.html',
        {
            'pedido': pedido,
            'cliente': pedido.cliente,
            'items': pedido.items.select_related('presentacion__producto').all(),
        },
    )
    text_content = _('A new purchase order #%(id)s was created for %(client)s.') % {
        'id': pedido.id,
        'client': pedido.cliente.nombre_empresa,
    }
    email = EmailMultiAlternatives(
        subject=_('New purchase order #%(id)s') % {'id': pedido.id},
        body=text_content,
        from_email=settings.DEFAULT_FROM_EMAIL or settings.SERVER_EMAIL,
        to=backoffice_emails,
    )
    email.attach_alternative(html_content, 'text/html')
    email.send(fail_silently=False)


def notificar_cliente_pedido(pedido):
    cliente_email = getattr(getattr(pedido.cliente, 'usuario', None), 'email', '')
    if not cliente_email:
        return

    html_content = render_to_string(
        'emails/pedido_cliente_confirmado.html',
        {
            'pedido': pedido,
            'cliente': pedido.cliente,
            'items': pedido.items.select_related('presentacion__producto').all(),
        },
    )
    text_content = _('Your purchase order #%(id)s was received successfully.') % {'id': pedido.id}
    email = EmailMultiAlternatives(
        subject=_('Purchase order received #%(id)s') % {'id': pedido.id},
        body=text_content,
        from_email=settings.DEFAULT_FROM_EMAIL or settings.SERVER_EMAIL,
        to=[cliente_email],
    )
    email.attach_alternative(html_content, 'text/html')
    email.send(fail_silently=False)
