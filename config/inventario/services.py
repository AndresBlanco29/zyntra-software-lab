import math
import unicodedata

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils.translation import gettext as _
from django.utils import timezone

from config.pedidos.models import PedidoItem
from config.productos.models import Presentacion

from .models import CompraProveedor, InventarioMovimiento, StockPresentacion, StockProductoFraccionado


def units_per_package(presentacion):
    return max(int(getattr(presentacion, 'unidades', 0) or 0), 1)


def inventory_units_for_packages(presentacion, package_quantity):
    packages = max(int(package_quantity or 0), 0)
    return packages * units_per_package(presentacion)


def inventory_packages_for_quantity(presentacion, package_quantity):
    return max(int(package_quantity or 0), 0)


def _normalize_content_term(value):
    normalized = unicodedata.normalize('NFKD', str(value or '').strip().lower())
    return ''.join(char for char in normalized if not unicodedata.combining(char))


def _content_term_aliases(value):
    normalized = _normalize_content_term(value)
    aliases = {normalized}
    if normalized.endswith('s'):
        aliases.add(normalized[:-1])
    if normalized.endswith('es'):
        aliases.add(normalized[:-2])
    return {alias for alias in aliases if alias}


def _resolve_fractional_rollup_presentacion(producto_id, contenido):
    contenido_aliases = _content_term_aliases(contenido)
    for candidate in Presentacion.objects.filter(producto_id=producto_id, unidades__gt=1).order_by('unidades', 'id'):
        candidate_aliases = set()
        candidate_aliases.update(_content_term_aliases(candidate.tipo_contenido))
        candidate_aliases.update(_content_term_aliases(candidate.tipo_contenido_en))
        if contenido_aliases & candidate_aliases:
            return candidate
    return None


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


def _lock_fractional_stock_records(product_content_pairs):
    stock_map = {}
    normalized_pairs = []
    for producto_id, contenido in product_content_pairs:
        normalized_contenido = str(contenido or '').strip()
        if not producto_id or not normalized_contenido:
            continue
        normalized_pairs.append((producto_id, normalized_contenido))
    for producto_id, contenido in sorted(set(normalized_pairs)):
        try:
            StockProductoFraccionado.objects.get_or_create(
                producto_id=producto_id,
                contenido=contenido,
                defaults={'stock_fisico': 0},
            )
        except IntegrityError:
            pass
    for stock in StockProductoFraccionado.objects.select_for_update().select_related('producto').filter(
        producto_id__in=[producto_id for producto_id, _ in normalized_pairs]
    ):
        stock_map[(stock.producto_id, stock.contenido)] = stock
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


