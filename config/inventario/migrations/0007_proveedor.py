from django.db import migrations, models


def backfill_suppliers(apps, schema_editor):
    CompraProveedor = apps.get_model('inventario', 'CompraProveedor')
    Proveedor = apps.get_model('inventario', 'Proveedor')

    for compra in CompraProveedor.objects.exclude(proveedor_nombre='').order_by('id'):
        nombre = (compra.proveedor_nombre or '').strip()
        if not nombre:
            continue
        supplier = Proveedor.objects.filter(nombre=nombre).first()
        sync_status = 'SYNCED' if compra.quickbooks_id else 'PENDING'
        defaults = {
            'email': (compra.proveedor_email or '').strip(),
            'telefono': (compra.proveedor_telefono or '').strip(),
            'company_name': nombre,
            'activo': True,
            'quickbooks_id': compra.quickbooks_id,
            'sync_status': sync_status,
            'last_synced_at': compra.last_synced_at,
        }
        if supplier is None:
            Proveedor.objects.create(nombre=nombre, **defaults)
            continue
        changed_fields = []
        for field_name, value in defaults.items():
            current_value = getattr(supplier, field_name)
            if current_value in (None, '') and value not in (None, ''):
                setattr(supplier, field_name, value)
                changed_fields.append(field_name)
        if changed_fields:
            supplier.save(update_fields=changed_fields + ['actualizado_en'])


class Migration(migrations.Migration):

    dependencies = [
        ('inventario', '0006_supplier_purchase_order_lifecycle'),
    ]

    operations = [
        migrations.CreateModel(
            name='Proveedor',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nombre', models.CharField(max_length=255, unique=True)),
                ('email', models.EmailField(blank=True, max_length=254)),
                ('telefono', models.CharField(blank=True, max_length=40)),
                ('company_name', models.CharField(blank=True, max_length=255)),
                ('notas', models.TextField(blank=True)),
                ('activo', models.BooleanField(default=True)),
                ('quickbooks_id', models.CharField(blank=True, db_index=True, max_length=100, null=True)),
                ('sync_status', models.CharField(choices=[('PENDING', 'Pending'), ('SYNCED', 'Synced'), ('FAILED', 'Failed')], db_index=True, default='PENDING', max_length=20)),
                ('last_synced_at', models.DateTimeField(blank=True, null=True)),
                ('creado_en', models.DateTimeField(auto_now_add=True)),
                ('actualizado_en', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Supplier',
                'verbose_name_plural': 'Suppliers',
                'ordering': ('nombre', 'id'),
            },
        ),
        migrations.RunPython(backfill_suppliers, migrations.RunPython.noop),
    ]
