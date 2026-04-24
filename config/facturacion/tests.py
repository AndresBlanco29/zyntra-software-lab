import base64
from datetime import datetime
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
from config.notificaciones.models import Notificacion
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

	def _create_verified_order(self, *, total='15.00', quantity=1):
		pedido = Pedido.objects.create(
			cliente=self.cliente,
			origen='CLIENTE',
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

	def _create_invoice(self, *, metodo_entrega='LTG', driver=None, total='15.00'):
		pedido = self._create_verified_order(total=total)
		return generar_invoice_desde_picking(
			pedido=pedido,
			metodo_entrega=metodo_entrega,
			driver=driver,
			usuario=self.backoffice,
		)

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
		self.assertEqual(invoice.items.first().precio_venta_sugerido_unitario, Decimal('1.79'))
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

	def test_generate_invoice_stores_estimated_delivery_for_driver_route(self):
		estimated_delivery_at = timezone.make_aware(datetime(2026, 4, 18, 9, 30), timezone.get_current_timezone())
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
			estimated_delivery_at=estimated_delivery_at,
		)

		self.assertEqual(invoice.delivery.estimated_delivery_at, estimated_delivery_at)

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

	def test_credit_note_requires_credit_type(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='LTG',
			driver=None,
			usuario=self.backoffice,
		)

		with self.assertRaisesMessage(ValidationError, 'A credit type is required for credit notes.'):
			crear_nota_ajuste_desde_invoice(
				invoice=invoice,
				tipo_documento='CREDITO',
				motivo='DAMAGE',
				tipo_credito='',
				descripcion='Producto dañado',
				usuario=self.backoffice,
				items_payload=[{
					'invoice_item': invoice.items.first(),
					'cantidad': 1,
					'monto_unitario': Decimal('15.00'),
				}],
			)

	def test_debit_note_rejects_credit_type(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='LTG',
			driver=None,
			usuario=self.backoffice,
		)

		with self.assertRaisesMessage(ValidationError, 'Debit notes cannot define a credit type.'):
			crear_nota_ajuste_desde_invoice(
				invoice=invoice,
				tipo_documento='DEBITO',
				motivo='DEFECT',
				tipo_credito='CREDIT_RETURN',
				descripcion='Recargo operativo',
				usuario=self.backoffice,
				items_payload=[{
					'invoice_item': invoice.items.first(),
					'cantidad': 1,
					'monto_unitario': Decimal('5.00'),
				}],
			)

	def test_adjustment_note_requires_positive_quantity_and_amount(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='LTG',
			driver=None,
			usuario=self.backoffice,
		)

		with self.assertRaisesMessage(ValidationError, 'Enter a unit amount greater than zero for each selected adjustment item.'):
			crear_nota_ajuste_desde_invoice(
				invoice=invoice,
				tipo_documento='DEBITO',
				motivo='DEFECT',
				tipo_credito='',
				descripcion='Recargo operativo',
				usuario=self.backoffice,
				items_payload=[{
					'invoice_item': invoice.items.first(),
					'cantidad': 1,
					'monto_unitario': Decimal('0.00'),
				}],
			)

		with self.assertRaisesMessage(ValidationError, 'Enter a quantity greater than zero for each selected adjustment item.'):
			crear_nota_ajuste_desde_invoice(
				invoice=invoice,
				tipo_documento='DEBITO',
				motivo='DEFECT',
				tipo_credito='',
				descripcion='Recargo operativo',
				usuario=self.backoffice,
				items_payload=[{
					'invoice_item': invoice.items.first(),
					'cantidad': 0,
					'monto_unitario': Decimal('5.00'),
				}],
			)

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

	def test_driver_complete_view_can_create_credit_note_draft(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
		)
		self.client.force_login(self.driver)

		response = self.client.post(reverse('driver_delivery_complete', args=[invoice.delivery.id]), {
			'estado_pago': 'PAGADO',
			'metodo_pago': 'CASH',
			'monto_pagado': '45.00',
			'recibido_por': 'Juan Perez',
			'firma_cliente_data': self.signature_data,
			'driver_note_tipo_documento': 'CREDITO',
			'driver_note_motivo': 'DAMAGE',
			'driver_note_tipo_credito': 'CREDIT_RETURN',
			'driver_note_descripcion': 'Caja dañada al entregar',
			f'driver_note_qty_{invoice.items.first().id}': '1',
			f'driver_note_amount_{invoice.items.first().id}': '15.00',
		})

		invoice.refresh_from_db()
		nota = invoice.notas_ajuste.get()
		self.assertRedirects(response, reverse('driver_delivery_detail', args=[invoice.delivery.id]))
		self.assertEqual(invoice.delivery.estado, 'ENTREGADA_PAGADA')
		self.assertEqual(nota.estado, 'BORRADOR')
		self.assertEqual(nota.tipo_documento, 'CREDITO')
		self.assertEqual(nota.tipo_credito, 'CREDIT_RETURN')
		self.assertEqual(nota.creada_por, self.driver)
		self.assertEqual(nota.total, Decimal('15.00'))
		notificacion = Notificacion.objects.filter(titulo__icontains=nota.numero).latest('creada_en')
		self.assertIn('requires review', notificacion.titulo)
		self.assertIn('approve or reject', notificacion.mensaje)
		self.assertEqual(notificacion.url, f'/facturacion/backoffice/invoices/{invoice.id}/')

	def test_driver_complete_view_can_attach_evidence_to_adjustment_note(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
		)
		self.client.force_login(self.driver)

		response = self.client.post(
			reverse('driver_delivery_complete', args=[invoice.delivery.id]),
			{
				'estado_pago': 'PAGADO',
				'metodo_pago': 'CASH',
				'monto_pagado': '45.00',
				'recibido_por': 'Juan Perez',
				'firma_cliente_data': self.signature_data,
				'driver_note_tipo_documento': 'CREDITO',
				'driver_note_motivo': 'DAMAGE',
				'driver_note_tipo_credito': 'CREDIT_RETURN',
				'driver_note_descripcion': 'Caja dañada al entregar',
				f'driver_note_qty_{invoice.items.first().id}': '1',
				f'driver_note_amount_{invoice.items.first().id}': '15.00',
				'driver_note_evidence_photos': self.photo_file,
			},
			format='multipart',
		)

		invoice.refresh_from_db()
		nota = invoice.notas_ajuste.get()
		self.assertRedirects(response, reverse('driver_delivery_detail', args=[invoice.delivery.id]))
		self.assertEqual(nota.evidence_photos.count(), 1)
		self.assertIn('invoice-notes/evidence/', nota.evidence_photos.first().image.name)

		self.client.force_login(self.backoffice)
		backoffice_response = self.client.get(reverse('backoffice_invoice_detail', args=[invoice.id]))
		self.assertContains(backoffice_response, nota.evidence_photos.first().image.url)

	def test_driver_can_create_adjustment_note_after_delivery(self):
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
				'recibido_por': 'Juan Perez',
				'firma_cliente_data': self.signature_data,
			},
			evidence_files=[],
		)
		self.client.force_login(self.driver)

		response = self.client.post(reverse('driver_delivery_create_note', args=[invoice.delivery.id]), {
			'driver_note_tipo_documento': 'DEBITO',
			'driver_note_motivo': 'DEFECT',
			'driver_note_descripcion': 'Cargo adicional después de la entrega',
			f'driver_note_qty_{invoice.items.first().id}': '1',
			f'driver_note_amount_{invoice.items.first().id}': '5.00',
		})

		invoice.refresh_from_db()
		nota = invoice.notas_ajuste.get()
		self.assertRedirects(response, reverse('driver_delivery_detail', args=[invoice.delivery.id]))
		self.assertEqual(nota.tipo_documento, 'DEBITO')
		self.assertEqual(nota.estado, 'BORRADOR')
		self.assertEqual(nota.creada_por, self.driver)
		notificacion = Notificacion.objects.filter(titulo__icontains=nota.numero).latest('creada_en')
		self.assertIn('requires review', notificacion.titulo)
		self.assertIn('approve or reject', notificacion.mensaje)
		self.assertEqual(notificacion.url, f'/facturacion/backoffice/invoices/{invoice.id}/')

	def test_backoffice_invoice_detail_highlights_driver_created_notes(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
		)
		crear_nota_ajuste_desde_invoice(
			invoice=invoice,
			tipo_documento='CREDITO',
			motivo='DAMAGE',
			tipo_credito='CREDIT_RETURN',
			descripcion='Driver created note',
			usuario=self.driver,
			items_payload=[{
				'invoice_item': invoice.items.first(),
				'cantidad': 1,
				'monto_unitario': Decimal('15.00'),
			}],
		)
		self.client.force_login(self.backoffice)

		response = self.client.get(reverse('backoffice_invoice_detail', args=[invoice.id]))

		self.assertContains(response, 'Driver-created adjustment notes detected.')
		self.assertContains(response, 'Created by driver')

	def test_backoffice_invoice_detail_shows_adjustment_action_and_products(self):
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
			descripcion='Customer returned damaged tortillas',
			usuario=self.backoffice,
			items_payload=[{
				'invoice_item': invoice.items.first(),
				'cantidad': 2,
				'monto_unitario': Decimal('15.00'),
			}],
		)
		aprobar_nota_ajuste(nota=nota, usuario=self.backoffice)
		self.client.force_login(self.backoffice)

		response = self.client.get(reverse('backoffice_invoice_detail', args=[invoice.id]))

		self.assertContains(response, 'Action taken')
		self.assertContains(response, 'Product returned and inventory was restocked.')
		self.assertContains(response, 'Products affected')
		self.assertContains(response, 'Tortilla 12')
		self.assertContains(response, 'Caja')
		self.assertContains(response, 'Customer returned damaged tortillas')

	def test_backoffice_dashboard_shows_pending_adjustment_note_counter(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
		)
		crear_nota_ajuste_desde_invoice(
			invoice=invoice,
			tipo_documento='CREDITO',
			motivo='DAMAGE',
			tipo_credito='CREDIT_RETURN',
			descripcion='Pending review note',
			usuario=self.driver,
			items_payload=[{
				'invoice_item': invoice.items.first(),
				'cantidad': 1,
				'monto_unitario': Decimal('15.00'),
			}],
		)
		self.client.force_login(self.backoffice)

		response = self.client.get(reverse('backoffice_dashboard'))

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.context['pending_adjustment_notes_count'], 1)
		self.assertEqual(response.context['unread_adjustment_notifications_count'], 1)
		self.assertContains(response, 'Adjustment notes pending approval')
		self.assertContains(response, 'unread adjustment note alerts')

	def test_backoffice_invoice_detail_marks_adjustment_review_notifications_as_read(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
		)
		crear_nota_ajuste_desde_invoice(
			invoice=invoice,
			tipo_documento='CREDITO',
			motivo='DAMAGE',
			tipo_credito='CREDIT_RETURN',
			descripcion='Read tracking note',
			usuario=self.driver,
			items_payload=[{
				'invoice_item': invoice.items.first(),
				'cantidad': 1,
				'monto_unitario': Decimal('15.00'),
			}],
		)
		adjustment_notification = Notificacion.objects.filter(tipo='NOTA_AJUSTE', url=f'/facturacion/backoffice/invoices/{invoice.id}/').latest('creada_en')
		invoice_notification = Notificacion.objects.filter(tipo='PEDIDO', url=f'/facturacion/backoffice/invoices/{invoice.id}/').latest('creada_en')
		self.client.force_login(self.backoffice)

		response = self.client.get(reverse('backoffice_invoice_detail', args=[invoice.id]))

		adjustment_notification.refresh_from_db()
		invoice_notification.refresh_from_db()
		self.assertEqual(response.status_code, 200)
		self.assertTrue(adjustment_notification.leida)
		self.assertFalse(invoice_notification.leida)

	def test_driver_complete_view_can_create_debit_note_draft(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
		)
		self.client.force_login(self.driver)

		response = self.client.post(reverse('driver_delivery_complete', args=[invoice.delivery.id]), {
			'estado_pago': 'PAGADO',
			'metodo_pago': 'CASH',
			'monto_pagado': '45.00',
			'recibido_por': 'Juan Perez',
			'firma_cliente_data': self.signature_data,
			'driver_note_tipo_documento': 'DEBITO',
			'driver_note_motivo': 'DEFECT',
			'driver_note_descripcion': 'Cargo adicional operativo',
			f'driver_note_qty_{invoice.items.first().id}': '1',
			f'driver_note_amount_{invoice.items.first().id}': '5.00',
		})

		invoice.refresh_from_db()
		nota = invoice.notas_ajuste.get()
		self.assertRedirects(response, reverse('driver_delivery_detail', args=[invoice.delivery.id]))
		self.assertEqual(invoice.delivery.estado, 'ENTREGADA_PAGADA')
		self.assertEqual(nota.estado, 'BORRADOR')
		self.assertEqual(nota.tipo_documento, 'DEBITO')
		self.assertEqual(nota.tipo_credito, '')
		self.assertEqual(nota.creada_por, self.driver)
		self.assertEqual(nota.total, Decimal('5.00'))

	def test_backoffice_invoice_create_note_uses_prefixed_form_fields(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='LTG',
			driver=None,
			usuario=self.backoffice,
		)
		self.client.force_login(self.backoffice)

		response = self.client.post(reverse('backoffice_invoice_create_note', args=[invoice.id]), {
			'note_tipo_documento': 'CREDITO',
			'note_motivo': 'DAMAGE',
			'note_tipo_credito': 'CREDIT_RETURN',
			'note_descripcion': 'Producto dañado en entrega',
			f'note_qty_{invoice.items.first().id}': '1',
			f'note_amount_{invoice.items.first().id}': '15.00',
		})

		invoice.refresh_from_db()
		nota = invoice.notas_ajuste.get()
		self.assertRedirects(response, reverse('backoffice_invoice_detail', args=[invoice.id]))
		self.assertEqual(nota.tipo_documento, 'CREDITO')
		self.assertEqual(nota.motivo, 'DAMAGE')
		self.assertEqual(nota.tipo_credito, 'CREDIT_RETURN')
		self.assertEqual(nota.total, Decimal('15.00'))

	def test_backoffice_invoice_create_debit_note_uses_prefixed_form_fields(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='LTG',
			driver=None,
			usuario=self.backoffice,
		)
		self.client.force_login(self.backoffice)

		response = self.client.post(reverse('backoffice_invoice_create_note', args=[invoice.id]), {
			'note_tipo_documento': 'DEBITO',
			'note_motivo': 'DEFECT',
			'note_tipo_credito': '',
			'note_descripcion': 'Recargo operativo en backoffice',
			f'note_qty_{invoice.items.first().id}': '1',
			f'note_amount_{invoice.items.first().id}': '5.00',
		})

		invoice.refresh_from_db()
		nota = invoice.notas_ajuste.get(tipo_documento='DEBITO')
		self.assertRedirects(response, reverse('backoffice_invoice_detail', args=[invoice.id]))
		self.assertEqual(nota.tipo_documento, 'DEBITO')
		self.assertEqual(nota.motivo, 'DEFECT')
		self.assertEqual(nota.tipo_credito, '')
		self.assertEqual(nota.total, Decimal('5.00'))

	def test_backoffice_invoice_detail_hides_credit_type_until_credit_selected(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='LTG',
			driver=None,
			usuario=self.backoffice,
		)
		self.client.force_login(self.backoffice)

		response = self.client.get(reverse('backoffice_invoice_detail', args=[invoice.id]))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'id="backofficeNoteType"', html=False)
		self.assertContains(response, 'id="backofficeCreditTypeWrapper"', html=False)
		self.assertContains(response, 'id="backofficeCreditType"', html=False)
		self.assertContains(response, 'syncCreditTypeVisibility', html=False)

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

	def test_backoffice_invoice_list_defaults_to_pending_dispatch(self):
		pending_invoice = self._create_invoice(metodo_entrega='LTG', total='10.00')
		pending_invoice.despachador_notificado = False
		pending_invoice.save(update_fields=['despachador_notificado'])

		ready_invoice = self._create_invoice(metodo_entrega='LTG', total='20.00')
		cancelled_invoice = self._create_invoice(metodo_entrega='LTG', total='30.00')
		cancelled_invoice.estado = 'ANULADA'
		cancelled_invoice.save(update_fields=['estado'])

		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_invoices_list'))

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.context['view_mode'], 'pending')
		self.assertEqual(list(response.context['invoices'].values_list('id', flat=True)), [pending_invoice.id])
		self.assertEqual(response.context['pending_count'], 1)
		self.assertEqual(response.context['ready_count'], 1)
		self.assertEqual(response.context['cancelled_count'], 1)

	def test_backoffice_invoice_list_can_filter_ready_delivered_and_cancelled(self):
		pending_invoice = self._create_invoice(metodo_entrega='LTG', total='10.00')
		pending_invoice.despachador_notificado = False
		pending_invoice.save(update_fields=['despachador_notificado'])

		ready_invoice = self._create_invoice(metodo_entrega='LTG', total='20.00')

		delivered_invoice = self._create_invoice(metodo_entrega='RUTA_DRIVER', driver=self.driver, total='30.00')
		complete_driver_delivery(
			delivery=delivered_invoice.delivery,
			driver_user=self.driver,
			payload={
				'estado_pago': 'PAGADO',
				'metodo_pago': 'CASH',
				'monto_pagado': '30.00',
				'recibido_por': 'Cliente factura',
				'firma_cliente_data': self.signature_data,
			},
			evidence_files=[],
		)

		cancelled_invoice = self._create_invoice(metodo_entrega='LTG', total='40.00')
		cancelled_invoice.estado = 'ANULADA'
		cancelled_invoice.save(update_fields=['estado'])

		self.client.force_login(self.backoffice)

		ready_response = self.client.get(reverse('backoffice_invoices_list'), {'view': 'ready'})
		delivered_response = self.client.get(reverse('backoffice_invoices_list'), {'view': 'delivered'})
		cancelled_response = self.client.get(reverse('backoffice_invoices_list'), {'view': 'cancelled'})

		self.assertEqual(list(ready_response.context['invoices'].values_list('id', flat=True)), [ready_invoice.id])
		self.assertEqual(list(delivered_response.context['invoices'].values_list('id', flat=True)), [delivered_invoice.id])
		self.assertEqual(list(cancelled_response.context['invoices'].values_list('id', flat=True)), [cancelled_invoice.id])

	def test_backoffice_invoice_list_renders_in_spanish_when_selected(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='LTG',
			driver=None,
			usuario=self.backoffice,
		)
		invoice.despachador_notificado = False
		invoice.save(update_fields=['despachador_notificado'])
		self.client.force_login(self.backoffice)
		self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = 'es'

		response = self.client.get(reverse('backoffice_invoices_list'), HTTP_ACCEPT_LANGUAGE='es')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, '<title>Facturas</title>', html=False)
		self.assertContains(response, 'Generadas a partir de cantidades verificadas en picking.', html=False)
		self.assertContains(response, 'Pendientes de despacho')
		self.assertContains(response, 'Facturas listas')
		self.assertContains(response, 'Facturas entregadas')
		self.assertContains(response, 'Facturas anuladas')
		self.assertContains(response, 'Despacho')
		self.assertContains(response, 'Pendiente')
		self.assertContains(response, invoice.numero)

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
		self.assertEqual(items[0]['requested_quantity'], '4')
		self.assertEqual(items[0]['dispatched_quantity'], '3')
		self.assertEqual(items[0]['customer_price'], '$15.00')
		self.assertEqual(items[0]['suggested_unit_price'], '$1.79')
		self.assertEqual(items[0]['profit_percentage'], '30.17%')

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

	def test_backoffice_generate_invoice_view_saves_estimated_delivery(self):
		self.client.force_login(self.backoffice)

		response = self.client.post(reverse('backoffice_generate_invoice', args=[self.pedido.id]), {
			'metodo_entrega': 'RUTA_DRIVER',
			'driver_id': str(self.driver.id),
			'estimated_delivery_at': '2026-04-18T09:30',
			f'suggested_unit_price_{self.pedido_item.id}': '2.75',
		})

		invoice = Invoice.objects.get(pedido=self.pedido)
		self.assertRedirects(response, reverse('backoffice_invoice_detail', args=[invoice.id]))
		self.assertEqual(timezone.localtime(invoice.delivery.estimated_delivery_at).strftime('%Y-%m-%d %H:%M'), '2026-04-18 09:30')

	def test_invoice_pdf_suggested_retail_uses_default_profit_suggestion(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='LTG',
			driver=None,
			usuario=self.backoffice,
		)

		suggested_unit_price = _resolve_invoice_suggested_unit_price(invoice.items.first())

		self.assertEqual(suggested_unit_price, Decimal('1.79'))

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

	def test_invoice_pdf_includes_customer_signature_section_when_signed(self):
		unsigned_invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='LTG',
			driver=None,
			usuario=self.backoffice,
		)
		pedido_signed = Pedido.objects.create(
			cliente=self.cliente,
			origen='CLIENTE',
			estado='VERIFICADO_AJUSTADO',
			total=Decimal('45.00'),
		)
		PedidoItem.objects.create(
			pedido=pedido_signed,
			presentacion=self.presentacion,
			cantidad_solicitada=4,
			cantidad=3,
			precio=Decimal('15.00'),
			subtotal=Decimal('45.00'),
		)
		invoice = generar_invoice_desde_picking(
			pedido=pedido_signed,
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

		self.client.force_login(self.backoffice)
		unsigned_response = self.client.get(reverse('backoffice_invoice_pdf', args=[unsigned_invoice.id]))
		response = self.client.get(reverse('backoffice_invoice_pdf', args=[invoice.id]))

		self.assertEqual(unsigned_response.status_code, 200)
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response['Content-Type'], 'application/pdf')
		self.assertGreater(len(response.content), len(unsigned_response.content))

	def test_backoffice_and_driver_delivery_details_show_estimated_delivery(self):
		estimated_delivery_at = timezone.make_aware(datetime(2026, 4, 18, 9, 30), timezone.get_current_timezone())
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
			estimated_delivery_at=estimated_delivery_at,
		)

		self.client.force_login(self.backoffice)
		backoffice_response = self.client.get(reverse('backoffice_invoice_detail', args=[invoice.id]))
		self.client.force_login(self.driver)
		driver_response = self.client.get(reverse('driver_delivery_detail', args=[invoice.delivery.id]))

		self.assertContains(backoffice_response, 'Estimated delivery')
		self.assertContains(backoffice_response, '18/04/2026 09:30')
		self.assertContains(driver_response, 'Estimated delivery')
		self.assertContains(driver_response, '18/04/2026 09:30')

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
		self.assertContains(response, 'Usar camara')
		self.assertContains(response, 'Tomar foto')
		self.assertContains(response, 'Fotos seleccionadas')
		self.assertContains(response, 'Aún no se han subido fotos de evidencia.', html=False)
		self.assertContains(response, 'Completar entrega')
		self.assertContains(response, 'Recibido por')
		self.assertContains(response, 'Método de pago')
		self.assertContains(response, 'Detalles del pago')
		self.assertContains(response, 'Firma del cliente')
		self.assertContains(response, 'Guardar entrega')
		self.assertContains(response, 'Asignada')
		self.assertContains(response, 'Pendiente')
		content = response.content.decode('utf-8')
		self.assertLess(content.index('data-driver-section="adjustment-note"'), content.index('data-driver-section="payment-details"'))

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

	def test_backoffice_and_driver_adjustment_reason_include_missing_item(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
		)

		self.client.force_login(self.backoffice)
		backoffice_response = self.client.get(reverse('backoffice_invoice_detail', args=[invoice.id]))

		self.assertEqual(backoffice_response.status_code, 200)
		self.assertContains(backoffice_response, 'value="MISSING_ITEM"', html=False)
		self.assertContains(backoffice_response, 'Missing item')

		self.client.force_login(self.driver)
		driver_response = self.client.get(reverse('driver_delivery_detail', args=[invoice.delivery.id]))

		self.assertEqual(driver_response.status_code, 200)
		self.assertContains(driver_response, 'value="MISSING_ITEM"', html=False)
		self.assertContains(driver_response, 'Missing item')

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

	def test_driver_delivery_list_orders_active_deliveries_by_estimated_delivery(self):
		invoice_1 = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
			estimated_delivery_at=timezone.make_aware(datetime(2026, 4, 18, 10, 0), timezone.get_current_timezone()),
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
			estimated_delivery_at=timezone.make_aware(datetime(2026, 4, 18, 9, 0), timezone.get_current_timezone()),
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

		self.client.force_login(self.driver)
		response = self.client.get(reverse('driver_delivery_list'))

		self.assertEqual(
			list(response.context['deliveries'].values_list('id', flat=True)),
			[invoice_2.delivery.id, invoice_1.delivery.id, invoice_3.delivery.id],
		)

	def test_driver_route_uses_assigned_delivery_order(self):
		invoice_1 = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
			estimated_delivery_at=timezone.make_aware(datetime(2026, 4, 18, 10, 0), timezone.get_current_timezone()),
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
			estimated_delivery_at=timezone.make_aware(datetime(2026, 4, 18, 9, 0), timezone.get_current_timezone()),
		)

		invoice_1.delivery.delivery_address = '111 Alpha St'
		invoice_1.delivery.save(update_fields=['delivery_address', 'updated_at'])
		invoice_2.delivery.delivery_address = '222 Beta St'
		invoice_2.delivery.save(update_fields=['delivery_address', 'updated_at'])

		self.client.force_login(self.driver)
		response = self.client.get(reverse('driver_delivery_route'), {
			'delivery_ids': [invoice_1.delivery.id, invoice_2.delivery.id],
		})

		self.assertEqual(response.status_code, 302)
		self.assertIn('destination=111+Alpha+St', response['Location'])
		self.assertIn('waypoints=222+Beta+St', response['Location'])

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
