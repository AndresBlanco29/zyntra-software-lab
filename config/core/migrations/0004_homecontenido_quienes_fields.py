from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0003_homecontenido'),
    ]

    operations = [
        migrations.AddField(
            model_name='homecontenido',
            name='quienes_descripcion',
            field=models.TextField(default='En La Tortilla Grocery LLC, somos el aliado de confianza de los negocios latinos. Ofrecemos productos y servicios mayoristas de alta calidad, adaptados a sus necesidades. Nos enfocamos en brindar soluciones eficientes para que su negocio crezca y prospere en un mercado competitivo.'),
        ),
        migrations.AddField(
            model_name='homecontenido',
            name='quienes_descripcion_en',
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='homecontenido',
            name='quienes_titulo',
            field=models.CharField(default='Quienes Somos?', max_length=120),
        ),
        migrations.AddField(
            model_name='homecontenido',
            name='quienes_titulo_en',
            field=models.CharField(blank=True, max_length=120, null=True),
        ),
    ]
