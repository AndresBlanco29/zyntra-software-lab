from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('clientes', '0001_initial'),
        ('cotizaciones', '0002_alter_cotizacion_vendedor'),
        ('productos', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='Pedido',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('origen', models.CharField(choices=[('CLIENTE', 'Cliente'), ('VENDEDOR', 'Vendedor')], max_length=20)),
                ('canal_toma', models.CharField(blank=True, default='', max_length=20)),
                ('estado', models.CharField(choices=[('RECIBIDO', 'Recibido'), ('EN_GESTION', 'En gestion'), ('LISTO_PARA_PICKING', 'Listo para picking'), ('DESPACHADO', 'Despachado'), ('CANCELADO', 'Cancelado')], default='RECIBIDO', max_length=30)),
                ('nota_cliente', models.TextField(blank=True)),
                ('nota_backoffice', models.TextField(blank=True)),
                ('acepta_terminos', models.BooleanField(default=False)),
                ('total', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('creada_en', models.DateTimeField(auto_now_add=True)),
                ('actualizada_en', models.DateTimeField(auto_now=True)),
                ('cliente', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='pedidos', to='clientes.cliente')),
                ('cotizacion', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='pedido_generado', to='cotizaciones.cotizacion')),
                ('vendedor', models.ForeignKey(blank=True, limit_choices_to={'role': 'vendedor'}, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='pedidos_generados', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ('-creada_en',)},
        ),
        migrations.CreateModel(
            name='PedidoItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('cantidad', models.PositiveIntegerField(default=1)),
                ('precio', models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ('subtotal', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('pedido', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='items', to='pedidos.pedido')),
                ('presentacion', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='productos.presentacion')),
            ],
        ),
    ]