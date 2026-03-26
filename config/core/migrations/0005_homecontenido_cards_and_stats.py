from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0004_homecontenido_quienes_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='homecontenido',
            name='beneficio_1_subtitulo',
            field=models.CharField(default='Productos siempre disponibles', max_length=160),
        ),
        migrations.AddField(
            model_name='homecontenido',
            name='beneficio_1_subtitulo_en',
            field=models.CharField(blank=True, max_length=160, null=True),
        ),
        migrations.AddField(
            model_name='homecontenido',
            name='beneficio_1_titulo',
            field=models.CharField(default='Abastecimiento Inteligente', max_length=120),
        ),
        migrations.AddField(
            model_name='homecontenido',
            name='beneficio_1_titulo_en',
            field=models.CharField(blank=True, max_length=120, null=True),
        ),
        migrations.AddField(
            model_name='homecontenido',
            name='beneficio_2_subtitulo',
            field=models.CharField(default='Entrega rapida y segura', max_length=160),
        ),
        migrations.AddField(
            model_name='homecontenido',
            name='beneficio_2_subtitulo_en',
            field=models.CharField(blank=True, max_length=160, null=True),
        ),
        migrations.AddField(
            model_name='homecontenido',
            name='beneficio_2_titulo',
            field=models.CharField(default='Logica Eficiente', max_length=120),
        ),
        migrations.AddField(
            model_name='homecontenido',
            name='beneficio_2_titulo_en',
            field=models.CharField(blank=True, max_length=120, null=True),
        ),
        migrations.AddField(
            model_name='homecontenido',
            name='beneficio_3_subtitulo',
            field=models.CharField(default='Compromiso y confianza', max_length=160),
        ),
        migrations.AddField(
            model_name='homecontenido',
            name='beneficio_3_subtitulo_en',
            field=models.CharField(blank=True, max_length=160, null=True),
        ),
        migrations.AddField(
            model_name='homecontenido',
            name='beneficio_3_titulo',
            field=models.CharField(default='Relacion a Largo Plazo', max_length=120),
        ),
        migrations.AddField(
            model_name='homecontenido',
            name='beneficio_3_titulo_en',
            field=models.CharField(blank=True, max_length=120, null=True),
        ),
        migrations.AddField(
            model_name='homecontenido',
            name='beneficio_4_subtitulo',
            field=models.CharField(default='Apoyo para tu expansion', max_length=160),
        ),
        migrations.AddField(
            model_name='homecontenido',
            name='beneficio_4_subtitulo_en',
            field=models.CharField(blank=True, max_length=160, null=True),
        ),
        migrations.AddField(
            model_name='homecontenido',
            name='beneficio_4_titulo',
            field=models.CharField(default='Crecimiento Sostenible', max_length=120),
        ),
        migrations.AddField(
            model_name='homecontenido',
            name='beneficio_4_titulo_en',
            field=models.CharField(blank=True, max_length=120, null=True),
        ),
        migrations.AddField(
            model_name='homecontenido',
            name='estadistica_1_label',
            field=models.CharField(default='Negocios Abastecidos', max_length=120),
        ),
        migrations.AddField(
            model_name='homecontenido',
            name='estadistica_1_label_en',
            field=models.CharField(blank=True, max_length=120, null=True),
        ),
        migrations.AddField(
            model_name='homecontenido',
            name='estadistica_1_valor',
            field=models.CharField(default='+100', max_length=60),
        ),
        migrations.AddField(
            model_name='homecontenido',
            name='estadistica_1_valor_en',
            field=models.CharField(blank=True, max_length=60, null=True),
        ),
        migrations.AddField(
            model_name='homecontenido',
            name='estadistica_2_label',
            field=models.CharField(default='Pedidos Exitosos', max_length=120),
        ),
        migrations.AddField(
            model_name='homecontenido',
            name='estadistica_2_label_en',
            field=models.CharField(blank=True, max_length=120, null=True),
        ),
        migrations.AddField(
            model_name='homecontenido',
            name='estadistica_2_valor',
            field=models.CharField(default='98%', max_length=60),
        ),
        migrations.AddField(
            model_name='homecontenido',
            name='estadistica_2_valor_en',
            field=models.CharField(blank=True, max_length=60, null=True),
        ),
        migrations.AddField(
            model_name='homecontenido',
            name='estadistica_3_label',
            field=models.CharField(default='De Experiencia', max_length=120),
        ),
        migrations.AddField(
            model_name='homecontenido',
            name='estadistica_3_label_en',
            field=models.CharField(blank=True, max_length=120, null=True),
        ),
        migrations.AddField(
            model_name='homecontenido',
            name='estadistica_3_valor',
            field=models.CharField(default='+5 Anos', max_length=60),
        ),
        migrations.AddField(
            model_name='homecontenido',
            name='estadistica_3_valor_en',
            field=models.CharField(blank=True, max_length=60, null=True),
        ),
    ]
