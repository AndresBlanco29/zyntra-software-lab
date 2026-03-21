from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0002_testimonio_negocio_en_testimonio_comentario_en'),
    ]

    operations = [
        migrations.CreateModel(
            name='HomeContenido',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('hero_titulo_principal', models.CharField(default='Tu Mayorista de', max_length=120)),
                ('hero_titulo_principal_en', models.CharField(blank=True, max_length=120, null=True)),
                ('hero_titulo_resaltado', models.CharField(default='Productos Latinos', max_length=120)),
                ('hero_titulo_resaltado_en', models.CharField(blank=True, max_length=120, null=True)),
                ('hero_titulo_final', models.CharField(default='de Confianza', max_length=120)),
                ('hero_titulo_final_en', models.CharField(blank=True, max_length=120, null=True)),
                ('hero_subtitulo', models.CharField(default='Haz tus pedidos de forma rapida y segura. Compras al por mayor.', max_length=220)),
                ('hero_subtitulo_en', models.CharField(blank=True, max_length=220, null=True)),
                ('hero_boton_texto', models.CharField(default='Ver Catalogo', max_length=80)),
                ('hero_boton_texto_en', models.CharField(blank=True, max_length=80, null=True)),
                ('cta_titulo', models.CharField(default='Tienes una tienda? Solicita tu cuenta mayorista hoy', max_length=220)),
                ('cta_titulo_en', models.CharField(blank=True, max_length=220, null=True)),
                ('cta_boton_registro_texto', models.CharField(default='Crear Cuenta', max_length=80)),
                ('cta_boton_registro_texto_en', models.CharField(blank=True, max_length=80, null=True)),
                ('cta_boton_catalogo_texto', models.CharField(default='Ver Catalogo', max_length=80)),
                ('cta_boton_catalogo_texto_en', models.CharField(blank=True, max_length=80, null=True)),
                ('activo', models.BooleanField(default=True)),
                ('actualizado', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['-actualizado'],
            },
        ),
    ]
