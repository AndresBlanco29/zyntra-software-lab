from config.core.migration_utils import separate_create_model
from config.productos.models import ConfiguracionPrecios
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('productos', '0012_presentacion_costo'),
    ]

    operations = [
        separate_create_model(
            ConfiguracionPrecios,
            migrations.CreateModel(
                name='ConfiguracionPrecios',
                fields=[
                    ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                    ('porcentaje_1', models.DecimalField(decimal_places=2, default=10, max_digits=5)),
                    ('porcentaje_2', models.DecimalField(decimal_places=2, default=20, max_digits=5)),
                    ('porcentaje_3', models.DecimalField(decimal_places=2, default=30, max_digits=5)),
                    ('porcentaje_4', models.DecimalField(decimal_places=2, default=40, max_digits=5)),
                    ('porcentaje_5', models.DecimalField(decimal_places=2, default=50, max_digits=5)),
                ],
                options={
                    'verbose_name': 'Configuracion de precios',
                    'verbose_name_plural': 'Configuracion de precios',
                },
            ),
        ),
    ]
