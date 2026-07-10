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
from datetime import timedelta

from config.inventario.services import (
    aplicar_verificacion_picking_inventario,
    eliminar_item_pedido_con_inventario,
    reemplazar_presentacion_item_pedido,
    reservar_stock_para_pedido_items,
    validar_disponibilidad_para_items,
)
from config.inventario.models import StockPresentacion
from config.notificaciones.models import crear_notificacion_backoffice, crear_notificacion_usuario
from config.productos.models import Presentacion

from .models import Pedido, PedidoEditLock, PedidoItem


PEDIDO_EDIT_LOCK_TIMEOUT = timedelta(minutes=5)


def _to_decimal(value, default='0'):
    try:
        return Decimal(str(value if value is not None else default))
    except Exception:
        return Decimal(str(default))


def _quantize_money(value):
    return _to_decimal(value, '0').quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _resolve_pedido_item_price(*, pedido, presentacion):
    cliente = getattr(pedido, 'cliente', None)
    tier = cliente.get_nivel_precio_normalizado() if cliente and hasattr(cliente, 'get_nivel_precio_normalizado') else None
    price = presentacion.get_price_for_tier(tier) if tier is not None else None
    if price is None:
        price = presentacion.precio_1
    return _quantize_money(price or 0)


def calcular_precio_unitario_neto_item(*, precio, descuento_aplicado=False, descuento_monto=0):
    precio_decimal = _quantize_money(precio)
    if not descuento_aplicado:
        return precio_decimal
    descuento = _quantize_money(descuento_monto)
    return _quantize_money(max(Decimal('0.00'), precio_decimal - descuento))


def calcular_subtotal_item_pedido(*, precio, cantidad, descuento_aplicado=False, descuento_monto=0):
    net_unit_price = calcular_precio_unitario_neto_item(
        precio=precio,
        descuento_aplicado=descuento_aplicado,
        descuento_monto=descuento_monto,
    )
    return _quantize_money(net_unit_price * Decimal(str(cantidad or 0)))


def normalizar_descuento_item_pedido(*, precio, descuento_aplicado, descuento_monto):
    aplicado = bool(descuento_aplicado)
    monto = _quantize_money(descuento_monto if aplicado else 0)
    precio_decimal = _quantize_money(precio)
    if aplicado and monto > precio_decimal:
        raise ValidationError(_('Discount cannot be greater than the unit price.'))
    return aplicado, monto


def recalcular_pedido(pedido):
    total = Decimal('0.00')
    for item in PedidoItem.objects.filter(pedido=pedido):
        item.subtotal = calcular_subtotal_item_pedido(
            precio=item.precio,
            cantidad=item.cantidad,
            descuento_aplicado=item.descuento_aplicado,
            descuento_monto=item.descuento_monto,
        )
        item.save(update_fields=['subtotal'])
        total += item.subtotal
    pedido.total = _quantize_money(total)
    pedido.save(update_fields=['total', 'actualizada_en'])
    return pedido


def actualizar_cantidad_linea_pedido_sin_aplicar_inventario(*, item, nueva_cantidad):
    objetivo = max(int(nueva_cantidad), 0)
    item.cantidad = objetivo
    item.save(update_fields=['cantidad'])
    return item


