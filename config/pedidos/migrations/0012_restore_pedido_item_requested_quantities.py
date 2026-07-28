from django.db import migrations


def restore_pedido_item_requested_quantities(apps, schema_editor):
    PedidoItem = apps.get_model('pedidos', 'PedidoItem')
    Pedido = apps.get_model('pedidos', 'Pedido')
    InventarioMovimiento = apps.get_model('inventario', 'InventarioMovimiento')

    for item in PedidoItem.objects.filter(cantidad_solicitada=0).iterator():
        documented = 0

        reservation_move = (
            InventarioMovimiento.objects.filter(
                pedido_item_id=item.id,
                tipo='RESERVA_PEDIDO',
                cantidad__gt=0,
            )
            .order_by('creado_en', 'id')
            .first()
        )
        if reservation_move:
            documented = int(reservation_move.cantidad or 0)

        if documented <= 0:
            pedido = Pedido.objects.filter(pk=item.pedido_id).only('cotizacion_id').first()
            cotizacion_id = getattr(pedido, 'cotizacion_id', None) if pedido else None
            if cotizacion_id:
                CotizacionItem = apps.get_model('cotizaciones', 'CotizacionItem')
                cotizacion_item = (
                    CotizacionItem.objects.filter(
                        cotizacion_id=cotizacion_id,
                        presentacion_id=item.presentacion_id,
                        cantidad__gt=0,
                    )
                    .order_by('id')
                    .first()
                )
                if cotizacion_item:
                    documented = int(cotizacion_item.cantidad or 0)

        if documented > 0:
            PedidoItem.objects.filter(pk=item.pk).update(cantidad_solicitada=documented)


class Migration(migrations.Migration):

    dependencies = [
        ('pedidos', '0011_pedido_credit_limit_flags'),
        ('inventario', '0002_operational_inventory'),
        ('cotizaciones', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(restore_pedido_item_requested_quantities, migrations.RunPython.noop),
    ]
