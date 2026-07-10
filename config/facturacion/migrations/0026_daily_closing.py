from decimal import Decimal

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

	dependencies = [
		migrations.swappable_dependency(settings.AUTH_USER_MODEL),
		('facturacion', '0025_delivery_customer_pickup'),
	]

	operations = [
		migrations.AddField(
			model_name='invoice',
			name='cierre_liberada',
			field=models.BooleanField(db_index=True, default=False),
		),
		migrations.AddField(
			model_name='invoice',
			name='cierre_liberada_en',
			field=models.DateTimeField(blank=True, null=True),
		),
		migrations.AddField(
			model_name='invoice',
			name='cierre_liberada_por',
			field=models.ForeignKey(
				blank=True,
				null=True,
				on_delete=django.db.models.deletion.SET_NULL,
				related_name='invoices_liberadas_cierre',
				to=settings.AUTH_USER_MODEL,
			),
		),
		migrations.CreateModel(
			name='CierreDiario',
			fields=[
				('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
				('fecha', models.DateField(db_index=True)),
				(
					'estado',
					models.CharField(
						choices=[
							('ABIERTO', 'Open'),
							('EN_REVISION', 'In review'),
							('LISTO', 'Ready to release'),
							('CERRADO', 'Closed'),
						],
						db_index=True,
						default='ABIERTO',
						max_length=20,
					),
				),
				('notas', models.TextField(blank=True)),
				('creado_en', models.DateTimeField(auto_now_add=True)),
				('actualizado_en', models.DateTimeField(auto_now=True)),
				('cerrado_en', models.DateTimeField(blank=True, null=True)),
				('total_documentos', models.PositiveIntegerField(default=0)),
				('total_invoices', models.PositiveIntegerField(default=0)),
				('monto_total', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=14)),
				('monto_pagado', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=14)),
				('balance_abierto', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=14)),
				('total_creditos', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=14)),
				('items_listos', models.PositiveIntegerField(default=0)),
				('items_bloqueados', models.PositiveIntegerField(default=0)),
				('items_liberados', models.PositiveIntegerField(default=0)),
				(
					'cerrado_por',
					models.ForeignKey(
						blank=True,
						null=True,
						on_delete=django.db.models.deletion.SET_NULL,
						related_name='cierres_diarios_cerrados',
						to=settings.AUTH_USER_MODEL,
					),
				),
				(
					'creado_por',
					models.ForeignKey(
						blank=True,
						null=True,
						on_delete=django.db.models.deletion.SET_NULL,
						related_name='cierres_diarios_creados',
						to=settings.AUTH_USER_MODEL,
					),
				),
			],
			options={
				'verbose_name': 'Daily closing',
				'verbose_name_plural': 'Daily closings',
				'ordering': ('-fecha', '-id'),
			},
		),
		migrations.CreateModel(
			name='CierreDiarioItem',
			fields=[
				('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
				(
					'estado',
					models.CharField(
						choices=[
							('PENDIENTE', 'Pending review'),
							('EN_REVISION', 'In review'),
							('BLOQUEADA', 'Blocked'),
							('LISTA', 'Ready'),
							('EXCLUIDA', 'Excluded'),
							('LIBERADA', 'Released to QuickBooks'),
						],
						db_index=True,
						default='PENDIENTE',
						max_length=20,
					),
				),
				('factura_revisada', models.BooleanField(default=False)),
				('pago_verificado', models.BooleanField(default=False)),
				('entrega_confirmada', models.BooleanField(default=False)),
				('devolucion_detectada', models.BooleanField(default=False)),
				('credit_memo_requerida', models.BooleanField(default=False)),
				('credit_memo_ok', models.BooleanField(default=False)),
				('lista_para_exportar', models.BooleanField(default=False)),
				('notas', models.TextField(blank=True)),
				('alertas', models.JSONField(blank=True, default=list)),
				('revisado_en', models.DateTimeField(blank=True, null=True)),
				('liberado_en', models.DateTimeField(blank=True, null=True)),
				('creado_en', models.DateTimeField(auto_now_add=True)),
				('actualizado_en', models.DateTimeField(auto_now=True)),
				(
					'cierre',
					models.ForeignKey(
						on_delete=django.db.models.deletion.CASCADE,
						related_name='items',
						to='facturacion.cierrediario',
					),
				),
				(
					'invoice',
					models.ForeignKey(
						on_delete=django.db.models.deletion.PROTECT,
						related_name='cierres_diarios_items',
						to='facturacion.invoice',
					),
				),
				(
					'revisado_por',
					models.ForeignKey(
						blank=True,
						null=True,
						on_delete=django.db.models.deletion.SET_NULL,
						related_name='cierres_diarios_items_revisados',
						to=settings.AUTH_USER_MODEL,
					),
				),
			],
			options={
				'verbose_name': 'Daily closing item',
				'verbose_name_plural': 'Daily closing items',
				'ordering': ('invoice_id',),
			},
		),
		migrations.AddConstraint(
			model_name='cierrediarioitem',
			constraint=models.UniqueConstraint(
				fields=('cierre', 'invoice'),
				name='facturacion_cierrediarioitem_cierre_invoice_uniq',
			),
		),
	]
