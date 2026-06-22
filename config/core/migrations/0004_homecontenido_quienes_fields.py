from django.db import migrations, models

from config.core.migration_utils import separate_add_fields

QUIENES_FIELD_SPECS = [
    (
        'quienes_descripcion',
        models.TextField(
            default='En La Tortilla Grocery LLC, somos el aliado de confianza de los negocios latinos. Ofrecemos productos y servicios mayoristas de alta calidad, adaptados a sus necesidades. Nos enfocamos en brindar soluciones eficientes para que su negocio crezca y prospere en un mercado competitivo.',
        ),
    ),
    ('quienes_descripcion_en', models.TextField(blank=True, null=True)),
    ('quienes_titulo', models.CharField(default='Quienes Somos?', max_length=120)),
    ('quienes_titulo_en', models.CharField(blank=True, max_length=120, null=True)),
]


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0003_homecontenido'),
    ]

    operations = [
        separate_add_fields('core', 'homecontenido', QUIENES_FIELD_SPECS),
    ]
