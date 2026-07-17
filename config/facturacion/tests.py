from unittest.mock import patch

import base64
from io import BytesIO
from datetime import datetime
from decimal import Decimal

from django.conf import settings
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.test.utils import override_settings
from django.utils import timezone
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate

from config.clientes.models import Cliente
from config.facturacion.models import Delivery, DeliveryNotificationLog, FacturacionRegistroAnulacion, Invoice, InvoiceItem, NotaAjuste, NotaAjusteAplicacion
from config.facturacion.services import _normalize_uploaded_file, _rewind_uploaded_file, anular_invoice, anular_nota_ajuste, aprobar_nota_ajuste, attach_invoice_item_net_dispatched_quantities, build_google_maps_route_url, build_invoice_shipment_summary, complete_driver_delivery, crear_nota_ajuste, crear_nota_ajuste_desde_invoice, eliminar_invoice, eliminar_nota_ajuste, ensure_delivery_for_invoice, generar_invoice_desde_picking, generar_invoice_directa_backoffice, invoice_delete_requires_confirmation_phrase, mark_delivery_unpaid_from_backoffice, resolve_customer_amount_owed, resolve_customer_overdue_balance, resolve_invoice_item_net_dispatched_quantity, resolve_invoice_payment_base_date, resolve_invoice_payment_due_date, start_delivery_route, unlock_client_from_delivery, validate_invoice_delete_confirmation_phrase
from config.facturacion.views import _build_invoice_pdf_barcode, _build_invoice_pdf_barcode_cell, _build_invoice_pdf_footer_layout, _build_invoice_pdf_item_data, _build_invoice_pdf_shipment_summary_table, _build_invoice_pdf_terms_paragraph, _build_invoice_pdf_totals_rows, _chunk_invoice_pdf_item_rows, _invoice_pdf_item_table_column_widths, _resolve_invoice_pdf_due_date_label, _resolve_invoice_suggested_unit_price, _save_adjustment_note_evidence_files
from config.integrations.quickbooks.constants import QUICKBOOKS_SYNC_STATUS_SYNCED
from config.inventario.models import InventarioMovimiento, StockPresentacion, StockProductoFraccionado
from config.inventario.services import registrar_entrada_manual, reservar_stock_para_pedido_items
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
			tipo_contenido='unidades',
			peso_por_caja=Decimal('33.827'),
			precio_1=Decimal('15.00'),
			precio_2=Decimal('16.00'),
			precio_3=Decimal('17.00'),
			precio_4=Decimal('18.00'),
			precio_5=Decimal('19.00'),
		)
		self.presentacion_unidad = Presentacion.objects.create(
			producto=producto,
			nombre='Unidad',
			unidades=1,
			tipo_contenido='unidad',
			precio_1=Decimal('1.25'),
			precio_2=Decimal('1.35'),
			precio_3=Decimal('1.45'),
			precio_4=Decimal('1.55'),
			precio_5=Decimal('1.65'),
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
		registrar_entrada_manual(presentacion=self.presentacion_unidad, cantidad=10, observacion='Seed unit stock')

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

	def _create_invoice(self, *, metodo_entrega='CUSTOMER_PICK_UP', driver=None, total='15.00'):
		pedido = self._create_verified_order(total=total)
		return generar_invoice_desde_picking(
			pedido=pedido,
			metodo_entrega=metodo_entrega,
			driver=driver,
			usuario=self.backoffice,
		)

	def _build_test_image(self, name='evidence.png'):
		return SimpleUploadedFile(
			name,
			base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO6pS7QAAAAASUVORK5CYII='),
			content_type='image/png',
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

	def test_generar_invoice_directa_backoffice_creates_invoice_and_discounts_inventory(self):
		starting_stock = StockPresentacion.objects.get(presentacion=self.presentacion).stock_fisico

		invoice = generar_invoice_directa_backoffice(
			cliente=self.cliente,
			items_payload=[{
				'presentacion': self.presentacion,
				'cantidad': 2,
				'precio': Decimal('18.00'),
			}],
			metodo_entrega='CUSTOMER_PICK_UP',
			usuario=self.backoffice,
			nota_backoffice='Venta mostrador',
		)

		invoice.refresh_from_db()
		invoice.pedido.refresh_from_db()
		stock = StockPresentacion.objects.get(presentacion=self.presentacion)
		pedido_item = invoice.pedido.items.get()
		self.assertEqual(invoice.metodo_entrega, 'CUSTOMER_PICK_UP')
		self.assertEqual(invoice.pedido.origen, 'BACKOFFICE')
		self.assertEqual(invoice.pedido.nota_backoffice, 'Venta mostrador')
		self.assertEqual(invoice.items.first().precio_unitario, Decimal('18.00'))
		self.assertEqual(stock.stock_fisico, starting_stock - 2)
		self.assertEqual(pedido_item.cantidad_inventario_aplicada, 2)
		self.assertTrue(InventarioMovimiento.objects.filter(pedido=invoice.pedido, tipo='SALIDA_PICKING').exists())

	def test_generar_invoice_desde_picking_applies_pending_inventory_before_invoice(self):
		starting_stock = 20
		stock, _created = StockPresentacion.objects.get_or_create(
			presentacion=self.presentacion,
			defaults={'stock_fisico': starting_stock, 'stock_reservado': 0, 'stock_disponible': starting_stock},
		)
		stock.stock_fisico = starting_stock
		stock.stock_reservado = 0
		stock.stock_disponible = starting_stock
		stock.save(update_fields=['stock_fisico', 'stock_reservado', 'stock_disponible', 'actualizado_en'])
		self.pedido_item.cantidad = 2
		self.pedido_item.cantidad_inventario_aplicada = 0
		self.pedido_item.save(update_fields=['cantidad', 'cantidad_inventario_aplicada'])

		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
		)

		self.pedido_item.refresh_from_db()
		stock.refresh_from_db()
		self.assertEqual(invoice.items.first().cantidad_facturada, 2)
		self.assertEqual(self.pedido_item.cantidad_inventario_aplicada, 2)
		self.assertEqual(stock.stock_fisico, starting_stock - 2)

	def test_generar_invoice_directa_backoffice_creates_driver_route_with_delivery(self):
		invoice = generar_invoice_directa_backoffice(
			cliente=self.cliente,
			items_payload=[{
				'presentacion': self.presentacion,
				'cantidad': 2,
				'precio': Decimal('18.00'),
			}],
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
			nota_backoffice='Entrega directa con driver',
		)

		invoice.refresh_from_db()
		self.assertEqual(invoice.metodo_entrega, 'RUTA_DRIVER')
		self.assertEqual(invoice.driver_id, self.driver.id)
		self.assertTrue(invoice.despachador_notificado)
		self.assertTrue(hasattr(invoice, 'delivery'))
		self.assertEqual(invoice.delivery.invoice_id, invoice.id)

	def test_generate_invoice_accepts_manual_suggested_unit_price(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
			suggested_unit_prices={self.pedido_item.id: Decimal('2.49')},
		)

		self.assertEqual(invoice.items.first().precio_venta_sugerido_unitario, Decimal('2.49'))

	def test_generate_invoice_applies_customer_credit_and_reduces_customer_balance(self):
		self.cliente.balance = Decimal('-30.00')
		self.cliente.save(update_fields=['balance'])

		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
			applied_customer_credit=Decimal('30.00'),
		)

		self.cliente.refresh_from_db()
		self.assertEqual(invoice.credito_cliente_aplicado, Decimal('30.00'))
		self.assertEqual(invoice.saldo_cliente, Decimal('15.00'))
		self.assertEqual(self.cliente.balance, Decimal('0.00'))

	def test_backoffice_generate_invoice_blocks_when_credit_limit_exceeded(self):
		self.cliente.credit_limit = Decimal('2000.00')
		self.cliente.balance = Decimal('1900.00')
		self.cliente.save(update_fields=['credit_limit', 'balance'])
		self.pedido.total = Decimal('2000.00')
		self.pedido.save(update_fields=['total', 'actualizada_en'])
		self.client.force_login(self.backoffice)

		response = self.client.post(reverse('backoffice_generate_invoice', args=[self.pedido.id]), {
			'metodo_entrega': 'CUSTOMER_PICK_UP',
			'driver_id': '',
		})

		self.assertEqual(response.status_code, 302)
		self.assertIn('credit_limit_alert=1', response.url)
		self.assertFalse(Invoice.objects.filter(pedido=self.pedido).exists())
		self.assertTrue(self.cliente.alertas_limite_credito.filter(pedido=self.pedido, estado='PENDIENTE').exists())

	def test_backoffice_generate_invoice_allows_release_after_credit_limit_alert(self):
		self.cliente.credit_limit = Decimal('2000.00')
		self.cliente.balance = Decimal('1900.00')
		self.cliente.save(update_fields=['credit_limit', 'balance'])
		self.pedido.total = Decimal('2000.00')
		self.pedido.save(update_fields=['total', 'actualizada_en'])
		self.client.force_login(self.backoffice)

		first_response = self.client.post(reverse('backoffice_generate_invoice', args=[self.pedido.id]), {
			'metodo_entrega': 'CUSTOMER_PICK_UP',
			'driver_id': '',
		})
		self.assertIn('credit_limit_alert=1', first_response.url)

		release_response = self.client.post(reverse('backoffice_resolve_credit_limit', args=[self.pedido.id]), {
			'action': 'release',
		})
		self.assertRedirects(release_response, reverse('backoffice_pedido_detalle', args=[self.pedido.id]))
		self.pedido.refresh_from_db()
		self.assertTrue(self.pedido.credit_limit_liberado)

		second_response = self.client.post(reverse('backoffice_generate_invoice', args=[self.pedido.id]), {
			'metodo_entrega': 'CUSTOMER_PICK_UP',
			'driver_id': '',
		})
		invoice = Invoice.objects.get(pedido=self.pedido)
		self.assertRedirects(
			second_response,
			f"{reverse('backoffice_invoice_detail', args=[invoice.id])}?focus_adjustment_note=1",
		)

	@override_settings(CREDIT_HOLD_TEST_EMAIL='credit-hold-test@example.com')
	def test_order_creation_places_credit_hold_when_limit_exceeded(self):
		from django.core import mail

		from config.clientes.credit_limit import pedido_tiene_credit_hold_pendiente
		from config.pedidos.services import asignar_picking_a_seleccionador, crear_pedido_desde_items

		self.cliente.credit_limit = Decimal('2000.00')
		self.cliente.balance = Decimal('1900.00')
		self.cliente.save(update_fields=['credit_limit', 'balance'])
		selector = Usuario.objects.create_user(username='selector-hold', password='secret123', role='seleccionador')

		pedido = crear_pedido_desde_items(
			cliente=self.cliente,
			items_payload=[{
				'presentacion': self.presentacion,
				'cantidad': 10,
				'precio': Decimal('20.00'),
			}],
			origen='VENDEDOR',
			vendedor=self.backoffice,
			reservar_inventario=False,
		)

		self.assertTrue(pedido_tiene_credit_hold_pendiente(pedido))
		self.assertTrue(
			self.cliente.alertas_limite_credito.filter(pedido=pedido, estado='PENDIENTE').exists()
		)
		self.assertTrue(
			Notificacion.objects.filter(titulo__icontains='CREDIT HOLD', url__contains=str(pedido.id)).exists()
		)
		self.assertTrue(mail.outbox)
		self.assertEqual(mail.outbox[-1].to, ['credit-hold-test@example.com'])
		self.assertIn('CREDIT HOLD', mail.outbox[-1].subject)

		with self.assertRaises(ValidationError):
			asignar_picking_a_seleccionador(pedido=pedido, seleccionador=selector)

		self.client.force_login(self.backoffice)
		release_response = self.client.post(reverse('backoffice_resolve_credit_limit', args=[pedido.id]), {
			'action': 'release',
			'comentario': 'Customer will pay tomorrow',
		})
		self.assertRedirects(release_response, reverse('backoffice_pedido_detalle', args=[pedido.id]))
		pedido.refresh_from_db()
		self.assertTrue(pedido.credit_limit_liberado)
		self.assertFalse(pedido_tiene_credit_hold_pendiente(pedido))

		asignar_picking_a_seleccionador(pedido=pedido, seleccionador=selector)
		pedido.refresh_from_db()
		self.assertEqual(pedido.estado, 'PARA_VERIFICAR')

	def test_blocked_credit_hold_can_be_unblocked(self):
		from config.clientes.credit_limit import pedido_tiene_credit_hold_pendiente, resolve_credit_limit_alert
		from config.pedidos.services import crear_pedido_desde_items

		self.cliente.credit_limit = Decimal('2000.00')
		self.cliente.balance = Decimal('1900.00')
		self.cliente.save(update_fields=['credit_limit', 'balance'])

		pedido = crear_pedido_desde_items(
			cliente=self.cliente,
			items_payload=[{
				'presentacion': self.presentacion,
				'cantidad': 10,
				'precio': Decimal('20.00'),
			}],
			origen='VENDEDOR',
			vendedor=self.backoffice,
			reservar_inventario=False,
		)
		alerta = self.cliente.alertas_limite_credito.get(pedido=pedido, estado='PENDIENTE')
		resolve_credit_limit_alert(pedido=pedido, usuario=self.backoffice, action='block')
		pedido.refresh_from_db()
		self.cliente.refresh_from_db()
		self.assertTrue(pedido.credit_limit_bloqueado)
		self.assertTrue(self.cliente.credit_hold)
		self.assertTrue(pedido_tiene_credit_hold_pendiente(pedido))

		self.client.force_login(self.backoffice)
		missing_name_response = self.client.post(reverse('backoffice_resolve_credit_limit', args=[pedido.id]), {
			'action': 'unblock',
			'comentario': 'Customer paid outstanding balance',
		})
		self.assertRedirects(missing_name_response, reverse('backoffice_pedido_detalle', args=[pedido.id]))
		pedido.refresh_from_db()
		self.assertTrue(pedido.credit_limit_bloqueado)

		response = self.client.post(reverse('backoffice_resolve_credit_limit', args=[pedido.id]), {
			'action': 'unblock',
			'autorizado_por': 'Maria Lopez',
			'comentario': 'Customer paid outstanding balance',
		})
		self.assertRedirects(response, reverse('backoffice_pedido_detalle', args=[pedido.id]))
		pedido.refresh_from_db()
		self.cliente.refresh_from_db()
		alerta.refresh_from_db()
		self.assertFalse(pedido.credit_limit_bloqueado)
		self.assertTrue(pedido.credit_limit_liberado)
		self.assertFalse(self.cliente.credit_hold)
		self.assertEqual(alerta.estado, 'LIBERADO')
		self.assertFalse(pedido_tiene_credit_hold_pendiente(pedido))

	def test_generate_invoice_keeps_zero_quantity_lines_with_zero_subtotal(self):
		self.pedido_item.cantidad = 0
		self.pedido_item.subtotal = Decimal('0.00')
		self.pedido_item.save(update_fields=['cantidad', 'subtotal'])
		self.pedido.total = Decimal('0.00')
		self.pedido.save(update_fields=['total', 'actualizada_en'])

		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
		)

		invoice_item = invoice.items.get()
		self.assertEqual(invoice_item.cantidad_facturada, 0)
		self.assertEqual(invoice_item.subtotal, Decimal('0.00'))
		self.assertEqual(invoice.subtotal, Decimal('0.00'))
		self.assertEqual(invoice.saldo_cliente, Decimal('0.00'))

	def test_synced_invoice_blocks_creating_new_adjustment_note(self):
		self.client.force_login(self.backoffice)
		invoice = self._create_invoice()
		invoice.quickbooks_id = 'QB-INV-1'
		invoice.sync_status = QUICKBOOKS_SYNC_STATUS_SYNCED
		invoice.save(update_fields=['quickbooks_id', 'sync_status'])

		response = self.client.post(reverse('backoffice_invoice_create_note', args=[invoice.id]), {}, follow=True)

		self.assertEqual(response.status_code, 200)
		self.assertFalse(invoice.notas_ajuste.exists())
		messages = [message.message for message in response.context['messages']]
		self.assertTrue(any('locked because it is already synced with QuickBooks' in message for message in messages))

	def test_synced_invoice_blocks_approving_draft_note(self):
		self.client.force_login(self.backoffice)
		invoice = self._create_invoice()
		nota = crear_nota_ajuste_desde_invoice(
			invoice=invoice,
			tipo_ajuste='FINANCIERO',
			tipo_documento='CREDITO',
			motivo='OTHER',
			tipo_credito='CREDIT_DUMP',
			descripcion='Draft before sync',
			usuario=self.backoffice,
			items_payload=[],
			monto=Decimal('5.00'),
		)
		invoice.quickbooks_id = 'QB-INV-2'
		invoice.sync_status = QUICKBOOKS_SYNC_STATUS_SYNCED
		invoice.save(update_fields=['quickbooks_id', 'sync_status'])

		response = self.client.post(reverse('backoffice_invoice_approve_note', args=[nota.id]), follow=True)

		self.assertEqual(response.status_code, 200)
		nota.refresh_from_db()
		self.assertEqual(nota.estado, 'BORRADOR')
		messages = [message.message for message in response.context['messages']]
		self.assertTrue(any('locked because it is already synced with QuickBooks' in message for message in messages))

	def test_synced_note_blocks_void_action(self):
		self.client.force_login(self.backoffice)
		invoice = self._create_invoice()
		nota = crear_nota_ajuste_desde_invoice(
			invoice=invoice,
			tipo_ajuste='FINANCIERO',
			tipo_documento='CREDITO',
			motivo='OTHER',
			tipo_credito='CREDIT_DUMP',
			descripcion='Already synced note',
			usuario=self.backoffice,
			items_payload=[],
			monto=Decimal('5.00'),
		)
		nota.quickbooks_id = 'QB-NOTE-1'
		nota.sync_status = QUICKBOOKS_SYNC_STATUS_SYNCED
		nota.save(update_fields=['quickbooks_id', 'sync_status'])

		response = self.client.post(reverse('backoffice_invoice_cancel_note', args=[nota.id]), follow=True)

		self.assertEqual(response.status_code, 200)
		nota.refresh_from_db()
		self.assertEqual(nota.estado, 'BORRADOR')
		messages = [message.message for message in response.context['messages']]
		self.assertTrue(any('Adjustment note' in message and 'locked because it is already synced with QuickBooks' in message for message in messages))

	def test_driver_cannot_create_note_for_synced_invoice(self):
		invoice = self._create_invoice(metodo_entrega='RUTA_DRIVER', driver=self.driver)
		invoice.delivery.estado = 'ENTREGADA_PAGADA'
		invoice.delivery.save(update_fields=['estado'])
		invoice.quickbooks_id = 'QB-INV-DRIVER'
		invoice.sync_status = QUICKBOOKS_SYNC_STATUS_SYNCED
		invoice.save(update_fields=['quickbooks_id', 'sync_status'])
		self.client.force_login(self.driver)

		response = self.client.post(
			reverse('driver_delivery_create_note', args=[invoice.delivery.id]),
			{
				'driver_note_tipo_ajuste': 'PRODUCTO',
				'driver_note_tipo_documento': 'CREDITO',
				'driver_note_motivo': 'DAMAGE',
				'driver_note_tipo_credito': 'CREDIT_DUMP',
				'driver_note_descripcion': 'Driver locked test',
				f'driver_note_qty_{invoice.items.first().id}': '1',
				f'driver_note_amount_{invoice.items.first().id}': '5.00',
			},
			follow=True,
		)

		self.assertEqual(response.status_code, 200)
		self.assertFalse(NotaAjuste.objects.filter(invoice=invoice, descripcion='Driver locked test').exists())
		messages = [message.message for message in response.context['messages']]
		self.assertTrue(any('locked because it is already synced with QuickBooks' in message for message in messages))

	def test_synced_general_note_cannot_be_applied_to_new_invoice(self):
		general_note = crear_nota_ajuste(
			cliente=self.cliente,
			invoice=None,
			tipo_ajuste='FINANCIERO',
			tipo_documento='CREDITO',
			motivo='OTHER',
			tipo_credito='CREDIT_DUMP',
			descripcion='General synced note',
			usuario=self.backoffice,
			items_payload=[],
			monto=Decimal('8.00'),
		)
		aprobar_nota_ajuste(nota=general_note, usuario=self.backoffice)
		general_note.quickbooks_id = 'QB-GENERAL-1'
		general_note.sync_status = QUICKBOOKS_SYNC_STATUS_SYNCED
		general_note.save(update_fields=['quickbooks_id', 'sync_status'])
		pedido = self._create_verified_order(total='15.00', quantity=1)

		with self.assertRaisesMessage(ValidationError, 'Adjustment note'):
			generar_invoice_desde_picking(
				pedido=pedido,
				metodo_entrega='CUSTOMER_PICK_UP',
				driver=None,
				usuario=self.backoffice,
				selected_note_applications={general_note.id: Decimal('5.00')},
			)

	def test_invoice_pdf_item_data_recovers_requested_quantity_from_reservation_history(self):
		reservar_stock_para_pedido_items(pedido=self.pedido, pedido_items=[self.pedido_item], creado_por=self.backoffice)
		self.pedido_item.cantidad_solicitada = 0
		self.pedido_item.cantidad = 0
		self.pedido_item.subtotal = Decimal('0.00')
		self.pedido_item.save(update_fields=['cantidad_solicitada', 'cantidad', 'subtotal'])
		self.pedido.total = Decimal('0.00')
		self.pedido.save(update_fields=['total', 'actualizada_en'])

		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
		)

		rows = _build_invoice_pdf_item_data(invoice)
		self.assertEqual(rows[0]['requested_quantity'], '4')
		self.assertEqual(rows[0]['dispatched_quantity'], '0')

	def test_invoice_pdf_item_data_uses_default_30_percent_profit_for_auto_suggested_prices(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
		)

		rows = _build_invoice_pdf_item_data(invoice)
		self.assertEqual(rows[0]['profit_percentage'], '30.00%')

	def test_backoffice_invoice_detail_shows_requested_and_zero_dispatched_quantities(self):
		self.client.force_login(self.backoffice)
		self.pedido_item.cantidad = 0
		self.pedido_item.subtotal = Decimal('0.00')
		self.pedido_item.save(update_fields=['cantidad', 'subtotal'])
		self.pedido.total = Decimal('0.00')
		self.pedido.save(update_fields=['total', 'actualizada_en'])

		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
		)

		response = self.client.get(reverse('backoffice_invoice_detail', args=[invoice.id]))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Requested qty.')
		self.assertContains(response, 'Dispatched qty.')
		self.assertContains(response, f'<td>{self.pedido_item.cantidad_solicitada}</td>', html=False)
		self.assertContains(response, '<td>0</td>', html=False)
		self.assertContains(response, '$0.00')

	def test_generate_invoice_requires_verified_order(self):
		self.pedido.estado = 'PARA_VERIFICAR'
		self.pedido.save(update_fields=['estado'])

		with self.assertRaises(ValidationError):
			generar_invoice_desde_picking(
				pedido=self.pedido,
				metodo_entrega='CUSTOMER_PICK_UP',
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

	def test_generate_invoice_rejects_ltg_delivery_method(self):
		with self.assertRaises(ValidationError):
			generar_invoice_desde_picking(
				pedido=self.pedido,
				metodo_entrega='LTG',
				driver=None,
				usuario=self.backoffice,
			)

	def test_generate_invoice_rejects_driver_for_customer_pickup(self):
		with self.assertRaises(ValidationError):
			generar_invoice_desde_picking(
				pedido=self.pedido,
				metodo_entrega='CUSTOMER_PICK_UP',
				driver=self.driver,
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
			metodo_entrega='CUSTOMER_PICK_UP',
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

	def test_approved_credit_return_reduces_invoice_dispatched_qty_and_case_count(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
		)
		item = invoice.items.get()
		nota = crear_nota_ajuste_desde_invoice(
			invoice=invoice,
			tipo_documento='CREDITO',
			motivo='DEFECT',
			tipo_credito='CREDIT_RETURN',
			descripcion='Defecto de fabrica',
			usuario=self.backoffice,
			items_payload=[{
				'invoice_item': item,
				'cantidad': 1,
				'monto_unitario': Decimal('15.00'),
			}],
		)

		aprobar_nota_ajuste(nota=nota, usuario=self.backoffice)
		item.refresh_from_db()

		self.assertEqual(item.cantidad_facturada, 3)
		self.assertEqual(resolve_invoice_item_net_dispatched_quantity(item), 2)
		self.assertEqual(build_invoice_shipment_summary(invoice)['total_cases'], 2)

		rows = _build_invoice_pdf_item_data(invoice)
		self.assertEqual(rows[0]['dispatched_quantity'], '2')

		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_invoice_detail', args=[invoice.id]))
		self.assertContains(response, 'data-label="Dispatched qty.">2</td>', html=False)

	def test_full_credit_return_sets_invoice_line_dispatched_qty_to_zero(self):
		pedido = self._create_verified_order(total='25.03', quantity=1)
		invoice = generar_invoice_desde_picking(
			pedido=pedido,
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
		)
		item = invoice.items.get()
		nota = crear_nota_ajuste_desde_invoice(
			invoice=invoice,
			tipo_documento='CREDITO',
			motivo='DEFECT',
			tipo_credito='CREDIT_RETURN',
			descripcion='Devolucion total',
			usuario=self.backoffice,
			items_payload=[{
				'invoice_item': item,
				'cantidad': 1,
				'monto_unitario': Decimal('25.03'),
			}],
		)

		aprobar_nota_ajuste(nota=nota, usuario=self.backoffice)

		self.assertEqual(resolve_invoice_item_net_dispatched_quantity(item), 0)
		self.assertEqual(build_invoice_shipment_summary(invoice)['total_cases'], 0)
		self.assertEqual(_build_invoice_pdf_item_data(invoice)[0]['dispatched_quantity'], '0')

	def test_credit_note_without_invoice_increases_customer_credit_balance(self):
		nota = crear_nota_ajuste(
			cliente=self.cliente,
			invoice=None,
			tipo_ajuste='FINANCIERO',
			tipo_documento='CREDITO',
			motivo='DEFECT',
			tipo_credito='CREDIT_DUMP',
			descripcion='Saldo a favor general',
			usuario=self.backoffice,
			items_payload=[],
			monto=Decimal('25.00'),
		)

		aprobar_nota_ajuste(nota=nota, usuario=self.backoffice)
		self.cliente.refresh_from_db()
		nota.refresh_from_db()

		self.assertEqual(self.cliente.balance, Decimal('-25.00'))
		self.assertEqual(nota.monto_aplicado_invoice, Decimal('0.00'))
		self.assertEqual(nota.monto_aplicado_cliente, Decimal('25.00'))

	def test_debit_note_without_invoice_increases_customer_due_balance(self):
		self.cliente.balance = Decimal('40.00')
		self.cliente.save(update_fields=['balance'])

		nota = crear_nota_ajuste(
			cliente=self.cliente,
			invoice=None,
			tipo_ajuste='FINANCIERO',
			tipo_documento='DEBITO',
			motivo='DEFECT',
			tipo_credito='',
			descripcion='Cargo financiero general',
			usuario=self.backoffice,
			items_payload=[],
			monto=Decimal('10.00'),
		)

		aprobar_nota_ajuste(nota=nota, usuario=self.backoffice)
		self.cliente.refresh_from_db()
		nota.refresh_from_db()

		self.assertEqual(self.cliente.balance, Decimal('50.00'))
		self.assertEqual(nota.monto_aplicado_invoice, Decimal('0.00'))
		self.assertEqual(nota.monto_aplicado_cliente, Decimal('10.00'))

	def test_general_credit_note_can_be_applied_partially_across_multiple_invoices(self):
		nota = crear_nota_ajuste(
			cliente=self.cliente,
			invoice=None,
			tipo_ajuste='FINANCIERO',
			tipo_documento='CREDITO',
			motivo='DEFECT',
			tipo_credito='CREDIT_DUMP',
			descripcion='Credito general para proxima invoice',
			usuario=self.backoffice,
			items_payload=[],
			monto=Decimal('25.00'),
		)

		aprobar_nota_ajuste(nota=nota, usuario=self.backoffice)
		invoice = generar_invoice_desde_picking(
			pedido=self._create_verified_order(total='20.00'),
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
			selected_note_applications={nota.id: Decimal('10.00')},
		)

		nota.refresh_from_db()
		invoice.refresh_from_db()
		self.cliente.refresh_from_db()

		self.assertIsNone(nota.invoice)
		self.assertEqual(nota.monto_aplicado_invoice, Decimal('10.00'))
		self.assertEqual(nota.monto_aplicado_cliente, Decimal('15.00'))
		self.assertEqual(invoice.total_creditos, Decimal('10.00'))
		self.assertEqual(invoice.saldo_cliente, Decimal('10.00'))
		self.assertEqual(self.cliente.balance, Decimal('-15.00'))
		self.assertTrue(NotaAjusteAplicacion.objects.filter(nota=nota, invoice=invoice, monto=Decimal('10.00')).exists())

		second_invoice = generar_invoice_desde_picking(
			pedido=self._create_verified_order(total='15.00'),
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
			selected_note_applications={nota.id: Decimal('15.00')},
		)

		nota.refresh_from_db()
		second_invoice.refresh_from_db()
		self.cliente.refresh_from_db()

		self.assertEqual(nota.monto_aplicado_invoice, Decimal('25.00'))
		self.assertEqual(nota.monto_aplicado_cliente, Decimal('0.00'))
		self.assertEqual(second_invoice.total_creditos, Decimal('15.00'))
		self.assertEqual(second_invoice.saldo_cliente, Decimal('0.00'))
		self.assertEqual(self.cliente.balance, Decimal('0.00'))

	def test_general_debit_note_can_be_applied_partially_or_skipped(self):
		nota = crear_nota_ajuste(
			cliente=self.cliente,
			invoice=None,
			tipo_ajuste='FINANCIERO',
			tipo_documento='DEBITO',
			motivo='DEFECT',
			tipo_credito='',
			descripcion='Debito general para cobrar por partes',
			usuario=self.backoffice,
			items_payload=[],
			monto=Decimal('100.00'),
		)

		aprobar_nota_ajuste(nota=nota, usuario=self.backoffice)
		invoice = generar_invoice_desde_picking(
			pedido=self._create_verified_order(total='45.00'),
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
			selected_note_applications={nota.id: Decimal('10.00')},
		)

		nota.refresh_from_db()
		invoice.refresh_from_db()
		self.cliente.refresh_from_db()

		self.assertEqual(nota.monto_aplicado_invoice, Decimal('10.00'))
		self.assertEqual(nota.monto_aplicado_cliente, Decimal('90.00'))
		self.assertEqual(invoice.total_debitos, Decimal('10.00'))
		self.assertEqual(invoice.saldo_cliente, Decimal('55.00'))
		self.assertEqual(self.cliente.balance, Decimal('90.00'))

		skipped_invoice = generar_invoice_desde_picking(
			pedido=self._create_verified_order(total='20.00'),
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
			selected_note_applications={},
		)

		nota.refresh_from_db()
		skipped_invoice.refresh_from_db()

		self.assertEqual(skipped_invoice.total_debitos, Decimal('0.00'))
		self.assertEqual(skipped_invoice.saldo_cliente, Decimal('20.00'))
		self.assertEqual(nota.monto_aplicado_cliente, Decimal('90.00'))

	def test_product_credit_note_without_invoice_restocks_inventory_and_increases_customer_credit_balance(self):
		nota = crear_nota_ajuste(
			cliente=self.cliente,
			invoice=None,
			tipo_ajuste='PRODUCTO',
			tipo_documento='CREDITO',
			motivo='DAMAGE',
			tipo_credito='CREDIT_RETURN',
			descripcion='Devolucion manual sin invoice',
			usuario=self.backoffice,
			items_payload=[{
				'invoice_item': None,
				'presentacion': self.presentacion,
				'descripcion': 'Tortilla 12',
				'cantidad': 2,
				'monto_unitario': Decimal('30.00'),
			}],
			monto=Decimal('0.00'),
		)

		aprobar_nota_ajuste(nota=nota, usuario=self.backoffice)
		self.cliente.refresh_from_db()
		nota.refresh_from_db()

		self.assertEqual(self.cliente.balance, Decimal('-30.00'))
		self.assertEqual(nota.inventario_estado, 'PROCESADO')
		self.assertEqual(StockPresentacion.objects.get(presentacion=self.presentacion).stock_fisico, 27)

	def test_product_credit_note_can_split_case_return_into_loose_units(self):
		nota = crear_nota_ajuste(
			cliente=self.cliente,
			invoice=None,
			tipo_ajuste='PRODUCTO',
			tipo_documento='CREDITO',
			motivo='DAMAGE',
			tipo_credito='CREDIT_RETURN',
			descripcion='Caja abierta con una unidad dañada',
			usuario=self.backoffice,
			items_payload=[{
				'invoice_item': None,
				'presentacion': self.presentacion,
				'cantidad': 1,
				'cantidad_unidades': 1,
				'monto_unitario': Decimal('16.25'),
			}],
			monto=Decimal('0.00'),
		)

		aprobar_nota_ajuste(nota=nota, usuario=self.backoffice)
		nota.refresh_from_db()
		self.cliente.refresh_from_db()

		self.assertEqual(nota.total, Decimal('16.25'))
		self.assertEqual(nota.items.count(), 2)
		self.assertEqual(self.cliente.balance, Decimal('-16.25'))
		self.assertEqual(StockPresentacion.objects.get(presentacion=self.presentacion).stock_fisico, 26)
		self.assertEqual(StockPresentacion.objects.get(presentacion=self.presentacion_unidad).stock_fisico, 11)

	def test_product_credit_note_partial_box_without_unit_presentation_uses_internal_fractional_stock(self):
		categoria = Categoria.objects.create(nombre='Categoria sin unidad')
		marca = Marca.objects.create(nombre='Marca sin unidad')
		producto = Producto.objects.create(nombre='Producto sin unidad', categoria=categoria, marca=marca)
		presentacion_caja = Presentacion.objects.create(
			producto=producto,
			nombre='Caja',
			unidades=12,
			tipo_contenido='unidades',
			precio_1=Decimal('15.00'),
		)

		nota = crear_nota_ajuste(
			cliente=self.cliente,
			invoice=None,
			tipo_ajuste='PRODUCTO',
			tipo_documento='CREDITO',
			motivo='DAMAGE',
			tipo_credito='CREDIT_RETURN',
			descripcion='Caja abierta con devolucion parcial',
			usuario=self.backoffice,
			items_payload=[{
				'invoice_item': None,
				'presentacion': presentacion_caja,
				'cantidad': 0,
				'cantidad_unidades': 10,
				'monto_unitario': Decimal('12.50'),
			}],
			monto=Decimal('0.00'),
		)

		aprobar_nota_ajuste(nota=nota, usuario=self.backoffice)
		fractional_stock = StockProductoFraccionado.objects.get(producto=producto, contenido='unidades')

		self.assertEqual(nota.total, Decimal('12.50'))
		self.assertEqual(fractional_stock.stock_fisico, 10)
		self.assertEqual(nota.items.get(contenido_fraccionado='unidades').cantidad, 10)

	def test_partial_returns_promote_fractional_stock_into_full_box_and_reverse_back(self):
		categoria = Categoria.objects.create(nombre='Categoria acumulada')
		marca = Marca.objects.create(nombre='Marca acumulada')
		producto = Producto.objects.create(nombre='Producto acumulado', categoria=categoria, marca=marca)
		presentacion_caja = Presentacion.objects.create(
			producto=producto,
			nombre='Caja',
			unidades=20,
			tipo_contenido='unidades',
			precio_1=Decimal('20.00'),
		)

		nota_primera = crear_nota_ajuste(
			cliente=self.cliente,
			invoice=None,
			tipo_ajuste='PRODUCTO',
			tipo_documento='CREDITO',
			motivo='DAMAGE',
			tipo_credito='CREDIT_RETURN',
			descripcion='Primera devolucion parcial',
			usuario=self.backoffice,
			items_payload=[{
				'invoice_item': None,
				'presentacion': presentacion_caja,
				'cantidad': 0,
				'cantidad_unidades': 15,
				'monto_unitario': Decimal('15.00'),
			}],
			monto=Decimal('0.00'),
		)
		aprobar_nota_ajuste(nota=nota_primera, usuario=self.backoffice)

		nota_segunda = crear_nota_ajuste(
			cliente=self.cliente,
			invoice=None,
			tipo_ajuste='PRODUCTO',
			tipo_documento='CREDITO',
			motivo='DAMAGE',
			tipo_credito='CREDIT_RETURN',
			descripcion='Segunda devolucion parcial',
			usuario=self.backoffice,
			items_payload=[{
				'invoice_item': None,
				'presentacion': presentacion_caja,
				'cantidad': 0,
				'cantidad_unidades': 5,
				'monto_unitario': Decimal('5.00'),
			}],
			monto=Decimal('0.00'),
		)
		aprobar_nota_ajuste(nota=nota_segunda, usuario=self.backoffice)

		box_stock = StockPresentacion.objects.get(presentacion=presentacion_caja)
		fractional_stock = StockProductoFraccionado.objects.get(producto=producto, contenido='unidades')
		self.assertEqual(box_stock.stock_fisico, 1)
		self.assertEqual(fractional_stock.stock_fisico, 0)

		anular_nota_ajuste(nota=nota_segunda)
		box_stock.refresh_from_db()
		fractional_stock.refresh_from_db()
		self.assertEqual(box_stock.stock_fisico, 0)
		self.assertEqual(fractional_stock.stock_fisico, 15)

	def test_product_credit_note_partial_pallet_uses_box_presentation_automatically(self):
		pallet = Presentacion.objects.create(
			producto=self.presentacion.producto,
			nombre='Pallet',
			unidades=30,
			tipo_contenido='cajas',
			precio_1=Decimal('300.00'),
		)

		nota = crear_nota_ajuste(
			cliente=self.cliente,
			invoice=None,
			tipo_ajuste='PRODUCTO',
			tipo_documento='CREDITO',
			motivo='DAMAGE',
			tipo_credito='CREDIT_RETURN',
			descripcion='Pallet con cajas devueltas',
			usuario=self.backoffice,
			items_payload=[{
				'invoice_item': None,
				'presentacion': pallet,
				'cantidad': 0,
				'cantidad_unidades': 5,
				'monto_unitario': Decimal('50.00'),
			}],
			monto=Decimal('0.00'),
		)

		aprobar_nota_ajuste(nota=nota, usuario=self.backoffice)

		self.assertEqual(StockPresentacion.objects.get(presentacion=self.presentacion).stock_fisico, 30)
		self.assertFalse(StockProductoFraccionado.objects.filter(producto=self.presentacion.producto, contenido='cajas').exists())

	def test_credit_note_on_paid_invoice_moves_excess_to_customer_balance(self):
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
		invoice.refresh_from_db()
		self.assertEqual(invoice.saldo_cliente, Decimal('0.00'))

		nota = crear_nota_ajuste(
			cliente=self.cliente,
			invoice=invoice,
			tipo_ajuste='FINANCIERO',
			tipo_documento='CREDITO',
			motivo='DEFECT',
			tipo_credito='CREDIT_DUMP',
			descripcion='Credito sobre factura ya pagada',
			usuario=self.backoffice,
			items_payload=[],
			monto=Decimal('15.00'),
		)

		aprobar_nota_ajuste(nota=nota, usuario=self.backoffice)
		invoice.refresh_from_db()
		self.cliente.refresh_from_db()
		nota.refresh_from_db()

		self.assertEqual(invoice.total_creditos, Decimal('15.00'))
		self.assertEqual(invoice.saldo_cliente, Decimal('0.00'))
		self.assertEqual(self.cliente.balance, Decimal('-15.00'))
		self.assertEqual(nota.monto_aplicado_invoice, Decimal('15.00'))
		self.assertEqual(nota.monto_aplicado_cliente, Decimal('15.00'))

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

	def test_customer_assigned_credit_note_requires_matching_invoice(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
		)
		other_user = Usuario.objects.create_user(username='cliente-otro', password='secret123', role='cliente')
		other_customer = Cliente.objects.create(
			usuario=other_user,
			nombre_empresa='Otro Cliente',
			telefono='5559990000',
			direccion='500 Market St',
			ciudad='Houston',
			estado='TX',
			codigo_postal='77001',
			pais='USA',
			sales_tax_number='TX-555',
			certificado_tax='certificados/otro.pdf',
		)

		with self.assertRaisesMessage(ValidationError, 'The selected invoice does not belong to the selected customer.'):
			crear_nota_ajuste(
				cliente=other_customer,
				invoice=invoice,
				tipo_documento='CREDITO',
				motivo='DAMAGE',
				tipo_credito='CREDIT_RETURN',
				descripcion='Intento invalido',
				usuario=self.backoffice,
				items_payload=[{
					'invoice_item': invoice.items.first(),
					'cantidad': 1,
					'monto_unitario': Decimal('15.00'),
				}],
			)

	def test_customer_assigned_credit_note_can_be_created_and_return_inventory(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
		)

		nota = crear_nota_ajuste(
			cliente=self.cliente,
			invoice=invoice,
			tipo_documento='CREDITO',
			motivo='DAMAGE',
			tipo_credito='CREDIT_RETURN',
			descripcion='Credito directo al cliente aplicado a invoice',
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

		self.assertEqual(nota.cliente, self.cliente)
		self.assertEqual(invoice.total_creditos, Decimal('15.00'))
		self.assertEqual(invoice.saldo_cliente, Decimal('30.00'))
		self.assertEqual(nota.inventario_estado, 'PROCESADO')
		self.assertTrue(InventarioMovimiento.objects.filter(nota_ajuste=nota, tipo='ENTRADA_NOTA_CREDITO').exists())

	def test_credit_note_requires_credit_type(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
		)

		with self.assertRaisesMessage(ValidationError, 'Product credit notes must use Credit Return or Credit Dump.'):
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
			metodo_entrega='CUSTOMER_PICK_UP',
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
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
		)

		with self.assertRaisesMessage(ValidationError, 'Enter an amount greater than zero for each selected adjustment item.'):
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
		invoice.refresh_from_db()
		self.assertEqual(delivery.estado, 'ENTREGADA_PAGADA')
		self.assertEqual(delivery.estado_pago, 'PAGADO')
		self.assertEqual(invoice.saldo_cliente, Decimal('0.00'))
		self.assertTrue(bool(delivery.firma_cliente))
		self.assertFalse(delivery.invoice.cliente.credit_hold)
		self.assertTrue(DeliveryNotificationLog.objects.filter(delivery=delivery).count(), 3)

	def test_resolve_customer_amount_owed_combines_stored_balance_and_invoice_outstanding(self):
		self.cliente.balance = Decimal('10.00')
		self.cliente.save(update_fields=['balance'])
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
		)
		amount_owed = resolve_customer_amount_owed(cliente=self.cliente, invoice=invoice)
		self.assertEqual(amount_owed, Decimal('10.00') + invoice.saldo_cliente)
		self.assertEqual(self.cliente.total_amount_owed, amount_owed)

	def test_resolve_customer_amount_owed_includes_synced_unpaid_delivery(self):
		self.cliente.balance = Decimal('10.00')
		self.cliente.save(update_fields=['balance'])
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
		)
		invoice.quickbooks_id = 'QB-TEST-1'
		invoice.save(update_fields=['quickbooks_id'])
		complete_driver_delivery(
			delivery=invoice.delivery,
			driver_user=self.driver,
			payload={
				'estado_pago': 'NO_PAGADO',
				'recibido_por': 'Maria',
				'motivo_no_pago': 'Caja cerrada',
				'firma_cliente_data': self.signature_data,
			},
			evidence_files=[self.photo_file],
		)
		invoice.refresh_from_db()
		amount_owed = resolve_customer_amount_owed(cliente=self.cliente, invoice=invoice)
		self.assertEqual(amount_owed, Decimal('10.00') + invoice.saldo_cliente)

	def test_resolve_customer_amount_owed_hides_corrupted_balance_without_open_invoices(self):
		self.cliente.balance = Decimal('53819.72')
		self.cliente.save(update_fields=['balance'])
		self.assertEqual(resolve_customer_amount_owed(cliente=self.cliente), Decimal('0.00'))

	def test_resolve_customer_amount_owed_shows_quickbooks_synced_balance_without_local_invoices(self):
		self.cliente.quickbooks_id = 'QB-MI-TIERRA'
		self.cliente.sync_status = 'SYNCED'
		self.cliente.balance = Decimal('22545.71')
		self.cliente.save(update_fields=['quickbooks_id', 'sync_status', 'balance'])
		self.assertEqual(resolve_customer_amount_owed(cliente=self.cliente), Decimal('22545.71'))

	@patch('config.clientes.balance_summary.timezone')
	def test_resolve_customer_overdue_balance_excludes_not_yet_due_invoices(self, mock_timezone):
		from datetime import date, datetime

		mock_timezone.localdate.return_value = date(2026, 7, 6)
		mock_timezone.make_aware.side_effect = timezone.make_aware

		overdue_invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='CUSTOMER_PICK_UP',
			usuario=self.backoffice,
		)
		Invoice.objects.filter(pk=overdue_invoice.pk).update(
			creada_en=timezone.make_aware(datetime(2026, 6, 1, 12, 0, 0)),
			fecha_documento=date(2026, 6, 1),
		)
		overdue_invoice.refresh_from_db()

		current_pedido = Pedido.objects.create(
			cliente=self.cliente,
			origen='BACKOFFICE',
			estado='INVOICE_GENERADA',
			total=Decimal('50.00'),
		)
		current_invoice = Invoice.objects.create(
			pedido=current_pedido,
			cliente=self.cliente,
			metodo_entrega='CUSTOMER_PICK_UP',
			subtotal=Decimal('50.00'),
			total_neto=Decimal('50.00'),
			saldo_cliente=Decimal('50.00'),
		)
		Invoice.objects.filter(pk=current_invoice.pk).update(
			creada_en=timezone.make_aware(datetime(2026, 7, 6, 10, 0, 0)),
			fecha_documento=date(2026, 7, 6),
		)

		self.assertEqual(
			resolve_customer_overdue_balance(cliente=self.cliente),
			overdue_invoice.saldo_cliente,
		)

	def test_backoffice_can_mark_completed_paid_delivery_as_unpaid(self):
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
				'payment_method_1': 'CASH',
				'payment_amount_1': str(invoice.saldo_cliente),
				'recibido_por': 'Juan Perez',
				'firma_cliente_data': self.signature_data,
			},
			evidence_files=[],
			payment_files={},
		)
		delivery.refresh_from_db()
		invoice.refresh_from_db()
		self.assertEqual(delivery.estado_pago, 'PAGADO')
		self.assertEqual(invoice.saldo_cliente, Decimal('0.00'))

		mark_delivery_unpaid_from_backoffice(
			delivery=delivery,
			backoffice_user=self.backoffice,
			motivo_no_pago='Customer did not pay',
		)
		delivery.refresh_from_db()
		invoice.refresh_from_db()
		self.assertEqual(delivery.estado_pago, 'NO_PAGADO')
		self.assertEqual(delivery.estado, 'ENTREGADA_SIN_PAGO')
		self.assertEqual(delivery.monto_pagado, Decimal('0.00'))
		self.assertEqual(delivery.motivo_no_pago, 'Customer did not pay')
		self.assertTrue(delivery.invoice.cliente.credit_hold)
		self.assertGreater(invoice.saldo_cliente, Decimal('0.00'))
		self.assertGreaterEqual(
			resolve_customer_amount_owed(cliente=delivery.invoice.cliente, invoice=invoice),
			invoice.saldo_cliente,
		)

	def test_driver_cannot_mark_delivery_unpaid_from_backoffice_endpoint(self):
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
				'payment_method_1': 'CASH',
				'payment_amount_1': str(invoice.saldo_cliente),
				'recibido_por': 'Juan Perez',
				'firma_cliente_data': self.signature_data,
			},
			evidence_files=[],
			payment_files={},
		)
		self.client.force_login(self.driver)
		response = self.client.post(
			reverse('backoffice_mark_delivery_unpaid', args=[delivery.id]),
			{'motivo_no_pago': 'Trying to revert payment'},
		)
		self.assertEqual(response.status_code, 302)
		delivery.refresh_from_db()
		self.assertEqual(delivery.estado_pago, 'PAGADO')

	def test_driver_non_payment_updates_customer_due_balance(self):
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
				'motivo_no_pago': 'Caja cerrada',
				'firma_cliente_data': self.signature_data,
			},
			evidence_files=[self.photo_file],
		)
		invoice.refresh_from_db()
		self.assertGreaterEqual(
			resolve_customer_amount_owed(cliente=delivery.invoice.cliente, invoice=invoice),
			invoice.saldo_cliente,
		)

	def test_driver_non_payment_blocks_customer_without_photo_or_received_by(self):
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
				'motivo_no_pago': 'Caja cerrada',
				'firma_cliente_data': self.signature_data,
			},
			evidence_files=[],
		)

		delivery.refresh_from_db()
		delivery.invoice.cliente.refresh_from_db()
		self.assertEqual(delivery.estado, 'ENTREGADA_SIN_PAGO')
		self.assertEqual(delivery.recibido_por, '')
		self.assertTrue(delivery.invoice.cliente.credit_hold)
		self.assertEqual(delivery.evidence_photos.count(), 0)

	def test_driver_can_complete_delivery_with_cash_and_cheque_split_payment(self):
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
				'metodo_pago': 'MIXTO',
				'monto_pagado_cash': '20.00',
				'monto_pagado_cheque': '25.00',
				'recibido_por': 'Juan Perez',
				'cheque_numero': 'CHK-55',
				'cheque_banco': 'Bank Test',
				'firma_cliente_data': self.signature_data,
			},
			evidence_files=[],
			cheque_image_file=self._build_test_image('cheque-proof.png'),
		)

		delivery.refresh_from_db()
		invoice.refresh_from_db()
		self.assertEqual(delivery.metodo_pago, 'MULTIPLE')
		self.assertEqual(delivery.monto_pagado_cash, Decimal('20.00'))
		self.assertEqual(delivery.monto_pagado_cheque, Decimal('25.00'))
		self.assertEqual(delivery.monto_pagado, Decimal('45.00'))
		self.assertTrue(bool(delivery.cheque_imagen))
		self.assertEqual(delivery.payments.count(), 2)
		self.assertEqual(invoice.saldo_cliente, Decimal('0.00'))

	def test_driver_can_complete_delivery_with_three_payment_methods(self):
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
				'recibido_por': 'Juan Perez',
				'firma_cliente_data': self.signature_data,
				'payment_method_1': 'CASH',
				'payment_amount_1': '10.00',
				'payment_method_2': 'CHEQUE',
				'payment_amount_2': '20.00',
				'payment_cheque_numero_2': 'CHK-77',
				'payment_cheque_banco_2': 'Bank Test',
				'payment_method_3': 'TARJETA',
				'payment_amount_3': '15.00',
				'payment_tarjeta_ultimos_4_3': '1234',
				'payment_tarjeta_autorizacion_3': 'AUTH-9',
			},
			evidence_files=[],
			payment_files={
				'payment_cheque_image_2': self._build_test_image('cheque-proof-2.png'),
			},
		)

		delivery.refresh_from_db()
		invoice.refresh_from_db()
		self.assertEqual(delivery.metodo_pago, 'MULTIPLE')
		self.assertEqual(delivery.monto_pagado, Decimal('45.00'))
		self.assertEqual(delivery.payments.count(), 3)
		self.assertEqual(list(delivery.payments.values_list('metodo_pago', flat=True)), ['CASH', 'CHEQUE', 'TARJETA'])
		self.assertEqual(invoice.saldo_cliente, Decimal('0.00'))

	def test_driver_rejects_empty_cheque_image_before_storage_upload(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
		)
		delivery = invoice.delivery

		with self.assertRaisesMessage(ValidationError, 'A cheque image is required for cheque payments.'):
			complete_driver_delivery(
				delivery=delivery,
				driver_user=self.driver,
				payload={
					'estado_pago': 'PAGADO',
					'recibido_por': 'Juan Perez',
					'firma_cliente_data': self.signature_data,
					'payment_method_1': 'CHEQUE',
					'payment_amount_1': '45.00',
					'payment_cheque_numero_1': 'CHK-EMPTY',
					'payment_cheque_banco_1': 'Bank Test',
				},
				evidence_files=[],
				payment_files={
					'payment_cheque_image_1': SimpleUploadedFile('empty-cheque.png', b'', content_type='image/png'),
				},
			)

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
			'monto_pagado': '30.00',
			'recibido_por': 'Juan Perez',
			'firma_cliente_data': self.signature_data,
			'driver_note_tipo_documento': 'CREDITO',
			'driver_note_motivo': 'DAMAGE',
			'driver_note_tipo_credito': 'CREDIT_DUMP',
			'driver_note_descripcion': 'Caja dañada al entregar',
			f'driver_note_qty_{invoice.items.first().id}': '1',
			f'driver_note_amount_{invoice.items.first().id}': '15.00',
		})

		invoice.refresh_from_db()
		nota = invoice.notas_ajuste.get()
		self.assertRedirects(response, reverse('driver_delivery_list'))
		self.assertEqual(invoice.delivery.estado, 'ENTREGADA_PAGADA')
		self.assertEqual(nota.estado, 'BORRADOR')
		self.assertEqual(nota.tipo_documento, 'CREDITO')
		self.assertEqual(nota.tipo_credito, 'CREDIT_DUMP')
		self.assertEqual(nota.creada_por, self.driver)
		self.assertEqual(nota.total, Decimal('15.00'))
		self.assertEqual(invoice.delivery.monto_pagado, Decimal('30.00'))
		notificacion = Notificacion.objects.filter(titulo__icontains=nota.numero).latest('creada_en')
		self.assertIn('requires review', notificacion.titulo)
		self.assertIn('approve or reject', notificacion.mensaje)
		self.assertEqual(notificacion.url, f'/facturacion/backoffice/invoices/{invoice.id}/')

	def test_backoffice_invoice_detail_shows_customer_pickup_completion_panel(self):
		invoice = self._create_invoice(metodo_entrega='CUSTOMER_PICK_UP', driver=None, total='20.00')
		self.client.force_login(self.backoffice)

		response = self.client.get(reverse('backoffice_invoice_detail', args=[invoice.id]))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, reverse('backoffice_invoice_complete_pickup', args=[invoice.id]))
		self.assertContains(response, 'Complete customer pick up')
		self.assertContains(response, 'pickupSaveAdjustmentNoteButton')
		self.assertContains(response, 'Save draft note')

	def test_invoice_displays_box_um_when_presentation_name_is_unit_size(self):
		categoria = Categoria.objects.create(nombre='Bebidas')
		marca = Marca.objects.create(nombre='Marca UM')
		producto = Producto.objects.create(
			nombre='PRUEBA 2 PRODUCTO 8/250ML',
			categoria=categoria,
			marca=marca,
			activo=True,
		)
		presentacion = Presentacion.objects.create(
			producto=producto,
			nombre='250 ML',
			unidades=8,
			tipo_contenido='250 ML',
			precio_1=Decimal('20.00'),
		)
		registrar_entrada_manual(presentacion=presentacion, cantidad=10, observacion='Seed misconfigured packaging')
		pedido = Pedido.objects.create(
			cliente=self.cliente,
			origen='CLIENTE',
			estado='VERIFICADO_AJUSTADO',
			total=Decimal('20.00'),
		)
		PedidoItem.objects.create(
			pedido=pedido,
			presentacion=presentacion,
			cantidad_solicitada=1,
			cantidad=1,
			precio=Decimal('20.00'),
			subtotal=Decimal('20.00'),
		)
		invoice = generar_invoice_desde_picking(
			pedido=pedido,
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
		)
		item = invoice.items.get()

		self.assertEqual(item.presentacion_nombre, 'CS')
		self.assertEqual(item.presentacion_nombre_display, 'CS')
		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_invoice_detail', args=[invoice.id]))
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'data-label="U/M">CS</td>', html=False)

	def test_pickup_flow_can_save_adjustment_note_before_completion(self):
		invoice = self._create_invoice(metodo_entrega='CUSTOMER_PICK_UP', driver=None, total='30.00')
		item = invoice.items.first()
		self.client.force_login(self.backoffice)

		response = self.client.post(
			reverse('backoffice_invoice_create_note', args=[invoice.id]),
			{
				'adjustment_field_prefix': 'driver_note_',
				'driver_note_tipo_documento': 'CREDITO',
				'driver_note_tipo_ajuste': 'PRODUCTO',
				'driver_note_motivo': 'DAMAGE',
				'driver_note_descripcion': 'Producto dañado en mostrador',
				f'driver_note_qty_{item.id}': '1',
				f'driver_note_amount_{item.id}': '15.00',
			},
		)

		self.assertRedirects(response, reverse('backoffice_invoice_detail', args=[invoice.id]))
		invoice.refresh_from_db()
		nota = invoice.notas_ajuste.get()
		self.assertEqual(nota.estado, 'BORRADOR')
		self.assertEqual(nota.tipo_documento, 'CREDITO')
		self.assertEqual(nota.total, Decimal('15.00'))
		self.assertFalse(invoice.delivery.is_completed)

	def test_backoffice_can_complete_customer_pickup_with_payment_and_signature(self):
		invoice = self._create_invoice(metodo_entrega='CUSTOMER_PICK_UP', driver=None, total='30.00')
		self.client.force_login(self.backoffice)

		response = self.client.post(
			reverse('backoffice_invoice_complete_pickup', args=[invoice.id]),
			{
				'estado_pago': 'PAGADO',
				'payment_method_1': 'CASH',
				'payment_amount_1': '30.00',
				'monto_pagado': '30.00',
				'recibido_por': 'Cliente Mostrador',
				'firma_cliente_data': self.signature_data,
			},
		)

		self.assertRedirects(response, reverse('backoffice_invoice_detail', args=[invoice.id]))
		invoice.refresh_from_db()
		invoice.pedido.refresh_from_db()
		self.assertTrue(hasattr(invoice, 'delivery'))
		self.assertTrue(invoice.delivery.is_customer_pickup)
		self.assertEqual(invoice.delivery.estado, 'ENTREGADA_PAGADA')
		self.assertEqual(invoice.delivery.estado_pago, 'PAGADO')
		self.assertEqual(invoice.delivery.monto_pagado, Decimal('30.00'))
		self.assertEqual(invoice.delivery.completed_by, self.backoffice)
		self.assertEqual(invoice.pedido.estado, 'DESPACHADO')

	def test_failed_pickup_complete_preserves_form_draft_in_session(self):
		from config.facturacion.form_drafts import INVOICE_PICKUP_DRAFT_SCOPE, get_workflow_draft

		invoice = self._create_invoice(metodo_entrega='CUSTOMER_PICK_UP', driver=None, total='30.00')
		self.client.force_login(self.backoffice)

		response = self.client.post(
			reverse('backoffice_invoice_complete_pickup', args=[invoice.id]),
			{
				'estado_pago': 'NO_PAGADO',
				'recibido_por': 'Cliente Mostrador',
				'notas_driver': 'Cliente pagará después',
				'firma_cliente_data': self.signature_data,
			},
		)

		self.assertRedirects(response, reverse('backoffice_invoice_detail', args=[invoice.id]))
		draft = get_workflow_draft(self.client.session, INVOICE_PICKUP_DRAFT_SCOPE, invoice.id)
		self.assertEqual(draft.get('estado_pago'), 'NO_PAGADO')
		self.assertEqual(draft.get('recibido_por'), 'Cliente Mostrador')
		self.assertEqual(draft.get('notas_driver'), 'Cliente pagará después')

		response = self.client.get(reverse('backoffice_invoice_detail', args=[invoice.id]))
		self.assertContains(response, 'pickup-form-draft-data')
		self.assertContains(response, 'Your previous entries were restored')

	def test_failed_adjustment_note_save_preserves_form_draft_in_session(self):
		from config.facturacion.form_drafts import INVOICE_ADJUSTMENT_DRAFT_SCOPE, get_workflow_draft

		invoice = self._create_invoice(metodo_entrega='CUSTOMER_PICK_UP', driver=None, total='30.00')
		self.client.force_login(self.backoffice)

		response = self.client.post(
			reverse('backoffice_invoice_create_note', args=[invoice.id]),
			{
				'note_descripcion': 'Producto dañado sin evidencia',
			},
		)

		self.assertRedirects(response, reverse('backoffice_invoice_detail', args=[invoice.id]))
		self.assertFalse(invoice.notas_ajuste.exists())
		draft = get_workflow_draft(self.client.session, INVOICE_ADJUSTMENT_DRAFT_SCOPE, invoice.id)
		self.assertEqual(draft.get('note_descripcion'), 'Producto dañado sin evidencia')

		response = self.client.get(reverse('backoffice_invoice_detail', args=[invoice.id]))
		self.assertContains(response, 'adjustment-note-form-draft-data')

	def test_driver_complete_view_can_create_credit_note_with_evidence_without_disabled_credit_type_field(self):
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
				'payment_method_1': 'CASH',
				'payment_amount_1': '30.00',
				'monto_pagado': '30.00',
				'recibido_por': 'Juan Perez',
				'firma_cliente_data': self.signature_data,
				'driver_note_tipo_documento': 'CREDITO',
				'driver_note_motivo': 'DAMAGE',
				'driver_note_descripcion': 'Caja dañada al entregar',
				f'driver_note_qty_{invoice.items.first().id}': '1',
				f'driver_note_amount_{invoice.items.first().id}': '15.00',
				'driver_note_evidence_photos': SimpleUploadedFile('damage.jpg', b'fake-image-bytes', content_type='image/jpeg'),
			},
		)

		invoice.refresh_from_db()
		nota = invoice.notas_ajuste.get()
		self.assertRedirects(response, reverse('driver_delivery_list'))
		self.assertEqual(invoice.delivery.estado, 'ENTREGADA_PAGADA')
		self.assertEqual(nota.tipo_credito, 'CREDIT_DUMP')
		self.assertEqual(nota.evidence_photos.count(), 1)

	def test_driver_complete_view_rejects_over_payment_without_reason(self):
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
				'monto_pagado': '30.01',
				'recibido_por': 'Juan Perez',
				'firma_cliente_data': self.signature_data,
				'driver_note_tipo_documento': 'CREDITO',
				'driver_note_motivo': 'DAMAGE',
				'driver_note_tipo_credito': 'CREDIT_DUMP',
				'driver_note_descripcion': 'Caja dañada al entregar',
				f'driver_note_qty_{invoice.items.first().id}': '1',
				f'driver_note_amount_{invoice.items.first().id}': '15.00',
			},
			follow=True,
		)

		invoice.refresh_from_db()
		self.assertEqual(invoice.delivery.estado, 'ASIGNADA')
		self.assertEqual(invoice.notas_ajuste.count(), 0)
		self.assertContains(response, 'Over Payment Reason is required when the paid amount exceeds the invoice total.')

	def test_driver_complete_view_rejects_short_payment_without_reason(self):
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
				'monto_pagado': '29.00',
				'recibido_por': 'Juan Perez',
				'firma_cliente_data': self.signature_data,
				'driver_note_tipo_documento': 'CREDITO',
				'driver_note_motivo': 'DAMAGE',
				'driver_note_tipo_credito': 'CREDIT_DUMP',
				'driver_note_descripcion': 'Caja dañada al entregar',
				f'driver_note_qty_{invoice.items.first().id}': '1',
				f'driver_note_amount_{invoice.items.first().id}': '15.00',
			},
			follow=True,
		)

		invoice.refresh_from_db()
		self.assertEqual(invoice.delivery.estado, 'ASIGNADA')
		self.assertEqual(invoice.notas_ajuste.count(), 0)
		self.assertContains(response, 'Short Payment Reason is required when the paid amount is less than the invoice total.')

	def test_driver_complete_view_accepts_over_payment_and_credits_customer_balance(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
		)
		cliente = invoice.cliente
		cliente.balance = Decimal('0.00')
		cliente.save(update_fields=['balance'])
		self.client.force_login(self.driver)

		response = self.client.post(
			reverse('driver_delivery_complete', args=[invoice.delivery.id]),
			{
				'estado_pago': 'PAGADO',
				'payment_method_1': 'CASH',
				'payment_amount_1': '50.00',
				'monto_pagado': '50.00',
				'recibido_por': 'Juan Perez',
				'firma_cliente_data': self.signature_data,
				'motivo_over_payment': 'Customer paid with a larger bill and asked to keep credit.',
			},
		)

		invoice.refresh_from_db()
		cliente.refresh_from_db()
		delivery = invoice.delivery
		self.assertRedirects(response, reverse('driver_delivery_list'))
		self.assertEqual(delivery.estado, 'ENTREGADA_PAGADA')
		self.assertEqual(delivery.monto_pagado, Decimal('50.00'))
		self.assertEqual(delivery.over_payment_amount, Decimal('5.00'))
		self.assertEqual(delivery.short_payment_amount, Decimal('0.00'))
		self.assertEqual(delivery.payment_balance_delta, Decimal('-5.00'))
		self.assertIn('larger bill', delivery.motivo_over_payment)
		self.assertEqual(invoice.saldo_cliente, Decimal('0.00'))
		self.assertEqual(cliente.customer_credit_balance, Decimal('5.00'))

	def test_driver_complete_view_accepts_short_payment_and_keeps_pending_balance(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
		)
		cliente = invoice.cliente
		cliente.balance = Decimal('0.00')
		cliente.save(update_fields=['balance'])
		self.client.force_login(self.driver)

		response = self.client.post(
			reverse('driver_delivery_complete', args=[invoice.delivery.id]),
			{
				'estado_pago': 'PAGADO',
				'payment_method_1': 'CASH',
				'payment_amount_1': '30.00',
				'monto_pagado': '30.00',
				'recibido_por': 'Juan Perez',
				'firma_cliente_data': self.signature_data,
				'motivo_short_payment': 'Customer only had thirty dollars available today.',
				'short_payment_evidence': SimpleUploadedFile('short-pay.jpg', b'fake-image-bytes', content_type='image/jpeg'),
			},
		)

		invoice.refresh_from_db()
		cliente.refresh_from_db()
		delivery = invoice.delivery
		self.assertRedirects(response, reverse('driver_delivery_list'))
		self.assertEqual(delivery.estado, 'ENTREGADA_PAGADA')
		self.assertEqual(delivery.monto_pagado, Decimal('30.00'))
		self.assertEqual(delivery.short_payment_amount, Decimal('15.00'))
		self.assertEqual(delivery.over_payment_amount, Decimal('0.00'))
		self.assertEqual(delivery.payment_balance_delta, Decimal('0.00'))
		self.assertIn('thirty dollars', delivery.motivo_short_payment)
		self.assertTrue(bool(delivery.short_payment_evidence))
		self.assertEqual(invoice.saldo_cliente, Decimal('15.00'))
		self.assertEqual(cliente.balance, Decimal('0.00'))
		self.assertEqual(resolve_customer_amount_owed(cliente=cliente, invoice=invoice), Decimal('15.00'))

	def test_driver_complete_view_uses_credit_return_for_factory_defect(self):
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
				'monto_pagado': '30.00',
				'recibido_por': 'Juan Perez',
				'firma_cliente_data': self.signature_data,
				'driver_note_tipo_documento': 'CREDITO',
				'driver_note_tipo_ajuste': 'PRODUCTO',
				'driver_note_motivo': 'DEFECT',
				'driver_note_tipo_credito': 'CREDIT_DUMP',
				'driver_note_descripcion': 'Producto con defecto de fabrica',
				f'driver_note_qty_{invoice.items.first().id}': '1',
				f'driver_note_amount_{invoice.items.first().id}': '15.00',
			},
		)

		invoice.refresh_from_db()
		nota = invoice.notas_ajuste.get()
		self.assertRedirects(response, reverse('driver_delivery_list'))
		self.assertEqual(nota.tipo_credito, 'CREDIT_RETURN')
		self.assertEqual(nota.motivo, 'DEFECT')
		self.assertEqual(nota.inventario_estado, 'PENDIENTE')

	def test_driver_complete_view_uses_credit_dump_for_damage(self):
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
				'monto_pagado': '30.00',
				'recibido_por': 'Juan Perez',
				'firma_cliente_data': self.signature_data,
				'driver_note_tipo_documento': 'CREDITO',
				'driver_note_tipo_ajuste': 'PRODUCTO',
				'driver_note_motivo': 'DAMAGE',
				'driver_note_tipo_credito': 'CREDIT_RETURN',
				'driver_note_descripcion': 'Producto dañado en transporte',
				f'driver_note_qty_{invoice.items.first().id}': '1',
				f'driver_note_amount_{invoice.items.first().id}': '15.00',
			},
		)

		invoice.refresh_from_db()
		nota = invoice.notas_ajuste.get()
		self.assertRedirects(response, reverse('driver_delivery_list'))
		self.assertEqual(nota.tipo_credito, 'CREDIT_DUMP')
		self.assertEqual(nota.motivo, 'DAMAGE')
		self.assertEqual(nota.inventario_estado, 'NO_APLICA')

	def test_driver_complete_view_can_attach_evidence_to_adjustment_note(self):
		invoice = self._create_invoice(metodo_entrega='RUTA_DRIVER', driver=self.driver, total='45.00')
		nota = crear_nota_ajuste_desde_invoice(
			invoice=invoice,
			tipo_documento='CREDITO',
			tipo_ajuste='PRODUCTO',
			motivo='DAMAGE',
			tipo_credito='CREDIT_RETURN',
			descripcion='Caja dañada al entregar',
			usuario=self.driver,
			items_payload=[{
				'invoice_item': invoice.items.first(),
				'cantidad': 1,
				'cantidad_unidades': 0,
				'monto_unitario': '15.00',
			}],
			monto='0.00',
		)
		_save_adjustment_note_evidence_files(nota, [self._build_test_image()])

		self.assertEqual(nota.evidence_photos.count(), 1)
		self.assertIn('invoice-notes/evidence/', nota.evidence_photos.first().image.name)

		self.client.force_login(self.backoffice)
		backoffice_response = self.client.get(reverse('backoffice_invoice_detail', args=[invoice.id]))
		self.assertContains(backoffice_response, nota.evidence_photos.first().image.url)

	def test_driver_complete_view_ignores_empty_adjustment_evidence_files(self):
		invoice = self._create_invoice(metodo_entrega='RUTA_DRIVER', driver=self.driver, total='45.00')
		nota = crear_nota_ajuste_desde_invoice(
			invoice=invoice,
			tipo_documento='CREDITO',
			tipo_ajuste='PRODUCTO',
			motivo='DAMAGE',
			tipo_credito='CREDIT_RETURN',
			descripcion='Caja dañada al entregar',
			usuario=self.driver,
			items_payload=[{
				'invoice_item': invoice.items.first(),
				'cantidad': 1,
				'cantidad_unidades': 0,
				'monto_unitario': '15.00',
			}],
			monto='0.00',
		)
		_save_adjustment_note_evidence_files(
			nota,
			[SimpleUploadedFile('empty-adjustment.png', b'', content_type='image/png')],
		)

		self.assertEqual(nota.evidence_photos.count(), 0)

	def test_normalize_uploaded_file_rejects_empty_stream_without_size(self):
		class UploadedWithoutSize:
			def __init__(self):
				self.name = 'empty-upload.png'
				self.file = BytesIO(b'')

		self.assertIsNone(_normalize_uploaded_file(UploadedWithoutSize()))

	def test_rewind_uploaded_file_resets_consumed_stream(self):
		uploaded = self._build_test_image('cheque-proof.png')
		uploaded.read()

		_rewind_uploaded_file(uploaded)

		self.assertEqual(uploaded.read(1), b'\x89')

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
			'driver_note_tipo_documento': 'CREDITO',
			'driver_note_motivo': 'DAMAGE',
			'driver_note_tipo_credito': 'CREDIT_DUMP',
			'driver_note_descripcion': 'Caja dañada después de la entrega',
			f'driver_note_qty_{invoice.items.first().id}': '1',
			f'driver_note_amount_{invoice.items.first().id}': '5.00',
		})

		invoice.refresh_from_db()
		nota = invoice.notas_ajuste.get()
		self.assertRedirects(response, reverse('driver_delivery_detail', args=[invoice.delivery.id]))
		self.assertEqual(nota.tipo_documento, 'CREDITO')
		self.assertEqual(nota.tipo_credito, 'CREDIT_DUMP')
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

	def test_backoffice_invoice_detail_shows_prominent_adjustment_note_after_invoice_generation(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
		)
		self.client.force_login(self.backoffice)

		response = self.client.get(reverse('backoffice_invoice_detail', args=[invoice.id]))

		self.assertEqual(response.status_code, 200)
		self.assertTrue(response.context['show_prominent_adjustment_note'])
		self.assertContains(response, 'driver-adjustment-callout', html=False)
		self.assertContains(response, 'id="backoffice-adjustment-note"', html=False)
		self.assertContains(response, 'id="toggleBackofficeCreateNote"', html=False)
		self.assertContains(response, 'Create note')
		self.assertContains(response, 'id="backofficeNoteFormBody"', html=False)
		self.assertContains(response, 'driver-adjustment-callout-body d-none', html=False)
		self.assertNotContains(response, 'scrollIntoView')
		self.assertContains(response, 'Start here: note type')
		self.assertContains(response, 'No adjustment note')
		self.assertContains(response, 'Requested qty.')
		self.assertContains(response, 'Dispatched qty.')
		self.assertNotContains(response, 'Choose customer and invoice')

	def test_backoffice_invoice_detail_shows_adjustment_action_and_products(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='CUSTOMER_PICK_UP',
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
				'monto_unitario': Decimal('30.00'),
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

	def test_driver_complete_view_rejects_debit_note_draft(self):
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
				'monto_pagado': '50.00',
				'recibido_por': 'Juan Perez',
				'firma_cliente_data': self.signature_data,
				'driver_note_tipo_documento': 'DEBITO',
				'driver_note_motivo': 'DEFECT',
				'driver_note_descripcion': 'Cargo adicional operativo',
				f'driver_note_qty_{invoice.items.first().id}': '1',
				f'driver_note_amount_{invoice.items.first().id}': '5.00',
			},
			follow=True,
		)

		invoice.refresh_from_db()
		self.assertEqual(invoice.notas_ajuste.count(), 0)
		self.assertEqual(invoice.delivery.estado, 'ASIGNADA')
		self.assertContains(response, 'Drivers can only request credit notes.')

	def test_driver_complete_view_rejects_financial_debit_note_draft(self):
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
				'monto_pagado': '52.50',
				'recibido_por': 'Juan Perez',
				'firma_cliente_data': self.signature_data,
				'driver_note_tipo_documento': 'DEBITO',
				'driver_note_tipo_ajuste': 'FINANCIERO',
				'driver_note_motivo': 'OTHER',
				'driver_note_descripcion': 'Cargo financiero por servicio especial',
				'driver_note_monto': '7.50',
			},
			follow=True,
		)

		invoice.refresh_from_db()
		self.assertFalse(invoice.notas_ajuste.filter(descripcion='Cargo financiero por servicio especial').exists())
		self.assertContains(response, 'Drivers can only request credit notes.')

	def test_driver_complete_view_rejects_financial_credit_note_draft(self):
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
				'recibido_por': 'Juan Perez',
				'firma_cliente_data': self.signature_data,
				'driver_note_tipo_documento': 'CREDITO',
				'driver_note_tipo_ajuste': 'FINANCIERO',
				'driver_note_motivo': 'OTHER',
				'driver_note_tipo_credito': 'CREDIT_DUMP',
				'driver_note_descripcion': 'Credito total aplicado en entrega',
				'driver_note_monto': '45.00',
			},
			follow=True,
		)

		invoice.refresh_from_db()
		self.assertFalse(invoice.notas_ajuste.filter(descripcion='Credito total aplicado en entrega').exists())
		self.assertContains(response, 'Drivers can only request product return credit notes.')

	def test_driver_complete_view_rejects_financial_credit_refund_flow(self):
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
				'monto_pagado': '5.00',
				'recibido_por': 'Juan Perez',
				'firma_cliente_data': self.signature_data,
				'driver_note_tipo_documento': 'CREDITO',
				'driver_note_tipo_ajuste': 'FINANCIERO',
				'driver_note_motivo': 'OTHER',
				'driver_note_tipo_credito': 'CREDIT_DUMP',
				'driver_note_descripcion': 'Credito superior al saldo',
				'driver_note_monto': '50.00',
			},
			follow=True,
		)

		invoice.refresh_from_db()
		self.assertEqual(invoice.delivery.estado, 'ASIGNADA')
		self.assertEqual(invoice.notas_ajuste.count(), 0)
		self.assertContains(response, 'Drivers can only request product return credit notes.')

	def test_driver_complete_view_rejects_payment_above_debit_adjusted_balance(self):
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
				'monto_pagado': '50.01',
				'recibido_por': 'Juan Perez',
				'firma_cliente_data': self.signature_data,
				'driver_note_tipo_documento': 'DEBITO',
				'driver_note_motivo': 'DEFECT',
				'driver_note_descripcion': 'Cargo adicional operativo',
				f'driver_note_qty_{invoice.items.first().id}': '1',
				f'driver_note_amount_{invoice.items.first().id}': '5.00',
			},
			follow=True,
		)

		invoice.refresh_from_db()
		self.assertEqual(invoice.delivery.estado, 'ASIGNADA')
		self.assertEqual(invoice.notas_ajuste.count(), 0)
		self.assertContains(response, 'Drivers can only request credit notes.')

	def test_driver_cannot_create_financial_debit_note_after_delivery(self):
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

		response = self.client.post(
			reverse('driver_delivery_create_note', args=[invoice.delivery.id]),
			{
				'driver_note_tipo_documento': 'DEBITO',
				'driver_note_tipo_ajuste': 'FINANCIERO',
				'driver_note_motivo': 'OTHER',
				'driver_note_descripcion': 'Cargo financiero posterior',
				'driver_note_monto': '6.25',
			},
			follow=True,
		)

		invoice.refresh_from_db()
		self.assertFalse(invoice.notas_ajuste.filter(descripcion='Cargo financiero posterior').exists())
		self.assertContains(response, 'Drivers can only request credit notes.')

	def test_backoffice_invoice_create_note_uses_prefixed_form_fields(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='CUSTOMER_PICK_UP',
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
			metodo_entrega='CUSTOMER_PICK_UP',
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
			metodo_entrega='CUSTOMER_PICK_UP',
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
		self.assertContains(response, 'Other')
		self.assertContains(response, 'Choose customer and invoice', html=False)
		self.assertContains(response, f'data-package-price="{invoice.items.first().precio_unitario}"', html=False)
		self.assertContains(response, 'id="backofficeReasonWrapper"', html=False)
		self.assertContains(response, 'id="backofficeReasonSelect"', html=False)
		self.assertContains(response, 'id="backofficeDescriptionLabel"', html=False)
		self.assertContains(response, 'id="backofficeDescriptionHelp"', html=False)
		self.assertContains(response, 'Amount to charge')
		self.assertContains(response, 'Product to charge')
		self.assertContains(response, 'Charge description')
		self.assertContains(response, 'Use this field to clearly explain what will be charged to the customer.')

	def test_backoffice_adjustment_note_create_view_renders_customer_and_invoice_selectors(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
		)
		self.client.force_login(self.backoffice)

		response = self.client.get(reverse('backoffice_adjustment_note_create'), {
			'cliente_id': self.cliente.id,
			'invoice_id': invoice.id,
		})

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'name="cliente_id"', html=False)
		self.assertContains(response, 'name="invoice_id"', html=False)
		self.assertContains(response, invoice.numero)
		self.assertContains(response, 'Credit note')
		self.assertContains(response, 'Current balance')
		self.assertContains(response, 'Available credit')
		self.assertContains(response, 'Other')
		self.assertContains(response, 'id="advancedReasonWrapper"', html=False)
		self.assertContains(response, 'id="advancedReasonSelect"', html=False)
		self.assertContains(response, 'id="advancedDescriptionLabel"', html=False)
		self.assertContains(response, 'id="advancedDescriptionHelp"', html=False)
		self.assertContains(response, 'id="buscadorClienteNotaAjuste"', html=False)
		self.assertContains(response, 'id="adjustmentNoteCustomerSelect"', html=False)
		self.assertContains(response, 'Amount to charge')
		self.assertContains(response, 'Product to charge')
		self.assertContains(response, 'Charge description')
		self.assertContains(response, 'Use this field to clearly explain what will be charged to the customer.')
		self.assertContains(response, f'data-package-price="{invoice.items.first().precio_unitario}"', html=False)

	def test_backoffice_adjustment_note_create_view_filters_customers_by_full_name_query(self):
		other_user = Usuario.objects.create_user(username='cliente-botanas', password='secret123', role='cliente')
		other_client = Cliente.objects.create(
			usuario=other_user,
			nombre_empresa='DON BOTANAS',
			telefono='5552223333',
			direccion='456 Main St',
			ciudad='Dallas',
			estado='TX',
			codigo_postal='75002',
			pais='USA',
			sales_tax_number='TX-456',
			certificado_tax='certificados/test.pdf',
		)
		self.client.force_login(self.backoffice)

		response = self.client.get(reverse('backoffice_adjustment_note_create'), {'q': 'BOTANAS'})

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, other_client.nombre_empresa)
		self.assertNotContains(response, f'value="{self.cliente.id}"')

	def test_backoffice_adjustment_note_create_view_translates_debit_labels_in_spanish(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
		)
		self.client.force_login(self.backoffice)
		self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = 'es'

		response = self.client.get(reverse('backoffice_adjustment_note_create'), {
			'cliente_id': self.cliente.id,
			'invoice_id': invoice.id,
		}, HTTP_ACCEPT_LANGUAGE='es')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Monto a cobrar')
		self.assertContains(response, 'Producto por cobrar')
		self.assertContains(response, 'Descripcion del cobro')
		self.assertContains(response, 'Usa este campo para explicar claramente que se le cobrara al cliente.')

	def test_backoffice_adjustment_note_create_view_creates_general_customer_credit_without_invoice(self):
		self.client.force_login(self.backoffice)

		response = self.client.post(reverse('backoffice_adjustment_note_create'), {
			'cliente_id': str(self.cliente.id),
			'note_tipo_documento': 'CREDITO',
			'note_tipo_ajuste': 'FINANCIERO',
			'note_motivo': 'DEFECT',
			'note_tipo_credito': 'CREDIT_DUMP',
			'note_descripcion': 'Credito general sin invoice',
			'note_monto': '22.50',
		})

		nota = NotaAjuste.objects.get(invoice__isnull=True, descripcion='Credito general sin invoice')
		self.assertRedirects(response, f"{reverse('backoffice_adjustment_note_create')}?cliente_id={self.cliente.id}")
		self.assertEqual(nota.cliente, self.cliente)
		self.assertEqual(nota.tipo_ajuste, 'FINANCIERO')
		self.assertEqual(nota.total, Decimal('22.50'))

	def test_backoffice_adjustment_note_create_view_creates_manual_product_credit_without_invoice(self):
		self.client.force_login(self.backoffice)

		response = self.client.post(reverse('backoffice_adjustment_note_create'), {
			'cliente_id': str(self.cliente.id),
			'note_tipo_documento': 'CREDITO',
			'note_tipo_ajuste': 'PRODUCTO',
			'note_motivo': 'DAMAGE',
			'note_tipo_credito': 'CREDIT_RETURN',
			'note_descripcion': 'Devolucion manual sin invoice desde pantalla avanzada',
			'note_manual_presentacion': [str(self.presentacion.id), '', ''],
			'note_manual_qty': ['1', '0', '0'],
			'note_manual_unit_qty': ['0', '0', '0'],
			'note_manual_amount': ['15.00', '', ''],
			'note_manual_description': ['Caja manual', '', ''],
		})

		nota = NotaAjuste.objects.get(invoice__isnull=True, descripcion='Devolucion manual sin invoice desde pantalla avanzada')
		self.assertRedirects(response, f"{reverse('backoffice_adjustment_note_create')}?cliente_id={self.cliente.id}")
		self.assertEqual(nota.tipo_ajuste, 'PRODUCTO')
		self.assertEqual(nota.total, Decimal('15.00'))
		self.assertEqual(nota.items.count(), 1)
		self.assertEqual(nota.items.first().presentacion, self.presentacion)
		self.assertEqual(nota.cliente, self.cliente)

	def test_backoffice_adjustment_note_create_view_lists_and_approves_general_customer_notes(self):
		nota = crear_nota_ajuste(
			cliente=self.cliente,
			invoice=None,
			tipo_ajuste='FINANCIERO',
			tipo_documento='CREDITO',
			motivo='DEFECT',
			tipo_credito='CREDIT_DUMP',
			descripcion='Pendiente aprobacion general',
			usuario=self.backoffice,
			items_payload=[],
			monto=Decimal('18.00'),
		)
		self.client.force_login(self.backoffice)

		response = self.client.get(reverse('backoffice_adjustment_note_create'), {
			'cliente_id': self.cliente.id,
		})

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, nota.numero)
		self.assertContains(response, 'Approve')

		approve_response = self.client.post(reverse('backoffice_invoice_approve_note', args=[nota.id]))
		nota.refresh_from_db()
		self.cliente.refresh_from_db()

		self.assertRedirects(approve_response, f"{reverse('backoffice_adjustment_note_create')}?cliente_id={self.cliente.id}")
		self.assertEqual(nota.estado, 'APROBADA')
		self.assertEqual(self.cliente.balance, Decimal('-18.00'))

	def test_draft_general_debit_note_can_be_approved_from_backoffice(self):
		nota = crear_nota_ajuste(
			cliente=self.cliente,
			invoice=None,
			tipo_ajuste='FINANCIERO',
			tipo_documento='DEBITO',
			motivo='OTHER',
			descripcion='PRUEBA NOTA DEBITO',
			usuario=self.backoffice,
			items_payload=[],
			monto=Decimal('10.00'),
		)
		self.assertTrue(nota.can_approve_from_backoffice())
		self.assertTrue(nota.can_void_from_backoffice())
		self.client.force_login(self.backoffice)

		response = self.client.get(reverse('backoffice_adjustment_note_create'), {
			'cliente_id': self.cliente.id,
		})

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, nota.numero)
		self.assertContains(response, 'Approve')

		approve_response = self.client.post(reverse('backoffice_invoice_approve_note', args=[nota.id]))
		nota.refresh_from_db()
		self.cliente.refresh_from_db()

		self.assertRedirects(approve_response, f"{reverse('backoffice_adjustment_note_create')}?cliente_id={self.cliente.id}")
		self.assertEqual(nota.estado, 'APROBADA')
		self.assertEqual(self.cliente.balance, Decimal('10.00'))

	def test_backoffice_adjustment_note_create_view_shows_saved_product_lines_for_general_notes(self):
		nota = crear_nota_ajuste(
			cliente=self.cliente,
			invoice=None,
			tipo_ajuste='PRODUCTO',
			tipo_documento='CREDITO',
			motivo='DAMAGE',
			tipo_credito='CREDIT_RETURN',
			descripcion='Productos vencidos retirados',
			usuario=self.backoffice,
			items_payload=[{
				'invoice_item': None,
				'presentacion': self.presentacion,
				'descripcion': 'Coca Cola caja vencida',
				'cantidad': 1,
				'monto_unitario': Decimal('15.00'),
			}],
			monto=Decimal('0.00'),
		)
		self.client.force_login(self.backoffice)

		response = self.client.get(reverse('backoffice_adjustment_note_create'), {
			'cliente_id': self.cliente.id,
		})

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, nota.numero)
		self.assertContains(response, 'Saved product lines')
		self.assertContains(response, 'Coca Cola caja vencida')
		self.assertContains(response, '$15.00')

	def test_backoffice_adjustment_note_create_view_creates_customer_assigned_note(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
		)
		self.client.force_login(self.backoffice)

		response = self.client.post(reverse('backoffice_adjustment_note_create'), {
			'cliente_id': str(self.cliente.id),
			'invoice_id': str(invoice.id),
			'note_tipo_documento': 'CREDITO',
			'note_motivo': 'DAMAGE',
			'note_tipo_credito': 'CREDIT_RETURN',
			'note_descripcion': 'Creada desde pantalla avanzada',
			f'note_qty_{invoice.items.first().id}': '1',
			f'note_unit_qty_{invoice.items.first().id}': '1',
			f'note_amount_{invoice.items.first().id}': '16.25',
		})

		nota = NotaAjuste.objects.get(invoice=invoice, descripcion='Creada desde pantalla avanzada')
		self.assertRedirects(response, reverse('backoffice_invoice_detail', args=[invoice.id]))
		self.assertEqual(nota.cliente, self.cliente)
		self.assertEqual(nota.estado, 'BORRADOR')
		self.assertEqual(nota.total, Decimal('16.25'))
		self.assertEqual(nota.items.count(), 2)

	def test_backoffice_invoice_detail_uses_clear_credit_note_labels(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
		)
		self.client.force_login(self.backoffice)

		response = self.client.get(reverse('backoffice_invoice_detail', args=[invoice.id]))

		self.assertContains(response, 'CS / pallets')
		self.assertContains(response, 'Calculated amount')
		self.assertContains(response, 'Calculated automatically from the quantities you enter.')
		self.assertContains(response, 'Partial content')

	def test_backoffice_adjustment_note_create_view_uses_line_total_for_manual_partial_return_without_invoice(self):
		self.client.force_login(self.backoffice)

		response = self.client.post(reverse('backoffice_adjustment_note_create'), {
			'cliente_id': str(self.cliente.id),
			'note_tipo_documento': 'CREDITO',
			'note_tipo_ajuste': 'PRODUCTO',
			'note_motivo': 'DAMAGE',
			'note_tipo_credito': 'CREDIT_RETURN',
			'note_descripcion': 'Devolucion parcial manual calculada desde pantalla avanzada',
			'note_manual_presentacion': [str(self.presentacion.id), '', ''],
			'note_manual_qty': ['0', '0', '0'],
			'note_manual_unit_qty': ['2', '0', '0'],
			'note_manual_amount': ['2.50', '', ''],
			'note_manual_description': ['Dos unidades manuales', '', ''],
		})

		nota = NotaAjuste.objects.get(invoice__isnull=True, descripcion='Devolucion parcial manual calculada desde pantalla avanzada')
		self.assertRedirects(response, f"{reverse('backoffice_adjustment_note_create')}?cliente_id={self.cliente.id}")
		self.assertEqual(nota.total, Decimal('2.50'))
		self.assertEqual(nota.items.count(), 1)
		self.assertEqual(nota.items.first().cantidad, 2)
		self.assertEqual(nota.items.first().monto_unitario, Decimal('1.25'))

	def test_advanced_adjustment_note_create_uses_customer_price_for_manual_lines(self):
		self.cliente.nivel_precio = 3
		self.cliente.save(update_fields=['nivel_precio'])
		self.client.force_login(self.backoffice)

		response = self.client.get(f"{reverse('backoffice_adjustment_note_create')}?cliente_id={self.cliente.id}")

		self.assertContains(response, 'data-package-price="17.00"', html=False)

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
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
		)
		self.client.force_login(self.backoffice)

		list_response = self.client.get(reverse('backoffice_invoices_list'))
		detail_response = self.client.get(reverse('backoffice_invoice_detail', args=[invoice.id]))
		pdf_response = self.client.get(reverse('backoffice_invoice_pdf', args=[invoice.id]))
		notes_response = self.client.get(reverse('backoffice_adjustment_notes_list'))

		self.assertEqual(list_response.status_code, 200)
		self.assertEqual(detail_response.status_code, 200)
		self.assertEqual(pdf_response.status_code, 200)
		self.assertEqual(notes_response.status_code, 200)
		self.assertEqual(pdf_response['Content-Type'], 'application/pdf')
		self.assertContains(list_response, reverse('backoffice_adjustment_notes_list'))
		self.assertContains(list_response, 'Credit / Debit Notes')
		self.assertNotContains(list_response, 'Create note')
		self.assertContains(list_response, 'Create direct invoice')
		self.assertContains(list_response, reverse('backoffice_create_direct_invoice'))

	def test_backoffice_create_direct_invoice_view_pickup_and_stock(self):
		self.cliente.aprobado = True
		self.cliente.save(update_fields=['aprobado'])
		starting_stock = StockPresentacion.objects.get(presentacion=self.presentacion).stock_fisico
		self.client.force_login(self.backoffice)

		get_response = self.client.get(reverse('backoffice_create_direct_invoice'))
		self.assertEqual(get_response.status_code, 200)
		self.assertContains(get_response, 'Create direct invoice')

		response = self.client.post(reverse('backoffice_create_direct_invoice'), {
			'cliente_id': str(self.cliente.id),
			'metodo_entrega': 'CUSTOMER_PICK_UP',
			'nota_backoffice': 'Counter sale',
			'line_presentacion_id_0': str(self.presentacion.id),
			'line_label_0': 'Tortilla 12 - Caja',
			'line_cantidad_0': '2',
			'line_precio_0': '18.00',
			'line_descuento_porcentaje_0': '0',
		})
		invoice = Invoice.objects.latest('id')
		self.assertRedirects(
			response,
			f"{reverse('backoffice_invoice_detail', args=[invoice.id])}?focus_adjustment_note=1",
		)
		stock = StockPresentacion.objects.get(presentacion=self.presentacion)
		self.assertEqual(invoice.metodo_entrega, 'CUSTOMER_PICK_UP')
		self.assertEqual(invoice.pedido.canal_toma, 'BACKOFFICE_DIRECT')
		self.assertEqual(invoice.pedido.nota_backoffice, 'Counter sale')
		self.assertEqual(stock.stock_fisico, starting_stock - 2)

	def test_backoffice_create_direct_invoice_view_route_with_driver(self):
		self.cliente.aprobado = True
		self.cliente.save(update_fields=['aprobado'])
		self.client.force_login(self.backoffice)
		response = self.client.post(reverse('backoffice_create_direct_invoice'), {
			'cliente_id': str(self.cliente.id),
			'metodo_entrega': 'RUTA_DRIVER',
			'driver_id': str(self.driver.id),
			'estimated_delivery_at': '2026-07-14T10:00',
			'line_presentacion_id_0': str(self.presentacion.id),
			'line_label_0': 'Tortilla 12 - Caja',
			'line_cantidad_0': '1',
			'line_precio_0': '15.00',
			'line_descuento_porcentaje_0': '0',
		})
		invoice = Invoice.objects.latest('id')
		self.assertEqual(response.status_code, 302)
		self.assertEqual(invoice.metodo_entrega, 'RUTA_DRIVER')
		self.assertEqual(invoice.driver_id, self.driver.id)
		self.assertTrue(hasattr(invoice, 'delivery'))

	def test_backoffice_create_direct_invoice_rejects_without_stock(self):
		self.cliente.aprobado = True
		self.cliente.save(update_fields=['aprobado'])
		StockPresentacion.objects.filter(presentacion=self.presentacion).update(
			stock_fisico=0,
			stock_disponible=0,
			stock_reservado=0,
		)
		self.client.force_login(self.backoffice)
		before = Invoice.objects.count()
		response = self.client.post(reverse('backoffice_create_direct_invoice'), {
			'cliente_id': str(self.cliente.id),
			'metodo_entrega': 'CUSTOMER_PICK_UP',
			'line_presentacion_id_0': str(self.presentacion.id),
			'line_label_0': 'Tortilla 12 - Caja',
			'line_cantidad_0': '2',
			'line_precio_0': '18.00',
			'line_descuento_porcentaje_0': '0',
		})
		self.assertEqual(response.status_code, 200)
		self.assertEqual(Invoice.objects.count(), before)
		messages = [str(item) for item in get_messages(response.wsgi_request)]
		self.assertTrue(messages)

	def test_backoffice_create_direct_invoice_requires_manage_permission(self):
		viewer = Usuario.objects.create_user(
			username='bo-view-only',
			password='secret123',
			role='backoffice',
			permission_overrides={'backoffice.orders.manage': False},
		)
		self.client.force_login(viewer)
		response = self.client.get(reverse('backoffice_create_direct_invoice'))
		self.assertEqual(response.status_code, 302)

	def test_backoffice_adjustment_notes_list_can_filter_by_customer_creator_and_invoice_query(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
		)
		admin_user = Usuario.objects.create_user(username='admin-notes', password='secret123', role='admin')
		other_customer_user = Usuario.objects.create_user(username='cliente-notes-2', password='secret123', role='cliente')
		other_customer = Cliente.objects.create(
			usuario=other_customer_user,
			nombre_empresa='Cliente Secundario',
			telefono='5550003333',
			direccion='456 Elm St',
			ciudad='Dallas',
			estado='TX',
			codigo_postal='75002',
			pais='USA',
			sales_tax_number='TX-999',
			certificado_tax='certificados/second.pdf',
		)
		other_order = Pedido.objects.create(
			cliente=other_customer,
			origen='CLIENTE',
			estado='VERIFICADO_AJUSTADO',
			total=Decimal('25.00'),
		)
		PedidoItem.objects.create(
			pedido=other_order,
			presentacion=self.presentacion,
			cantidad_solicitada=2,
			cantidad=2,
			precio=Decimal('12.50'),
			subtotal=Decimal('25.00'),
		)
		other_invoice = generar_invoice_desde_picking(
			pedido=other_order,
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
		)

		driver_note = crear_nota_ajuste_desde_invoice(
			invoice=invoice,
			tipo_documento='DEBITO',
			tipo_ajuste='PRODUCTO',
			motivo='OTHER',
			tipo_credito='',
			descripcion='Driver recargo de entrega',
			usuario=self.driver,
			items_payload=[{
				'invoice_item': invoice.items.first(),
				'cantidad': 1,
				'monto_unitario': Decimal('5.00'),
			}],
		)
		crear_nota_ajuste_desde_invoice(
			invoice=invoice,
			tipo_documento='DEBITO',
			tipo_ajuste='PRODUCTO',
			motivo='OTHER',
			tipo_credito='',
			descripcion='Backoffice recargo interno',
			usuario=self.backoffice,
			items_payload=[{
				'invoice_item': invoice.items.first(),
				'cantidad': 1,
				'monto_unitario': Decimal('4.00'),
			}],
		)
		admin_note = crear_nota_ajuste_desde_invoice(
			invoice=other_invoice,
			tipo_documento='DEBITO',
			tipo_ajuste='PRODUCTO',
			motivo='OTHER',
			tipo_credito='',
			descripcion='Admin recargo especial',
			usuario=admin_user,
			items_payload=[{
				'invoice_item': other_invoice.items.first(),
				'cantidad': 1,
				'monto_unitario': Decimal('3.00'),
			}],
		)

		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_adjustment_notes_list'), {
			'cliente_id': str(self.cliente.id),
			'creada_por': 'driver',
			'q': invoice.numero,
		})

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, driver_note.numero)
		self.assertNotContains(response, admin_note.numero)
		self.assertNotContains(response, 'Backoffice recargo interno')
		self.assertEqual(response.context['summary']['total'], 1)
		self.assertEqual(response.context['selected_creator_role'], 'driver')

	def test_backoffice_invoice_list_shows_quickbooks_imported_invoices_in_ready(self):
		local_invoice = self._create_invoice(metodo_entrega='CUSTOMER_PICK_UP', total='10.00')
		local_invoice.despachador_notificado = False
		local_invoice.save(update_fields=['despachador_notificado'])

		imported_invoice = self._create_invoice(metodo_entrega='CUSTOMER_PICK_UP', total='20.00')
		imported_invoice.despachador_notificado = False
		imported_invoice.save(update_fields=['despachador_notificado'])
		imported_invoice.pedido.canal_toma = 'QUICKBOOKS_IMPORT'
		imported_invoice.pedido.save(update_fields=['canal_toma'])

		self.client.force_login(self.backoffice)
		pending_response = self.client.get(reverse('backoffice_invoices_list'))
		ready_response = self.client.get(reverse('backoffice_invoices_list'), {'view': 'ready'})

		self.assertEqual(pending_response.status_code, 200)
		self.assertEqual([invoice.id for invoice in pending_response.context['page_obj']], [local_invoice.id])
		self.assertEqual(pending_response.context['pending_count'], 1)
		self.assertEqual(ready_response.context['ready_count'], 1)
		self.assertEqual([invoice.id for invoice in ready_response.context['page_obj']], [imported_invoice.id])

	def test_backoffice_invoice_list_defaults_to_pending_dispatch(self):
		pending_invoice = self._create_invoice(metodo_entrega='CUSTOMER_PICK_UP', total='10.00')
		pending_invoice.despachador_notificado = False
		pending_invoice.save(update_fields=['despachador_notificado'])

		ready_invoice = self._create_invoice(metodo_entrega='CUSTOMER_PICK_UP', total='20.00')
		cancelled_invoice = self._create_invoice(metodo_entrega='CUSTOMER_PICK_UP', total='30.00')
		cancelled_invoice.estado = 'ANULADA'
		cancelled_invoice.save(update_fields=['estado'])

		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_invoices_list'))

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.context['view_mode'], 'pending')
		self.assertEqual([invoice.id for invoice in response.context['page_obj']], [pending_invoice.id])
		self.assertEqual(response.context['pending_count'], 1)
		self.assertEqual(response.context['ready_count'], 1)
		self.assertEqual(response.context['cancelled_count'], 1)

	def test_backoffice_invoice_list_can_filter_ready_delivered_and_cancelled(self):
		pending_invoice = self._create_invoice(metodo_entrega='CUSTOMER_PICK_UP', total='10.00')
		pending_invoice.despachador_notificado = False
		pending_invoice.save(update_fields=['despachador_notificado'])

		ready_invoice = self._create_invoice(metodo_entrega='CUSTOMER_PICK_UP', total='20.00')

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

		cancelled_invoice = self._create_invoice(metodo_entrega='CUSTOMER_PICK_UP', total='40.00')
		cancelled_invoice.estado = 'ANULADA'
		cancelled_invoice.save(update_fields=['estado'])

		self.client.force_login(self.backoffice)

		ready_response = self.client.get(reverse('backoffice_invoices_list'), {'view': 'ready'})
		delivered_response = self.client.get(reverse('backoffice_invoices_list'), {'view': 'delivered'})
		cancelled_response = self.client.get(reverse('backoffice_invoices_list'), {'view': 'cancelled'})

		self.assertEqual([invoice.id for invoice in ready_response.context['page_obj']], [ready_invoice.id])
		self.assertEqual([invoice.id for invoice in delivered_response.context['page_obj']], [delivered_invoice.id])
		self.assertEqual([invoice.id for invoice in cancelled_response.context['page_obj']], [cancelled_invoice.id])

	def test_backoffice_invoice_list_supports_search_and_pagination(self):
		self.client.force_login(self.backoffice)
		for index in range(3):
			invoice = self._create_invoice(metodo_entrega='CUSTOMER_PICK_UP', total=f'{10 + index}.00')
			invoice.despachador_notificado = False
			invoice.save(update_fields=['despachador_notificado'])

		search_response = self.client.get(reverse('backoffice_invoices_list'), {'q': self.cliente.nombre_empresa[:5]})
		self.assertEqual(search_response.status_code, 200)
		self.assertEqual(search_response.context['page_obj'].paginator.count, 3)

		page_response = self.client.get(reverse('backoffice_invoices_list'), {'page': '1'})
		self.assertEqual(page_response.status_code, 200)
		self.assertLessEqual(len(list(page_response.context['page_obj'])), 50)

	def test_backoffice_invoice_list_filters_by_quickbooks_payment_status(self):
		pending_invoice = self._create_invoice(metodo_entrega='CUSTOMER_PICK_UP', total='10.00')
		pending_invoice.despachador_notificado = False
		pending_invoice.quickbooks_id = 'QB-INV-PAID'
		pending_invoice.qb_payment_status = 'PAID'
		pending_invoice.save(update_fields=['despachador_notificado', 'quickbooks_id', 'qb_payment_status'])

		due_invoice = self._create_invoice(metodo_entrega='CUSTOMER_PICK_UP', total='20.00')
		due_invoice.despachador_notificado = False
		due_invoice.quickbooks_id = 'QB-INV-DUE'
		due_invoice.qb_payment_status = 'DUE'
		due_invoice.save(update_fields=['despachador_notificado', 'quickbooks_id', 'qb_payment_status'])

		self.client.force_login(self.backoffice)
		paid_response = self.client.get(reverse('backoffice_invoices_list'), {'qb_status': 'paid'})
		due_response = self.client.get(reverse('backoffice_invoices_list'), {'qb_status': 'due'})

		self.assertEqual([invoice.id for invoice in paid_response.context['page_obj']], [pending_invoice.id])
		self.assertEqual([invoice.id for invoice in due_response.context['page_obj']], [due_invoice.id])
		self.assertEqual(paid_response.context['qb_status_counts']['paid'], 1)
		self.assertEqual(due_response.context['qb_status_counts']['due'], 1)

	def test_backoffice_invoice_list_renders_in_spanish_when_selected(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='CUSTOMER_PICK_UP',
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
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
		)

		items = _build_invoice_pdf_item_data(invoice)

		self.assertEqual(len(items), 1)
		self.assertEqual(items[0]['barcode'], '7501234567890')
		self.assertEqual(items[0]['pack_size'], 'CS')
		self.assertEqual(items[0]['requested_quantity'], '4')
		self.assertEqual(items[0]['dispatched_quantity'], '3')
		self.assertEqual(items[0]['customer_price'], '$15.00')
		self.assertEqual(items[0]['suggested_unit_price'], '$1.79')
		self.assertEqual(items[0]['profit_percentage'], '30.00%')

	def test_invoice_pdf_item_data_uses_sold_price_when_list_price_missing(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
		)
		item = invoice.items.get()
		item.precio_unitario_lista = None
		item.precio_unitario = Decimal('14.99')
		item.save(update_fields=['precio_unitario_lista', 'precio_unitario'])

		rows = _build_invoice_pdf_item_data(invoice)

		self.assertEqual(rows[0]['list_price'], '$14.99')
		self.assertEqual(rows[0]['customer_price'], '$14.99')

	def test_invoice_pdf_item_data_is_sorted_alphabetically(self):
		categoria = Categoria.objects.get(nombre='Tortillas')
		marca = Marca.objects.get(nombre='Marca Test')
		producto_z = Producto.objects.create(nombre='Zulu Soda', categoria=categoria, marca=marca, codigo_barras='7500000000001')
		producto_a = Producto.objects.create(nombre='Alpha Soda', categoria=categoria, marca=marca, codigo_barras='7500000000002')
		presentacion_z = Presentacion.objects.create(
			producto=producto_z,
			nombre='Caja',
			unidades=1,
			tipo_contenido='caja',
			precio_1=Decimal('10.00'),
		)
		presentacion_a = Presentacion.objects.create(
			producto=producto_a,
			nombre='Caja',
			unidades=1,
			tipo_contenido='caja',
			precio_1=Decimal('8.00'),
		)
		registrar_entrada_manual(presentacion=presentacion_z, cantidad=5, observacion='Z stock')
		registrar_entrada_manual(presentacion=presentacion_a, cantidad=5, observacion='A stock')
		PedidoItem.objects.create(
			pedido=self.pedido,
			presentacion=presentacion_z,
			cantidad_solicitada=1,
			cantidad=1,
			precio=Decimal('10.00'),
			subtotal=Decimal('10.00'),
		)
		PedidoItem.objects.create(
			pedido=self.pedido,
			presentacion=presentacion_a,
			cantidad_solicitada=1,
			cantidad=1,
			precio=Decimal('8.00'),
			subtotal=Decimal('8.00'),
		)

		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
		)

		rows = _build_invoice_pdf_item_data(invoice)

		self.assertEqual(
			[row['product_name'] for row in rows],
			['Alpha Soda', 'Tortilla 12', 'Zulu Soda'],
		)

	def test_invoice_pdf_item_rows_chunk_helper_keeps_legacy_groups_of_twenty(self):
		rows = [{'index': number} for number in range(23)]

		chunks = _chunk_invoice_pdf_item_rows(rows)

		self.assertEqual([len(chunk) for chunk in chunks], [20, 3])
		self.assertEqual(chunks[0][0]['index'], 0)
		self.assertEqual(chunks[-1][-1]['index'], 22)

	def test_invoice_pdf_uses_continuous_table_without_forced_page_breaks(self):
		from reportlab.platypus import PageBreak, Table

		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
		)
		extra_items = [
			InvoiceItem(
				invoice=invoice,
				presentacion=self.presentacion,
				producto_nombre=f'Extra Product {index:02d}',
				presentacion_nombre='Case',
				cantidad_facturada=1,
				precio_unitario=Decimal('1.00'),
				subtotal=Decimal('1.00'),
			)
			for index in range(25)
		]
		InvoiceItem.objects.bulk_create(extra_items)

		captured = {}
		original_build = SimpleDocTemplate.build

		def capture_build(doc, flowables, **kwargs):
			captured['flowables'] = list(flowables)
			return original_build(doc, flowables, **kwargs)

		self.client.force_login(self.backoffice)
		with patch.object(SimpleDocTemplate, 'build', capture_build):
			response = self.client.get(reverse('backoffice_invoice_pdf', args=[invoice.id]))

		self.assertEqual(response.status_code, 200)
		flowables = captured['flowables']
		self.assertFalse(any(isinstance(flowable, PageBreak) for flowable in flowables))
		item_tables = [
			flowable
			for flowable in flowables
			if isinstance(flowable, Table) and len(getattr(flowable, '_cellvalues', []) or []) >= 20
		]
		self.assertEqual(len(item_tables), 1)

	def test_invoice_pdf_item_table_column_widths_use_full_content_width(self):
		content_width = 564
		column_widths = _invoice_pdf_item_table_column_widths(content_width)
		self.assertEqual(len(column_widths), 9)
		self.assertAlmostEqual(sum(column_widths), content_width, places=2)
		self.assertGreater(column_widths[1], column_widths[3])
		self.assertGreaterEqual(column_widths[-1], column_widths[6])

	def test_invoice_pdf_barcode_uses_small_human_readable_font(self):
		barcode = _build_invoice_pdf_barcode('123456789012')

		self.assertEqual(barcode.__class__.__name__, 'Code128')
		self.assertEqual(barcode.fontName, 'Helvetica')
		self.assertEqual(barcode.fontSize, 6)
		self.assertEqual(barcode.barHeight, 22)

	def test_invoice_pdf_barcode_cell_uses_same_height_with_or_without_barcode(self):
		styles = getSampleStyleSheet()
		with_barcode = _build_invoice_pdf_barcode_cell('123456789012', max_width=66, placeholder_style=styles['BodyText'])
		without_barcode = _build_invoice_pdf_barcode_cell('', max_width=66, placeholder_style=styles['BodyText'])

		self.assertEqual(with_barcode._rowHeights, without_barcode._rowHeights)
		self.assertEqual(with_barcode._colWidths, without_barcode._colWidths)

	def test_invoice_pdf_terms_shows_client_due_balance_only(self):
		self.cliente.balance = Decimal('20408.57')
		self.cliente.save(update_fields=['balance'])
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
		)
		styles = getSampleStyleSheet()
		terms_paragraph = _build_invoice_pdf_terms_paragraph(invoice, styles['BodyText'])

		self.assertIn('DUE BALANCE', terms_paragraph.text)
		self.assertIn('$20,408.57', terms_paragraph.text)
		self.assertNotIn('Outstanding invoice balance', terms_paragraph.text)
		self.assertNotIn('Final invoice total', terms_paragraph.text)
		self.assertNotIn('Customer credit applied', terms_paragraph.text)
		self.assertNotIn('Delivery method', terms_paragraph.text)

	def test_invoice_pdf_terms_shows_configured_payment_term(self):
		self.cliente.terminos_pago = Cliente.PAYMENT_TERMS_NET7
		self.cliente.balance = Decimal('20408.57')
		self.cliente.save(update_fields=['terminos_pago', 'balance'])
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
		)
		styles = getSampleStyleSheet()
		terms_paragraph = _build_invoice_pdf_terms_paragraph(invoice, styles['BodyText'])

		self.assertIn('NET7', terms_paragraph.text)
		self.assertIn('DUE BALANCE', terms_paragraph.text)
		self.assertIn('$20,408.57', terms_paragraph.text)

	def test_invoice_pdf_due_date_uses_payment_terms_from_invoice_date_when_no_estimated_delivery(self):
		self.cliente.terminos_pago = Cliente.PAYMENT_TERMS_NET7
		self.cliente.save(update_fields=['terminos_pago'])
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
		)
		invoice_date = resolve_invoice_payment_base_date(invoice)
		expected_due_date = self.cliente.get_payment_due_date(invoice_date)

		self.assertEqual(_resolve_invoice_pdf_due_date_label(invoice), expected_due_date.strftime('%m/%d/%Y'))

	def test_invoice_pdf_due_date_uses_estimated_delivery_for_driver_route(self):
		self.cliente.terminos_pago = Cliente.PAYMENT_TERMS_NET7
		self.cliente.save(update_fields=['terminos_pago'])
		estimated_delivery_at = timezone.make_aware(datetime(2026, 6, 25, 17, 16), timezone.get_current_timezone())
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
			estimated_delivery_at=estimated_delivery_at,
		)

		self.assertEqual(_resolve_invoice_pdf_due_date_label(invoice), '07/02/2026')
		self.assertEqual(resolve_invoice_payment_due_date(invoice).strftime('%m/%d/%Y'), '07/02/2026')

	def test_invoice_pdf_due_date_shows_dash_when_terms_not_configured(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
		)

		self.assertEqual(_resolve_invoice_pdf_due_date_label(invoice), '-')

	def test_invoice_pdf_due_date_for_prepay_matches_base_date(self):
		self.cliente.terminos_pago = Cliente.PAYMENT_TERMS_PREPAY
		self.cliente.save(update_fields=['terminos_pago'])
		estimated_delivery_at = timezone.make_aware(datetime(2026, 6, 25, 17, 16), timezone.get_current_timezone())
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
			estimated_delivery_at=estimated_delivery_at,
		)

		self.assertEqual(
			_resolve_invoice_pdf_due_date_label(invoice),
			resolve_invoice_payment_base_date(invoice).strftime('%m/%d/%Y'),
		)

	def test_invoice_pdf_totals_rows_include_customer_credit_applied(self):
		self.cliente.balance = Decimal('-30.00')
		self.cliente.save(update_fields=['balance'])

		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
			applied_customer_credit=Decimal('24.98'),
		)

		styles = getSampleStyleSheet()
		meta_label_style = ParagraphStyle('InvoiceMetaLabelTest', parent=styles['BodyText'])
		meta_value_style = ParagraphStyle('InvoiceMetaValueTest', parent=styles['BodyText'])
		section_title_style = ParagraphStyle('InvoiceSectionTitleTest', parent=styles['BodyText'])

		rows = _build_invoice_pdf_totals_rows(
			invoice,
			meta_label_style=meta_label_style,
			meta_value_style=meta_value_style,
			section_title_style=section_title_style,
			body_style=styles['BodyText'],
		)

		self.assertEqual(rows[1][0].text, 'Customer credit applied')
		self.assertEqual(rows[1][1].text, '$24.98')
		self.assertEqual(rows[-1][0].text, 'Total invoice')
		self.assertEqual(rows[-1][1].text, '<b>$20.02</b>')

	def test_invoice_pdf_totals_rows_use_total_invoice_label(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
		)
		invoice.credito_cliente_aplicado = Decimal('19.34')
		invoice.total_creditos = Decimal('0.50')
		invoice.total_debitos = Decimal('11.36')
		invoice.total_neto = Decimal('25.60')
		invoice.saldo_cliente = Decimal('0.00')

		styles = getSampleStyleSheet()
		meta_label_style = ParagraphStyle('InvoiceMetaLabelBalanceTest', parent=styles['BodyText'])
		meta_value_style = ParagraphStyle('InvoiceMetaValueBalanceTest', parent=styles['BodyText'])
		section_title_style = ParagraphStyle('InvoiceSectionTitleBalanceTest', parent=styles['BodyText'])

		rows = _build_invoice_pdf_totals_rows(
			invoice,
			meta_label_style=meta_label_style,
			meta_value_style=meta_value_style,
			section_title_style=section_title_style,
			body_style=styles['BodyText'],
		)

		self.assertEqual(rows[-1][0].text, 'Total invoice')
		self.assertEqual(rows[-1][1].text, '<b>$25.60</b>')
		self.assertEqual(rows[-2][0].text, 'Debit notes')
		self.assertEqual(rows[-2][1].text, '$11.36')

	def test_backoffice_generate_invoice_view_saves_custom_suggested_unit_price(self):
		self.client.force_login(self.backoffice)

		response = self.client.post(reverse('backoffice_generate_invoice', args=[self.pedido.id]), {
			'metodo_entrega': 'CUSTOMER_PICK_UP',
			'driver_id': '',
			f'suggested_unit_price_{self.pedido_item.id}': '2.75',
		})

		invoice = Invoice.objects.get(pedido=self.pedido)
		self.assertRedirects(
			response,
			f"{reverse('backoffice_invoice_detail', args=[invoice.id])}?focus_adjustment_note=1",
		)
		self.assertEqual(invoice.items.first().precio_venta_sugerido_unitario, Decimal('2.75'))

	def test_generar_invoice_desde_picking_applies_line_discount(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
			line_discounts={self.pedido_item.id: Decimal('10.00')},
		)
		item = invoice.items.get()
		self.assertEqual(item.precio_unitario_lista, Decimal('15.00'))
		self.assertEqual(item.descuento_porcentaje, Decimal('10.00'))
		self.assertEqual(item.precio_unitario, Decimal('13.50'))
		self.assertEqual(item.subtotal, Decimal('40.50'))
		self.assertEqual(invoice.subtotal, Decimal('40.50'))

		rows = _build_invoice_pdf_item_data(invoice)
		self.assertEqual(rows[0]['list_price'], '$15.00')
		self.assertEqual(rows[0]['discount_percentage'], '10.00%')
		self.assertEqual(rows[0]['customer_price'], '$13.50')
		self.assertEqual(rows[0]['line_discount_amount'], '$4.50')

		styles = getSampleStyleSheet()
		total_rows = _build_invoice_pdf_totals_rows(
			invoice,
			meta_label_style=ParagraphStyle('InvoiceMetaLabelDiscountTest', parent=styles['BodyText']),
			meta_value_style=ParagraphStyle('InvoiceMetaValueDiscountTest', parent=styles['BodyText']),
			section_title_style=ParagraphStyle('InvoiceSectionTitleDiscountTest', parent=styles['BodyText']),
			body_style=styles['BodyText'],
		)
		self.assertEqual(total_rows[1][0].text, 'Discounts applied')
		self.assertEqual(total_rows[1][1].text, '-$4.50')

	def test_backoffice_generate_invoice_view_applies_line_discount(self):
		self.client.force_login(self.backoffice)

		response = self.client.post(reverse('backoffice_generate_invoice', args=[self.pedido.id]), {
			'metodo_entrega': 'CUSTOMER_PICK_UP',
			'driver_id': '',
			f'line_discount_percentage_{self.pedido_item.id}': '10',
		})

		invoice = Invoice.objects.get(pedido=self.pedido)
		self.assertRedirects(
			response,
			f"{reverse('backoffice_invoice_detail', args=[invoice.id])}?focus_adjustment_note=1",
		)
		item = invoice.items.get()
		self.assertEqual(item.precio_unitario, Decimal('13.50'))
		self.assertEqual(invoice.subtotal, Decimal('40.50'))

	def test_generar_invoice_directa_backoffice_applies_line_discount(self):
		invoice = generar_invoice_directa_backoffice(
			cliente=self.cliente,
			items_payload=[{
				'presentacion': self.presentacion,
				'cantidad': 2,
				'precio': Decimal('20.00'),
				'descuento_porcentaje': Decimal('25.00'),
			}],
			metodo_entrega='CUSTOMER_PICK_UP',
			usuario=self.backoffice,
		)
		item = invoice.items.get()
		self.assertEqual(item.precio_unitario_lista, Decimal('20.00'))
		self.assertEqual(item.descuento_porcentaje, Decimal('25.00'))
		self.assertEqual(item.precio_unitario, Decimal('15.00'))
		self.assertEqual(item.subtotal, Decimal('30.00'))

	def test_backoffice_generate_invoice_view_saves_estimated_delivery(self):
		self.client.force_login(self.backoffice)

		response = self.client.post(reverse('backoffice_generate_invoice', args=[self.pedido.id]), {
			'metodo_entrega': 'RUTA_DRIVER',
			'driver_id': str(self.driver.id),
			'estimated_delivery_at': '2026-04-18T09:30',
			f'suggested_unit_price_{self.pedido_item.id}': '2.75',
		})

		invoice = Invoice.objects.get(pedido=self.pedido)
		self.assertRedirects(
			response,
			f"{reverse('backoffice_invoice_detail', args=[invoice.id])}?focus_adjustment_note=1",
		)
		self.assertEqual(timezone.localtime(invoice.delivery.estimated_delivery_at).strftime('%Y-%m-%d %H:%M'), '2026-04-18 09:30')

	def test_backoffice_generate_invoice_view_applies_selected_general_note_amounts(self):
		nota_credito = crear_nota_ajuste(
			cliente=self.cliente,
			invoice=None,
			tipo_ajuste='FINANCIERO',
			tipo_documento='CREDITO',
			motivo='DEFECT',
			tipo_credito='CREDIT_DUMP',
			descripcion='Credito general editable',
			usuario=self.backoffice,
			items_payload=[],
			monto=Decimal('40.00'),
		)
		nota_debito = crear_nota_ajuste(
			cliente=self.cliente,
			invoice=None,
			tipo_ajuste='FINANCIERO',
			tipo_documento='DEBITO',
			motivo='DEFECT',
			tipo_credito='',
			descripcion='Debito general editable',
			usuario=self.backoffice,
			items_payload=[],
			monto=Decimal('100.00'),
		)

		aprobar_nota_ajuste(nota=nota_credito, usuario=self.backoffice)
		aprobar_nota_ajuste(nota=nota_debito, usuario=self.backoffice)
		self.client.force_login(self.backoffice)

		response = self.client.post(reverse('backoffice_generate_invoice', args=[self.pedido.id]), {
			'metodo_entrega': 'CUSTOMER_PICK_UP',
			'driver_id': '',
			f'general_note_apply_{nota_credito.id}': '10.00',
			f'general_note_apply_{nota_debito.id}': '30.00',
		})

		invoice = Invoice.objects.get(pedido=self.pedido)
		nota_credito.refresh_from_db()
		nota_debito.refresh_from_db()
		self.cliente.refresh_from_db()

		self.assertRedirects(
			response,
			f"{reverse('backoffice_invoice_detail', args=[invoice.id])}?focus_adjustment_note=1",
		)
		self.assertEqual(invoice.total_creditos, Decimal('10.00'))
		self.assertEqual(invoice.total_debitos, Decimal('30.00'))
		self.assertEqual(invoice.saldo_cliente, Decimal('65.00'))
		self.assertEqual(nota_credito.monto_aplicado_cliente, Decimal('30.00'))
		self.assertEqual(nota_debito.monto_aplicado_cliente, Decimal('70.00'))
		self.assertEqual(self.cliente.balance, Decimal('40.00'))

	def test_invoice_pdf_suggested_retail_uses_default_profit_suggestion(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='CUSTOMER_PICK_UP',
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
		self.assertContains(response, 'Total paid')
		self.assertContains(response, '$45.00')
		self.assertContains(response, 'Customer signature proof')
		self.assertContains(response, 'Carlos Cliente')
		self.assertContains(response, delivery.firma_cliente.url)

	def test_invoice_pdf_includes_customer_signature_section_when_signed(self):
		unsigned_invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='CUSTOMER_PICK_UP',
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

	def test_build_invoice_shipment_summary_sums_cases_and_weight(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
		)
		summary = build_invoice_shipment_summary(invoice)
		self.assertEqual(summary['total_cases'], 3)
		self.assertEqual(summary['total_weight'], Decimal('101.5'))

	def test_invoice_generation_snapshots_case_weight_on_items(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
		)
		item = invoice.items.get()
		self.assertEqual(item.peso_por_caja, Decimal('33.827'))

	def test_invoice_pdf_includes_cases_and_weight_summary(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
		)
		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_invoice_pdf', args=[invoice.id]))
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response['Content-Type'], 'application/pdf')
		self.assertGreater(len(response.content), 500)
		self.assertIn(b'%PDF', response.content[:8])
		from config.core.datetime_formats import format_local_date
		from config.facturacion.services import resolve_invoice_sale_reference_date
		expected_date = format_local_date(resolve_invoice_sale_reference_date(invoice))
		self.assertTrue(expected_date)
		# Shipment labels are covered by the dedicated summary-table unit test;
		# this asserts the full invoice PDF still builds with a document date.
		summary_table = _build_invoice_pdf_shipment_summary_table(
			{'total_cases': 3, 'total_weight': Decimal('101.5'), 'total_pallets': Decimal('1.00')},
			box_style=ParagraphStyle('InvoiceSummaryBoxDateTest', parent=getSampleStyleSheet()['BodyText'], fontName='Helvetica-Bold', fontSize=8),
			value_style=ParagraphStyle('InvoiceSummaryValueDateTest', parent=getSampleStyleSheet()['BodyText'], fontSize=8),
			total_width=244,
		)
		self.assertIsNotNone(summary_table)

	def test_invoice_pdf_shipment_summary_table_builds_without_reportlab_error(self):
		styles = getSampleStyleSheet()
		box_style = ParagraphStyle('InvoiceSummaryBoxTest', parent=styles['BodyText'], fontName='Helvetica-Bold', fontSize=8)
		value_style = ParagraphStyle('InvoiceSummaryValueTest', parent=styles['BodyText'], fontSize=8)
		summary_table = _build_invoice_pdf_shipment_summary_table(
			{'total_cases': 180, 'total_weight': Decimal('6088.2')},
			box_style=box_style,
			value_style=value_style,
			total_width=244,
		)
		buffer = BytesIO()
		document = SimpleDocTemplate(buffer, pagesize=letter)
		document.build([summary_table])
		self.assertGreater(len(buffer.getvalue()), 100)

	def test_invoice_pdf_footer_layout_builds_without_reportlab_error(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
		)
		styles = getSampleStyleSheet()
		page_width, _page_height = letter
		content_width = page_width - 48
		item_column_widths = _invoice_pdf_item_table_column_widths(content_width)
		left_width = item_column_widths[0] + item_column_widths[1]
		meta_label_style = ParagraphStyle('MetaLabel', parent=styles['BodyText'], fontName='Helvetica-Bold', fontSize=7)
		meta_value_style = ParagraphStyle('MetaValue', parent=styles['BodyText'], fontSize=8)
		section_title_style = ParagraphStyle('SectionTitle', parent=styles['BodyText'], fontName='Helvetica-Bold', fontSize=8)
		body_style = ParagraphStyle('Body', parent=styles['BodyText'], fontSize=7.5)
		note_style = ParagraphStyle('Note', parent=styles['BodyText'], fontSize=6.5)
		footer = _build_invoice_pdf_footer_layout(
			invoice,
			content_width=content_width,
			left_width=left_width,
			meta_label_style=meta_label_style,
			meta_value_style=meta_value_style,
			section_title_style=section_title_style,
			body_style=body_style,
			note_style=note_style,
		)
		buffer = BytesIO()
		document = SimpleDocTemplate(buffer, pagesize=letter, leftMargin=24, rightMargin=24)
		document.build([footer])
		self.assertGreater(len(buffer.getvalue()), 500)

	def test_backoffice_invoice_detail_shows_cases_and_weight_summary(self):
		self.pedido.cantidad_pallets = Decimal('3.50')
		self.pedido.save(update_fields=['cantidad_pallets', 'actualizada_en'])
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
		)
		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_invoice_detail', args=[invoice.id]))
		self.assertContains(response, 'No. of Cases:')
		self.assertContains(response, 'Total WGT:')
		self.assertContains(response, 'Pallets:')
		self.assertContains(response, '3.50')

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
		self.assertContains(detail_response, 'QTY ORD')
		self.assertContains(detail_response, 'QTY DSP')
		self.assertContains(detail_response, f'<td>{self.pedido_item.cantidad_solicitada}</td>', html=False)
		self.assertContains(detail_response, f'<td>{invoice.items.first().cantidad_facturada}</td>', html=False)

	def test_admin_and_backoffice_see_all_assigned_driver_deliveries(self):
		other_driver = Usuario.objects.create_user(username='driver-other-fact', password='secret123', role='driver')
		admin_user = Usuario.objects.create_user(username='admin-driver-oversee', password='secret123', role='admin')
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
		)
		other_invoice = self._create_invoice(metodo_entrega='RUTA_DRIVER', driver=other_driver)

		self.client.force_login(admin_user)
		admin_list = self.client.get(reverse('driver_delivery_list'))
		admin_detail = self.client.get(reverse('driver_delivery_detail', args=[invoice.delivery.id]))
		self.assertEqual(admin_list.status_code, 200)
		self.assertEqual(admin_detail.status_code, 200)
		self.assertContains(admin_list, invoice.numero)
		self.assertContains(admin_list, other_invoice.numero)
		self.assertContains(admin_list, self.driver.username)
		self.assertContains(admin_list, other_driver.username)
		self.assertEqual(admin_list.context['active_count'], 2)
		self.assertTrue(admin_list.context['can_oversee_driver_deliveries'])

		self.client.force_login(self.backoffice)
		bo_list = self.client.get(reverse('driver_delivery_list'))
		bo_detail = self.client.get(reverse('driver_delivery_detail', args=[other_invoice.delivery.id]))
		self.assertEqual(bo_list.status_code, 200)
		self.assertEqual(bo_detail.status_code, 200)
		self.assertContains(bo_list, invoice.numero)
		self.assertContains(bo_list, other_invoice.numero)
		self.assertEqual(bo_list.context['active_count'], 2)

		self.client.force_login(self.driver)
		driver_list = self.client.get(reverse('driver_delivery_list'))
		self.assertEqual(driver_list.status_code, 200)
		self.assertContains(driver_list, invoice.numero)
		self.assertNotContains(driver_list, other_invoice.numero)
		self.assertEqual(driver_list.context['active_count'], 1)
		self.assertFalse(driver_list.context['can_oversee_driver_deliveries'])

		self.client.force_login(other_driver)
		other_list = self.client.get(reverse('driver_delivery_list'))
		self.assertContains(other_list, other_invoice.numero)
		self.assertNotContains(other_list, invoice.numero)
		forbidden_detail = self.client.get(reverse('driver_delivery_detail', args=[invoice.delivery.id]))
		self.assertEqual(forbidden_detail.status_code, 404)

	def test_backoffice_can_complete_delivery_assigned_to_driver(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
		)
		delivery = invoice.delivery
		self.client.force_login(self.backoffice)
		start_response = self.client.post(reverse('driver_delivery_start_route', args=[delivery.id]))
		self.assertRedirects(start_response, reverse('driver_delivery_tracking', args=[delivery.id]))
		delivery.refresh_from_db()
		self.assertEqual(delivery.estado, 'EN_RUTA')
		self.assertEqual(delivery.driver_id, self.driver.id)

		complete_response = self.client.post(
			reverse('driver_delivery_complete', args=[delivery.id]),
			{
				'estado_pago': 'PAGADO',
				'payment_method_1': 'CASH',
				'payment_amount_1': str(invoice.saldo_cliente),
				'recibido_por': 'BackOffice User',
				'firma_cliente_data': self.signature_data,
			},
		)
		self.assertRedirects(complete_response, reverse('driver_delivery_list'))
		delivery.refresh_from_db()
		self.assertEqual(delivery.estado, 'ENTREGADA_PAGADA')
		self.assertEqual(delivery.driver_id, self.driver.id)

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
		self.assertContains(response, 'QTY ORD')
		self.assertContains(response, 'QTY DSP')
		self.assertContains(response, 'Consulta de Google Maps')
		self.assertContains(response, 'Evidence invoice picture')
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
		self.assertContains(response, 'Monto a cobrar')
		self.assertContains(response, 'Producto por cobrar')
		self.assertContains(response, 'Descripcion del cobro')

	def test_driver_delivery_detail_shows_credit_only_note_ui(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
		)
		self.client.force_login(self.driver)

		response = self.client.get(reverse('driver_delivery_detail', args=[invoice.delivery.id]))

		self.assertEqual(response.status_code, 200)
		self.assertNotContains(response, 'Debit note')
		self.assertNotContains(response, 'Financial amount')
		self.assertContains(response, 'id="driverReasonWrapper"', html=False)
		self.assertContains(response, 'id="driverReasonSelect"', html=False)
		self.assertContains(response, 'id="driverDescriptionLabel"', html=False)
		self.assertContains(response, 'id="driverDescriptionHelp"', html=False)
		self.assertContains(response, 'Credit note')
		self.assertContains(response, 'Product return / item lines')
		self.assertContains(response, 'Driver credit notes use returned invoice lines and are always recorded as Credit Dump, so damaged products do not return to inventory.', html=False)
		self.assertNotContains(response, 'Credit type')
		self.assertContains(response, 'Use this field for the manual comment, especially when the reason is Other.')
		content = response.content.decode('utf-8')
		self.assertLess(content.index('data-driver-section="adjustment-note"'), content.index('data-driver-section="payment-details"'))

	def test_driver_delivery_detail_defaults_adjustment_mode_to_product(self):
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
		)
		self.client.force_login(self.driver)

		response = self.client.get(reverse('driver_delivery_detail', args=[invoice.delivery.id]))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'name="driver_note_tipo_ajuste" id="driverAdjustmentType" value="PRODUCTO"', html=False)
		self.assertContains(response, 'id="driverCreditTypeHint"', html=False)
		self.assertContains(response, 'Product return / item lines')

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
		self.assertRedirects(
			response,
			reverse('driver_delivery_detail', args=[invoice.delivery.id]) + '#driver-evidence',
			fetch_redirect_response=False,
		)
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

		self.assertRedirects(
			response,
			reverse('driver_delivery_detail', args=[invoice.delivery.id]) + '#driver-evidence',
			fetch_redirect_response=False,
		)
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
		self.assertEqual(payload['tracking']['location_updated_label'], timezone.localtime(invoice.delivery.location_updated_at).strftime('%m/%d/%Y %H:%M:%S'))
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


