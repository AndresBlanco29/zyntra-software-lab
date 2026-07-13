from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('clientes', '0012_take_order_draft'),
    ]

    operations = [
        migrations.AlterField(
            model_name='cliente',
            name='terminos_pago',
            field=models.CharField(
                blank=True,
                choices=[
                    ('PREPAY', 'prepay'),
                    ('COD', 'COD'),
                    ('NET7', 'NET7'),
                    ('ACH_NET7', 'ACH NET 7'),
                    ('NET14', 'NET14'),
                    ('NET21', 'NET21'),
                ],
                default='',
                max_length=16,
            ),
        ),
    ]
