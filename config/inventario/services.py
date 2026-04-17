from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils.translation import gettext as _

from config.pedidos.models import PedidoItem

from .models import InventarioMovimiento, StockPresentacion


def _lock_stock_records(presentacion_ids):
    presentacion_ids = list(presentacion_ids)
    stock_map = {}
    for presentacion_id in sorted(set(presentacion_ids)):
        try:
            StockPresentacion.objects.get_or_create(
                presentacion_id=presentacion_id,
                defaults={
                    'stock_fisico': 0,
                    'stock_reservado': 0,
                    'stock_disponible': 0,
                },
            )
        except IntegrityError:
            pass
    for stock in StockPresentacion.objects.select_for_update().select_related('presentacion__producto').filter(
        presentacion_id__in=presentacion_ids
    ).order_by('presentacion_id'):
        stock_map[stock.presentacion_id] = stock
    return stock_map


def _apply_inventory_change(
    *,
    stock,
    categoria,
    tipo,
    cantidad,
    delta_fisico,
    delta_reservado,
    referencia,
    idempotency_key=None,
    observacion='',
    pedido=None,
    pedido_item=None,
    invoice=None,
    nota_ajuste=None,
    nota_ajuste_item=None,
    creado_por=None,
):
    if idempotency_key:
        existing = InventarioMovimiento.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            return existing

    before_fisico = stock.stock_fisico
    before_reservado = stock.stock_reservado
    before_disponible = stock.stock_disponible
    after_fisico = before_fisico + delta_fisico
    after_reservado = before_reservado + delta_reservado
    after_disponible = after_fisico - after_reservado

    if after_fisico < 0:
        raise ValidationError(
            _('Insufficient physical stock for %(product)s - %(presentation)s.') % {
                'product': stock.presentacion.producto.nombre,
                'presentation': stock.presentacion.nombre,
            }
        )
    if after_reservado < 0:
        raise ValidationError(_('Reserved stock cannot be negative.'))
    if after_disponible < 0:
        raise ValidationError(
            _('Insufficient available stock for %(product)s - %(presentation)s.') % {
                'product': stock.presentacion.producto.nombre,
                'presentation': stock.presentacion.nombre,
            }
        )

    stock.stock_fisico = after_fisico
    stock.stock_reservado = after_reservado
    stock.stock_disponible = after_disponible
    stock.save(update_fields=['stock_fisico', 'stock_reservado', 'stock_disponible', 'actualizado_en'])

    return InventarioMovimiento.objects.create(
        presentacion=stock.presentacion,
        stock=stock,
        categoria=categoria,
        tipo=tipo,
        cantidad=cantidad,
        delta_fisico=delta_fisico,
        delta_reservado=delta_reservado,
        stock_fisico_anterior=before_fisico,
        stock_fisico_posterior=after_fisico,
        stock_reservado_anterior=before_reservado,
        stock_reservado_posterior=after_reservado,
        stock_disponible_anterior=before_disponible,
        stock_disponible_posterior=after_disponible,
        referencia=referencia,
        idempotency_key=idempotency_key,
        observacion=observacion,
        pedido=pedido,
        pedido_item=pedido_item,
        invoice=invoice,
        nota_ajuste=nota_ajuste,
        nota_ajuste_item=nota_ajuste_item,
        creado_por=creado_por,
    )


@transaction.atomic
def registrar_entrada_manual(*, presentacion, cantidad, observacion='', creado_por=None):
    cantidad = max(int(cantidad or 0), 1)
    stock = _lock_stock_records([presentacion.id])[presentacion.id]
    return _apply_inventory_change(
        stock=stock,
        categoria='ENTRADA',
        tipo='ENTRADA_MANUAL',
        cantidad=cantidad,
        delta_fisico=cantidad,
        delta_reservado=0,
        referencia=f'STOCK-{presentacion.id}',
        idempotency_key=None,
        observacion=observacion,
        creado_por=creado_por,
    )


@transaction.atomic
def registrar_salida_manual(*, presentacion, cantidad, observacion='', creado_por=None):
    cantidad = max(int(cantidad or 0), 1)
    stock = _lock_stock_records([presentacion.id])[presentacion.id]
    return _apply_inventory_change(
        stock=stock,
        categoria='SALIDA',
        tipo='SALIDA_MANUAL',
        cantidad=cantidad,
        delta_fisico=-cantidad,
        delta_reservado=0,
        referencia=f'STOCK-{presentacion.id}',
        idempotency_key=None,
        observacion=observacion,
        creado_por=creado_por,
    )