def _apply_fractional_inventory_change(
    *,
    stock,
    delta_fisico,
    observacion='',
    referencia='',
    invoice=None,
    nota_ajuste=None,
    nota_ajuste_item=None,
    creado_por=None,
):
    promotion_presentacion = _resolve_fractional_rollup_presentacion(stock.producto_id, stock.contenido)
    units_per_package = max(int(getattr(promotion_presentacion, 'unidades', 0) or 0), 1) if promotion_presentacion else 1

    if delta_fisico < 0 and stock.stock_fisico + delta_fisico < 0 and promotion_presentacion is not None:
        missing_quantity = abs(stock.stock_fisico + delta_fisico)
        packages_to_break = int(math.ceil(missing_quantity / units_per_package))
        package_stock = _lock_stock_records([promotion_presentacion.id])[promotion_presentacion.id]
        _apply_inventory_change(
            stock=package_stock,
            categoria='AJUSTE',
            tipo='DESCONSOLIDACION_FRACCIONADA',
            cantidad=packages_to_break,
            delta_fisico=-packages_to_break,
            delta_reservado=0,
            referencia=referencia or f'FRACTIONAL-{stock.producto_id}',
            observacion=observacion,
            invoice=invoice,
            nota_ajuste=nota_ajuste,
            nota_ajuste_item=nota_ajuste_item,
            creado_por=creado_por,
        )
        stock.stock_fisico += packages_to_break * units_per_package
        stock.save(update_fields=['stock_fisico', 'actualizado_en'])

    after_fisico = stock.stock_fisico + delta_fisico
    if after_fisico < 0:
        raise ValidationError(
            _('Insufficient fractional stock for %(product)s - %(content)s.') % {
                'product': stock.producto.nombre,
                'content': stock.contenido,
            }
        )
    stock.stock_fisico = after_fisico
    stock.save(update_fields=['stock_fisico', 'actualizado_en'])

    if delta_fisico > 0 and promotion_presentacion is not None and stock.stock_fisico >= units_per_package:
        packages_to_promote = stock.stock_fisico // units_per_package
        stock.stock_fisico = stock.stock_fisico % units_per_package
        stock.save(update_fields=['stock_fisico', 'actualizado_en'])
        package_stock = _lock_stock_records([promotion_presentacion.id])[promotion_presentacion.id]
        _apply_inventory_change(
            stock=package_stock,
            categoria='AJUSTE',
            tipo='CONSOLIDACION_FRACCIONADA',
            cantidad=packages_to_promote,
            delta_fisico=packages_to_promote,
            delta_reservado=0,
            referencia=referencia or f'FRACTIONAL-{stock.producto_id}',
            observacion=observacion,
            invoice=invoice,
            nota_ajuste=nota_ajuste,
            nota_ajuste_item=nota_ajuste_item,
            creado_por=creado_por,
        )
    return stock


@transaction.atomic
def registrar_entrada_manual(*, presentacion, cantidad, observacion='', creado_por=None, referencia=None, idempotency_key=None):
    cantidad = max(int(cantidad or 0), 1)
    stock = _lock_stock_records([presentacion.id])[presentacion.id]
    return _apply_inventory_change(
        stock=stock,
        categoria='ENTRADA',
        tipo='ENTRADA_MANUAL',
        cantidad=cantidad,
        delta_fisico=cantidad,
        delta_reservado=0,
        referencia=referencia or f'STOCK-{presentacion.id}',
        idempotency_key=idempotency_key,
        observacion=observacion,
        creado_por=creado_por,
    )

@transaction.atomic
def registrar_recepcion_compra_proveedor(*, compra, creado_por=None):
    locked_compra = CompraProveedor.objects.select_for_update().prefetch_related('lineas__presentacion__producto').get(pk=compra.pk)
    if locked_compra.estado == CompraProveedor.STATUS_CANCELLED:
        raise ValidationError(_('Cancelled purchase orders cannot receive inventory.'))
    if not locked_compra.lineas.exists():
        raise ValidationError(_('Add at least one supplier purchase line before receiving inventory.'))
    if locked_compra.inventory_applied:
        return locked_compra

    for linea in locked_compra.lineas.all():
        registrar_entrada_manual(
            presentacion=linea.presentacion,
            cantidad=linea.cantidad,
            observacion=f'{locked_compra.po_number or locked_compra.bill_number or locked_compra.pk} - {locked_compra.proveedor_nombre}',
            creado_por=creado_por or locked_compra.creado_por,
            referencia=f'SUPPLIER-PURCHASE-{locked_compra.pk}',
            idempotency_key=f'supplier-purchase:{locked_compra.pk}:line:{linea.pk}',
        )

    locked_compra.estado = CompraProveedor.STATUS_RECEIVED
    locked_compra.inventory_applied = True
    locked_compra.inventory_received_at = timezone.now()
    locked_compra.inventory_received_by = creado_por
    locked_compra.save(
        update_fields=['estado', 'inventory_applied', 'inventory_received_at', 'inventory_received_by', 'actualizado_en']
    )
    return locked_compra


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
        reserved_packages = inventory_packages_for_quantity(item['presentacion'], cantidad)
        stock = stock_map[item['presentacion'].id]
        available_packages = stock.computed_stock_disponible()
        if available_packages < reserved_packages:
            raise ValidationError(
                _('Insufficient available stock for %(product)s - %(presentation)s. Requested %(requested)s, available %(available)s.') % {
                    'product': stock.presentacion.producto.nombre,
                    'presentation': stock.presentacion.nombre,
                    'requested': cantidad,
                    'available': available_packages,
                }
            )


