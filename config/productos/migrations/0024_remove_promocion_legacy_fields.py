from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('productos', '0023_backfill_promocion_escalas'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='promocion',
            name='cantidad_minima',
        ),
        migrations.RemoveField(
            model_name='promocion',
            name='tipo_beneficio',
        ),
        migrations.RemoveField(
            model_name='promocion',
            name='valor_beneficio',
        ),
    ]