class InvoiceVoidDeleteTests(TestCase):
	def setUp(self):
		self.backoffice = Usuario.objects.create_user(username='void-backoffice', password='secret123', role='backoffice')
		self.cliente_user = Usuario.objects.create_user(username='void-cliente', password='secret123', role='cliente')
		self.cliente = Cliente.objects.create(
			usuario=self.cliente_user,
			nombre_empresa='Cliente Void',
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
		marca = Marca.objects.create(nombre='Marca Void')
		producto = Producto.objects.create(nombre='Tortilla Void', categoria=categoria, marca=marca)
		self.presentacion = Presentacion.objects.create(
			producto=producto,
			nombre='Caja',
			unidades=12,
			tipo_contenido='unidades',
			precio_3=Decimal('17.00'),
		)
		registrar_entrada_manual(presentacion=self.presentacion, cantidad=50, observacion='Seed stock')
		self.pedido = Pedido.objects.create(cliente=self.cliente, origen='CLIENTE', estado='VERIFICADO_AJUSTADO', total=Decimal('51.00'))
		self.pedido_item = PedidoItem.objects.create(
			pedido=self.pedido,
			presentacion=self.presentacion,
			cantidad_solicitada=3,
			cantidad=3,
			cantidad_inventario_aplicada=3,
			precio=Decimal('17.00'),
			subtotal=Decimal('51.00'),
		)
		self.invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
		)

	def test_void_invoice_restores_inventory_and_creates_record(self):
		stock_before = StockPresentacion.objects.get(presentacion=self.presentacion).stock_fisico

		anular_invoice(invoice=self.invoice, usuario=self.backoffice, motivo='Cliente cancelo')

		self.invoice.refresh_from_db()
		self.pedido_item.refresh_from_db()
		stock_after = StockPresentacion.objects.get(presentacion=self.presentacion).stock_fisico
		self.assertEqual(self.invoice.estado, 'ANULADA')
		self.assertEqual(self.pedido_item.cantidad_inventario_aplicada, 0)
		self.assertEqual(stock_after, stock_before + 3)
		self.assertTrue(FacturacionRegistroAnulacion.objects.filter(invoice=self.invoice, tipo_documento='INVOICE').exists())
		self.assertEqual(self.pedido.estado, 'VERIFICADO_AJUSTADO')

	def test_delete_invoice_removes_record_and_restores_inventory(self):
		stock_before = StockPresentacion.objects.get(presentacion=self.presentacion).stock_fisico
		invoice_id = self.invoice.id

		eliminar_invoice(invoice=self.invoice)

		self.assertFalse(Invoice.objects.filter(id=invoice_id).exists())
		self.assertFalse(FacturacionRegistroAnulacion.objects.filter(documento_id=invoice_id).exists())
		self.pedido_item.refresh_from_db()
		self.assertEqual(self.pedido_item.cantidad_inventario_aplicada, 0)
		self.assertEqual(StockPresentacion.objects.get(presentacion=self.presentacion).stock_fisico, stock_before + 3)
		self.pedido.refresh_from_db()
		self.assertEqual(self.pedido.estado, 'CANCELADO')

	def test_delete_delivered_invoice_allowed_when_not_synced_to_quickbooks(self):
		driver = Usuario.objects.create_user(username='void-driver', password='secret123', role='driver')
		self.invoice.metodo_entrega = 'RUTA_DRIVER'
		self.invoice.driver = driver
		self.invoice.save(update_fields=['metodo_entrega', 'driver', 'actualizada_en'])
		delivery = ensure_delivery_for_invoice(self.invoice)
		delivery.estado = 'ENTREGADA_PAGADA'
		delivery.save(update_fields=['estado', 'updated_at'])
		self.invoice.refresh_from_db()
		self.assertTrue(self.invoice.delivery_blocks_void_delete())
		self.assertTrue(self.invoice.can_void_from_backoffice())
		self.assertTrue(self.invoice.can_delete_from_backoffice())

		invoice_id = self.invoice.id
		delivery_id = delivery.id
		eliminar_invoice(invoice=self.invoice)

		self.assertFalse(Invoice.objects.filter(id=invoice_id).exists())
		self.assertFalse(Delivery.objects.filter(id=delivery_id).exists())

	def test_void_delivered_unpaid_invoice_creates_record_and_clears_hold(self):
		driver = Usuario.objects.create_user(username='void-delivered-driver', password='secret123', role='driver')
		self.invoice.metodo_entrega = 'RUTA_DRIVER'
		self.invoice.driver = driver
		self.invoice.save(update_fields=['metodo_entrega', 'driver', 'actualizada_en'])
		delivery = ensure_delivery_for_invoice(self.invoice)
		delivery.estado = 'ENTREGADA_SIN_PAGO'
		delivery.estado_pago = 'NO_PAGADO'
		delivery.client_blocked_on_delivery = True
		delivery.monto_pagado = Decimal('0.00')
		delivery.save(update_fields=[
			'estado',
			'estado_pago',
			'client_blocked_on_delivery',
			'monto_pagado',
			'updated_at',
		])
		self.cliente.credit_hold = True
		self.cliente.save(update_fields=['credit_hold'])
		stock_before = StockPresentacion.objects.get(presentacion=self.presentacion).stock_fisico

		anular_invoice(invoice=self.invoice, usuario=self.backoffice, motivo='Cliente rechazo entrega')

		self.invoice.refresh_from_db()
		self.cliente.refresh_from_db()
		delivery.refresh_from_db()
		registro = FacturacionRegistroAnulacion.objects.get(invoice=self.invoice, tipo_documento='INVOICE')
		self.assertEqual(self.invoice.estado, 'ANULADA')
		self.assertFalse(self.cliente.credit_hold)
		self.assertFalse(delivery.client_blocked_on_delivery)
		self.assertEqual(delivery.monto_pagado, Decimal('0.00'))
		self.assertEqual(StockPresentacion.objects.get(presentacion=self.presentacion).stock_fisico, stock_before + 3)
		self.assertEqual(registro.snapshot.get('delivery', {}).get('estado'), 'ENTREGADA_SIN_PAGO')
		self.assertFalse(
			Invoice.objects.filter(
				id=self.invoice.id,
				estado='GENERADA',
				delivery__estado__in=['ENTREGADA_PAGADA', 'ENTREGADA_SIN_PAGO'],
			).exists()
		)
		self.assertTrue(Invoice.objects.filter(id=self.invoice.id, estado='ANULADA').exists())

	def test_void_blocked_while_delivery_on_route(self):
		driver = Usuario.objects.create_user(username='void-onroute-driver', password='secret123', role='driver')
		self.invoice.metodo_entrega = 'RUTA_DRIVER'
		self.invoice.driver = driver
		self.invoice.save(update_fields=['metodo_entrega', 'driver', 'actualizada_en'])
		delivery = ensure_delivery_for_invoice(self.invoice)
		delivery.estado = 'EN_RUTA'
		delivery.save(update_fields=['estado', 'updated_at'])
		self.invoice.refresh_from_db()

		self.assertTrue(self.invoice.delivery_is_on_route())
		self.assertFalse(self.invoice.can_void_from_backoffice())
		with self.assertRaises(ValidationError):
			anular_invoice(invoice=self.invoice, usuario=self.backoffice, motivo='No permitido en ruta')

	def test_delete_delivered_invoice_allowed_when_synced_to_quickbooks(self):
		driver = Usuario.objects.create_user(username='void-driver-synced', password='secret123', role='driver')
		self.invoice.metodo_entrega = 'RUTA_DRIVER'
		self.invoice.driver = driver
		self.invoice.quickbooks_id = 'QB-123'
		self.invoice.sync_status = 'SYNCED'
		self.invoice.save(update_fields=['metodo_entrega', 'driver', 'quickbooks_id', 'sync_status', 'actualizada_en'])
		delivery = ensure_delivery_for_invoice(self.invoice)
		delivery.estado = 'ENTREGADA_PAGADA'
		delivery.save(update_fields=['estado', 'updated_at'])
		self.invoice.refresh_from_db()
		self.assertTrue(self.invoice.can_delete_from_backoffice())

		invoice_id = self.invoice.id
		eliminar_invoice(invoice=self.invoice)

		self.assertFalse(Invoice.objects.filter(id=invoice_id).exists())

	def test_delete_delivered_invoice_with_synced_linked_note(self):
		driver = Usuario.objects.create_user(username='void-driver-note', password='secret123', role='driver')
		self.invoice.metodo_entrega = 'RUTA_DRIVER'
		self.invoice.driver = driver
		self.invoice.save(update_fields=['metodo_entrega', 'driver', 'actualizada_en'])
		delivery = ensure_delivery_for_invoice(self.invoice)
		delivery.estado = 'ENTREGADA_PAGADA'
		delivery.save(update_fields=['estado', 'updated_at'])
		nota = crear_nota_ajuste_desde_invoice(
			invoice=self.invoice,
			tipo_documento='CREDITO',
			motivo='DAMAGE',
			tipo_credito='CREDIT_DUMP',
			descripcion='Nota sync test',
			usuario=self.backoffice,
			items_payload=[{'invoice_item': self.invoice.items.first(), 'cantidad': 1, 'monto_unitario': Decimal('17.00')}],
		)
		nota.quickbooks_id = 'QB-NOTE-1'
		nota.sync_status = 'SYNCED'
		nota.save(update_fields=['quickbooks_id', 'sync_status'])

		invoice_id = self.invoice.id
		note_id = nota.id
		eliminar_invoice(invoice=self.invoice)

		self.assertFalse(Invoice.objects.filter(id=invoice_id).exists())
		self.assertFalse(NotaAjuste.objects.filter(id=note_id).exists())

	def test_delete_credit_note_removes_record_and_reverses_inventory(self):
		nota = crear_nota_ajuste_desde_invoice(
			invoice=self.invoice,
			tipo_documento='CREDITO',
			motivo='DAMAGE',
			tipo_credito='CREDIT_RETURN',
			descripcion='Devolucion test',
			usuario=self.backoffice,
			items_payload=[{'invoice_item': self.invoice.items.first(), 'cantidad': 1, 'monto_unitario': Decimal('17.00')}],
		)
		aprobar_nota_ajuste(nota=nota, usuario=self.backoffice)
		stock_after_credit = StockPresentacion.objects.get(presentacion=self.presentacion).stock_fisico
		note_id = nota.id

		eliminar_nota_ajuste(nota=nota)

		self.assertFalse(NotaAjuste.objects.filter(id=note_id).exists())
		self.assertEqual(StockPresentacion.objects.get(presentacion=self.presentacion).stock_fisico, stock_after_credit - 1)

	def test_delete_synced_general_note_allowed_from_backoffice(self):
		nota = crear_nota_ajuste(
			cliente=self.cliente,
			tipo_documento='DEBITO',
			motivo='OTHER',
			descripcion='prueba',
			usuario=self.backoffice,
			monto=Decimal('10.00'),
		)
		aprobar_nota_ajuste(nota=nota, usuario=self.backoffice)
		nota.quickbooks_id = 'QB-DEBIT-1'
		nota.sync_status = 'SYNCED'
		nota.save(update_fields=['quickbooks_id', 'sync_status'])
		self.assertTrue(nota.can_delete_from_backoffice())

		note_id = nota.id
		eliminar_nota_ajuste(nota=nota)

		self.assertFalse(NotaAjuste.objects.filter(id=note_id).exists())

	def test_synced_invoice_can_delete_from_backoffice_before_delivery(self):
		self.invoice.quickbooks_id = 'QB-INVOICE-1'
		self.invoice.sync_status = QUICKBOOKS_SYNC_STATUS_SYNCED
		self.invoice.save(update_fields=['quickbooks_id', 'sync_status', 'actualizada_en'])
		self.invoice.refresh_from_db()

		self.assertFalse(self.invoice.can_void_from_backoffice())
		self.assertTrue(self.invoice.can_delete_from_backoffice())
		self.assertTrue(self.invoice.requires_delete_confirmation_phrase())
		self.assertTrue(invoice_delete_requires_confirmation_phrase(self.invoice))

	def test_validate_delete_confirmation_phrase_blocks_invalid_values(self):
		self.invoice.quickbooks_id = 'QB-INVOICE-2'
		self.invoice.sync_status = QUICKBOOKS_SYNC_STATUS_SYNCED
		self.invoice.save(update_fields=['quickbooks_id', 'sync_status', 'actualizada_en'])

		with self.assertRaises(ValidationError):
			validate_invoice_delete_confirmation_phrase(invoice=self.invoice, confirmation_phrase='yes')

		validate_invoice_delete_confirmation_phrase(invoice=self.invoice, confirmation_phrase='confirm')
		validate_invoice_delete_confirmation_phrase(invoice=self.invoice, confirmation_phrase='DELETE')

	def test_backoffice_delete_synced_invoice_requires_confirmation_phrase(self):
		self.invoice.quickbooks_id = 'QB-INVOICE-3'
		self.invoice.sync_status = QUICKBOOKS_SYNC_STATUS_SYNCED
		self.invoice.save(update_fields=['quickbooks_id', 'sync_status', 'actualizada_en'])
		invoice_id = self.invoice.id
		delete_url = reverse('backoffice_invoice_delete', args=[invoice_id])

		self.client.force_login(self.backoffice)
		response = self.client.post(delete_url, {'next': reverse('backoffice_invoices_list')})
		self.assertEqual(response.status_code, 302)
		self.assertTrue(Invoice.objects.filter(id=invoice_id).exists())
		messages = [str(message) for message in get_messages(response.wsgi_request)]
		self.assertTrue(any('CONFIRM' in message or 'DELETE' in message for message in messages))

		response = self.client.post(
			delete_url,
			{'next': reverse('backoffice_invoices_list'), 'confirmation_phrase': 'confirm'},
		)
		self.assertEqual(response.status_code, 302)
		self.assertFalse(Invoice.objects.filter(id=invoice_id).exists())
