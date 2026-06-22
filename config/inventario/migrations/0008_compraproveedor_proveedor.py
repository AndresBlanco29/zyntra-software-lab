from django.db import migrations, models
from config.core.migration_utils import wrap_add_field_operations



def link_purchase_orders_to_suppliers(apps, schema_editor):
    CompraProveedor = apps.get_model('inventario', 'CompraProveedor')
    Proveedor = apps.get_model('inventario', 'Proveedor')

    for compra in CompraProveedor.objects.filter(proveedor__isnull=True).exclude(proveedor_nombre='').order_by('id'):
        nombre = (compra.proveedor_nombre or '').strip()
        if not nombre:
            continue
        supplier = Proveedor.objects.filter(nombre=nombre).first()
        if supplier is None:
            continue
        compra.proveedor = supplier
        compra.save(update_fields=['proveedor'])


class Migration(migrations.Migration):

    dependencies = [
        ('inventario', '0007_proveedor'),
    ]

    operations = wrap_add_field_operations('inventario', [
        migrations.AddField(
            model_name='compraproveedor',
            name='proveedor',
            field=models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name='compras', to='inventario.proveedor'),
        ),
        migrations.RunPython(link_purchase_orders_to_suppliers, migrations.RunPython.noop),
    
    ])

