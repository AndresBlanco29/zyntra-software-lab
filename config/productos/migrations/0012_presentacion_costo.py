from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('productos', '0011_marca_activo'),
    ]

    operations = [
        migrations.AddField(
            model_name='presentacion',
            name='costo',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
    ]