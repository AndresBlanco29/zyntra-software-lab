from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('facturacion', '0004_delivery_live_tracking'),
    ]

    operations = [
        migrations.AddField(
            model_name='invoiceitem',
            name='precio_venta_sugerido_unitario',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.RunPython(
            code=lambda apps, schema_editor: _populate_suggested_unit_prices(apps),
            reverse_code=migrations.RunPython.noop,
        ),
    ]


def _populate_suggested_unit_prices(apps):
    InvoiceItem = apps.get_model('facturacion', 'InvoiceItem')
    for item in InvoiceItem.objects.select_related('presentacion').all():
        if item.precio_venta_sugerido_unitario is not None:
            continue

        base_price = Decimal(str(item.precio_unitario or '0')).quantize(Decimal('0.01'))
        presentacion = item.presentacion
        if presentacion:
            configured_prices = sorted({
                Decimal(str(price or '0')).quantize(Decimal('0.01'))
                for price in (
                    presentacion.precio_1,
                    presentacion.precio_2,
                    presentacion.precio_3,
                    presentacion.precio_4,
                    presentacion.precio_5,
                )
                if Decimal(str(price or '0')) > 0
            })
            suggested_case_price = base_price
            for configured_price in configured_prices:
                if configured_price > base_price:
                    suggested_case_price = configured_price
                    break
            else:
                if configured_prices:
                    suggested_case_price = configured_prices[-1]

            if getattr(presentacion, 'unidades', 0):
                item.precio_venta_sugerido_unitario = (suggested_case_price / Decimal(str(presentacion.unidades))).quantize(Decimal('0.01'))
            else:
                item.precio_venta_sugerido_unitario = suggested_case_price
        else:
            item.precio_venta_sugerido_unitario = base_price

        item.save(update_fields=['precio_venta_sugerido_unitario'])
