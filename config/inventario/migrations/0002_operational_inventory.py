from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

	dependencies = [
		migrations.swappable_dependency(settings.AUTH_USER_MODEL),
		('facturacion', '0002_delivery_workflow'),
		('inventario', '0001_initial'),
		('pedidos', '0007_pedidoitem_inventory_tracking'),
	]

	operations = [
		migrations.DeleteModel(
			name='MovimientoInventarioPendiente',
		),
		migrations.CreateModel(
			name='StockPresentacion',
			fields=[
				('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
				('stock_fisico', models.PositiveIntegerField(default=0)),
				('stock_reservado', models.PositiveIntegerField(default=0)),
				('stock_disponible', models.PositiveIntegerField(default=0)),
				('actualizado_en', models.DateTimeField(auto_now=True)),
				('presentacion', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='stock_operativo', to='productos.presentacion')),
			],
			options={'ordering': ('presentacion__producto__nombre', 'presentacion__nombre')},
		),
		migrations.CreateModel(
			name='InventarioMovimiento',
			fields=[
				('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
				('categoria', models.CharField(choices=[('ENTRADA', 'Entry'), ('SALIDA', 'Exit'), ('AJUSTE', 'Adjustment'), ('RESERVA', 'Reservation')], max_length=20)),
				('tipo', models.CharField(choices=[('ENTRADA_MANUAL', 'Manual entry'), ('SALIDA_MANUAL', 'Manual exit'), ('AJUSTE_POSITIVO', 'Positive adjustment'), ('AJUSTE_NEGATIVO', 'Negative adjustment'), ('RESERVA_PEDIDO', 'Order reservation'), ('LIBERACION_PEDIDO', 'Order reservation release'), ('SALIDA_PICKING', 'Picking deduction'), ('AJUSTE_PICKING', 'Picking adjustment'), ('ENTRADA_NOTA_CREDITO', 'Credit note return'), ('REVERSO_NOTA_CREDITO', 'Credit note reversal'), ('ANULACION_PEDIDO', 'Order cancellation reversal')], max_length=30)),
				('cantidad', models.PositiveIntegerField(default=0)),
				('delta_fisico', models.IntegerField(default=0)),
				('delta_reservado', models.IntegerField(default=0)),
				('stock_fisico_anterior', models.PositiveIntegerField(default=0)),
				('stock_fisico_posterior', models.PositiveIntegerField(default=0)),
				('stock_reservado_anterior', models.PositiveIntegerField(default=0)),
				('stock_reservado_posterior', models.PositiveIntegerField(default=0)),
				('stock_disponible_anterior', models.PositiveIntegerField(default=0)),
				('stock_disponible_posterior', models.PositiveIntegerField(default=0)),
				('referencia', models.CharField(max_length=120)),
				('idempotency_key', models.CharField(blank=True, max_length=160, null=True, unique=True)),
				('observacion', models.TextField(blank=True)),
				('creado_en', models.DateTimeField(auto_now_add=True)),
				('creado_por', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='movimientos_inventario_creados', to=settings.AUTH_USER_MODEL)),
				('invoice', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='movimientos_inventario', to='facturacion.invoice')),
				('nota_ajuste', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='movimientos_inventario', to='facturacion.notaajuste')),
				('nota_ajuste_item', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='movimientos_inventario', to='facturacion.notaajusteitem')),
				('pedido', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='movimientos_inventario', to='pedidos.pedido')),
				('pedido_item', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='movimientos_inventario', to='pedidos.pedidoitem')),
				('presentacion', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='movimientos_inventario', to='productos.presentacion')),
				('stock', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='movimientos', to='inventario.stockpresentacion')),
			],
			options={'ordering': ('-creado_en', '-id')},
		),
	]