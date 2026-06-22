from django.db import migrations, models

from config.core.migration_utils import separate_add_fields

TESTIMONIO_FIELD_SPECS = [
    ('comentario_en', models.TextField(blank=True, null=True)),
    ('negocio_en', models.CharField(blank=True, max_length=150, null=True)),
]


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_initial'),
    ]

    operations = [
        separate_add_fields('core', 'testimonio', TESTIMONIO_FIELD_SPECS),
    ]
