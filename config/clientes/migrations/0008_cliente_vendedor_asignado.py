import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('clientes', '0007_cliente_quickbooks_sync_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='cliente',
            name='vendedor_asignado',
            field=models.ForeignKey(
                blank=True,
                limit_choices_to={'role': 'vendedor'},
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='clientes_asignados',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='cliente',
            name='vendedor_asignado_en',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='cliente',
            name='vendedor_asignado_por',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='clientes_asignados_por',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