@transaction.atomic
def reservar_stock_para_pedido_items(*, pedido, pedido_items, creado_por=None):
    stock_map = _lock_stock_records(item.presentacion_id for item in pedido_items)
    for item in pedido_items:
        cantidad = max(int(item.cantidad_solicitada or item.cantidad or 0), 1)
        reserved_packages = inventory_packages_for_quantity(item.presentacion, cantidad)
        stock = stock_map[item.presentacion_id]
        _apply_inventory_change(
            stock=stock,
            categoria='RESERVA',
            tipo='RESERVA_PEDIDO',
            cantidad=cantidad,
            delta_fisico=0,
            delta_reservado=reserved_packages,
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
    objetivo = max(int(nueva_cantidad), 0)
    actual = int(locked_item.cantidad_reservada_inventario or 0)
    delta = objetivo - actual
    if delta == 0:
        locked_item.cantidad = objetivo
        locked_item.save(update_fields=['cantidad'])
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
            delta_reservado=inventory_packages_for_quantity(locked_item.presentacion, delta),
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
            delta_reservado=-inventory_packages_for_quantity(locked_item.presentacion, abs(delta)),
            referencia=f'PO-{locked_item.pedido_id}',
            idempotency_key=f'PO-AJUSTE-MENOS-{locked_item.id}-{objetivo}',
            pedido=locked_item.pedido,
            pedido_item=locked_item,
            creado_por=creado_por,
        )

    locked_item.cantidad = objetivo
    locked_item.cantidad_reservada_inventario = objetivo
    locked_item.save(update_fields=['cantidad', 'cantidad_reservada_inventario'])
    return locked_item


@transaction.atomic
def ajustar_cantidad_item_pedido_despues_picking(*, item, nueva_cantidad, creado_por=None):
    locked_item = PedidoItem.objects.select_for_update().select_related('pedido', 'presentacion__producto').get(pk=item.pk)
    objetivo = max(int(nueva_cantidad), 0)
    actual = int(locked_item.cantidad_inventario_aplicada or locked_item.cantidad or 0)
    delta = objetivo - actual

    if delta == 0:
        locked_item.cantidad = objetivo
        locked_item.save(update_fields=['cantidad'])
        return locked_item

    stock = _lock_stock_records([locked_item.presentacion_id])[locked_item.presentacion_id]
    inventory_delta = inventory_packages_for_quantity(locked_item.presentacion, abs(delta)) * (1 if delta > 0 else -1)
    _apply_inventory_change(
        stock=stock,
        categoria='SALIDA' if delta > 0 else 'AJUSTE',
        tipo='SALIDA_PICKING' if delta > 0 else 'AJUSTE_PICKING',
        cantidad=abs(delta),
        delta_fisico=inventory_delta,
        delta_reservado=0,
        referencia=f'PO-{locked_item.pedido_id}',
        idempotency_key=f'PO-BACKOFFICE-AJUSTE-{locked_item.id}-{objetivo}',
        pedido=locked_item.pedido,
        pedido_item=locked_item,
        creado_por=creado_por,
    )

    locked_item.cantidad = objetivo
    locked_item.cantidad_inventario_aplicada = objetivo
    locked_item.save(update_fields=['cantidad', 'cantidad_inventario_aplicada'])
    return locked_item