@transaction.atomic
def registrar_ajuste_manual(*, presentacion, delta_cantidad, observacion='', creado_por=None):
    delta_cantidad = int(delta_cantidad or 0)
    if delta_cantidad == 0:
        raise ValidationError(_('Adjustment quantity cannot be zero.'))
    stock = _lock_stock_records([presentacion.id])[presentacion.id]
    return _apply_inventory_change(
        stock=stock,
        categoria='AJUSTE',
        tipo='AJUSTE_POSITIVO' if delta_cantidad > 0 else 'AJUSTE_NEGATIVO',
        cantidad=abs(delta_cantidad),
        delta_fisico=delta_cantidad,
        delta_reservado=0,
        referencia=f'STOCK-{presentacion.id}',
        idempotency_key=None,
        observacion=observacion,
        creado_por=creado_por,
    )


@transaction.atomic
def validar_disponibilidad_para_items(items_payload, bypass_stock_check=False):
    if bypass_stock_check:
        return

    stock_map = _lock_stock_records(item['presentacion'].id for item in items_payload)
    for item in items_payload:
        cantidad = max(int(item['cantidad']), 1)
        stock = stock_map[item['presentacion'].id]
        if stock.stock_disponible < cantidad:
            raise ValidationError(
                _('Insufficient available stock for %(product)s - %(presentation)s. Requested %(requested)s, available %(available)s.') % {
                    'product': stock.presentacion.producto.nombre,
                    'presentation': stock.presentacion.nombre,
                    'requested': cantidad,
                    'available': stock.stock_disponible,
                }
            )


@transaction.atomic
def reservar_stock_para_pedido_items(*, pedido, pedido_items, creado_por=None):
    stock_map = _lock_stock_records(item.presentacion_id for item in pedido_items)
    for item in pedido_items:
        cantidad = max(int(item.cantidad_solicitada or item.cantidad or 0), 1)
        stock = stock_map[item.presentacion_id]
        _apply_inventory_change(
            stock=stock,
            categoria='RESERVA',
            tipo='RESERVA_PEDIDO',
            cantidad=cantidad,
            delta_fisico=0,
            delta_reservado=cantidad,
            referencia=f'PO-{pedido.id}',
            idempotency_key=f'PO-RESERVA-{item.id}',
            pedido=pedido,
            pedido_item=item,
            creado_por=creado_por,
        )
        item.cantidad_reservada_inventario = cantidad
        item.cantidad_inventario_aplicada = 0
        item.save(update_fields=['cantidad_reservada_inventario', 'cantidad_inventario_aplicada'])


@transaction.atomic
def ajustar_reserva_item_pedido(*, item, nueva_cantidad, creado_por=None):
    locked_item = PedidoItem.objects.select_for_update().select_related('pedido', 'presentacion__producto').get(pk=item.pk)
    stock = _lock_stock_records([locked_item.presentacion_id])[locked_item.presentacion_id]
    objetivo = max(int(nueva_cantidad), 1)
    actual = int(locked_item.cantidad_reservada_inventario or 0)
    delta = objetivo - actual
    if delta == 0:
        locked_item.cantidad_solicitada = objetivo
        locked_item.cantidad = objetivo
        locked_item.save(update_fields=['cantidad_solicitada', 'cantidad'])
        return locked_item

    if locked_item.cantidad_inventario_aplicada:
        raise ValidationError(_('Reserved quantities cannot be edited after picking has affected stock.'))

    if delta > 0:
        _apply_inventory_change(
            stock=stock,
            categoria='RESERVA',
            tipo='RESERVA_PEDIDO',
            cantidad=delta,
            delta_fisico=0,
            delta_reservado=delta,
            referencia=f'PO-{locked_item.pedido_id}',
            idempotency_key=f'PO-AJUSTE-MAS-{locked_item.id}-{objetivo}',
            pedido=locked_item.pedido,
            pedido_item=locked_item,
            creado_por=creado_por,
        )
    else:
        _apply_inventory_change(
            stock=stock,
            categoria='RESERVA',
            tipo='LIBERACION_PEDIDO',
            cantidad=abs(delta),
            delta_fisico=0,
            delta_reservado=delta,
            referencia=f'PO-{locked_item.pedido_id}',
            idempotency_key=f'PO-AJUSTE-MENOS-{locked_item.id}-{objetivo}',
            pedido=locked_item.pedido,
            pedido_item=locked_item,
            creado_por=creado_por,
        )

    locked_item.cantidad_solicitada = objetivo
    locked_item.cantidad = objetivo
    locked_item.cantidad_reservada_inventario = objetivo
    locked_item.save(update_fields=['cantidad_solicitada', 'cantidad', 'cantidad_reservada_inventario'])
    return locked_item


