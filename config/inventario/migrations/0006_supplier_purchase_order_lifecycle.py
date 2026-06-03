from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


def _backfill_supplier_purchase_orders(apps, schema_editor):
    CompraProveedor = apps.get_model('inventario', 'CompraProveedor')
    current_year = django.utils.timezone.now().year
    for compra in CompraProveedor.objects.all().order_by('id'):
        update_fields = []
        if not compra.po_number:
            compra.po_number = f'PO-{current_year}-{compra.id:06d}'
            update_fields.append('po_number')
        if compra.quickbooks_id and compra.sync_status == 'SYNCED':
            if compra.estado == 'BORRADOR':
                compra.estado = 'RECIBIDA'
                update_fields.append('estado')
            if not compra.inventory_applied:
                compra.inventory_applied = True
                update_fields.append('inventory_applied')
            if compra.inventory_received_at is None:
                compra.inventory_received_at = compra.last_synced_at or compra.actualizado_en or compra.creado_en
                update_fields.append('inventory_received_at')
        if update_fields:
            compra.save(update_fields=update_fields)


class Migration(migrations.Migration):

    dependencies = [
        ('inventario', '0005_compraproveedor_compraproveedorlinea'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='compraproveedor',
            name='estado',
            field=models.CharField(
                choices=[
                    ('BORRADOR', 'Draft'),
                    ('ENVIADA', 'Sent'),
                    ('RECIBIDA', 'Received'),
                    ('CANCELADA', 'Cancelled'),
                ],
                db_index=True,
                default='BORRADOR',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='compraproveedor',
            name='inventory_applied',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='compraproveedor',
            name='inventory_received_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='compraproveedor',
            name='inventory_received_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='compras_proveedor_recibidas', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='compraproveedor',
            name='po_number',
            field=models.CharField(blank=True, max_length=100, unique=True),
        ),
        migrations.RunPython(
            code=_backfill_supplier_purchase_orders,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
