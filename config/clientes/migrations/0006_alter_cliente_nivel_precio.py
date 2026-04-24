from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('clientes', '0005_cliente_nivel_precio'),
    ]

    operations = [
        migrations.AlterField(
            model_name='cliente',
            name='nivel_precio',
            field=models.PositiveSmallIntegerField(
                blank=True,
                choices=[
                    (0, 'Sin precios'),
                    (1, 'Precio 1'),
                    (2, 'Precio 2'),
                    (3, 'Precio 3'),
                    (4, 'Precio 4'),
                    (5, 'Precio 5'),
                ],
                default=0,
            ),
        ),
    ]