@transaction.atomic
def eliminar_item_pedido_con_inventario(*, item, creado_por=None):
    locked_item = PedidoItem.objects.select_for_update().select_related('pedido').get(pk=item.pk)
    stock = _lock_stock_records([locked_item.presentacion_id])[locked_item.presentacion_id]
    if locked_item.cantidad_reservada_inventario:
        _apply_inventory_change(
            stock=stock,
            categoria='RESERVA',
            tipo='LIBERACION_PEDIDO',
            cantidad=locked_item.cantidad_reservada_inventario,
            delta_fisico=0,
            delta_reservado=-locked_item.cantidad_reservada_inventario,
            referencia=f'PO-{locked_item.pedido_id}',
            idempotency_key=f'PO-DELETE-RESERVA-{locked_item.id}',
            pedido=locked_item.pedido,
            pedido_item=locked_item,
            creado_por=creado_por,
        )
    if locked_item.cantidad_inventario_aplicada:
        _apply_inventory_change(
            stock=stock,
            categoria='AJUSTE',
            tipo='ANULACION_PEDIDO',
            cantidad=locked_item.cantidad_inventario_aplicada,
            delta_fisico=locked_item.cantidad_inventario_aplicada,
            delta_reservado=0,
            referencia=f'PO-{locked_item.pedido_id}',
            idempotency_key=f'PO-DELETE-FISICO-{locked_item.id}',
            pedido=locked_item.pedido,
            pedido_item=locked_item,
            creado_por=creado_por,
        )
    locked_item.delete()


@transaction.atomic
def aplicar_verificacion_picking_inventario(*, pedido, pedido_item_ids, creado_por=None):
    items = list(
        PedidoItem.objects.select_for_update()
        .select_related('pedido', 'presentacion__producto')
        .filter(id__in=pedido_item_ids)
        .order_by('presentacion_id', 'id')
    )
    stock_map = _lock_stock_records(item.presentacion_id for item in items)
    for item in items:
        stock = stock_map[item.presentacion_id]
        reservado_pendiente = int(item.cantidad_reservada_inventario or 0)
        aplicado_previo = int(item.cantidad_inventario_aplicada or 0)
        nuevo_real = int(item.cantidad or 0)
        delta_real = nuevo_real - aplicado_previo
        reserva_a_liberar = min(reservado_pendiente, int(stock.stock_reservado or 0))

        if reserva_a_liberar:
            _apply_inventory_change(
                stock=stock,
                categoria='RESERVA',
                tipo='LIBERACION_PEDIDO',
                cantidad=reserva_a_liberar,
                delta_fisico=0,
                delta_reservado=-reserva_a_liberar,
                referencia=f'PO-{pedido.id}',
                idempotency_key=f'PICK-LIBERA-{item.id}-{reserva_a_liberar}-{nuevo_real}',
                pedido=pedido,
                pedido_item=item,
                creado_por=creado_por,
            )

        if delta_real:
            _apply_inventory_change(
                stock=stock,
                categoria='SALIDA' if delta_real > 0 else 'AJUSTE',
                tipo='SALIDA_PICKING' if delta_real > 0 else 'AJUSTE_PICKING',
                cantidad=abs(delta_real),
                delta_fisico=-delta_real,
                delta_reservado=0,
                referencia=f'PO-{pedido.id}',
                idempotency_key=f'PICK-FISICO-{item.id}-{nuevo_real}',
                pedido=pedido,
                pedido_item=item,
                creado_por=creado_por,
            )

        item.cantidad_reservada_inventario = 0
        item.cantidad_inventario_aplicada = nuevo_real
        item.save(update_fields=['cantidad_reservada_inventario', 'cantidad_inventario_aplicada'])


@transaction.atomic
def cancelar_pedido_con_inventario(*, pedido, creado_por=None):
    items = list(
        PedidoItem.objects.select_for_update()
        .select_related('presentacion__producto')
        .filter(pedido=pedido)
        .order_by('presentacion_id', 'id')
    )
    stock_map = _lock_stock_records(item.presentacion_id for item in items)
    for item in items:
        stock = stock_map[item.presentacion_id]
        if item.cantidad_reservada_inventario:
            _apply_inventory_change(
                stock=stock,
                categoria='RESERVA',
                tipo='LIBERACION_PEDIDO',
                cantidad=item.cantidad_reservada_inventario,
                delta_fisico=0,
                delta_reservado=-item.cantidad_reservada_inventario,
                referencia=f'PO-{pedido.id}',
                idempotency_key=f'PO-CANCEL-RESERVA-{item.id}',
                pedido=pedido,
                pedido_item=item,
                creado_por=creado_por,
            )
        if item.cantidad_inventario_aplicada:
            _apply_inventory_change(
                stock=stock,
                categoria='AJUSTE',
                tipo='ANULACION_PEDIDO',
                cantidad=item.cantidad_inventario_aplicada,
                delta_fisico=item.cantidad_inventario_aplicada,
                delta_reservado=0,
                referencia=f'PO-{pedido.id}',
                idempotency_key=f'PO-CANCEL-FISICO-{item.id}',
                pedido=pedido,
                pedido_item=item,
                creado_por=creado_por,
            )