@transaction.atomic
def reemplazar_presentacion_item_pedido(*, item, nueva_presentacion, creado_por=None):
    locked_item = PedidoItem.objects.select_for_update().select_related('pedido', 'presentacion__producto').get(pk=item.pk)
    if locked_item.presentacion_id == nueva_presentacion.id:
        return locked_item
    if locked_item.cantidad_inventario_aplicada:
        raise ValidationError(_('Reserved quantities cannot be edited after picking has affected stock.'))
    if nueva_presentacion.producto_id != locked_item.presentacion.producto_id:
        raise ValidationError(_('Unit of measure can only be changed to another presentation of the same product.'))

    stock_map = _lock_stock_records([locked_item.presentacion_id, nueva_presentacion.id])
    reserva_actual = int(locked_item.cantidad_reservada_inventario or 0)
    stock_actual = stock_map[locked_item.presentacion_id]
    stock_nuevo = stock_map[nueva_presentacion.id]

    if reserva_actual:
        _apply_inventory_change(
            stock=stock_actual,
            categoria='RESERVA',
            tipo='LIBERACION_PEDIDO',
            cantidad=reserva_actual,
            delta_fisico=0,
            delta_reservado=-inventory_packages_for_quantity(locked_item.presentacion, reserva_actual),
            referencia=f'PO-{locked_item.pedido_id}',
            idempotency_key=f'PO-SWAP-OUT-{locked_item.id}-{locked_item.presentacion_id}-{nueva_presentacion.id}',
            pedido=locked_item.pedido,
            pedido_item=locked_item,
            creado_por=creado_por,
        )
        _apply_inventory_change(
            stock=stock_nuevo,
            categoria='RESERVA',
            tipo='RESERVA_PEDIDO',
            cantidad=reserva_actual,
            delta_fisico=0,
            delta_reservado=inventory_packages_for_quantity(nueva_presentacion, reserva_actual),
            referencia=f'PO-{locked_item.pedido_id}',
            idempotency_key=f'PO-SWAP-IN-{locked_item.id}-{locked_item.presentacion_id}-{nueva_presentacion.id}',
            pedido=locked_item.pedido,
            pedido_item=locked_item,
            creado_por=creado_por,
        )

    locked_item.presentacion = nueva_presentacion
    locked_item.save(update_fields=['presentacion'])
    return locked_item


@transaction.atomic
def reemplazar_presentacion_item_pedido_despues_picking(*, item, nueva_presentacion, creado_por=None):
    locked_item = PedidoItem.objects.select_for_update().select_related('pedido', 'presentacion__producto').get(pk=item.pk)
    if locked_item.presentacion_id == nueva_presentacion.id:
        return locked_item
    if nueva_presentacion.producto_id != locked_item.presentacion.producto_id:
        raise ValidationError(_('Unit of measure can only be changed to another presentation of the same product.'))

    stock_map = _lock_stock_records([locked_item.presentacion_id, nueva_presentacion.id])
    stock_actual = stock_map[locked_item.presentacion_id]
    stock_nuevo = stock_map[nueva_presentacion.id]
    aplicado_actual = int(locked_item.cantidad_inventario_aplicada or locked_item.cantidad or 0)
    reservado_actual = int(locked_item.cantidad_reservada_inventario or 0)

    if reservado_actual:
        _apply_inventory_change(
            stock=stock_actual,
            categoria='RESERVA',
            tipo='LIBERACION_PEDIDO',
            cantidad=reservado_actual,
            delta_fisico=0,
            delta_reservado=-inventory_packages_for_quantity(locked_item.presentacion, reservado_actual),
            referencia=f'PO-{locked_item.pedido_id}',
            idempotency_key=f'PO-BO-SWAP-RES-OUT-{locked_item.id}-{locked_item.presentacion_id}-{nueva_presentacion.id}',
            pedido=locked_item.pedido,
            pedido_item=locked_item,
            creado_por=creado_por,
        )
        _apply_inventory_change(
            stock=stock_nuevo,
            categoria='RESERVA',
            tipo='RESERVA_PEDIDO',
            cantidad=reservado_actual,
            delta_fisico=0,
            delta_reservado=inventory_packages_for_quantity(nueva_presentacion, reservado_actual),
            referencia=f'PO-{locked_item.pedido_id}',
            idempotency_key=f'PO-BO-SWAP-RES-IN-{locked_item.id}-{locked_item.presentacion_id}-{nueva_presentacion.id}',
            pedido=locked_item.pedido,
            pedido_item=locked_item,
            creado_por=creado_por,
        )

    if aplicado_actual:
        _apply_inventory_change(
            stock=stock_actual,
            categoria='AJUSTE',
            tipo='AJUSTE_PICKING',
            cantidad=aplicado_actual,
            delta_fisico=aplicado_actual,
            delta_reservado=0,
            referencia=f'PO-{locked_item.pedido_id}',
            idempotency_key=f'PO-BO-SWAP-APPLIED-OUT-{locked_item.id}-{locked_item.presentacion_id}-{nueva_presentacion.id}',
            pedido=locked_item.pedido,
            pedido_item=locked_item,
            creado_por=creado_por,
        )
        _apply_inventory_change(
            stock=stock_nuevo,
            categoria='SALIDA',
            tipo='SALIDA_PICKING',
            cantidad=aplicado_actual,
            delta_fisico=-aplicado_actual,
            delta_reservado=0,
            referencia=f'PO-{locked_item.pedido_id}',
            idempotency_key=f'PO-BO-SWAP-APPLIED-IN-{locked_item.id}-{locked_item.presentacion_id}-{nueva_presentacion.id}',
            pedido=locked_item.pedido,
            pedido_item=locked_item,
            creado_por=creado_por,
        )

    locked_item.presentacion = nueva_presentacion
    locked_item.save(update_fields=['presentacion'])
    return locked_item