def reemplazar_presentacion_linea_pedido_sin_aplicar_inventario(*, item, nueva_presentacion):
    if nueva_presentacion.producto_id != item.presentacion.producto_id:
        raise ValidationError(_('Unit of measure can only be changed to another presentation of the same product.'))
    item.presentacion = nueva_presentacion
    item.save(update_fields=['presentacion'])
    return item


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
    request=None,
):
    if not items_payload:
        raise ValidationError(_('You must add at least one item to create the sales order.'))

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
        descuento_aplicado, descuento_monto = normalizar_descuento_item_pedido(
            precio=precio,
            descuento_aplicado=payload.get('descuento_aplicado', False),
            descuento_monto=payload.get('descuento_monto', 0),
        )
        subtotal = calcular_subtotal_item_pedido(
            precio=precio,
            cantidad=cantidad,
            descuento_aplicado=descuento_aplicado,
            descuento_monto=descuento_monto,
        )
        pedido_items.append(
            PedidoItem(
                pedido=pedido,
                presentacion=presentacion,
                cantidad_solicitada=cantidad,
                cantidad=cantidad,
                precio=precio,
                descuento_aplicado=descuento_aplicado,
                descuento_monto=descuento_monto,
                subtotal=subtotal,
            )
        )
        total += subtotal

    created_items = list(PedidoItem.objects.bulk_create(pedido_items))
    if created_items and any(item.pk is None for item in created_items):
        created_items = list(PedidoItem.objects.filter(pedido=pedido).order_by('id'))

    pedido.total = _quantize_money(total)
    pedido.save(update_fields=['total', 'actualizada_en'])

    if reservar_inventario and created_items:
        reservar_stock_para_pedido_items(pedido=pedido, pedido_items=created_items, creado_por=vendedor)

    from config.auditoria.business_events import log_business_event
    from config.auditoria.models import AuditLog

    log_business_event(
        vendedor,
        action_label=_('Created sales order #%(id)s for %(client)s') % {
            'id': pedido.id,
            'client': cliente.nombre_empresa,
        },
        action_category=AuditLog.CATEGORY_CREATE,
        entity_type='Pedido',
        entity_id=str(pedido.id),
        entity_label=_('Order #%(id)s - %(client)s') % {'id': pedido.id, 'client': cliente.nombre_empresa},
        metadata={
            'origen': origen,
            'canal_toma': canal_toma,
            'total': str(pedido.total),
            'items_count': len(created_items),
            'line_items': [
                {
                    'presentacion_id': item.presentacion_id,
                    'cantidad': item.cantidad,
                    'precio': str(item.precio),
                    'descuento_aplicado': bool(item.descuento_aplicado),
                    'descuento_monto': str(item.descuento_monto),
                }
                for item in created_items
            ],
        },
        request=request,
    )

    return pedido


def validar_estado_backoffice_con_bloqueo(pedido, nuevo_estado):
    if pedido.picking_bloqueado and nuevo_estado != pedido.estado:
        raise ValidationError(_('This sales order is blocked by an unresolved picking note.'))


def _pedido_edit_lock_display_name(user):
    if not user:
        return ''
    return (user.get_full_name() or '').strip() or user.username


def _pedido_edit_lock_is_stale(lock):
    return timezone.now() - lock.last_seen_at > PEDIDO_EDIT_LOCK_TIMEOUT


def get_active_pedido_edit_lock(pedido):
    try:
        lock = PedidoEditLock.objects.select_related('locked_by').get(pedido=pedido)
    except PedidoEditLock.DoesNotExist:
        return None
    if _pedido_edit_lock_is_stale(lock):
        lock.delete()
        return None
    return lock


@transaction.atomic
def acquire_pedido_edit_lock(*, pedido, user):
    now = timezone.now()
    lock = (
        PedidoEditLock.objects.select_for_update()
        .select_related('locked_by')
        .filter(pedido=pedido)
        .first()
    )
    if lock is None:
        return PedidoEditLock.objects.create(pedido=pedido, locked_by=user, last_seen_at=now)

    if lock.locked_by_id == user.id:
        lock.last_seen_at = now
        lock.save(update_fields=['last_seen_at'])
        return lock

    if _pedido_edit_lock_is_stale(lock):
        lock.locked_by = user
        lock.locked_at = now
        lock.last_seen_at = now
        lock.save(update_fields=['locked_by', 'locked_at', 'last_seen_at'])
        return lock

    return lock


def refresh_pedido_edit_lock(*, pedido, user):
    lock = get_active_pedido_edit_lock(pedido)
    if not lock:
        return acquire_pedido_edit_lock(pedido=pedido, user=user)
    if lock.locked_by_id != user.id:
        editor_name = _pedido_edit_lock_display_name(getattr(lock, 'locked_by', None))
        raise ValidationError(
            _('This sales order is currently being edited by %(user)s.') % {'user': editor_name or _('another user')}
        )
    lock.last_seen_at = timezone.now()
    lock.save(update_fields=['last_seen_at'])
    return lock


def release_pedido_edit_lock(*, pedido=None, pedido_id=None, user):
    resolved_pedido_id = pedido_id if pedido_id is not None else getattr(pedido, 'pk', None)
    if not resolved_pedido_id:
        return
    PedidoEditLock.objects.filter(pedido_id=resolved_pedido_id, locked_by=user).delete()


