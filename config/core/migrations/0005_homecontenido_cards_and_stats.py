from django.db import migrations, models

from config.core.migration_utils import separate_add_fields

HOME_CONTENIDO_CARD_FIELD_SPECS = [
    ('beneficio_1_subtitulo', models.CharField(default='Productos siempre disponibles', max_length=160)),
    ('beneficio_1_subtitulo_en', models.CharField(blank=True, max_length=160, null=True)),
    ('beneficio_1_titulo', models.CharField(default='Abastecimiento Inteligente', max_length=120)),
    ('beneficio_1_titulo_en', models.CharField(blank=True, max_length=120, null=True)),
    ('beneficio_2_subtitulo', models.CharField(default='Entrega rapida y segura', max_length=160)),
    ('beneficio_2_subtitulo_en', models.CharField(blank=True, max_length=160, null=True)),
    ('beneficio_2_titulo', models.CharField(default='Logica Eficiente', max_length=120)),
    ('beneficio_2_titulo_en', models.CharField(blank=True, max_length=120, null=True)),
    ('beneficio_3_subtitulo', models.CharField(default='Compromiso y confianza', max_length=160)),
    ('beneficio_3_subtitulo_en', models.CharField(blank=True, max_length=160, null=True)),
    ('beneficio_3_titulo', models.CharField(default='Relacion a Largo Plazo', max_length=120)),
    ('beneficio_3_titulo_en', models.CharField(blank=True, max_length=120, null=True)),
    ('beneficio_4_subtitulo', models.CharField(default='Apoyo para tu expansion', max_length=160)),
    ('beneficio_4_subtitulo_en', models.CharField(blank=True, max_length=160, null=True)),
    ('beneficio_4_titulo', models.CharField(default='Crecimiento Sostenible', max_length=120)),
    ('beneficio_4_titulo_en', models.CharField(blank=True, max_length=120, null=True)),
    ('estadistica_1_label', models.CharField(default='Negocios Abastecidos', max_length=120)),
    ('estadistica_1_label_en', models.CharField(blank=True, max_length=120, null=True)),
    ('estadistica_1_valor', models.CharField(default='+100', max_length=60)),
    ('estadistica_1_valor_en', models.CharField(blank=True, max_length=60, null=True)),
    ('estadistica_2_label', models.CharField(default='Pedidos Exitosos', max_length=120)),
    ('estadistica_2_label_en', models.CharField(blank=True, max_length=120, null=True)),
    ('estadistica_2_valor', models.CharField(default='98%', max_length=60)),
    ('estadistica_2_valor_en', models.CharField(blank=True, max_length=60, null=True)),
    ('estadistica_3_label', models.CharField(default='De Experiencia', max_length=120)),
    ('estadistica_3_label_en', models.CharField(blank=True, max_length=120, null=True)),
    ('estadistica_3_valor', models.CharField(default='+5 Anos', max_length=60)),
    ('estadistica_3_valor_en', models.CharField(blank=True, max_length=60, null=True)),
]


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0004_homecontenido_quienes_fields'),
    ]

    operations = [
        separate_add_fields('core', 'homecontenido', HOME_CONTENIDO_CARD_FIELD_SPECS),
    ]
