from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('clientes', '0008_cliente_vendedor_asignado'),
    ]

    operations = [
        migrations.AddField(
            model_name='cliente',
            name='terminos_pago',
            field=models.CharField(
                blank=True,
                choices=[
                    ('PREPAY', 'prepay'),
                    ('COD', 'COD'),
                    ('NET7', 'NET7'),
                    ('NET14', 'NET14'),
                    ('NET21', 'NET21'),
                ],
                default='',
                max_length=10,
            ),
        ),
    ]
