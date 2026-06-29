from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('productos', '0018_seed_configuracion_descuentos'),
    ]

    operations = [
        migrations.AddField(
            model_name='presentacion',
            name='peso_por_caja',
            field=models.DecimalField(
                blank=True,
                decimal_places=3,
                help_text='Case weight in pounds used on invoices for Total WGT.',
                max_digits=10,
                null=True,
                verbose_name='Weight per case (LB)',
            ),
        ),
    ]
