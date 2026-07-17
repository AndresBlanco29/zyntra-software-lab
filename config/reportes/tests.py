import base64
from io import StringIO
from decimal import Decimal

from django.core import mail
from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from config.clientes.models import Cliente
from config.facturacion.services import generar_invoice_desde_picking, start_delivery_route, complete_driver_delivery
from config.inventario.services import registrar_entrada_manual
from config.pedidos.models import Pedido, PedidoItem
from config.productos.models import Categoria, Marca, Presentacion, Producto
from config.usuarios.models import Usuario


@override_settings(
	EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
	TWILIO_ACCOUNT_SID='',
	TWILIO_AUTH_TOKEN='',
	TWILIO_SMS_FROM='',
	TWILIO_WHATSAPP_FROM='',
	APP_BASE_URL='https://example.com',
)
class ReportsDashboardTests(TestCase):
	def setUp(self):
		self.backoffice = Usuario.objects.create_user(username='backoffice-reports', password='secret123', role='backoffice')
		self.driver = Usuario.objects.create_user(username='driver-reports', password='secret123', role='driver', first_name='Driver', last_name='Report')
		self.vendor = Usuario.objects.create_user(username='vendor-reports', password='secret123', role='vendedor', first_name='Vendor', last_name='Report')
		self.cliente_user = Usuario.objects.create_user(username='cliente-reports', password='secret123', role='cliente')
		self.cliente = Cliente.objects.create(
			usuario=self.cliente_user,
			nombre_empresa='Cliente Reportes',
			telefono='5551112222',
			direccion='123 Main St',
			ciudad='Dallas',
			estado='TX',
			codigo_postal='75001',
			pais='USA',
			sales_tax_number='TX-123',
			certificado_tax='certificados/test.pdf',
		)
		categoria = Categoria.objects.create(nombre='Tortillas Reporte')
		marca = Marca.objects.create(nombre='Marca Reporte')
		producto = Producto.objects.create(nombre='Tortilla Reporte', categoria=categoria, marca=marca, codigo_barras='7501234567891')
		self.presentacion = Presentacion.objects.create(
			producto=producto,
			nombre='Caja',
			unidades=12,
			tipo_contenido='unidades',
			precio_1=Decimal('15.00'),
			precio_2=Decimal('16.00'),
			precio_3=Decimal('17.00'),
			precio_4=Decimal('18.00'),
			precio_5=Decimal('19.00'),
		)
		registrar_entrada_manual(presentacion=self.presentacion, cantidad=25, observacion='Seed stock reports')
		self.signature_data = 'data:image/png;base64,' + base64.b64encode(
			base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO6pS7QAAAAASUVORK5CYII=')
		).decode('ascii')
		self.cheque_image = SimpleUploadedFile(
			'cheque-proof.png',
			base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO6pS7QAAAAASUVORK5CYII='),
			content_type='image/png',
		)

	def _create_verified_order(self, *, total='45.00', quantity=3):
		pedido = Pedido.objects.create(
			cliente=self.cliente,
			vendedor=self.vendor,
			origen='VENDEDOR',
			estado='VERIFICADO_AJUSTADO',
			total=Decimal(total),
		)
		PedidoItem.objects.create(
			pedido=pedido,
			presentacion=self.presentacion,
			cantidad_solicitada=quantity,
			cantidad=quantity,
			precio=Decimal(total) / Decimal(quantity),
			subtotal=Decimal(total),
		)
		return pedido

	def _create_paid_delivery(self):
		pedido = self._create_verified_order()
		invoice = generar_invoice_desde_picking(
			pedido=pedido,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
		)
		delivery = invoice.delivery
		start_delivery_route(delivery=delivery, driver_user=self.driver)
		complete_driver_delivery(
			delivery=delivery,
			driver_user=self.driver,
			payload={
				'estado_pago': 'PAGADO',
				'metodo_pago': 'MIXTO',
				'monto_pagado_cash': '20.00',
				'monto_pagado_cheque': '25.00',
				'cheque_numero': 'CHK-REPORT',
				'cheque_banco': 'Bank Report',
				'recibido_por': 'Cliente Reportes',
				'firma_cliente_data': self.signature_data,
				'notas_driver': 'Entrega cerrada',
			},
			evidence_files=[],
			cheque_image_file=self.cheque_image,
		)
		return invoice

	def test_reports_dashboard_renders_with_metrics(self):
		invoice = self._create_paid_delivery()
		self.client.force_login(self.backoffice)

		response = self.client.get(reverse('reportes_dashboard'))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Reports Center')
		self.assertEqual(response.context['close_snapshot']['deliveries_count'], 1)
		self.assertEqual(response.context['close_snapshot']['collected_amount'], invoice.total_neto)
		self.assertEqual(response.context['summary_cards'][0]['value'], 1)
		self.assertEqual(response.context['driver_rows'][0]['name'], 'Driver Report')
		self.assertEqual(response.context['vendor_rows'][0]['name'], 'Vendor Report')
		self.assertEqual(response.context['top_products'][0]['name'], 'Tortilla Reporte')
		self.assertEqual(response.context['customer_rows'][0]['name'], 'Cliente Reportes')
		self.assertEqual(response.context['category_rows'][0]['name'], 'Tortillas Reporte')
		self.assertIn('product_rankings', response.context)
		self.assertIn('inventory', response.context)
		self.assertIn('period_sales', response.context)
		self.assertContains(response, 'Excel')
		self.assertContains(response, 'Business Intelligence')
		self.assertContains(response, 'Send by email')

	def test_reports_dashboard_handles_delivery_without_driver(self):
		invoice = self._create_paid_delivery()
		invoice.delivery.driver = None
		invoice.delivery.save(update_fields=['driver'])
		self.client.force_login(self.backoffice)

		response = self.client.get(reverse('reportes_dashboard'))

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.context['driver_rows'][0]['name'], 'Unassigned')
		self.assertEqual(response.context['recent_close_rows'][0]['driver_name'], 'Unassigned')

	def test_reports_exports_excel_and_pdf(self):
		self._create_paid_delivery()
		self.client.force_login(self.backoffice)

		excel_response = self.client.get(reverse('reportes_export_excel'))
		pdf_response = self.client.get(reverse('reportes_export_pdf'))

		self.assertEqual(excel_response.status_code, 200)
		self.assertIn('application/vnd.ms-excel', excel_response['Content-Type'])
		self.assertIn('attachment;', excel_response['Content-Disposition'])
		self.assertContains(excel_response, 'Reports Center')
		self.assertEqual(pdf_response.status_code, 200)
		self.assertEqual(pdf_response['Content-Type'], 'application/pdf')
		self.assertIn('attachment;', pdf_response['Content-Disposition'])

	def test_reports_dashboard_filters_by_driver_and_section_export(self):
		self._create_paid_delivery()
		other_driver = Usuario.objects.create_user(username='other-driver-report', password='secret123', role='driver', first_name='Other', last_name='Driver')
		self.client.force_login(self.backoffice)

		response = self.client.get(reverse('reportes_dashboard'), {'driver_id': other_driver.id, 'section': 'drivers'})
		excel_response = self.client.get(reverse('reportes_export_excel'), {'driver_id': self.driver.id, 'section': 'drivers'})

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.context['driver_rows'], [])
		self.assertContains(excel_response, 'Driver close')
		self.assertNotContains(excel_response, 'Executive summary')

	def test_reports_can_send_email_now(self):
		self._create_paid_delivery()
		self.backoffice.email = 'backoffice@example.com'
		self.backoffice.save(update_fields=['email'])
		self.client.force_login(self.backoffice)

		response = self.client.post(reverse('reportes_send_email_now'), {
			'period': 'today',
			'section': 'summary',
		})

		self.assertEqual(response.status_code, 302)
		self.assertEqual(len(mail.outbox), 1)
		self.assertIn('report', mail.outbox[0].subject.lower())
		self.assertEqual(mail.outbox[0].attachments[0][2], 'application/pdf')

	def test_daily_reports_command_sends_email(self):
		self._create_paid_delivery()
		self.backoffice.email = 'backoffice@example.com'
		self.backoffice.save(update_fields=['email'])

		call_command('send_daily_reports', stdout=StringIO())

		self.assertEqual(len(mail.outbox), 1)
		self.assertIn('today', mail.outbox[0].body.lower())

	def test_user_without_reports_permission_is_redirected(self):
		vendor_user = Usuario.objects.create_user(username='vendor-no-reports', password='secret123', role='vendedor')
		self.client.force_login(vendor_user)

		response = self.client.get(reverse('reportes_dashboard'))

		self.assertEqual(response.status_code, 302)
