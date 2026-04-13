import base64
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.test.utils import override_settings

from config.clientes.models import Cliente
from config.facturacion.models import Delivery, DeliveryNotificationLog, Invoice, NotaAjuste
from config.facturacion.services import aprobar_nota_ajuste, complete_driver_delivery, crear_nota_ajuste_desde_invoice, generar_invoice_desde_picking, start_delivery_route, unlock_client_from_delivery
from config.inventario.models import InventarioMovimiento, StockPresentacion
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
class InvoiceFlowTests(TestCase):
	def setUp(self):
		self.backoffice = Usuario.objects.create_user(username='backoffice-fact', password='secret123', role='backoffice')
		self.driver = Usuario.objects.create_user(username='driver-fact', password='secret123', role='driver')
		self.cliente_user = Usuario.objects.create_user(username='cliente-fact', password='secret123', role='cliente')
		self.cliente = Cliente.objects.create(
			usuario=self.cliente_user,
			nombre_empresa='Cliente Facturacion',
			telefono='5551112222',
			direccion='123 Main St',
			ciudad='Dallas',
			estado='TX',
			codigo_postal='75001',
			pais='USA',
			sales_tax_number='TX-123',
			certificado_tax='certificados/test.pdf',
		)
		categoria = Categoria.objects.create(nombre='Tortillas')
		marca = Marca.objects.create(nombre='Marca Test')
		producto = Producto.objects.create(nombre='Tortilla 12', categoria=categoria, marca=marca)
		self.presentacion = Presentacion.objects.create(
			producto=producto,
			nombre='Caja',
			unidades=12,
			tipo_contenido='caja',
			precio_1=Decimal('15.00'),
		)
		self.pedido = Pedido.objects.create(
			cliente=self.cliente,
			origen='CLIENTE',
			estado='VERIFICADO_AJUSTADO',
			total=Decimal('45.00'),
		)
		self.pedido_item = PedidoItem.objects.create(
			pedido=self.pedido,
			presentacion=self.presentacion,
			cantidad_solicitada=4,
			cantidad=3,
			precio=Decimal('15.00'),
			subtotal=Decimal('45.00'),
		)
		self.signature_data = 'data:image/png;base64,' + base64.b64encode(
			base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO6pS7QAAAAASUVORK5CYII=')
		).decode('ascii')
		self.photo_file = SimpleUploadedFile(
			'evidence.png',
			base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO6pS7QAAAAASUVORK5CYII='),
			content_type='image/png',
		)
		registrar_entrada_manual(presentacion=self.presentacion, cantidad=25, observacion='Seed stock')

	def test_generate_invoice_uses_verified_quantities(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
		)

		self.pedido.refresh_from_db()
		self.assertEqual(self.pedido.estado, 'INVOICE_GENERADA')
		self.assertEqual(invoice.items.count(), 1)
		self.assertEqual(invoice.items.first().cantidad_facturada, 3)
		self.assertEqual(invoice.saldo_cliente, Decimal('45.00'))
		self.assertTrue(invoice.despachador_notificado)
		self.assertTrue(hasattr(invoice, 'delivery'))

	def test_generate_invoice_requires_verified_order(self):
		self.pedido.estado = 'PARA_VERIFICAR'
		self.pedido.save(update_fields=['estado'])

		with self.assertRaises(ValidationError):
			generar_invoice_desde_picking(
				pedido=self.pedido,
				metodo_entrega='LTG',
				driver=None,
				usuario=self.backoffice,
			)

	def test_generate_invoice_requires_driver_for_route(self):
		with self.assertRaises(ValidationError):
			generar_invoice_desde_picking(
				pedido=self.pedido,
				metodo_entrega='RUTA_DRIVER',
				driver=None,
				usuario=self.backoffice,
			)

	def test_credit_note_updates_balance(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='LTG',
			driver=None,
			usuario=self.backoffice,
		)
		nota = crear_nota_ajuste_desde_invoice(
			invoice=invoice,
			tipo_documento='CREDITO',
			motivo='DAMAGE',
			tipo_credito='CREDIT_RETURN',
			descripcion='Producto dañado',
			usuario=self.backoffice,
			items_payload=[{
				'invoice_item': invoice.items.first(),
				'cantidad': 1,
				'monto_unitario': Decimal('15.00'),
			}],
		)

		aprobar_nota_ajuste(nota=nota, usuario=self.backoffice)
		invoice.refresh_from_db()
		nota.refresh_from_db()

		self.assertEqual(nota.estado, 'APROBADA')
		self.assertEqual(invoice.total_creditos, Decimal('15.00'))
		self.assertEqual(invoice.saldo_cliente, Decimal('30.00'))
		self.assertEqual(nota.inventario_estado, 'PROCESADO')
		self.assertEqual(StockPresentacion.objects.get(presentacion=self.presentacion).stock_fisico, 26)
		self.assertTrue(InventarioMovimiento.objects.filter(nota_ajuste=nota, tipo='ENTRADA_NOTA_CREDITO').exists())

	def test_debit_note_updates_balance_without_inventory(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
		)
		nota = crear_nota_ajuste_desde_invoice(
			invoice=invoice,
			tipo_documento='DEBITO',
			motivo='DEFECT',
			tipo_credito='',
			descripcion='Recargo operativo',
			usuario=self.backoffice,
			items_payload=[{
				'invoice_item': invoice.items.first(),
				'cantidad': 1,
				'monto_unitario': Decimal('5.00'),
			}],
		)

		aprobar_nota_ajuste(nota=nota, usuario=self.backoffice)
		invoice.refresh_from_db()

		self.assertEqual(invoice.total_debitos, Decimal('5.00'))
		self.assertEqual(invoice.saldo_cliente, Decimal('50.00'))

	def test_driver_can_complete_paid_delivery(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
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
				'metodo_pago': 'TARJETA',
				'monto_pagado': '45.00',
				'recibido_por': 'Juan Perez',
				'tarjeta_ultimos_4': '1234',
				'tarjeta_autorizacion': 'AUTH-1',
				'firma_cliente_data': self.signature_data,
				'notas_driver': 'Entrega correcta',
			},
			evidence_files=[],
		)

		delivery.refresh_from_db()
		self.assertEqual(delivery.estado, 'ENTREGADA_PAGADA')
		self.assertEqual(delivery.estado_pago, 'PAGADO')
		self.assertFalse(delivery.invoice.cliente.credit_hold)
		self.assertTrue(DeliveryNotificationLog.objects.filter(delivery=delivery).count(), 3)

	def test_driver_non_payment_blocks_customer_and_requires_photo(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
		)
		delivery = invoice.delivery

		with self.assertRaises(ValidationError):
			complete_driver_delivery(
				delivery=delivery,
				driver_user=self.driver,
				payload={
					'estado_pago': 'NO_PAGADO',
					'recibido_por': 'Maria',
					'motivo_no_pago': 'Caja cerrada',
					'firma_cliente_data': self.signature_data,
				},
				evidence_files=[],
			)

		complete_driver_delivery(
			delivery=delivery,
			driver_user=self.driver,
			payload={
				'estado_pago': 'NO_PAGADO',
				'recibido_por': 'Maria',
				'motivo_no_pago': 'Caja cerrada',
				'firma_cliente_data': self.signature_data,
			},
			evidence_files=[self.photo_file],
		)

		delivery.refresh_from_db()
		delivery.invoice.cliente.refresh_from_db()
		self.assertEqual(delivery.estado, 'ENTREGADA_SIN_PAGO')
		self.assertTrue(delivery.invoice.cliente.credit_hold)
		self.assertEqual(delivery.evidence_photos.count(), 1)

	def test_backoffice_can_unlock_blocked_customer(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
		)
		delivery = invoice.delivery
		complete_driver_delivery(
			delivery=delivery,
			driver_user=self.driver,
			payload={
				'estado_pago': 'NO_PAGADO',
				'recibido_por': 'Maria',
				'motivo_no_pago': 'No cash today',
				'firma_cliente_data': self.signature_data,
			},
			evidence_files=[SimpleUploadedFile(
				'evidence-2.png',
				base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO6pS7QAAAAASUVORK5CYII='),
				content_type='image/png',
			)],
		)

		unlock_client_from_delivery(delivery=delivery, backoffice_user=self.backoffice)

		delivery.invoice.cliente.refresh_from_db()
		delivery.refresh_from_db()
		self.assertFalse(delivery.invoice.cliente.credit_hold)
		self.assertEqual(delivery.client_unlocked_by, self.backoffice)

	def test_invoice_views_render_for_backoffice(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='LTG',
			driver=None,
			usuario=self.backoffice,
		)
		self.client.force_login(self.backoffice)

		list_response = self.client.get(reverse('backoffice_invoices_list'))
		detail_response = self.client.get(reverse('backoffice_invoice_detail', args=[invoice.id]))
		pdf_response = self.client.get(reverse('backoffice_invoice_pdf', args=[invoice.id]))

		self.assertEqual(list_response.status_code, 200)
		self.assertEqual(detail_response.status_code, 200)
		self.assertEqual(pdf_response.status_code, 200)
		self.assertEqual(pdf_response['Content-Type'], 'application/pdf')

	def test_driver_views_render(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
		)
		self.client.force_login(self.driver)

		list_response = self.client.get(reverse('driver_delivery_list'))
		detail_response = self.client.get(reverse('driver_delivery_detail', args=[invoice.delivery.id]))
		pdf_response = self.client.get(reverse('driver_invoice_pdf', args=[invoice.delivery.id]))

		self.assertEqual(list_response.status_code, 200)
		self.assertEqual(detail_response.status_code, 200)
		self.assertEqual(pdf_response.status_code, 200)
