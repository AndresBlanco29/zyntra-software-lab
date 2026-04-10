from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='Notificacion',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('destino', models.CharField(default='BACKOFFICE', max_length=20)),
                ('tipo', models.CharField(choices=[('COTIZACION', 'Cotizacion'), ('PEDIDO', 'Pedido')], max_length=20)),
                ('titulo', models.CharField(max_length=255)),
                ('mensaje', models.TextField(blank=True)),
                ('url', models.CharField(blank=True, max_length=255)),
                ('leida', models.BooleanField(default=False)),
                ('creada_en', models.DateTimeField(auto_now_add=True)),
            ],
            options={'ordering': ('-creada_en',)},
        ),
    ]