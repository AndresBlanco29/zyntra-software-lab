from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pedidos', '0003_ensure_canal_toma'),
    ]

    operations = [
        migrations.AddField(
            model_name='pedido',
            name='acepta_terminos_en',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]