@transaction.atomic
def eliminar_item_pedido_con_inventario(*, item, creado_por=None):
    locked_item = PedidoItem.objects.select_for_update().select_related('pedido').get(pk=item.pk)
    stock = _lock_stock_records([locked_item.presentacion_id])[locked_item.presentacion_id]
    if locked_item.cantidad_reservada_inventario:
        reserved_packages = inventory_packages_for_quantity(locked_item.presentacion, locked_item.cantidad_reservada_inventario)
        _apply_inventory_change(
            stock=stock,
            categoria='RESERVA',
            tipo='LIBERACION_PEDIDO',
            cantidad=locked_item.cantidad_reservada_inventario,
            delta_fisico=0,
            delta_reservado=-reserved_packages,
            referencia=f'PO-{locked_item.pedido_id}',
            idempotency_key=f'PO-DELETE-RESERVA-{locked_item.id}',
            pedido=locked_item.pedido,
            pedido_item=locked_item,
            creado_por=creado_por,
        )
    if locked_item.cantidad_inventario_aplicada:
        applied_packages = inventory_packages_for_quantity(locked_item.presentacion, locked_item.cantidad_inventario_aplicada)
        _apply_inventory_change(
            stock=stock,
            categoria='AJUSTE',
            tipo='ANULACION_PEDIDO',
            cantidad=locked_item.cantidad_inventario_aplicada,
            delta_fisico=applied_packages,
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
        reserved_packages_pending = inventory_packages_for_quantity(item.presentacion, reservado_pendiente)
        reserva_a_liberar = min(reserved_packages_pending, int(stock.stock_reservado or 0))

        if reserva_a_liberar:
            _apply_inventory_change(
                stock=stock,
                categoria='RESERVA',
                tipo='LIBERACION_PEDIDO',
                cantidad=reservado_pendiente,
                delta_fisico=0,
                delta_reservado=-reserva_a_liberar,
                referencia=f'PO-{pedido.id}',
                idempotency_key=f'PICK-LIBERA-{item.id}-{reserva_a_liberar}-{nuevo_real}',
                pedido=pedido,
                pedido_item=item,
                creado_por=creado_por,
            )

        if delta_real:
            inventory_delta = inventory_packages_for_quantity(item.presentacion, abs(delta_real)) * (-1 if delta_real > 0 else 1)
            is_free_gift = bool(getattr(item, 'es_regalo', False)) and delta_real > 0
            _apply_inventory_change(
                stock=stock,
                categoria='SALIDA' if delta_real > 0 else 'AJUSTE',
                tipo='SALIDA_REGALO' if is_free_gift else ('SALIDA_PICKING' if delta_real > 0 else 'AJUSTE_PICKING'),
                cantidad=abs(delta_real),
                delta_fisico=inventory_delta,
                delta_reservado=0,
                referencia=f'PO-{pedido.id}',
                idempotency_key=f'PICK-FISICO-{item.id}-{nuevo_real}',
                pedido=pedido,
                pedido_item=item,
                creado_por=creado_por,
                observacion='FREE promotional product' if is_free_gift else '',
            )

        item.cantidad_reservada_inventario = 0
        item.cantidad_inventario_aplicada = nuevo_real
        item.save(update_fields=['cantidad_reservada_inventario', 'cantidad_inventario_aplicada'])


def aplicar_inventario_pendiente_pedido(*, pedido, pedido_item_ids=None, creado_por=None):
    items_queryset = pedido.items.all()
    if pedido_item_ids is not None:
        items_queryset = items_queryset.filter(id__in=pedido_item_ids)
    pending_item_ids = [
        item.id
        for item in items_queryset
        if int(item.cantidad or 0) > int(item.cantidad_inventario_aplicada or 0)
    ]
    if not pending_item_ids:
        return []
    aplicar_verificacion_picking_inventario(
        pedido=pedido,
        pedido_item_ids=pending_item_ids,
        creado_por=creado_por,
    )
    return pending_item_ids


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
            reserved_packages = inventory_packages_for_quantity(item.presentacion, item.cantidad_reservada_inventario)
            _apply_inventory_change(
                stock=stock,
                categoria='RESERVA',
                tipo='LIBERACION_PEDIDO',
                cantidad=item.cantidad_reservada_inventario,
                delta_fisico=0,
                delta_reservado=-reserved_packages,
                referencia=f'PO-{pedido.id}',
                idempotency_key=f'PO-CANCEL-RESERVA-{item.id}',
                pedido=pedido,
                pedido_item=item,
                creado_por=creado_por,
            )
        if item.cantidad_inventario_aplicada:
            applied_packages = inventory_packages_for_quantity(item.presentacion, item.cantidad_inventario_aplicada)
            _apply_inventory_change(
                stock=stock,
                categoria='AJUSTE',
                tipo='ANULACION_PEDIDO',
                cantidad=item.cantidad_inventario_aplicada,
                delta_fisico=applied_packages,
                delta_reservado=0,
                referencia=f'PO-{pedido.id}',
                idempotency_key=f'PO-CANCEL-FISICO-{item.id}',
                pedido=pedido,
                pedido_item=item,
                creado_por=creado_por,
            )


@transaction.atomic
def restaurar_inventario_por_anulacion_factura(*, pedido, invoice, creado_por=None):
    items = list(
        PedidoItem.objects.select_for_update()
        .select_related('presentacion__producto')
        .filter(pedido=pedido)
        .order_by('presentacion_id', 'id')
    )
    if not items:
        return
    stock_map = _lock_stock_records(item.presentacion_id for item in items)
    for item in items:
        stock = stock_map[item.presentacion_id]
        if item.cantidad_inventario_aplicada:
            applied_packages = inventory_packages_for_quantity(item.presentacion, item.cantidad_inventario_aplicada)
            _apply_inventory_change(
                stock=stock,
                categoria='AJUSTE',
                tipo='ANULACION_PEDIDO',
                cantidad=item.cantidad_inventario_aplicada,
                delta_fisico=applied_packages,
                delta_reservado=0,
                referencia=f'INV-{invoice.numero}',
                idempotency_key=f'INV-VOID-FISICO-{invoice.id}-{item.id}',
                pedido=pedido,
                pedido_item=item,
                invoice=invoice,
                creado_por=creado_por,
            )
            item.cantidad_inventario_aplicada = 0
            item.save(update_fields=['cantidad_inventario_aplicada'])
