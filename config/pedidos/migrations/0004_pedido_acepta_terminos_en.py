from django.db import migrations, models
from config.core.migration_utils import wrap_add_field_operations



class Migration(migrations.Migration):

    dependencies = [
        ('pedidos', '0003_ensure_canal_toma'),
    ]

    operations = wrap_add_field_operations('pedidos', [
        migrations.AddField(
            model_name='pedido',
            name='acepta_terminos_en',
            field=models.DateTimeField(blank=True, null=True),
        ),
    
    ])
