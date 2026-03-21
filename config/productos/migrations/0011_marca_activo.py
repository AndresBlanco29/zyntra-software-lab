from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('productos', '0010_producto_codigo_barras_alter_presentacion_precio_1_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='marca',
            name='activo',
            field=models.BooleanField(default=True),
        ),
    ]