def user_holds_pedido_edit_lock(*, pedido, user):
    lock = get_active_pedido_edit_lock(pedido)
    return bool(lock and lock.locked_by_id == user.id)


def ensure_pedido_edit_lock_owner(*, pedido, user):
    lock = get_active_pedido_edit_lock(pedido)
    if lock and lock.locked_by_id != user.id:
        raise ValidationError(
            _('This sales order is currently being edited by %(user)s.') % {
                'user': _pedido_edit_lock_display_name(lock.locked_by),
            }
        )
    if not lock:
        acquire_pedido_edit_lock(pedido=pedido, user=user)


def build_pedido_edit_lock_context(*, pedido, user):
    blocked = False
    blocked_by = ''
    holds_lock = False

    if not user or not user.is_authenticated:
        return {
            'pedido_edit_blocked': blocked,
            'pedido_edit_blocked_by': blocked_by,
            'pedido_edit_holds_lock': holds_lock,
        }

    if user.has_internal_permission('backoffice.orders.manage'):
        lock = acquire_pedido_edit_lock(pedido=pedido, user=user)
        holds_lock = lock.locked_by_id == user.id
        if not holds_lock:
            blocked = True
            blocked_by = _pedido_edit_lock_display_name(lock.locked_by)
    else:
        lock = get_active_pedido_edit_lock(pedido)
        if lock:
            blocked = True
            blocked_by = _pedido_edit_lock_display_name(lock.locked_by)

    return {
        'pedido_edit_blocked': blocked,
        'pedido_edit_blocked_by': blocked_by,
        'pedido_edit_holds_lock': holds_lock,
    }


def puede_anular_pedido_desde_backoffice(pedido):
    return pedido.estado != 'CANCELADO' and not hasattr(pedido, 'invoice')


def puede_eliminar_pedido_desde_backoffice(pedido):
    if hasattr(pedido, 'invoice'):
        return False
    return not PedidoItem.objects.filter(pedido=pedido, cantidad_inventario_aplicada__gt=0).exists()


@transaction.atomic
def anular_pedido_desde_backoffice(*, pedido, usuario=None):
    if not puede_anular_pedido_desde_backoffice(pedido):
        raise ValidationError(_('This sales order cannot be voided.'))
    pedido.estado = 'CANCELADO'
    pedido.save(update_fields=['estado', 'actualizada_en'])
    if usuario is not None:
        from config.auditoria.business_events import log_business_event
        from config.auditoria.models import AuditLog
        log_business_event(
            usuario,
            action_label=_('Voided sales order #%(id)s') % {'id': pedido.id},
            action_category=AuditLog.CATEGORY_DELETE,
            entity_type='Pedido',
            entity_id=str(pedido.id),
            entity_label=_('Order #%(id)s') % {'id': pedido.id},
        )
    return pedido


@transaction.atomic
def eliminar_pedido_desde_backoffice(*, pedido):
    if not puede_eliminar_pedido_desde_backoffice(pedido):
        raise ValidationError(
            _('This sales order cannot be deleted because it has an invoice or picking already affected inventory.')
        )
    pedido.delete()


@transaction.atomic
def eliminar_linea_pedido_desde_backoffice(*, item, creado_por=None):
    if int(item.cantidad_inventario_aplicada or 0) > 0:
        eliminar_item_pedido_con_inventario(item=item, creado_por=creado_por)
        return
    item.delete()


