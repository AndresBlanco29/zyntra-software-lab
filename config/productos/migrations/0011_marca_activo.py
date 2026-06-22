from django.db import migrations, models
from config.core.migration_utils import wrap_add_field_operations



class Migration(migrations.Migration):

    dependencies = [
        ('productos', '0010_producto_codigo_barras_alter_presentacion_precio_1_and_more'),
    ]

    operations = wrap_add_field_operations('productos', [
        migrations.AddField(
            model_name='marca',
            name='activo',
            field=models.BooleanField(default=True),
        ),
    
    ])

