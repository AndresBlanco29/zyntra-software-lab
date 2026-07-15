from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('productos', '0020_presentacion_qb_price'),
    ]

    operations = [
        migrations.AddField(
            model_name='presentacion',
            name='pallet_tie',
            field=models.PositiveIntegerField(
                blank=True,
                help_text='How many cases go on one pallet layer (bed).',
                null=True,
                verbose_name='Pallet tie',
            ),
        ),
        migrations.AddField(
            model_name='presentacion',
            name='pallet_high',
            field=models.PositiveIntegerField(
                blank=True,
                help_text='How many layers go on one pallet.',
                null=True,
                verbose_name='Pallet high',
            ),
        ),
        migrations.AddField(
            model_name='presentacion',
            name='pallet_quantity',
            field=models.PositiveIntegerField(
                blank=True,
                help_text='Total cases per pallet (pallet tie × pallet high).',
                null=True,
                verbose_name='Pallet quantity',
            ),
        ),
    ]
