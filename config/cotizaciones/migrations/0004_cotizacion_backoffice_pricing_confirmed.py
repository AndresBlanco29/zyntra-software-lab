from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cotizaciones', '0003_entrega2_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='cotizacion',
            name='backoffice_pricing_confirmed',
            field=models.BooleanField(default=False),
        ),
    ]