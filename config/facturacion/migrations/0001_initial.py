from decimal import Decimal

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

	initial = True

	dependencies = [
		migrations.swappable_dependency(settings.AUTH_USER_MODEL),
		('clientes', '0001_initial'),
		('pedidos', '0006_pedido_estado_invoice_generada'),
		('productos', '0014_alter_configuracionprecios_options'),
	]

	operations = [
		migrations.CreateModel(
			name='Invoice',
			fields=[
				('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
				('numero', models.CharField(blank=True, max_length=30, unique=True)),
				('metodo_entrega', models.CharField(choices=[('RUTA_DRIVER', 'Route with driver'), ('LTG', 'LTG'), ('CUSTOMER_PICK_UP', 'Customer Pick Up')], max_length=30)),
				('estado', models.CharField(choices=[('GENERADA', 'Invoice generated'), ('ANULADA', 'Cancelled')], default='GENERADA', max_length=20)),
				('subtotal', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12)),
				('total_creditos', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12)),
				('total_debitos', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12)),
				('total_neto', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12)),
				('saldo_cliente', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12)),
				('despachador_notificado', models.BooleanField(default=False)),
				('notificado_en', models.DateTimeField(blank=True, null=True)),
				('pdf_generado_en', models.DateTimeField(blank=True, null=True)),
				('creada_en', models.DateTimeField(auto_now_add=True)),
				('actualizada_en', models.DateTimeField(auto_now=True)),
				('cliente', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='invoices', to='clientes.cliente')),
				('creada_por', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='invoices_creadas', to=settings.AUTH_USER_MODEL)),
				('driver', models.ForeignKey(blank=True, limit_choices_to={'role': 'driver'}, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='invoices_asignadas', to=settings.AUTH_USER_MODEL)),
				('pedido', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='invoice', to='pedidos.pedido')),
			],
			options={'ordering': ('-creada_en',)},
		),
		migrations.CreateModel(
			name='InvoiceItem',
			fields=[
				('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
				('producto_nombre', models.CharField(max_length=255)),
				('presentacion_nombre', models.CharField(max_length=120)),
				('cantidad_facturada', models.PositiveIntegerField(default=1)),
				('precio_unitario', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=10)),
				('subtotal', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12)),
				('invoice', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='items', to='facturacion.invoice')),
				('pedido_item', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='invoice_items', to='pedidos.pedidoitem')),
				('presentacion', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='productos.presentacion')),
			],
			options={'ordering': ('id',)},
		),
		migrations.CreateModel(
			name='NotaAjuste',
			fields=[
				('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
				('numero', models.CharField(blank=True, max_length=30, unique=True)),
				('tipo_documento', models.CharField(choices=[('CREDITO', 'Credit note'), ('DEBITO', 'Debit note')], max_length=20)),
				('estado', models.CharField(choices=[('BORRADOR', 'Draft'), ('APROBADA', 'Approved'), ('ANULADA', 'Cancelled')], default='BORRADOR', max_length=20)),
				('motivo', models.CharField(choices=[('DAMAGE', 'Damage'), ('DEFECT', 'Defect')], max_length=20)),
				('tipo_credito', models.CharField(blank=True, choices=[('CREDIT_DUMP', 'Credit Dump'), ('CREDIT_RETURN', 'Credit Return')], max_length=20)),
				('descripcion', models.TextField(blank=True)),
				('total', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12)),
				('impacto_saldo', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12)),
				('inventario_estado', models.CharField(choices=[('NO_APLICA', 'Not applicable'), ('PENDIENTE', 'Pending'), ('ANULADO', 'Cancelled')], default='NO_APLICA', max_length=20)),
				('creada_en', models.DateTimeField(auto_now_add=True)),
				('aprobada_en', models.DateTimeField(blank=True, null=True)),
				('anulada_en', models.DateTimeField(blank=True, null=True)),
				('aprobada_por', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='notas_ajuste_aprobadas', to=settings.AUTH_USER_MODEL)),
				('creada_por', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='notas_ajuste_creadas', to=settings.AUTH_USER_MODEL)),
				('invoice', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='notas_ajuste', to='facturacion.invoice')),
			],
			options={'ordering': ('-creada_en',)},
		),
		migrations.CreateModel(
			name='NotaAjusteItem',
			fields=[
				('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
				('descripcion', models.CharField(max_length=255)),
				('cantidad', models.PositiveIntegerField(default=1)),
				('monto_unitario', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=10)),
				('total', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12)),
				('invoice_item', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='notas_ajuste', to='facturacion.invoiceitem')),
				('nota', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='items', to='facturacion.notaajuste')),
				('presentacion', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='productos.presentacion')),
			],
			options={'ordering': ('id',)},
		),
	]