def evaluar_stock_fisico_verificacion_picking(*, pedido_items, cantidades_reales):
    stock_map = {
        stock.presentacion_id: stock
        for stock in StockPresentacion.objects.select_related('presentacion__producto').filter(
            presentacion_id__in={item.presentacion_id for item in pedido_items}
        )
    }
    evaluation = {}
    for item in pedido_items:
        stock = stock_map.get(item.presentacion_id)
        cantidad_real = max(int(cantidades_reales.get(item.id, item.cantidad) or 0), 0)
        cantidad_aplicada_previa = max(int(item.cantidad_inventario_aplicada or 0), 0)
        cantidad_pendiente_aplicar = max(cantidad_real - cantidad_aplicada_previa, 0)
        reserved_packages_for_item = max(int(item.cantidad_reservada_inventario or 0), 0)
        stock_fisico = int(getattr(stock, 'stock_fisico', 0) or 0)
        stock_reservado = int(getattr(stock, 'stock_reservado', 0) or 0)
        if stock is not None:
            available_packages = stock.packages_available_for_picking(reserved_packages_for_item)
            stock_disponible = stock.computed_stock_disponible()
        else:
            available_packages = 0
            stock_reservado = 0
            stock_disponible = 0
        shortage_packages = max(cantidad_pendiente_aplicar - available_packages, 0)

        evaluation[item.id] = {
            'units_per_package': max(int(getattr(item.presentacion, 'unidades', 0) or 0), 1),
            'stock_fisico': stock_fisico,
            'stock_disponible': stock_disponible,
            'stock_reservado': stock_reservado,
            'available_packages': available_packages,
            'cantidad_real': cantidad_real,
            'cantidad_aplicada_previa': cantidad_aplicada_previa,
            'cantidad_pendiente_aplicar': cantidad_pendiente_aplicar,
            'has_shortage': shortage_packages > 0,
            'shortage_amount': shortage_packages,
        }
    return evaluation


@transaction.atomic
def asignar_picking_a_seleccionador(*, pedido, seleccionador, asignado_por=None):
    if getattr(seleccionador, 'role', '') != 'seleccionador':
        raise ValidationError(_('Only selector users can be assigned to a picking ticket.'))
    if pedido.estado not in {'RECIBIDO', 'EN_GESTION', 'LISTO_PARA_PICKING', 'PARA_VERIFICAR'}:
        raise ValidationError(_('This sales order cannot be assigned to picking in its current status.'))

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

    from config.auditoria.business_events import log_business_event
    from config.auditoria.models import AuditLog

    log_business_event(
        asignado_por or seleccionador,
        action_label=_('Assigned picking for order #%(id)s to %(selector)s') % {
            'id': pedido.id,
            'selector': seleccionador.get_full_name() or seleccionador.username,
        },
        action_category=AuditLog.CATEGORY_ACTION,
        entity_type='Pedido',
        entity_id=str(pedido.id),
        entity_label=_('Order #%(id)s') % {'id': pedido.id},
        metadata={'selector_id': seleccionador.id, 'estado': pedido.estado},
    )

    return pedido


def resolve_picking_send_ui_state(pedido):
    if hasattr(pedido, 'invoice') or pedido.estado in {'INVOICE_GENERADA', 'DESPACHADO', 'CANCELADO'}:
        return False, _('Sent')
    if pedido.estado == 'VERIFICADO_AJUSTADO':
        return False, _('Picking completed')
    if pedido.estado == 'PARA_VERIFICAR' and pedido.seleccionador_id:
        return False, _('Sent to picker')
    if pedido.estado in {'RECIBIDO', 'EN_GESTION', 'LISTO_PARA_PICKING', 'PARA_VERIFICAR'}:
        return True, _('Send picking')
    return False, _('Sent')


