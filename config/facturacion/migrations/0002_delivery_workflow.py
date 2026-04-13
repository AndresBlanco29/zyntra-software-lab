from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def create_deliveries_for_existing_route_invoices(apps, schema_editor):
	Invoice = apps.get_model('facturacion', 'Invoice')
	Delivery = apps.get_model('facturacion', 'Delivery')
	for invoice in Invoice.objects.filter(metodo_entrega='RUTA_DRIVER').exclude(driver__isnull=True).select_related('cliente').iterator():
		Delivery.objects.get_or_create(
			invoice=invoice,
			defaults={
				'driver_id': invoice.driver_id,
				'delivery_address': invoice.cliente.direccion,
				'delivery_city': invoice.cliente.ciudad,
				'delivery_state': invoice.cliente.estado,
				'delivery_postal_code': invoice.cliente.codigo_postal or '',
				'delivery_country': invoice.cliente.pais or 'USA',
			},
		)


class Migration(migrations.Migration):

	dependencies = [
		migrations.swappable_dependency(settings.AUTH_USER_MODEL),
		('facturacion', '0001_initial'),
	]

	operations = [
		migrations.CreateModel(
			name='Delivery',
			fields=[
				('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
				('estado', models.CharField(choices=[('ASIGNADA', 'Assigned'), ('EN_RUTA', 'On route'), ('ENTREGADA_PAGADA', 'Delivered and paid'), ('ENTREGADA_SIN_PAGO', 'Delivered without payment')], default='ASIGNADA', max_length=30)),
				('estado_pago', models.CharField(choices=[('PENDIENTE', 'Pending'), ('PAGADO', 'Paid'), ('NO_PAGADO', 'Unpaid')], default='PENDIENTE', max_length=20)),
				('metodo_pago', models.CharField(blank=True, choices=[('CASH', 'Cash'), ('CHEQUE', 'Cheque'), ('TRANSFERENCIA', 'Transfer'), ('TARJETA', 'Card'), ('ZELLE', 'Zelle'), ('ACH', 'ACH')], max_length=20)),
				('monto_pagado', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
				('recibido_por', models.CharField(blank=True, max_length=160)),
				('motivo_no_pago', models.TextField(blank=True)),
				('notas_driver', models.TextField(blank=True)),
				('firma_cliente', models.ImageField(blank=True, null=True, upload_to='delivery/signatures/')),
				('firma_recibida_en', models.DateTimeField(blank=True, null=True)),
				('cheque_numero', models.CharField(blank=True, max_length=80)),
				('cheque_banco', models.CharField(blank=True, max_length=120)),
				('transferencia_referencia', models.CharField(blank=True, max_length=120)),
				('tarjeta_ultimos_4', models.CharField(blank=True, max_length=4)),
				('tarjeta_autorizacion', models.CharField(blank=True, max_length=80)),
				('zelle_referencia', models.CharField(blank=True, max_length=120)),
				('zelle_remitente', models.CharField(blank=True, max_length=160)),
				('ach_referencia', models.CharField(blank=True, max_length=120)),
				('ach_cuenta_ultimos_4', models.CharField(blank=True, max_length=4)),
				('delivery_address', models.CharField(max_length=255)),
				('delivery_city', models.CharField(max_length=100)),
				('delivery_state', models.CharField(max_length=100)),
				('delivery_postal_code', models.CharField(blank=True, max_length=20)),
				('delivery_country', models.CharField(default='USA', max_length=100)),
				('client_blocked_on_delivery', models.BooleanField(default=False)),
				('client_unlocked_at', models.DateTimeField(blank=True, null=True)),
				('route_started_at', models.DateTimeField(blank=True, null=True)),
				('delivered_at', models.DateTimeField(blank=True, null=True)),
				('notifications_sent_at', models.DateTimeField(blank=True, null=True)),
				('created_at', models.DateTimeField(auto_now_add=True)),
				('updated_at', models.DateTimeField(auto_now=True)),
				('client_unlocked_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='deliveries_unlocked', to=settings.AUTH_USER_MODEL)),
				('driver', models.ForeignKey(limit_choices_to={'role': 'driver'}, on_delete=django.db.models.deletion.PROTECT, related_name='deliveries', to=settings.AUTH_USER_MODEL)),
				('invoice', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='delivery', to='facturacion.invoice')),
			],
			options={'ordering': ('estado', 'created_at')},
		),
		migrations.CreateModel(
			name='DeliveryEvidencePhoto',
			fields=[
				('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
				('image', models.ImageField(upload_to='delivery/evidence/')),
				('caption', models.CharField(blank=True, max_length=255)),
				('uploaded_at', models.DateTimeField(auto_now_add=True)),
				('delivery', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='evidence_photos', to='facturacion.delivery')),
			],
			options={'ordering': ('uploaded_at',)},
		),
		migrations.CreateModel(
			name='DeliveryNotificationLog',
			fields=[
				('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
				('channel', models.CharField(choices=[('EMAIL', 'Email'), ('SMS', 'SMS'), ('WHATSAPP', 'WhatsApp')], max_length=20)),
				('status', models.CharField(choices=[('SENT', 'Sent'), ('FAILED', 'Failed'), ('SKIPPED', 'Skipped')], max_length=20)),
				('target', models.CharField(blank=True, max_length=255)),
				('message', models.TextField(blank=True)),
				('error_message', models.TextField(blank=True)),
				('created_at', models.DateTimeField(auto_now_add=True)),
				('delivery', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='notification_logs', to='facturacion.delivery')),
			],
			options={'ordering': ('created_at',)},
		),
		migrations.RunPython(create_deliveries_for_existing_route_invoices, migrations.RunPython.noop),
	]