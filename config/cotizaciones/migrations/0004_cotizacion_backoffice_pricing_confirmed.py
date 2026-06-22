from django.db import migrations, models
from config.core.migration_utils import wrap_add_field_operations



class Migration(migrations.Migration):

    dependencies = [
        ('cotizaciones', '0003_entrega2_fields'),
    ]

    operations = wrap_add_field_operations('cotizaciones', [
        migrations.AddField(
            model_name='cotizacion',
            name='backoffice_pricing_confirmed',
            field=models.BooleanField(default=False),
        ),
    
    ])