@transaction.atomic
def guardar_verificacion_picking(
    *,
    pedido,
    seleccionador,
    cantidades_reales,
    nota,
    nota_resuelta,
    presentacion_updates=None,
    additional_items=None,
):
    if pedido.seleccionador_id != seleccionador.id:
        raise PermissionDenied(_('You are not assigned to this picking ticket.'))

    nota_texto = (nota or '').strip()
    presentacion_updates = presentacion_updates or {}
    additional_items = additional_items or []

    items = list(pedido.items.select_related('presentacion__producto').all())

    for item in items:
        nueva_presentacion_id = presentacion_updates.get(item.id)
        if not nueva_presentacion_id or nueva_presentacion_id == item.presentacion_id:
            continue

        nueva_presentacion = Presentacion.objects.select_related('producto').get(id=nueva_presentacion_id)
        original_presentacion_id = item.selector_original_presentacion_id or item.presentacion_id
        item = reemplazar_presentacion_item_pedido(item=item, nueva_presentacion=nueva_presentacion, creado_por=seleccionador)
        item.selector_original_presentacion_id = original_presentacion_id
        item.precio = _resolve_pedido_item_price(pedido=pedido, presentacion=nueva_presentacion)
        item.subtotal = _quantize_money(item.precio * Decimal(str(item.cantidad or 0)))
        item.save(update_fields=['selector_original_presentacion', 'precio', 'subtotal'])

    nuevos_items_creados = []
    for payload in additional_items:
        presentacion = Presentacion.objects.select_related('producto').get(id=payload['presentacion_id'])
        cantidad = max(int(payload.get('cantidad') or 0), 1)
        precio = _resolve_pedido_item_price(pedido=pedido, presentacion=presentacion)
        nuevo_item = PedidoItem.objects.create(
            pedido=pedido,
            presentacion=presentacion,
            selector_added_by_picker=True,
            cantidad_solicitada=cantidad,
            cantidad=cantidad,
            precio=precio,
            subtotal=_quantize_money(precio * Decimal(str(cantidad))),
        )
        reservar_stock_para_pedido_items(pedido=pedido, pedido_items=[nuevo_item], creado_por=seleccionador)
        nuevos_items_creados.append(nuevo_item)

    items = list(pedido.items.select_for_update().select_related('presentacion__producto').all())
    stock_evaluation = evaluar_stock_fisico_verificacion_picking(
        pedido_items=items,
        cantidades_reales=cantidades_reales,
    )
    has_stock_shortage = any(item_result['has_shortage'] for item_result in stock_evaluation.values())

    if has_stock_shortage:
        if not nota_texto:
            raise ValidationError(_('A picking note is required when physical stock is insufficient.'))
        nota_resuelta = False
    elif not nota_resuelta:
        raise ValidationError(_('Picker approval is required when physical stock is available.'))

    for item in items:
        cantidad_real = max(int(cantidades_reales.get(item.id, item.cantidad) or 0), 0)
        item.cantidad = cantidad_real
        item.subtotal = _quantize_money(_to_decimal(item.precio) * Decimal(str(cantidad_real)))
        item.save(update_fields=['cantidad', 'subtotal'])

    if not has_stock_shortage:
        aplicar_verificacion_picking_inventario(
            pedido=pedido,
            pedido_item_ids=[item.id for item in items],
            creado_por=seleccionador,
        )

    recalcular_pedido(pedido)
    pedido.estado = 'VERIFICADO_AJUSTADO'
    pedido.nota_seleccionador = nota_texto
    pedido.nota_seleccionador_resuelta = bool(nota_resuelta) and not has_stock_shortage
    pedido.picking_verificado_en = timezone.now()
    pedido.save(update_fields=[
        'estado',
        'nota_seleccionador',
        'nota_seleccionador_resuelta',
        'picking_verificado_en',
        'picking_bloqueado',
        'actualizada_en',
    ])

    if has_stock_shortage:
        crear_notificacion_backoffice(
            titulo=_('Picking verification saved with stock shortage for PO #%(id)s') % {'id': pedido.id},
            mensaje=_('%(selector)s reported insufficient physical stock for %(client)s. The order remains blocked for review.') % {
                'selector': seleccionador.get_full_name() or seleccionador.username,
                'client': pedido.cliente.nombre_empresa,
            },
            tipo='PEDIDO',
            url=reverse('backoffice_pedido_detalle', args=[pedido.id]),
        )
    else:
        movement_message = ''
        if nuevos_items_creados or any(item.selector_changed_presentation for item in items):
            movement_message = ' ' + _('BackOffice should review the picker changes highlighted on the order detail.')
        crear_notificacion_backoffice(
            titulo=_('Picking verification completed for PO #%(id)s') % {'id': pedido.id},
            mensaje=(
                _('%(selector)s completed the picking verification for %(client)s.') % {
                    'selector': seleccionador.get_full_name() or seleccionador.username,
                    'client': pedido.cliente.nombre_empresa,
                }
            ) + movement_message,
            tipo='PEDIDO',
            url=reverse('backoffice_pedido_detalle', args=[pedido.id]),
        )

    from config.auditoria.business_events import log_business_event
    from config.auditoria.models import AuditLog

    log_business_event(
        seleccionador,
        action_label=_('Completed picking verification for order #%(id)s') % {'id': pedido.id},
        action_category=AuditLog.CATEGORY_UPDATE,
        entity_type='Pedido',
        entity_id=str(pedido.id),
        entity_label=_('Order #%(id)s - %(client)s') % {'id': pedido.id, 'client': pedido.cliente.nombre_empresa},
        metadata={
            'estado': pedido.estado,
            'has_stock_shortage': has_stock_shortage,
            'nota_resuelta': pedido.nota_seleccionador_resuelta,
        },
    )

    return pedido


