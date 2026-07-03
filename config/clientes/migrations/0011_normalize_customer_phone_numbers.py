from django.db import migrations


def normalize_customer_phone_numbers(apps, schema_editor):
    Cliente = apps.get_model('clientes', 'Cliente')
    from config.clientes.phone import normalize_stored_phone_number

    for cliente in Cliente.objects.all().iterator():
        normalized = normalize_stored_phone_number(cliente.telefono)
        if normalized and normalized != cliente.telefono:
            cliente.telefono = normalized
            cliente.save(update_fields=['telefono'])


class Migration(migrations.Migration):

    dependencies = [
        ('clientes', '0010_cliente_credit_limit'),
    ]

    operations = [
        migrations.RunPython(normalize_customer_phone_numbers, migrations.RunPython.noop),
    ]
