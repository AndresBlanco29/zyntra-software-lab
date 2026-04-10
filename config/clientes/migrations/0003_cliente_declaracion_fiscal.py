from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('clientes', '0002_cliente_codigo_postal_cliente_pais'),
    ]

    operations = [
        migrations.AddField(
            model_name='cliente',
            name='declaracion_fiscal_aceptada',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='cliente',
            name='declaracion_fiscal_aceptada_en',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]