@transaction.atomic
def resolver_bloqueo_picking_desde_backoffice(*, pedido, usuario):
    if hasattr(pedido, 'invoice'):
        raise ValidationError(_('Orders with a generated invoice cannot be unlocked.'))
    if not pedido.picking_bloqueado:
        raise ValidationError(_('This order is not blocked.'))
    if pedido.estado != 'VERIFICADO_AJUSTADO':
        raise ValidationError(_('Picking must be verified before unlocking the order.'))

    pedido.nota_seleccionador_resuelta = True
    pedido.save(update_fields=['nota_seleccionador_resuelta', 'picking_bloqueado', 'actualizada_en'])

    from config.auditoria.business_events import log_business_event
    from config.auditoria.models import AuditLog

    log_business_event(
        usuario,
        action_label=_('Unlocked picking block for order #%(id)s') % {'id': pedido.id},
        action_category=AuditLog.CATEGORY_UPDATE,
        entity_type='Pedido',
        entity_id=str(pedido.id),
        entity_label=_('Order #%(id)s') % {'id': pedido.id},
        metadata={'inventory_applied_on_unlock': False},
    )

    return pedido


def notificar_backoffice_pedido(pedido):
    from config.core.email_branding import attach_inline_brand_logo, brand_email_context

    if pedido.origen == 'VENDEDOR':
        vendor_name = ''
        if pedido.vendedor_id:
            vendor_name = (pedido.vendedor.get_full_name() or '').strip() or pedido.vendedor.username
        mensaje = _('%(vendor)s created a new order for %(client)s.') % {
            'vendor': vendor_name or _('A vendor'),
            'client': pedido.cliente.nombre_empresa,
        }
    else:
        mensaje = _('%(client)s submitted a new order.') % {'client': pedido.cliente.nombre_empresa}

    crear_notificacion_backoffice(
        titulo=_('New order #%(id)s') % {'id': pedido.id},
        mensaje=mensaje,
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
            **brand_email_context(),
        },
    )
    text_content = _('A new order #%(id)s was created for %(client)s.') % {
        'id': pedido.id,
        'client': pedido.cliente.nombre_empresa,
    }
    email = EmailMultiAlternatives(
        subject=_('New order #%(id)s') % {'id': pedido.id},
        body=text_content,
        from_email=settings.DEFAULT_FROM_EMAIL or settings.SERVER_EMAIL,
        to=backoffice_emails,
    )
    email.attach_alternative(html_content, 'text/html')
    attach_inline_brand_logo(email)
    email.send(fail_silently=False)


def notificar_cliente_pedido(pedido, *, include_prices=True):
    from config.core.email_branding import attach_inline_brand_logo, brand_email_context

    cliente_email = (getattr(getattr(pedido.cliente, 'usuario', None), 'email', '') or '').strip()
    if not cliente_email:
        return False

    html_content = render_to_string(
        'emails/pedido_cliente_confirmado.html',
        {
            'pedido': pedido,
            'cliente': pedido.cliente,
            'items': pedido.items.select_related('presentacion__producto').all(),
            'include_prices': include_prices,
            'total': pedido.total,
            **brand_email_context(),
        },
    )
    text_content = _('Your sales order #%(id)s was generated successfully and is now being prepared for dispatch.') % {
        'id': pedido.id,
    }
    email = EmailMultiAlternatives(
        subject=_('Sales order in process #%(id)s') % {'id': pedido.id},
        body=text_content,
        from_email=settings.DEFAULT_FROM_EMAIL or settings.SERVER_EMAIL,
        to=[cliente_email],
    )
    email.attach_alternative(html_content, 'text/html')
    attach_inline_brand_logo(email)
    email.send(fail_silently=False)
    return True
