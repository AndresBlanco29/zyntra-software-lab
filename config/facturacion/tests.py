import base64
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.test.utils import override_settings
from django.utils import timezone

from config.clientes.models import Cliente
from config.facturacion.models import Delivery, DeliveryNotificationLog, Invoice, NotaAjuste
from config.facturacion.services import aprobar_nota_ajuste, build_google_maps_route_url, complete_driver_delivery, crear_nota_ajuste_desde_invoice, generar_invoice_desde_picking, start_delivery_route, unlock_client_from_delivery
from config.facturacion.views import _build_invoice_pdf_item_data, _resolve_invoice_suggested_unit_price
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
		producto = Producto.objects.create(nombre='Tortilla 12', categoria=categoria, marca=marca, codigo_barras='7501234567890')
		self.presentacion = Presentacion.objects.create(
			producto=producto,
			nombre='Caja',
			unidades=12,
			tipo_contenido='caja',
			precio_1=Decimal('15.00'),
			precio_2=Decimal('16.00'),
			precio_3=Decimal('17.00'),
			precio_4=Decimal('18.00'),
			precio_5=Decimal('19.00'),
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
		self.assertEqual(invoice.items.first().precio_venta_sugerido_unitario, Decimal('1.33'))
		self.assertEqual(invoice.saldo_cliente, Decimal('45.00'))
		self.assertTrue(invoice.despachador_notificado)
		self.assertTrue(hasattr(invoice, 'delivery'))

	def test_generate_invoice_accepts_manual_suggested_unit_price(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='LTG',
			driver=None,
			usuario=self.backoffice,
			suggested_unit_prices={self.pedido_item.id: Decimal('2.49')},
		)

		self.assertEqual(invoice.items.first().precio_venta_sugerido_unitario, Decimal('2.49'))

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
		self.assertTrue(bool(delivery.firma_cliente))
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

	def test_invoice_pdf_item_data_exposes_barcode_and_suggested_retail(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='LTG',
			driver=None,
			usuario=self.backoffice,
		)

		items = _build_invoice_pdf_item_data(invoice)

		self.assertEqual(len(items), 1)
		self.assertEqual(items[0]['barcode'], '7501234567890')
		self.assertEqual(items[0]['pack_size'], 'Caja x 12')
		self.assertEqual(items[0]['customer_price'], '$15.00')
		self.assertEqual(items[0]['suggested_unit_price'], '$1.33')

	def test_backoffice_generate_invoice_view_saves_custom_suggested_unit_price(self):
		self.client.force_login(self.backoffice)

		response = self.client.post(reverse('backoffice_generate_invoice', args=[self.pedido.id]), {
			'metodo_entrega': 'LTG',
			'driver_id': '',
			f'suggested_unit_price_{self.pedido_item.id}': '2.75',
		})

		invoice = Invoice.objects.get(pedido=self.pedido)
		self.assertRedirects(response, reverse('backoffice_invoice_detail', args=[invoice.id]))
		self.assertEqual(invoice.items.first().precio_venta_sugerido_unitario, Decimal('2.75'))

	def test_invoice_pdf_suggested_retail_uses_next_price_tier(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='LTG',
			driver=None,
			usuario=self.backoffice,
		)

		suggested_unit_price = _resolve_invoice_suggested_unit_price(invoice.items.first())

		self.assertEqual(suggested_unit_price, Decimal('1.33'))

	def test_backoffice_invoice_detail_shows_saved_signature_proof(self):
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
				'estado_pago': 'PAGADO',
				'metodo_pago': 'CASH',
				'monto_pagado': '45.00',
				'recibido_por': 'Carlos Cliente',
				'firma_cliente_data': self.signature_data,
				'notas_driver': 'Cliente recibio completo',
			},
			evidence_files=[],
		)

		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_invoice_detail', args=[invoice.id]))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Customer signature proof')
		self.assertContains(response, 'Carlos Cliente')
		self.assertContains(response, delivery.firma_cliente.url)

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
		self.assertContains(list_response, invoice.numero)
		self.assertContains(list_response, invoice.delivery.route_query_address)
		self.assertContains(detail_response, invoice.delivery.route_query_address)
		self.assertContains(detail_response, reverse('driver_delivery_upload_evidence', args=[invoice.delivery.id]))

	def test_driver_delivery_list_renders_in_spanish_when_selected(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
		)
		self.client.force_login(self.driver)
		self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = 'es'

		response = self.client.get(reverse('driver_delivery_list'), HTTP_ACCEPT_LANGUAGE='es')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, '<title>Entregas del conductor</title>', html=False)
		self.assertContains(response, 'Facturas asignadas listas para ruta y prueba de entrega.', html=False)
		self.assertContains(response, 'Generar ruta')
		self.assertContains(response, 'Dirección')
		self.assertContains(response, 'Pago')
		self.assertContains(response, 'Descargar')
		self.assertContains(response, 'Abrir')
		self.assertContains(response, 'Asignada')
		self.assertContains(response, 'Pendiente')
		self.assertContains(response, invoice.delivery.route_query_address)

	def test_driver_delivery_detail_renders_in_spanish_when_selected(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
		)
		self.client.force_login(self.driver)
		self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = 'es'

		response = self.client.get(reverse('driver_delivery_detail', args=[invoice.delivery.id]), HTTP_ACCEPT_LANGUAGE='es')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Descargar PDF')
		self.assertContains(response, 'Resumen de entrega')
		self.assertContains(response, 'Estado de pago')
		self.assertContains(response, 'Saldo del cliente')
		self.assertContains(response, 'Consulta de Google Maps')
		self.assertContains(response, 'Evidencia')
		self.assertContains(response, 'Subir evidencia')
		self.assertContains(response, 'Aún no se han subido fotos de evidencia.', html=False)
		self.assertContains(response, 'Completar entrega')
		self.assertContains(response, 'Recibido por')
		self.assertContains(response, 'Método de pago')
		self.assertContains(response, 'Firma del cliente')
		self.assertContains(response, 'Guardar entrega')
		self.assertContains(response, 'Asignada')
		self.assertContains(response, 'Pendiente')

	def test_driver_delivery_tracking_renders_in_spanish_when_selected(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
		)
		start_delivery_route(delivery=invoice.delivery, driver_user=self.driver)
		self.client.force_login(self.driver)
		self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = 'es'

		response = self.client.get(reverse('driver_delivery_tracking', args=[invoice.delivery.id]), HTTP_ACCEPT_LANGUAGE='es')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, '<title>Seguimiento en vivo - ', html=False)
		self.assertContains(response, 'Seguimiento de ruta en vivo')
		self.assertContains(response, 'Volver a la entrega')
		self.assertContains(response, 'Abrir Google Maps')
		self.assertContains(response, 'Si ocultas esta página, bloqueas el teléfono o cambias completamente a otra aplicación, el navegador puede pausar las actualizaciones de GPS. Abre Maps en una pestaña separada y mantén esta pantalla de seguimiento visible siempre que sea posible.', html=False)
		self.assertContains(response, 'Posición actual')
		self.assertContains(response, 'Esperando GPS')
		self.assertContains(response, 'Estado del seguimiento')
		self.assertContains(response, 'Destino')
		self.assertContains(response, 'Estado de entrega')
		self.assertContains(response, 'Último envío')
		self.assertContains(response, 'Precisión')
		self.assertContains(response, 'Velocidad')
		self.assertContains(response, 'Coordenadas')
		self.assertContains(response, 'Solicita permiso de ubicación en tu navegador para iniciar el seguimiento en vivo.', html=False)

	def test_backoffice_invoice_live_tracking_renders_in_spanish_when_selected(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
		)
		start_delivery_route(delivery=invoice.delivery, driver_user=self.driver)
		self.client.force_login(self.backoffice)
		self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = 'es'

		response = self.client.get(reverse('backoffice_invoice_live_tracking', args=[invoice.id]), HTTP_ACCEPT_LANGUAGE='es')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, '<title>Rastrear conductor - ', html=False)
		self.assertContains(response, 'Seguimiento en vivo del conductor')
		self.assertContains(response, 'Ver todos los conductores en vivo')
		self.assertContains(response, 'Volver a la factura')
		self.assertContains(response, 'Abrir ubicación en vivo')
		self.assertContains(response, 'Posición del vehículo')
		self.assertContains(response, 'Esperando señal')
		self.assertContains(response, 'Transmisión en vivo')
		self.assertContains(response, 'Estado de entrega')
		self.assertContains(response, 'Última señal')
		self.assertContains(response, 'Antigüedad de la señal')
		self.assertContains(response, 'Precisión')
		self.assertContains(response, 'Velocidad')
		self.assertContains(response, 'Coordenadas')
		self.assertContains(response, 'Destino')
		self.assertContains(response, 'Esta página se actualiza automáticamente mientras el conductor mantenga abierta la pantalla de seguimiento.', html=False)

	def test_driver_can_upload_evidence_after_completed_delivery(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
		)
		complete_driver_delivery(
			delivery=invoice.delivery,
			driver_user=self.driver,
			payload={
				'estado_pago': 'PAGADO',
				'metodo_pago': 'CASH',
				'monto_pagado': '45.00',
				'recibido_por': 'Carlos Cliente',
				'firma_cliente_data': self.signature_data,
			},
			evidence_files=[],
		)
		self.client.force_login(self.driver)

		response = self.client.post(
			reverse('driver_delivery_upload_evidence', args=[invoice.delivery.id]),
			{'evidence_photos': [self.photo_file]},
		)

		invoice.delivery.refresh_from_db()
		self.assertRedirects(response, reverse('driver_delivery_detail', args=[invoice.delivery.id]))
		self.assertEqual(invoice.delivery.evidence_photos.count(), 1)

	def test_driver_evidence_upload_requires_photo(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
		)
		self.client.force_login(self.driver)

		response = self.client.post(reverse('driver_delivery_upload_evidence', args=[invoice.delivery.id]), {})

		self.assertRedirects(response, reverse('driver_delivery_detail', args=[invoice.delivery.id]))
		self.assertEqual(invoice.delivery.evidence_photos.count(), 0)

	def test_start_route_redirects_driver_to_tracking_page(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
		)
		self.client.force_login(self.driver)

		response = self.client.post(reverse('driver_delivery_start_route', args=[invoice.delivery.id]))

		invoice.delivery.refresh_from_db()
		self.assertEqual(invoice.delivery.estado, 'EN_RUTA')
		self.assertRedirects(response, reverse('driver_delivery_tracking', args=[invoice.delivery.id]))

	def test_driver_location_update_persists_live_coordinates(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
		)
		start_delivery_route(delivery=invoice.delivery, driver_user=self.driver)
		self.client.force_login(self.driver)

		response = self.client.post(reverse('driver_delivery_update_location', args=[invoice.delivery.id]), {
			'latitude': '32.776664',
			'longitude': '-96.796988',
			'accuracy_meters': '8.5',
			'speed_mps': '12.3',
			'heading': '182.4',
		})

		invoice.delivery.refresh_from_db()
		self.assertEqual(response.status_code, 200)
		payload = response.json()
		self.assertTrue(payload['success'])
		self.assertEqual(payload['tracking']['delivery_id'], invoice.delivery.id)
		self.assertEqual(payload['tracking']['invoice_number'], invoice.numero)
		self.assertEqual(payload['tracking']['driver_name'], self.driver.username)
		self.assertEqual(payload['tracking']['status'], 'On route')
		self.assertEqual(payload['tracking']['payment_status'], 'Pending')
		self.assertTrue(payload['tracking']['has_location'])
		self.assertEqual(payload['tracking']['latitude'], 32.776664)
		self.assertEqual(payload['tracking']['longitude'], -96.796988)
		self.assertEqual(payload['tracking']['accuracy_meters'], 8.5)
		self.assertEqual(payload['tracking']['speed_mps'], 12.3)
		self.assertEqual(payload['tracking']['heading'], 182.4)
		self.assertEqual(payload['tracking']['location_updated_label'], timezone.localtime(invoice.delivery.location_updated_at).strftime('%d/%m/%Y %H:%M:%S'))
		self.assertIsNotNone(payload['tracking']['location_updated_at'])
		self.assertGreaterEqual(payload['tracking']['location_age_seconds'], 0)
		self.assertLessEqual(payload['tracking']['location_age_seconds'], 5)
		self.assertEqual(payload['tracking']['maps_url'], 'https://www.google.com/maps?q=32.776664,-96.796988')
		self.assertEqual(payload['tracking']['destination_maps_url'], invoice.delivery.google_maps_url)
		self.assertEqual(payload['tracking']['destination_address'], invoice.delivery.route_address)

	def test_driver_tracking_page_keeps_maps_link_separate_from_tracking_screen(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
		)
		start_delivery_route(delivery=invoice.delivery, driver_user=self.driver)
		self.client.force_login(self.driver)

		response = self.client.get(reverse('driver_delivery_tracking', args=[invoice.delivery.id]))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'target="_blank"')
		self.assertContains(response, 'keep this tracking screen visible whenever possible', html=False)

	def test_backoffice_tracking_endpoint_returns_live_driver_data(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
		)
		invoice.delivery.current_latitude = Decimal('32.776664')
		invoice.delivery.current_longitude = Decimal('-96.796988')
		invoice.delivery.current_accuracy_meters = Decimal('5.25')
		invoice.delivery.location_updated_at = timezone.now()
		invoice.delivery.save(update_fields=['current_latitude', 'current_longitude', 'current_accuracy_meters', 'location_updated_at', 'updated_at'])

		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_invoice_tracking_data', args=[invoice.id]))

		self.assertEqual(response.status_code, 200)
		payload = response.json()
		self.assertTrue(payload['success'])
		self.assertEqual(payload['tracking']['invoice_number'], invoice.numero)
		self.assertEqual(payload['tracking']['driver_name'], self.driver.username)
		self.assertTrue(payload['tracking']['has_location'])
		self.assertEqual(payload['tracking']['latitude'], 32.776664)
		self.assertEqual(payload['tracking']['longitude'], -96.796988)

	def test_backoffice_invoice_detail_shows_live_tracking_link(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
		)
		self.client.force_login(self.backoffice)

		response = self.client.get(reverse('backoffice_invoice_detail', args=[invoice.id]))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, reverse('backoffice_invoice_live_tracking', args=[invoice.id]))

	def test_backoffice_live_drivers_page_lists_only_in_route_deliveries(self):
		invoice_live = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
		)
		start_delivery_route(delivery=invoice_live.delivery, driver_user=self.driver)

		second_driver = Usuario.objects.create_user(username='driver-live-2', password='secret123', role='driver')
		pedido_2 = Pedido.objects.create(
			cliente=self.cliente,
			origen='CLIENTE',
			estado='VERIFICADO_AJUSTADO',
			total=Decimal('30.00'),
		)
		PedidoItem.objects.create(
			pedido=pedido_2,
			presentacion=self.presentacion,
			cantidad_solicitada=2,
			cantidad=2,
			precio=Decimal('15.00'),
			subtotal=Decimal('30.00'),
		)
		invoice_completed = generar_invoice_desde_picking(
			pedido=pedido_2,
			metodo_entrega='RUTA_DRIVER',
			driver=second_driver,
			usuario=self.backoffice,
		)
		complete_driver_delivery(
			delivery=invoice_completed.delivery,
			driver_user=second_driver,
			payload={
				'estado_pago': 'PAGADO',
				'metodo_pago': 'CASH',
				'monto_pagado': '30.00',
				'recibido_por': 'Cliente Ruta',
				'firma_cliente_data': self.signature_data,
			},
			evidence_files=[],
		)

		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_live_drivers'))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, invoice_live.numero)
		self.assertNotContains(response, invoice_completed.numero)

	def test_backoffice_live_drivers_data_returns_multiple_active_drivers(self):
		invoice_1 = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
		)
		start_delivery_route(delivery=invoice_1.delivery, driver_user=self.driver)
		invoice_1.delivery.current_latitude = Decimal('32.776664')
		invoice_1.delivery.current_longitude = Decimal('-96.796988')
		invoice_1.delivery.location_updated_at = timezone.now()
		invoice_1.delivery.save(update_fields=['current_latitude', 'current_longitude', 'location_updated_at', 'updated_at'])

		second_driver = Usuario.objects.create_user(username='driver-live-3', password='secret123', role='driver')
		pedido_2 = Pedido.objects.create(
			cliente=self.cliente,
			origen='CLIENTE',
			estado='VERIFICADO_AJUSTADO',
			total=Decimal('15.00'),
		)
		PedidoItem.objects.create(
			pedido=pedido_2,
			presentacion=self.presentacion,
			cantidad_solicitada=1,
			cantidad=1,
			precio=Decimal('15.00'),
			subtotal=Decimal('15.00'),
		)
		invoice_2 = generar_invoice_desde_picking(
			pedido=pedido_2,
			metodo_entrega='RUTA_DRIVER',
			driver=second_driver,
			usuario=self.backoffice,
		)
		start_delivery_route(delivery=invoice_2.delivery, driver_user=second_driver)
		invoice_2.delivery.current_latitude = Decimal('40.712776')
		invoice_2.delivery.current_longitude = Decimal('-74.005974')
		invoice_2.delivery.location_updated_at = timezone.now()
		invoice_2.delivery.save(update_fields=['current_latitude', 'current_longitude', 'location_updated_at', 'updated_at'])

		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_live_drivers_data'))

		self.assertEqual(response.status_code, 200)
		payload = response.json()
		self.assertTrue(payload['success'])
		self.assertEqual(len(payload['drivers']), 2)
		invoice_numbers = {driver['invoice_number'] for driver in payload['drivers']}
		self.assertEqual(invoice_numbers, {invoice_1.numero, invoice_2.numero})

	def test_completed_driver_delivery_moves_to_delivered_view(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
		)
		complete_driver_delivery(
			delivery=invoice.delivery,
			driver_user=self.driver,
			payload={
				'estado_pago': 'PAGADO',
				'metodo_pago': 'CASH',
				'monto_pagado': '45.00',
				'recibido_por': 'Carlos Cliente',
				'firma_cliente_data': self.signature_data,
			},
			evidence_files=[],
		)

		self.client.force_login(self.driver)
		active_response = self.client.get(reverse('driver_delivery_list'))
		completed_response = self.client.get(reverse('driver_delivery_list'), {'view': 'completed'})

		self.assertEqual(active_response.status_code, 200)
		self.assertEqual(completed_response.status_code, 200)
		self.assertNotContains(active_response, invoice.numero)
		self.assertContains(completed_response, invoice.numero)
		self.assertContains(completed_response, 'Delivered Invoices')

	def test_driver_completed_view_shows_unpaid_deliveries(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
		)
		complete_driver_delivery(
			delivery=invoice.delivery,
			driver_user=self.driver,
			payload={
				'estado_pago': 'NO_PAGADO',
				'recibido_por': 'Maria Cliente',
				'motivo_no_pago': 'Store closed',
				'firma_cliente_data': self.signature_data,
			},
			evidence_files=[self.photo_file],
		)

		self.client.force_login(self.driver)
		response = self.client.get(reverse('driver_delivery_list'), {'view': 'completed'})

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, invoice.numero)
		self.assertContains(response, 'Delivered without payment')

	def test_driver_route_requires_selected_deliveries(self):
		generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
		)
		self.client.force_login(self.driver)

		response = self.client.get(reverse('driver_delivery_route'))

		self.assertRedirects(response, reverse('driver_delivery_list'))

	def test_driver_route_uses_only_selected_deliveries(self):
		invoice_1 = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
		)
		pedido_2 = Pedido.objects.create(
			cliente=self.cliente,
			origen='CLIENTE',
			estado='VERIFICADO_AJUSTADO',
			total=Decimal('30.00'),
		)
		PedidoItem.objects.create(
			pedido=pedido_2,
			presentacion=self.presentacion,
			cantidad_solicitada=2,
			cantidad=2,
			precio=Decimal('15.00'),
			subtotal=Decimal('30.00'),
		)
		invoice_2 = generar_invoice_desde_picking(
			pedido=pedido_2,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
		)
		pedido_3 = Pedido.objects.create(
			cliente=self.cliente,
			origen='CLIENTE',
			estado='VERIFICADO_AJUSTADO',
			total=Decimal('15.00'),
		)
		PedidoItem.objects.create(
			pedido=pedido_3,
			presentacion=self.presentacion,
			cantidad_solicitada=1,
			cantidad=1,
			precio=Decimal('15.00'),
			subtotal=Decimal('15.00'),
		)
		invoice_3 = generar_invoice_desde_picking(
			pedido=pedido_3,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
		)

		invoice_1.delivery.delivery_address = '111 Alpha St'
		invoice_1.delivery.save(update_fields=['delivery_address', 'updated_at'])
		invoice_2.delivery.delivery_address = '222 Beta St'
		invoice_2.delivery.save(update_fields=['delivery_address', 'updated_at'])
		invoice_3.delivery.delivery_address = '333 Gamma St'
		invoice_3.delivery.save(update_fields=['delivery_address', 'updated_at'])

		self.client.force_login(self.driver)
		response = self.client.get(reverse('driver_delivery_route'), {
			'delivery_ids': [invoice_1.delivery.id, invoice_3.delivery.id],
		})

		self.assertEqual(response.status_code, 302)
		self.assertIn('111+Alpha+St', response['Location'])
		self.assertIn('333+Gamma+St', response['Location'])
		self.assertNotIn('222+Beta+St', response['Location'])

	def test_google_maps_route_url_optimizes_intermediate_stops(self):
		invoice_1 = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
		)
		pedido_2 = Pedido.objects.create(
			cliente=self.cliente,
			origen='CLIENTE',
			estado='VERIFICADO_AJUSTADO',
			total=Decimal('30.00'),
		)
		PedidoItem.objects.create(
			pedido=pedido_2,
			presentacion=self.presentacion,
			cantidad_solicitada=2,
			cantidad=2,
			precio=Decimal('15.00'),
			subtotal=Decimal('30.00'),
		)
		invoice_2 = generar_invoice_desde_picking(
			pedido=pedido_2,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
		)
		pedido_3 = Pedido.objects.create(
			cliente=self.cliente,
			origen='CLIENTE',
			estado='VERIFICADO_AJUSTADO',
			total=Decimal('15.00'),
		)
		PedidoItem.objects.create(
			pedido=pedido_3,
			presentacion=self.presentacion,
			cantidad_solicitada=1,
			cantidad=1,
			precio=Decimal('15.00'),
			subtotal=Decimal('15.00'),
		)
		invoice_3 = generar_invoice_desde_picking(
			pedido=pedido_3,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
		)

		invoice_1.delivery.delivery_address = '111 Alpha St'
		invoice_1.delivery.save(update_fields=['delivery_address', 'updated_at'])
		invoice_2.delivery.delivery_address = '222 Beta St'
		invoice_2.delivery.save(update_fields=['delivery_address', 'updated_at'])
		invoice_3.delivery.delivery_address = '333 Gamma St'
		invoice_3.delivery.save(update_fields=['delivery_address', 'updated_at'])

		maps_url = build_google_maps_route_url([invoice_1.delivery, invoice_2.delivery, invoice_3.delivery])

		self.assertIn('destination=333+Gamma+St', maps_url)
		self.assertNotIn('origin=', maps_url)
		self.assertNotIn('optimize%3Atrue', maps_url)
		self.assertIn('waypoints=', maps_url)
		self.assertIn('111+Alpha+St', maps_url)
		self.assertIn('222+Beta+St', maps_url)
