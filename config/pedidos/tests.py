from decimal import Decimal
from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from config.clientes.models import Cliente
from config.core.datetime_formats import format_local_datetime
from config.facturacion.models import Invoice, InvoiceItem
from config.facturacion.services import anular_invoice, eliminar_invoice, generar_invoice_desde_picking, resolve_invoice_sale_reference_date
from config.inventario.models import StockPresentacion
from config.inventario.services import registrar_entrada_manual
from config.notificaciones.models import Notificacion
from config.pedidos.models import Pedido, PedidoEditLock, PedidoItem
from config.auditoria.models import AuditLog
from config.pedidos.services import (
	PEDIDO_EDIT_LOCK_TIMEOUT,
	acquire_pedido_edit_lock,
	asignar_picking_a_seleccionador,
	build_multi_pedido_inventory_needs_analysis,
	build_pedido_inventory_needs_analysis,
	devolver_pedido_desde_picking,
	evaluar_stock_fisico_verificacion_picking,
	guardar_verificacion_picking,
	_resolve_picker_added_item_price,
	resolver_bloqueo_picking_desde_backoffice,
	resolver_nota_cliente_desde_backoffice,
	resolve_picking_send_ui_state,
)
from config.productos.models import Categoria, ConfiguracionDescuentos, Marca, Presentacion, Producto
from config.usuarios.models import Usuario


class OrderNotificationRecipientTests(TestCase):
	def setUp(self):
		Usuario.objects.create_user(
			username='staff-inbox',
			password='secret123',
			role='backoffice',
			email='carito30033@gmail.com',
		)

	@override_settings(ORDER_NOTIFICATION_EMAILS=['ltgordersapp@gmail.com'])
	def test_shared_mailbox_replaces_personal_staff_inboxes(self):
		from config.pedidos.services import resolve_order_notification_recipients

		recipients = resolve_order_notification_recipients()

		self.assertEqual(recipients, ['ltgordersapp@gmail.com'])
		self.assertNotIn('carito30033@gmail.com', recipients)

	@override_settings(ORDER_NOTIFICATION_EMAILS=[])
	def test_staff_users_are_used_when_no_mailbox_is_configured(self):
		from config.pedidos.services import resolve_order_notification_recipients

		self.assertEqual(resolve_order_notification_recipients(), ['carito30033@gmail.com'])


class PickingVerificationFlowTests(TestCase):
	def setUp(self):
		self.backoffice = Usuario.objects.create_user(username='backoffice', password='secret123', role='backoffice')
		self.selector = Usuario.objects.create_user(username='selector-1', password='secret123', role='seleccionador', first_name='Ana')
		self.other_selector = Usuario.objects.create_user(username='selector-2', password='secret123', role='seleccionador')
		self.customer_user = Usuario.objects.create_user(username='customer', password='secret123', role='cliente', email='customer@example.com')
		self.cliente = Cliente.objects.create(
			usuario=self.customer_user,
			nombre_empresa='Cliente Demo',
			telefono='5551234567',
			direccion='123 Main St',
			ciudad='Marietta',
			estado='GA',
			codigo_postal='30062',
			pais='USA',
			sales_tax_number='TAX-123',
			certificado_tax=SimpleUploadedFile('certificado.txt', b'certificado'),
			aprobado=True,
		)

		categoria = Categoria.objects.create(nombre='Categoria test')
		marca = Marca.objects.create(nombre='Marca test')
		producto = Producto.objects.create(nombre='Producto test', categoria=categoria, marca=marca, activo=True)
		self.presentacion = Presentacion.objects.create(
			producto=producto,
			nombre='Caja',
			unidades=1,
			tipo_contenido='caja',
			costo=Decimal('10.00'),
			precio_1=Decimal('12.00'),
		)
		self.presentacion_unidad = Presentacion.objects.create(
			producto=producto,
			nombre='Unidad',
			unidades=1,
			tipo_contenido='unidad',
			costo=Decimal('2.00'),
			precio_1=Decimal('3.50'),
		)
		otro_producto = Producto.objects.create(nombre='Producto extra', categoria=categoria, marca=marca, activo=True)
		self.presentacion_extra = Presentacion.objects.create(
			producto=otro_producto,
			nombre='Pack',
			unidades=1,
			tipo_contenido='pack',
			costo=Decimal('4.00'),
			precio_1=Decimal('6.00'),
		)
		registrar_entrada_manual(presentacion=self.presentacion, cantidad=10, observacion='Initial stock')
		registrar_entrada_manual(presentacion=self.presentacion_unidad, cantidad=10, observacion='Alt stock')
		registrar_entrada_manual(presentacion=self.presentacion_extra, cantidad=10, observacion='Extra stock')

		self.pedido = Pedido.objects.create(
			cliente=self.cliente,
			origen='CLIENTE',
			estado='LISTO_PARA_PICKING',
			total=Decimal('24.00'),
		)
		self.item = PedidoItem.objects.create(
			pedido=self.pedido,
			presentacion=self.presentacion,
			cantidad_solicitada=2,
			cantidad_reservada_inventario=2,
			cantidad=2,
			precio=Decimal('12.00'),
			subtotal=Decimal('24.00'),
		)
		stock = StockPresentacion.objects.get(presentacion=self.presentacion)
		stock.stock_reservado = 2
		stock.stock_disponible = stock.stock_fisico - 2
		stock.save(update_fields=['stock_reservado', 'stock_disponible', 'actualizado_en'])

	def test_backoffice_detail_shows_available_stock_per_line(self):
		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_pedido_detalle', args=[self.pedido.id]))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Available stock: 10 CS')
		self.assertContains(response, 'text-success fw-semibold')
		self.assertNotContains(response, 'Physical:')

	def test_inventory_needs_analysis_flags_shortage_and_out_of_stock(self):
		self.presentacion.producto.codigo_barras = 'SKU-TEST-001'
		self.presentacion.producto.save(update_fields=['codigo_barras'])
		StockPresentacion.objects.filter(presentacion=self.presentacion).update(
			stock_fisico=1,
			stock_reservado=2,
			stock_disponible=0,
		)
		analysis = build_pedido_inventory_needs_analysis(pedido=self.pedido)

		self.assertTrue(analysis['has_needs'])
		self.assertEqual(analysis['needs_purchase_count'], 1)
		row = analysis['rows'][0]
		self.assertEqual(row['sku'], 'SKU-TEST-001')
		self.assertEqual(row['requested_quantity'], 2)
		self.assertEqual(row['reserved_quantity'], 2)
		self.assertEqual(row['pending_quantity'], 0)
		self.assertEqual(row['available_stock'], 1)
		self.assertEqual(row['to_buy_quantity'], 1)
		self.assertEqual(row['status'], 'insufficient')

		StockPresentacion.objects.filter(presentacion=self.presentacion).update(
			stock_fisico=0,
			stock_reservado=2,
			stock_disponible=0,
		)
		analysis = build_pedido_inventory_needs_analysis(pedido=self.pedido)
		row = analysis['rows'][0]
		self.assertEqual(row['available_stock'], 0)
		self.assertEqual(row['to_buy_quantity'], 2)
		self.assertEqual(row['status'], 'out_of_stock')

	def test_backoffice_detail_shows_inventory_analysis_and_export_button(self):
		StockPresentacion.objects.filter(presentacion=self.presentacion).update(
			stock_fisico=0,
			stock_reservado=2,
			stock_disponible=0,
		)
		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_pedido_detalle', args=[self.pedido.id]))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Inventory analysis')
		self.assertContains(response, 'Export Inventory Report')
		self.assertContains(response, reverse('backoffice_inventory_needs_pdf', args=[self.pedido.id]))
		self.assertContains(response, 'Out of stock')
		self.assertContains(response, 'table-danger')

	def test_backoffice_inventory_needs_pdf_exports_purchase_rows(self):
		self.presentacion.producto.codigo_barras = 'SKU-PDF-9'
		self.presentacion.producto.save(update_fields=['codigo_barras'])
		StockPresentacion.objects.filter(presentacion=self.presentacion).update(
			stock_fisico=0,
			stock_reservado=2,
			stock_disponible=0,
		)
		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_inventory_needs_pdf', args=[self.pedido.id]))

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response['Content-Type'], 'application/pdf')
		self.assertIn(f'inventory-needs-order-{self.pedido.id}.pdf', response['Content-Disposition'])
		self.assertTrue(response.content.startswith(b'%PDF'))

	def test_multi_pedido_inventory_needs_aggregates_without_double_counting_stock(self):
		other_pedido = Pedido.objects.create(
			cliente=self.cliente,
			origen='CLIENTE',
			estado='LISTO_PARA_PICKING',
			total=Decimal('36.00'),
		)
		PedidoItem.objects.create(
			pedido=other_pedido,
			presentacion=self.presentacion,
			cantidad=3,
			cantidad_solicitada=3,
			precio=Decimal('12.00'),
			subtotal=Decimal('36.00'),
		)
		self.item.cantidad_reservada_inventario = 0
		self.item.save(update_fields=['cantidad_reservada_inventario'])
		StockPresentacion.objects.filter(presentacion=self.presentacion).update(
			stock_fisico=4,
			stock_reservado=0,
			stock_disponible=4,
		)

		analysis = build_multi_pedido_inventory_needs_analysis(pedidos=[self.pedido, other_pedido])
		self.assertEqual(analysis['pedido_count'], 2)
		self.assertEqual(analysis['needs_purchase_count'], 1)
		row = analysis['rows'][0]
		self.assertEqual(row['requested_quantity'], 5)
		self.assertEqual(row['available_stock'], 4)
		self.assertEqual(row['to_buy_quantity'], 1)
		self.assertEqual(row['order_count'], 2)

	def test_backoffice_inventory_needs_report_from_selected_orders(self):
		StockPresentacion.objects.filter(presentacion=self.presentacion).update(
			stock_fisico=0,
			stock_reservado=2,
			stock_disponible=0,
		)
		self.client.force_login(self.backoffice)
		response = self.client.post(
			reverse('backoffice_inventory_needs_report'),
			{'pedido_ids': [self.pedido.id], 'view': 'pending'},
		)
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'General Inventory Analysis')
		self.assertContains(response, 'Out of stock')
		self.assertContains(response, f'#{self.pedido.numero_display}')

	def test_devolver_pedido_desde_picking_clears_assignment_and_logs_history(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		self.pedido.picking_progress = {'draft': True}
		self.pedido.picking_progress_saved_at = timezone.now()
		self.pedido.save(update_fields=['picking_progress', 'picking_progress_saved_at', 'actualizada_en'])

		devolver_pedido_desde_picking(
			pedido=self.pedido,
			usuario=self.backoffice,
			destino='LISTO_PARA_PICKING',
		)
		self.pedido.refresh_from_db()

		self.assertEqual(self.pedido.estado, 'LISTO_PARA_PICKING')
		self.assertIsNone(self.pedido.seleccionador_id)
		self.assertIsNone(self.pedido.picking_asignado_en)
		self.assertEqual(self.pedido.picking_progress, {})
		self.assertIsNone(self.pedido.picking_progress_saved_at)
		log = AuditLog.objects.filter(
			entity_type='Pedido',
			entity_id=str(self.pedido.id),
		).order_by('-id').first()
		self.assertIsNotNone(log)
		self.assertEqual((log.metadata or {}).get('action'), 'return_from_picking')
		self.assertIn('Returned order', str(log.action_label))

	def test_backoffice_can_return_order_from_picking_via_post(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		self.client.force_login(self.backoffice)
		detail = self.client.get(reverse('backoffice_pedido_detalle', args=[self.pedido.id]))
		self.assertContains(detail, 'Return from picking')
		self.assertContains(detail, reverse('backoffice_devolver_desde_picking', args=[self.pedido.id]))

		response = self.client.post(
			reverse('backoffice_devolver_desde_picking', args=[self.pedido.id]),
			{'destino': 'EN_GESTION'},
		)
		self.assertEqual(response.status_code, 302)
		self.pedido.refresh_from_db()
		self.assertEqual(self.pedido.estado, 'EN_GESTION')
		self.assertIsNone(self.pedido.seleccionador_id)

	def test_selector_picking_detail_add_product_button_is_at_end_and_styled(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		self.client.force_login(self.selector)
		response = self.client.get(reverse('selector_picking_detail', args=[self.pedido.id]))
		self.assertEqual(response.status_code, 200)
		content = response.content.decode()
		button_pos = content.find('id="addProductRowButton"')
		tbody_end = content.find('</tbody>', content.find('id="pickerItemsTableBody"'))
		self.assertGreater(button_pos, tbody_end)
		self.assertIn('picker-add-product-btn', content)
		self.assertIn('picker-add-pulse', content)

	def test_backoffice_detail_shows_order_comment(self):
		self.pedido.nota_cliente = 'Leave at back door'
		self.pedido.save(update_fields=['nota_cliente'])
		self.client.force_login(self.backoffice)

		response = self.client.get(reverse('backoffice_pedido_detalle', args=[self.pedido.id]))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Order comment')
		self.assertContains(response, 'pedidoNotaClienteDisplay')
		self.assertContains(response, 'Leave at back door')
		content = response.content.decode()
		self.assertGreater(content.rfind('pedidoNotaClienteDisplay'), content.find('Generate Picking Ticket'))

	def test_backoffice_detail_hides_order_comment_when_empty(self):
		self.pedido.nota_cliente = ''
		self.pedido.nota_cliente_resuelta = True
		self.pedido.save(update_fields=['nota_cliente', 'nota_cliente_resuelta'])
		self.client.force_login(self.backoffice)

		response = self.client.get(reverse('backoffice_pedido_detalle', args=[self.pedido.id]))

		self.assertEqual(response.status_code, 200)
		self.assertNotContains(response, 'pedidoNotaClienteDisplay')
		self.assertFalse(response.context['can_resolve_nota_cliente'])
		can_send, label = resolve_picking_send_ui_state(self.pedido)
		self.assertTrue(can_send)
		self.assertEqual(str(label), 'Send picking')

	def test_order_with_comment_blocks_picking_until_resolved(self):
		from config.pedidos.services import crear_pedido_desde_items

		pedido = crear_pedido_desde_items(
			cliente=self.cliente,
			origen='VENDEDOR',
			vendedor=self.backoffice,
			nota_cliente='Call before delivery',
			reservar_inventario=False,
			items_payload=[
				{'presentacion': self.presentacion, 'cantidad': 1, 'precio': Decimal('12.00')},
			],
		)
		self.assertTrue(pedido.tiene_nota_cliente_pendiente)
		self.assertFalse(pedido.nota_cliente_resuelta)

		with self.assertRaises(ValidationError):
			asignar_picking_a_seleccionador(pedido=pedido, seleccionador=self.selector)

		can_send, label = resolve_picking_send_ui_state(pedido)
		self.assertFalse(can_send)
		self.assertEqual(str(label), 'Resolve order comment')

		self.client.force_login(self.backoffice)
		detail = self.client.get(reverse('backoffice_pedido_detalle', args=[pedido.id]))
		self.assertTrue(detail.context['can_resolve_nota_cliente'])
		self.assertContains(detail, 'Resolve order comment')

		response = self.client.post(reverse('backoffice_resolver_nota_cliente', args=[pedido.id]))
		self.assertEqual(response.status_code, 302)
		pedido.refresh_from_db()
		self.assertTrue(pedido.nota_cliente_resuelta)
		self.assertFalse(pedido.tiene_nota_cliente_pendiente)

		asignar_picking_a_seleccionador(pedido=pedido, seleccionador=self.selector)
		pedido.refresh_from_db()
		self.assertEqual(pedido.estado, 'PARA_VERIFICAR')

	def test_unresolved_order_comment_blocks_invoice_generation(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		guardar_verificacion_picking(
			pedido=self.pedido,
			seleccionador=self.selector,
			cantidades_reales={self.item.id: 2},
			nota='OK',
			nota_resuelta=True,
		)
		self.pedido.nota_cliente = 'Urgent special packing'
		self.pedido.nota_cliente_resuelta = False
		self.pedido.save(update_fields=['nota_cliente', 'nota_cliente_resuelta', 'actualizada_en'])

		with self.assertRaises(ValidationError):
			generar_invoice_desde_picking(
				pedido=self.pedido,
				metodo_entrega='CUSTOMER_PICK_UP',
				driver=None,
				usuario=self.backoffice,
			)

		resolver_nota_cliente_desde_backoffice(pedido=self.pedido, usuario=self.backoffice)
		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
		)
		self.assertIsNotNone(invoice.id)

	def test_assigning_picking_sets_selector_and_notifies(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)

		self.pedido.refresh_from_db()
		self.item.refresh_from_db()

		self.assertEqual(self.pedido.estado, 'PARA_VERIFICAR')
		self.assertEqual(self.pedido.seleccionador, self.selector)
		self.assertEqual(self.item.cantidad_solicitada, 2)
		self.assertTrue(Notificacion.objects.filter(usuario=self.selector, titulo__icontains='picking').exists())

	def test_resolve_picking_send_ui_state_after_assignment(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)

		can_send, label = resolve_picking_send_ui_state(self.pedido)

		self.assertFalse(can_send)
		self.assertEqual(str(label), 'Sent to picker')

	def test_resolve_picking_send_ui_state_after_picking_completed(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		guardar_verificacion_picking(
			pedido=self.pedido,
			seleccionador=self.selector,
			cantidades_reales={self.item.id: 2},
			nota='OK',
			nota_resuelta=True,
		)
		self.pedido.refresh_from_db()

		can_send, label = resolve_picking_send_ui_state(self.pedido)

		self.assertFalse(can_send)
		self.assertEqual(str(label), 'Picking completed')

	def test_backoffice_detail_disables_send_picking_after_picking_completed(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		guardar_verificacion_picking(
			pedido=self.pedido,
			seleccionador=self.selector,
			cantidades_reales={self.item.id: 2},
			nota='OK',
			nota_resuelta=True,
		)

		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_pedido_detalle', args=[self.pedido.id]))

		self.assertEqual(response.status_code, 200)
		self.assertFalse(response.context['can_send_picking'])
		self.assertContains(response, 'Picking completed')
		self.assertContains(response, 'Picking was already completed. Review the order and generate the invoice when ready.')
		self.assertNotContains(response, 'name="seleccionador_id"')

	def test_backoffice_detail_keeps_lines_editable_during_selector_verification(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)

		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_pedido_detalle', args=[self.pedido.id]))

		self.assertEqual(response.status_code, 200)
		self.assertFalse(response.context['lineas_bloqueadas_para_picking'])
		self.assertFalse(response.context['pedido_form_disabled'])
		self.assertNotContains(response, 'locked while the selector verification workflow is active')

	def test_backoffice_can_edit_lines_while_pending_selector_verification(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)

		self.client.force_login(self.backoffice)
		response = self.client.post(reverse('backoffice_pedido_detalle', args=[self.pedido.id]), {
			'estado': 'PARA_VERIFICAR',
			'nota_backoffice': 'Ajuste manual antes de factura',
			f'cantidad_{self.item.id}': '1',
			f'precio_{self.item.id}': '12.00',
		})

		self.assertEqual(response.status_code, 302)
		self.item.refresh_from_db()
		self.assertEqual(self.item.cantidad, 1)

	def test_backoffice_detail_locks_lines_after_invoice_generation(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		guardar_verificacion_picking(
			pedido=self.pedido,
			seleccionador=self.selector,
			cantidades_reales={self.item.id: 2},
			nota='Verificado',
			nota_resuelta=True,
		)
		generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
		)

		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_pedido_detalle', args=[self.pedido.id]))

		self.assertEqual(response.status_code, 200)
		self.assertTrue(response.context['lineas_bloqueadas_para_picking'])
		self.assertTrue(response.context['pedido_form_disabled'])
		self.assertContains(response, 'Order lines are locked because this order already has an invoice generated.')

	def test_verification_allows_empty_note_when_stock_is_insufficient(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		StockPresentacion.objects.filter(presentacion=self.presentacion).update(stock_fisico=0)

		guardar_verificacion_picking(
			pedido=self.pedido,
			seleccionador=self.selector,
			cantidades_reales={self.item.id: 1},
			nota='   ',
			nota_resuelta=False,
		)

		self.pedido.refresh_from_db()
		self.assertEqual(self.pedido.nota_seleccionador, '')
		self.assertFalse(self.pedido.picking_bloqueado)

	def test_verification_requires_picker_approval_when_stock_is_available(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)

		with self.assertRaises(ValidationError):
			guardar_verificacion_picking(
				pedido=self.pedido,
				seleccionador=self.selector,
				cantidades_reales={self.item.id: 2},
				nota='',
				nota_resuelta=False,
			)

	def test_verification_updates_quantities_and_blocks_when_unresolved(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		StockPresentacion.objects.filter(presentacion=self.presentacion).update(stock_fisico=0)
		stock = StockPresentacion.objects.get(presentacion=self.presentacion)

		guardar_verificacion_picking(
			pedido=self.pedido,
			seleccionador=self.selector,
			cantidades_reales={self.item.id: 1},
			nota='Falto una unidad en almacen.',
			nota_resuelta=False,
		)

		self.pedido.refresh_from_db()
		self.item.refresh_from_db()

		self.assertEqual(self.pedido.estado, 'VERIFICADO_AJUSTADO')
		self.assertTrue(self.pedido.picking_bloqueado)
		self.assertEqual(self.item.cantidad, 1)
		self.assertEqual(self.pedido.total, Decimal('12.00'))
		stock.refresh_from_db()
		self.assertEqual(stock.stock_fisico, 0)
		self.assertTrue(Notificacion.objects.filter(titulo__icontains='stock shortage').exists())

	def test_selector_only_sees_assigned_picking_tickets(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		other_order = Pedido.objects.create(
			cliente=self.cliente,
			origen='CLIENTE',
			estado='LISTO_PARA_PICKING',
			total=Decimal('24.00'),
		)
		other_item = PedidoItem.objects.create(
			pedido=other_order,
			presentacion=self.presentacion,
			cantidad_solicitada=1,
			cantidad_reservada_inventario=1,
			cantidad=1,
			precio=Decimal('12.00'),
			subtotal=Decimal('12.00'),
		)
		stock = StockPresentacion.objects.get(presentacion=self.presentacion)
		stock.stock_reservado += 1
		stock.stock_disponible = stock.stock_fisico - stock.stock_reservado
		stock.save(update_fields=['stock_reservado', 'stock_disponible', 'actualizado_en'])
		asignar_picking_a_seleccionador(pedido=other_order, seleccionador=self.other_selector)

		self.client.force_login(self.selector)
		response = self.client.get(reverse('selector_picking_list'))
		self.assertContains(response, 'Cliente Demo')
		self.assertNotContains(response, reverse('selector_picking_detail', args=[other_order.id]))

		detail_response = self.client.get(reverse('selector_picking_detail', args=[self.pedido.id]))
		self.assertEqual(detail_response.status_code, 200)
		self.assertNotContains(detail_response, '12.00')

		other_detail_response = self.client.get(reverse('selector_picking_detail', args=[other_order.id]))
		self.assertEqual(other_detail_response.status_code, 404)

		other_item.refresh_from_db()

	def test_selector_list_defaults_to_pending_tickets_and_completed_view_shows_processed(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		processed_order = Pedido.objects.create(
			cliente=self.cliente,
			origen='CLIENTE',
			estado='LISTO_PARA_PICKING',
			total=Decimal('12.00'),
		)
		processed_item = PedidoItem.objects.create(
			pedido=processed_order,
			presentacion=self.presentacion,
			cantidad_solicitada=1,
			cantidad_reservada_inventario=1,
			cantidad=1,
			precio=Decimal('12.00'),
			subtotal=Decimal('12.00'),
		)
		stock = StockPresentacion.objects.get(presentacion=self.presentacion)
		stock.stock_reservado += 1
		stock.stock_disponible = stock.stock_fisico - stock.stock_reservado
		stock.save(update_fields=['stock_reservado', 'stock_disponible', 'actualizado_en'])
		asignar_picking_a_seleccionador(pedido=processed_order, seleccionador=self.selector)
		guardar_verificacion_picking(
			pedido=processed_order,
			seleccionador=self.selector,
			cantidades_reales={processed_item.id: 1},
			nota='Verificado',
			nota_resuelta=True,
		)

		self.client.force_login(self.selector)
		pending_response = self.client.get(reverse('selector_picking_list'))
		self.assertContains(pending_response, 'Pending Picking Tickets')
		self.assertContains(pending_response, reverse('selector_picking_detail', args=[self.pedido.id]))
		self.assertNotContains(pending_response, reverse('selector_picking_detail', args=[processed_order.id]))

		completed_response = self.client.get(reverse('selector_picking_list') + '?view=completed')
		self.assertContains(completed_response, 'Processed Picking Tickets')
		self.assertContains(completed_response, reverse('selector_picking_detail', args=[processed_order.id]))
		self.assertNotContains(completed_response, reverse('selector_picking_detail', args=[self.pedido.id]))
		self.assertContains(completed_response, 'Search picking tickets')

	def test_selector_processed_list_shades_invoiced_tickets_and_puts_them_last(self):
		waiting_order = Pedido.objects.create(
			cliente=self.cliente,
			origen='CLIENTE',
			estado='LISTO_PARA_PICKING',
			total=Decimal('12.00'),
		)
		waiting_item = PedidoItem.objects.create(
			pedido=waiting_order,
			presentacion=self.presentacion,
			cantidad_solicitada=1,
			cantidad_reservada_inventario=1,
			cantidad=1,
			precio=Decimal('12.00'),
			subtotal=Decimal('12.00'),
		)
		stock = StockPresentacion.objects.get(presentacion=self.presentacion)
		stock.stock_reservado += 1
		stock.stock_disponible = stock.stock_fisico - stock.stock_reservado
		stock.save(update_fields=['stock_reservado', 'stock_disponible', 'actualizado_en'])
		asignar_picking_a_seleccionador(pedido=waiting_order, seleccionador=self.selector)
		guardar_verificacion_picking(
			pedido=waiting_order,
			seleccionador=self.selector,
			cantidades_reales={waiting_item.id: 1},
			nota='Waiting invoice',
			nota_resuelta=True,
		)

		invoiced_order = Pedido.objects.create(
			cliente=self.cliente,
			origen='CLIENTE',
			estado='LISTO_PARA_PICKING',
			total=Decimal('12.00'),
		)
		invoiced_item = PedidoItem.objects.create(
			pedido=invoiced_order,
			presentacion=self.presentacion,
			cantidad_solicitada=1,
			cantidad_reservada_inventario=1,
			cantidad=1,
			precio=Decimal('12.00'),
			subtotal=Decimal('12.00'),
		)
		stock = StockPresentacion.objects.get(presentacion=self.presentacion)
		stock.stock_reservado += 1
		stock.stock_disponible = stock.stock_fisico - stock.stock_reservado
		stock.save(update_fields=['stock_reservado', 'stock_disponible', 'actualizado_en'])
		asignar_picking_a_seleccionador(pedido=invoiced_order, seleccionador=self.selector)
		guardar_verificacion_picking(
			pedido=invoiced_order,
			seleccionador=self.selector,
			cantidades_reales={invoiced_item.id: 1},
			nota='Ready to invoice',
			nota_resuelta=True,
		)
		generar_invoice_desde_picking(
			pedido=invoiced_order,
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
		)

		self.client.force_login(self.selector)
		response = self.client.get(reverse('selector_picking_list') + '?view=completed')
		self.assertEqual(response.status_code, 200)
		body = response.content.decode('utf-8')
		waiting_pos = body.find(f'#{waiting_order.id}')
		invoiced_pos = body.find(f'#{invoiced_order.id}')
		self.assertGreater(waiting_pos, -1)
		self.assertGreater(invoiced_pos, -1)
		self.assertLess(waiting_pos, invoiced_pos)
		self.assertContains(response, 'Process completed')
		self.assertContains(response, 'selector-picking-row--completed', html=False)

	def test_selector_processed_list_marks_voided_and_deleted_invoices_completed_last(self):
		waiting_order = Pedido.objects.create(
			cliente=self.cliente,
			origen='CLIENTE',
			estado='LISTO_PARA_PICKING',
			total=Decimal('12.00'),
		)
		waiting_item = PedidoItem.objects.create(
			pedido=waiting_order,
			presentacion=self.presentacion,
			cantidad_solicitada=1,
			cantidad_reservada_inventario=1,
			cantidad=1,
			precio=Decimal('12.00'),
			subtotal=Decimal('12.00'),
		)
		stock = StockPresentacion.objects.get(presentacion=self.presentacion)
		stock.stock_reservado += 1
		stock.stock_disponible = stock.stock_fisico - stock.stock_reservado
		stock.save(update_fields=['stock_reservado', 'stock_disponible', 'actualizado_en'])
		asignar_picking_a_seleccionador(pedido=waiting_order, seleccionador=self.selector)
		guardar_verificacion_picking(
			pedido=waiting_order,
			seleccionador=self.selector,
			cantidades_reales={waiting_item.id: 1},
			nota='Waiting invoice',
			nota_resuelta=True,
		)

		voided_order = Pedido.objects.create(
			cliente=self.cliente,
			origen='CLIENTE',
			estado='LISTO_PARA_PICKING',
			total=Decimal('12.00'),
		)
		voided_item = PedidoItem.objects.create(
			pedido=voided_order,
			presentacion=self.presentacion,
			cantidad_solicitada=1,
			cantidad_reservada_inventario=1,
			cantidad=1,
			precio=Decimal('12.00'),
			subtotal=Decimal('12.00'),
		)
		stock = StockPresentacion.objects.get(presentacion=self.presentacion)
		stock.stock_reservado += 1
		stock.stock_disponible = stock.stock_fisico - stock.stock_reservado
		stock.save(update_fields=['stock_reservado', 'stock_disponible', 'actualizado_en'])
		asignar_picking_a_seleccionador(pedido=voided_order, seleccionador=self.selector)
		guardar_verificacion_picking(
			pedido=voided_order,
			seleccionador=self.selector,
			cantidades_reales={voided_item.id: 1},
			nota='Void later',
			nota_resuelta=True,
		)
		voided_invoice = generar_invoice_desde_picking(
			pedido=voided_order,
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
		)
		anular_invoice(invoice=voided_invoice, usuario=self.backoffice, motivo='Already billed elsewhere')

		deleted_order = Pedido.objects.create(
			cliente=self.cliente,
			origen='CLIENTE',
			estado='LISTO_PARA_PICKING',
			total=Decimal('12.00'),
		)
		deleted_item = PedidoItem.objects.create(
			pedido=deleted_order,
			presentacion=self.presentacion,
			cantidad_solicitada=1,
			cantidad_reservada_inventario=1,
			cantidad=1,
			precio=Decimal('12.00'),
			subtotal=Decimal('12.00'),
		)
		stock = StockPresentacion.objects.get(presentacion=self.presentacion)
		stock.stock_reservado += 1
		stock.stock_disponible = stock.stock_fisico - stock.stock_reservado
		stock.save(update_fields=['stock_reservado', 'stock_disponible', 'actualizado_en'])
		asignar_picking_a_seleccionador(pedido=deleted_order, seleccionador=self.selector)
		guardar_verificacion_picking(
			pedido=deleted_order,
			seleccionador=self.selector,
			cantidades_reales={deleted_item.id: 1},
			nota='Delete later',
			nota_resuelta=True,
		)
		deleted_invoice = generar_invoice_desde_picking(
			pedido=deleted_order,
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
		)
		eliminar_invoice(invoice=deleted_invoice)

		self.client.force_login(self.selector)
		pending_response = self.client.get(reverse('selector_picking_list'))
		self.assertNotContains(pending_response, reverse('selector_picking_detail', args=[voided_order.id]))
		self.assertNotContains(pending_response, reverse('selector_picking_detail', args=[deleted_order.id]))

		response = self.client.get(reverse('selector_picking_list') + '?view=completed')
		self.assertEqual(response.status_code, 200)
		body = response.content.decode('utf-8')
		waiting_pos = body.find(f'#{waiting_order.id}')
		voided_pos = body.find(f'#{voided_order.id}')
		deleted_pos = body.find(f'#{deleted_order.id}')
		self.assertGreater(waiting_pos, -1)
		self.assertGreater(voided_pos, -1)
		self.assertGreater(deleted_pos, -1)
		self.assertLess(waiting_pos, voided_pos)
		self.assertLess(waiting_pos, deleted_pos)
		self.assertContains(response, 'Invoice voided')
		self.assertContains(response, 'Cancelled')
		self.assertContains(response, 'Process completed')
		self.assertContains(response, 'selector-picking-row--completed', html=False)

	def test_selector_picking_list_search_filters_by_customer_or_id(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		other_cliente_user = Usuario.objects.create_user(username='cliente-picker-search', password='secret123', role='cliente')
		other_cliente = Cliente.objects.create(
			usuario=other_cliente_user,
			nombre_empresa='Alpha Market Search',
			telefono='5559998888',
			direccion='9 Search St',
			ciudad='Dallas',
			estado='TX',
			codigo_postal='75001',
			pais='USA',
			sales_tax_number='TX-SEARCH',
			certificado_tax='certificados/test.pdf',
		)
		other_order = Pedido.objects.create(
			cliente=other_cliente,
			origen='CLIENTE',
			estado='LISTO_PARA_PICKING',
			total=Decimal('12.00'),
		)
		PedidoItem.objects.create(
			pedido=other_order,
			presentacion=self.presentacion,
			cantidad_solicitada=1,
			cantidad=1,
			precio=Decimal('12.00'),
			subtotal=Decimal('12.00'),
		)
		asignar_picking_a_seleccionador(pedido=other_order, seleccionador=self.selector)

		self.client.force_login(self.selector)
		by_name = self.client.get(reverse('selector_picking_list'), {'q': 'Alpha Market'})
		self.assertContains(by_name, reverse('selector_picking_detail', args=[other_order.id]))
		self.assertNotContains(by_name, reverse('selector_picking_detail', args=[self.pedido.id]))

		by_id = self.client.get(reverse('selector_picking_list'), {'q': str(self.pedido.id)})
		self.assertContains(by_id, reverse('selector_picking_detail', args=[self.pedido.id]))
		self.assertNotContains(by_id, reverse('selector_picking_detail', args=[other_order.id]))

	def test_completed_picking_ticket_shows_saved_quantities(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		guardar_verificacion_picking(
			pedido=self.pedido,
			seleccionador=self.selector,
			cantidades_reales={self.item.id: 7},
			nota='Cantidades verificadas',
			nota_resuelta=True,
		)

		self.client.force_login(self.selector)
		response = self.client.get(reverse('selector_picking_detail', args=[self.pedido.id]))

		self.assertEqual(response.status_code, 200)
		self.assertContains(
			response,
			f'name="cantidad_real_{self.item.id}" value="7"',
			html=False,
		)
		self.item.refresh_from_db()
		self.assertEqual(self.item.cantidad_solicitada, 2)
		self.assertEqual(self.item.cantidad, 7)

	def test_selector_can_complete_picking_with_qty_above_ordered(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		self.client.force_login(self.selector)

		response = self.client.post(reverse('selector_picking_detail', args=[self.pedido.id]), {
			'submit_action': 'complete_verification',
			'cantidad_pallets': '1',
			f'presentacion_{self.item.id}': str(self.presentacion.id),
			f'cantidad_real_{self.item.id}': '5',
			f'linea_revisada_{self.item.id}': 'on',
			'nota_seleccionador': 'Cliente acepta 5 CS',
			'nota_seleccionador_resuelta': 'on',
		})

		self.assertEqual(response.status_code, 302)
		self.pedido.refresh_from_db()
		self.item.refresh_from_db()
		self.assertEqual(self.pedido.estado, 'VERIFICADO_AJUSTADO')
		self.assertEqual(self.item.cantidad_solicitada, 2)
		self.assertEqual(self.item.cantidad, 5)
		self.assertEqual(self.item.cantidad_reservada_inventario, 5)

	def test_selector_picking_detail_includes_overpick_confirmation_copy(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		self.client.force_login(self.selector)

		response = self.client.get(reverse('selector_picking_detail', args=[self.pedido.id]))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'overpickConfirmTemplate')
		self.assertContains(response, 'more than the')
		self.assertContains(response, 'you may pick more than ordered if confirmed')

	def test_selector_picking_detail_uses_tablet_friendly_layout_hooks(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		self.client.force_login(self.selector)

		response = self.client.get(reverse('selector_picking_detail', args=[self.pedido.id]))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'mobile-stack-table--detail')
		self.assertContains(response, 'd-xl-none me-2 sidebar-toggle-btn')
		self.assertContains(response, 'window.innerWidth >= 1200')

	def test_selector_picking_list_renders_in_spanish_when_selected(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		self.client.force_login(self.selector)
		self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = 'es'

		response = self.client.get(reverse('selector_picking_list'), HTTP_ACCEPT_LANGUAGE='es')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, '<title>Tickets de picking asignados</title>', html=False)
		self.assertContains(response, 'Tickets de picking asignados')
		self.assertContains(response, 'Aquí solo se muestran los tickets de picking pendientes asignados a ti.', html=False)
		self.assertContains(response, 'Vistas de picking')
		self.assertContains(response, 'Tickets de picking pendientes')
		self.assertContains(response, 'Cliente')
		self.assertContains(response, 'Estado')
		self.assertContains(response, 'Bloqueo')
		self.assertContains(response, 'Asignado el')
		self.assertContains(response, 'Acción')
		self.assertContains(response, 'Desbloqueado')
		self.assertContains(response, 'Verificar')

	def test_selector_picking_detail_renders_in_spanish_when_selected(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		self.client.force_login(self.selector)
		self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = 'es'

		response = self.client.get(reverse('selector_picking_detail', args=[self.pedido.id]), HTTP_ACCEPT_LANGUAGE='es')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, '<title>Verificación de picking #', html=False)
		self.assertContains(response, 'Verificación de picking - PO #')
		self.assertContains(response, 'Cliente: Cliente Demo', html=False)
		self.assertContains(response, 'Volver')
		self.assertContains(response, 'Estado:')
		self.assertContains(response, 'Asignado el:')
		self.assertContains(response, 'Bloqueo del pedido:')
		self.assertContains(response, 'Desbloqueado')
		self.assertContains(response, 'Cantidades reales por producto')
		self.assertContains(response, 'Producto')
		self.assertContains(response, 'U/M')
		self.assertContains(response, 'QTY ORD')
		self.assertContains(response, 'QTY PICK')
		self.assertContains(response, 'Nota')
		self.assertContains(response, 'Si hay stock fisico disponible, la aprobacion del picker es obligatoria para guardar esta verificacion como desbloqueada.', html=False)
		self.assertContains(response, 'Aprobado por el picker')
		self.assertContains(response, 'Guardar verificación')
		self.assertContains(response, 'CS pedidos')
		self.assertContains(response, 'CS despachados')
		self.assertContains(response, 'CS no enviados')
		self.assertContains(response, 'id="pickerDispatchSummary"', html=False)

	def test_selector_post_verification_redirects_to_assigned_list(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		self.client.force_login(self.selector)

		response = self.client.post(reverse('selector_picking_detail', args=[self.pedido.id]), {
			'cantidad_pallets': '1.5',
			f'cantidad_real_{self.item.id}': '2',
			'nota_seleccionador': 'Todo correcto',
			'nota_seleccionador_resuelta': 'on',
			f'linea_revisada_{self.item.id}': 'on',
		})

		self.assertEqual(response.status_code, 302)
		self.assertEqual(response.url, reverse('selector_picking_list') + '?view=completed')

	def test_selector_can_change_unit_of_measure_during_picking(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		self.client.force_login(self.selector)

		response = self.client.post(reverse('selector_picking_detail', args=[self.pedido.id]), {
			'cantidad_pallets': '1.5',
			f'presentacion_{self.item.id}': str(self.presentacion_unidad.id),
			f'cantidad_real_{self.item.id}': '2',
			'nota_seleccionador': 'Cambio de U/M en picking',
			'nota_seleccionador_resuelta': 'on',
			f'linea_revisada_{self.item.id}': 'on',
		})

		self.assertEqual(response.status_code, 302)
		self.item.refresh_from_db()
		self.pedido.refresh_from_db()
		self.assertEqual(self.item.presentacion, self.presentacion_unidad)
		self.assertEqual(self.item.selector_original_presentacion, self.presentacion)
		self.assertEqual(self.item.cantidad_inventario_aplicada, 2)
		self.assertEqual(self.pedido.estado, 'VERIFICADO_AJUSTADO')

	def test_selector_can_add_product_during_picking(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		self.client.force_login(self.selector)

		response = self.client.post(reverse('selector_picking_detail', args=[self.pedido.id]), {
			'cantidad_pallets': '1.5',
			f'presentacion_{self.item.id}': str(self.presentacion.id),
			f'cantidad_real_{self.item.id}': '2',
			'presentacion_nueva': str(self.presentacion_extra.id),
			'cantidad_nueva': '1',
			'nota_seleccionador': 'Agregado por picker',
			'nota_seleccionador_resuelta': 'on',
			f'linea_revisada_{self.item.id}': 'on',
			'linea_revisada_adicional[]': 'on',
		})

		self.assertEqual(response.status_code, 302)
		nuevo_item = PedidoItem.objects.get(pedido=self.pedido, presentacion=self.presentacion_extra)
		self.assertTrue(nuevo_item.selector_added_by_picker)
		self.assertEqual(nuevo_item.cantidad, 1)
		self.assertEqual(nuevo_item.cantidad_inventario_aplicada, 1)

	def test_picker_added_product_price_uses_qb_price_then_price_3(self):
		Presentacion.objects.filter(pk=self.presentacion_extra.pk).update(
			qb_price=Decimal('99.99'),
			precio_3=Decimal('77.00'),
			precio_1=Decimal('55.00'),
		)
		self.presentacion_extra.refresh_from_db()
		self.assertEqual(_resolve_picker_added_item_price(presentacion=self.presentacion_extra), Decimal('99.99'))

		Presentacion.objects.filter(pk=self.presentacion_extra.pk).update(qb_price=None)
		self.presentacion_extra.refresh_from_db()
		self.assertEqual(_resolve_picker_added_item_price(presentacion=self.presentacion_extra), Decimal('77.00'))

		Presentacion.objects.filter(pk=self.presentacion_extra.pk).update(precio_3=Decimal('0.00'))
		self.presentacion_extra.refresh_from_db()
		self.assertEqual(_resolve_picker_added_item_price(presentacion=self.presentacion_extra), Decimal('55.00'))

	def test_selector_cannot_add_duplicate_product_already_on_order(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		self.client.force_login(self.selector)

		response = self.client.post(reverse('selector_picking_detail', args=[self.pedido.id]), {
			'cantidad_pallets': '1.5',
			f'presentacion_{self.item.id}': str(self.presentacion.id),
			f'cantidad_real_{self.item.id}': '2',
			'presentacion_nueva[]': str(self.presentacion.id),
			'cantidad_nueva[]': '1',
			'nota_seleccionador': 'Intento duplicado',
			'nota_seleccionador_resuelta': 'on',
			f'linea_revisada_{self.item.id}': 'on',
			'linea_revisada_adicional[]': 'on',
		})

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'already on this picking list')
		self.assertEqual(PedidoItem.objects.filter(pedido=self.pedido).count(), 1)

	def test_selector_cannot_add_same_product_with_different_presentation(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		self.client.force_login(self.selector)

		response = self.client.post(reverse('selector_picking_detail', args=[self.pedido.id]), {
			'cantidad_pallets': '1.5',
			f'presentacion_{self.item.id}': str(self.presentacion.id),
			f'cantidad_real_{self.item.id}': '2',
			'presentacion_nueva[]': str(self.presentacion_unidad.id),
			'cantidad_nueva[]': '1',
			'nota_seleccionador': 'Intento duplicado por U/M',
			'nota_seleccionador_resuelta': 'on',
			f'linea_revisada_{self.item.id}': 'on',
			'linea_revisada_adicional[]': 'on',
		})

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'already on this picking list')
		self.assertEqual(PedidoItem.objects.filter(pedido=self.pedido).count(), 1)

	def test_selector_cannot_save_progress_with_duplicate_added_products(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		self.client.force_login(self.selector)

		response = self.client.post(reverse('selector_picking_detail', args=[self.pedido.id]), {
			'submit_action': 'save_progress',
			'cantidad_pallets': '1.5',
			f'presentacion_{self.item.id}': str(self.presentacion.id),
			f'cantidad_real_{self.item.id}': '2',
			'presentacion_nueva[]': [str(self.presentacion_extra.id), str(self.presentacion_extra.id)],
			'cantidad_nueva[]': ['1', '2'],
		})

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'already on this picking list')
		self.pedido.refresh_from_db()
		self.assertFalse(self.pedido.picking_progress)

	def test_backoffice_detail_highlights_picker_um_changes_and_added_products(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		guardar_verificacion_picking(
			pedido=self.pedido,
			seleccionador=self.selector,
			cantidades_reales={self.item.id: 2},
			nota='Cambios del picker',
			nota_resuelta=True,
			presentacion_updates={self.item.id: self.presentacion_unidad.id},
			additional_items=[{'presentacion_id': self.presentacion_extra.id, 'cantidad': 1}],
		)

		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_pedido_detalle', args=[self.pedido.id]))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Rows marked in red were changed by the picker')
		self.assertContains(response, 'Added by picker')
		self.assertContains(response, 'U/M changed by picker')
		self.assertContains(response, 'table-danger')

	def test_backoffice_detail_hides_picker_banner_after_backoffice_quantity_edit(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		guardar_verificacion_picking(
			pedido=self.pedido,
			seleccionador=self.selector,
			cantidades_reales={self.item.id: 2},
			nota='Cantidad ajustada por picker',
			nota_resuelta=True,
		)

		self.client.force_login(self.backoffice)
		response = self.client.post(reverse('backoffice_pedido_detalle', args=[self.pedido.id]), {
			'estado': 'RECIBIDO',
			'nota_backoffice': 'Ajuste final de backoffice',
			f'cantidad_{self.item.id}': '3',
			f'precio_{self.item.id}': '12.00',
		})

		self.assertEqual(response.status_code, 302)

		get_response = self.client.get(reverse('backoffice_pedido_detalle', args=[self.pedido.id]))
		self.assertEqual(get_response.status_code, 200)
		self.assertNotContains(get_response, 'Rows marked in red were changed by the picker')
		self.assertContains(get_response, 'table-danger')

	def test_backoffice_detail_shows_unlock_button_after_stock_shortage(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		StockPresentacion.objects.filter(presentacion=self.presentacion).update(stock_fisico=0, stock_disponible=0)
		guardar_verificacion_picking(
			pedido=self.pedido,
			seleccionador=self.selector,
			cantidades_reales={self.item.id: 2},
			nota='Falta stock fisico',
			nota_resuelta=False,
		)

		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_pedido_detalle', args=[self.pedido.id]))

		self.assertEqual(response.status_code, 200)
		self.assertTrue(response.context['can_unlock_pedido'])
		self.assertContains(response, 'Unlock order')

	def test_backoffice_can_unlock_order_after_reviewing_stock_shortage(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		StockPresentacion.objects.filter(presentacion=self.presentacion).update(stock_fisico=0, stock_disponible=0)
		guardar_verificacion_picking(
			pedido=self.pedido,
			seleccionador=self.selector,
			cantidades_reales={self.item.id: 2},
			nota='Falta stock fisico',
			nota_resuelta=False,
		)
		StockPresentacion.objects.filter(presentacion=self.presentacion).update(stock_fisico=10, stock_disponible=8)

		self.client.force_login(self.backoffice)
		response = self.client.post(reverse('backoffice_resolver_bloqueo_picking', args=[self.pedido.id]))

		self.assertEqual(response.status_code, 302)
		self.pedido.refresh_from_db()
		self.item.refresh_from_db()
		self.assertFalse(self.pedido.picking_bloqueado)
		self.assertTrue(self.pedido.nota_seleccionador_resuelta)
		self.assertEqual(self.item.cantidad_inventario_aplicada, 0)
		stock = StockPresentacion.objects.get(presentacion=self.presentacion)
		self.assertEqual(stock.stock_fisico, 10)

		detail_response = self.client.get(reverse('backoffice_pedido_detalle', args=[self.pedido.id]))
		self.assertContains(detail_response, 'name="metodo_entrega"', html=False)
		self.assertNotContains(detail_response, 'data-stock-shortage="true"', html=False)

	def test_backoffice_can_unlock_order_even_when_inventory_is_unavailable(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		StockPresentacion.objects.filter(presentacion=self.presentacion).update(stock_fisico=0, stock_disponible=0)
		guardar_verificacion_picking(
			pedido=self.pedido,
			seleccionador=self.selector,
			cantidades_reales={self.item.id: 2},
			nota='Falta stock fisico',
			nota_resuelta=False,
		)

		self.client.force_login(self.backoffice)
		response = self.client.post(reverse('backoffice_resolver_bloqueo_picking', args=[self.pedido.id]))

		self.assertEqual(response.status_code, 302)
		self.pedido.refresh_from_db()
		self.assertFalse(self.pedido.picking_bloqueado)
		self.assertEqual(self.item.cantidad_inventario_aplicada, 0)

	def test_backoffice_unlock_after_zeroing_shortage_line_preserves_physical_stock(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		# Report a real shortage first (QI = 0), then restock QI without local deductions.
		StockPresentacion.objects.filter(presentacion=self.presentacion).update(stock_fisico=0, stock_disponible=0)
		guardar_verificacion_picking(
			pedido=self.pedido,
			seleccionador=self.selector,
			cantidades_reales={self.item.id: 2},
			nota='Falta stock fisico',
			nota_resuelta=False,
		)
		StockPresentacion.objects.filter(presentacion=self.presentacion).update(stock_fisico=6, stock_disponible=6)

		self.client.force_login(self.backoffice)
		self.client.post(reverse('backoffice_pedido_detalle', args=[self.pedido.id]), {
			'estado': 'VERIFICADO_AJUSTADO',
			'nota_backoffice': 'No despachar esta linea',
			f'cantidad_{self.item.id}': '0',
			f'precio_{self.item.id}': '12.00',
		})
		self.client.post(reverse('backoffice_resolver_bloqueo_picking', args=[self.pedido.id]))

		self.item.refresh_from_db()
		stock = StockPresentacion.objects.get(presentacion=self.presentacion)
		self.assertEqual(self.item.cantidad, 0)
		self.assertEqual(self.item.cantidad_inventario_aplicada, 0)
		self.assertEqual(stock.stock_fisico, 6)

		detail_response = self.client.get(reverse('backoffice_pedido_detalle', args=[self.pedido.id]))
		self.assertContains(detail_response, 'Available stock: 6 CS')
		self.assertNotContains(detail_response, 'Insufficient stock')
		self.assertNotContains(detail_response, 'data-stock-shortage="true"', html=False)

	def test_resolver_bloqueo_service_rejects_already_unlocked_order(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		guardar_verificacion_picking(
			pedido=self.pedido,
			seleccionador=self.selector,
			cantidades_reales={self.item.id: 2},
			nota='Verificado',
			nota_resuelta=True,
		)

		with self.assertRaises(ValidationError):
			resolver_bloqueo_picking_desde_backoffice(pedido=self.pedido, usuario=self.backoffice)

	def test_evaluar_stock_no_shortage_when_enough_boxes_are_available(self):
		self.presentacion.unidades = 8
		self.presentacion.save(update_fields=['unidades'])
		StockPresentacion.objects.filter(presentacion=self.presentacion).update(
			stock_fisico=32,
			stock_reservado=0,
			stock_disponible=32,
		)
		self.item.cantidad = 20
		self.item.save(update_fields=['cantidad'])

		evaluation = evaluar_stock_fisico_verificacion_picking(
			pedido_items=[self.item],
			cantidades_reales={self.item.id: 20},
		)

		self.assertFalse(evaluation[self.item.id]['has_shortage'])
		self.assertEqual(evaluation[self.item.id]['available_packages'], 32)
		self.assertEqual(evaluation[self.item.id]['shortage_amount'], 0)

	def test_evaluar_stock_counts_reserved_units_for_same_order(self):
		self.presentacion.unidades = 8
		self.presentacion.save(update_fields=['unidades'])
		self.item.cantidad = 20
		self.item.cantidad_reservada_inventario = 20
		self.item.save(update_fields=['cantidad', 'cantidad_reservada_inventario'])
		StockPresentacion.objects.filter(presentacion=self.presentacion).update(
			stock_fisico=32,
			stock_reservado=20,
			stock_disponible=12,
		)

		evaluation = evaluar_stock_fisico_verificacion_picking(
			pedido_items=[self.item],
			cantidades_reales={self.item.id: 20},
		)

		self.assertFalse(evaluation[self.item.id]['has_shortage'])

	def test_evaluar_stock_ignores_stale_available_field_when_physical_is_enough(self):
		StockPresentacion.objects.filter(presentacion=self.presentacion).update(
			stock_fisico=227,
			stock_reservado=0,
			stock_disponible=2,
		)
		self.item.cantidad = 15
		self.item.save(update_fields=['cantidad'])

		evaluation = evaluar_stock_fisico_verificacion_picking(
			pedido_items=[self.item],
			cantidades_reales={self.item.id: 15},
		)

		self.assertFalse(evaluation[self.item.id]['has_shortage'])
		self.assertEqual(evaluation[self.item.id]['available_packages'], 227)
		self.assertEqual(evaluation[self.item.id]['shortage_amount'], 0)

	def test_selector_post_with_stock_error_preserves_typed_quantities_and_note(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		self.client.force_login(self.selector)
		StockPresentacion.objects.filter(presentacion=self.presentacion).update(stock_fisico=0)

		response = self.client.post(reverse('selector_picking_detail', args=[self.pedido.id]), {
			'cantidad_pallets': '1.5',
			f'cantidad_real_{self.item.id}': '2',
			'nota_seleccionador': 'Mantener cantidad digitada',
			'nota_seleccionador_resuelta': 'on',
			f'linea_revisada_{self.item.id}': 'on',
		})

		self.assertEqual(response.status_code, 302)
		self.assertEqual(response.url, reverse('selector_picking_list'))
		self.pedido.refresh_from_db()
		self.item.refresh_from_db()
		self.assertTrue(self.pedido.picking_bloqueado)
		self.assertEqual(self.pedido.nota_seleccionador, 'Mantener cantidad digitada')
		self.assertFalse(self.pedido.nota_seleccionador_resuelta)
		self.assertEqual(self.item.cantidad, 2)

	def test_selector_picking_detail_starts_qty_pick_at_zero(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		self.client.force_login(self.selector)

		response = self.client.get(reverse('selector_picking_detail', args=[self.pedido.id]))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, f'name="cantidad_real_{self.item.id}" value="0"', html=False)
		self.assertContains(response, f'name="linea_revisada_{self.item.id}"', html=False)
		self.assertContains(response, 'Search products by full name', html=False)
		self.assertContains(response, 'Reviewed')
		self.assertContains(response, 'data-requested-quantity="2"', html=False)
		self.assertContains(response, 'Available stock: 10 CS')
		self.assertContains(response, 'text-success')
		self.assertContains(response, 'id="pickerSummaryOrdered"', html=False)
		self.assertContains(response, 'CS ordered')
		self.assertContains(response, 'name="cantidad_pallets"', html=False)
		self.assertContains(response, 'Pallets:')

	def test_picker_catalog_and_labels_use_available_not_quick_inventory(self):
		"""Sales Pending Sync must reduce the picker 'Available stock' label and search JSON."""
		StockPresentacion.objects.filter(presentacion=self.presentacion).update(
			stock_fisico=10,
			stock_reservado=0,
			stock_disponible=10,
		)
		other_pedido = Pedido.objects.create(
			cliente=self.cliente,
			origen='CLIENTE',
			estado='INVOICE_GENERADA',
			total=Decimal('48.00'),
		)
		other_item = PedidoItem.objects.create(
			pedido=other_pedido,
			presentacion=self.presentacion,
			cantidad=4,
			precio=Decimal('12.00'),
			subtotal=Decimal('48.00'),
		)
		invoice = Invoice.objects.create(
			pedido=other_pedido,
			cliente=self.cliente,
			metodo_entrega='CUSTOMER_PICK_UP',
			estado='GENERADA',
			subtotal=Decimal('48.00'),
			total_neto=Decimal('48.00'),
		)
		InvoiceItem.objects.create(
			invoice=invoice,
			pedido_item=other_item,
			presentacion=self.presentacion,
			producto_nombre=self.presentacion.producto.nombre,
			presentacion_nombre=self.presentacion.nombre,
			cantidad_facturada=4,
			precio_unitario=Decimal('12.00'),
			subtotal=Decimal('48.00'),
		)

		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		self.client.force_login(self.selector)
		response = self.client.get(reverse('selector_picking_detail', args=[self.pedido.id]))

		self.assertEqual(response.status_code, 200)
		# Dual-ledger Available = QI 10 - pending sync 4 = 6 (not Quick Inventory 10).
		self.assertContains(response, 'Available stock: 6 CS')
		self.assertNotContains(response, 'Available stock: 10 CS')
		self.assertContains(
			response,
			f'"id": "{self.presentacion.id}"',
			html=False,
		)
		self.assertContains(
			response,
			f'"product_id": "{self.presentacion.producto_id}"',
			html=False,
		)
		self.assertContains(response, '"stock_physical": 6', html=False)

	def test_selector_can_save_and_restore_picking_progress_without_completing(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		self.client.force_login(self.selector)

		response = self.client.post(reverse('selector_picking_detail', args=[self.pedido.id]), {
			'submit_action': 'save_progress',
			'cantidad_pallets': '1.5',
			f'presentacion_{self.item.id}': str(self.presentacion_unidad.id),
			f'cantidad_real_{self.item.id}': '1',
			f'linea_revisada_{self.item.id}': 'on',
			'presentacion_nueva[]': str(self.presentacion_extra.id),
			'cantidad_nueva[]': '3',
			'linea_revisada_adicional[]': 'on',
			'nota_seleccionador': 'Picking parcialmente avanzado',
			'nota_seleccionador_resuelta': 'on',
		})

		self.assertRedirects(response, reverse('selector_picking_detail', args=[self.pedido.id]))
		self.pedido.refresh_from_db()
		self.item.refresh_from_db()
		self.assertEqual(self.pedido.estado, 'PARA_VERIFICAR')
		self.assertIsNone(self.pedido.picking_verificado_en)
		self.assertIsNotNone(self.pedido.picking_progress_saved_at)
		self.assertEqual(self.item.cantidad, 2)
		self.assertEqual(self.item.presentacion, self.presentacion)
		self.assertEqual(self.pedido.picking_progress['quantities'][str(self.item.id)], 1)
		self.assertEqual(self.pedido.picking_progress['additional_items'][0]['cantidad'], 3)

		list_response = self.client.get(reverse('selector_picking_list'))
		self.assertEqual(list_response.status_code, 200)
		self.assertContains(list_response, 'selector-picking-draft-badge')
		self.assertContains(list_response, 'DRAFT')

		detail = self.client.get(reverse('selector_picking_detail', args=[self.pedido.id]))
		self.assertEqual(detail.status_code, 200)
		self.assertContains(detail, 'Saved picking progress was restored.')
		self.assertContains(detail, 'pickerLeaveGuardModal')
		self.assertContains(detail, 'selector_picking_leave_guard.js')
		self.assertContains(detail, 'Continue editing')
		self.assertContains(detail, 'Save progress and leave')
		self.assertContains(detail, f'name="cantidad_real_{self.item.id}" value="1"', html=False)
		self.assertContains(
			detail,
			f'<option value="{self.presentacion_unidad.id}" data-stock-physical="10" selected>',
			html=False,
		)
		self.assertContains(detail, 'Picking parcialmente avanzado')
		self.assertContains(detail, 'value="1.5"', html=False)
		self.assertContains(detail, 'Save progress')

		completed = self.client.post(reverse('selector_picking_detail', args=[self.pedido.id]), {
			'submit_action': 'complete_verification',
			'cantidad_pallets': '1.5',
			f'presentacion_{self.item.id}': str(self.presentacion.id),
			f'cantidad_real_{self.item.id}': '2',
			f'linea_revisada_{self.item.id}': 'on',
			'nota_seleccionador': 'Verificación terminada',
			'nota_seleccionador_resuelta': 'on',
		})
		self.assertEqual(completed.status_code, 302)
		self.pedido.refresh_from_db()
		self.assertEqual(self.pedido.estado, 'VERIFICADO_AJUSTADO')
		self.assertEqual(self.pedido.picking_progress, {})
		self.assertIsNone(self.pedido.picking_progress_saved_at)

		pending_list = self.client.get(reverse('selector_picking_list'))
		self.assertNotContains(pending_list, reverse('selector_picking_detail', args=[self.pedido.id]))

	def test_save_progress_can_redirect_to_safe_next_url(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		self.client.force_login(self.selector)
		list_url = reverse('selector_picking_list')

		response = self.client.post(reverse('selector_picking_detail', args=[self.pedido.id]), {
			'submit_action': 'save_progress',
			'next': list_url,
			'cantidad_pallets': '1',
			f'presentacion_{self.item.id}': str(self.presentacion.id),
			f'cantidad_real_{self.item.id}': '1',
			f'linea_revisada_{self.item.id}': 'on',
		})

		self.assertRedirects(response, list_url)
		self.pedido.refresh_from_db()
		self.assertIsNotNone(self.pedido.picking_progress_saved_at)
		self.assertEqual(self.pedido.picking_progress['quantities'][str(self.item.id)], 1)

	def test_save_progress_ajax_returns_json_without_redirect(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		self.client.force_login(self.selector)

		response = self.client.post(
			reverse('selector_picking_detail', args=[self.pedido.id]),
			{
				'submit_action': 'save_progress',
				'ajax': '1',
				'cantidad_pallets': '1',
				f'presentacion_{self.item.id}': str(self.presentacion.id),
				f'cantidad_real_{self.item.id}': '1',
				f'linea_revisada_{self.item.id}': 'on',
			},
			HTTP_X_REQUESTED_WITH='XMLHttpRequest',
		)

		self.assertEqual(response.status_code, 200)
		payload = response.json()
		self.assertTrue(payload['ok'])
		self.assertIn('saved_at', payload)
		self.pedido.refresh_from_db()
		self.assertIsNotNone(self.pedido.picking_progress_saved_at)

	def test_save_progress_does_not_duplicate_additional_items_on_second_save(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		self.client.force_login(self.selector)

		first_save = self.client.post(reverse('selector_picking_detail', args=[self.pedido.id]), {
			'submit_action': 'save_progress',
			'cantidad_pallets': '1',
			f'presentacion_{self.item.id}': str(self.presentacion.id),
			f'cantidad_real_{self.item.id}': '1',
			f'linea_revisada_{self.item.id}': 'on',
			'presentacion_nueva[]': str(self.presentacion_extra.id),
			'cantidad_nueva[]': '1',
			'linea_revisada_adicional[]': 'on',
		})
		self.assertRedirects(first_save, reverse('selector_picking_detail', args=[self.pedido.id]))
		self.pedido.refresh_from_db()
		self.assertEqual(len(self.pedido.picking_progress.get('additional_items') or []), 1)

		# Second save simulates the restored form posting the same added product again
		# (plus Reviewed checked), which previously appended onto the draft and duplicated.
		second_save = self.client.post(reverse('selector_picking_detail', args=[self.pedido.id]), {
			'submit_action': 'save_progress',
			'cantidad_pallets': '1',
			f'presentacion_{self.item.id}': str(self.presentacion.id),
			f'cantidad_real_{self.item.id}': '1',
			f'linea_revisada_{self.item.id}': 'on',
			'presentacion_nueva[]': str(self.presentacion_extra.id),
			'cantidad_nueva[]': '1',
			'linea_revisada_adicional[]': 'on',
		})
		self.assertRedirects(second_save, reverse('selector_picking_detail', args=[self.pedido.id]))
		self.pedido.refresh_from_db()
		additional_items = self.pedido.picking_progress.get('additional_items') or []
		self.assertEqual(len(additional_items), 1)
		self.assertEqual(str(additional_items[0]['presentacion_id']), str(self.presentacion_extra.id))
		self.assertTrue(additional_items[0].get('reviewed'))

	def test_selector_picking_products_are_sorted_by_case_weight_descending(self):
		self.presentacion.peso_por_caja = Decimal('5.000')
		self.presentacion.save(update_fields=['peso_por_caja'])
		self.presentacion_extra.peso_por_caja = Decimal('20.000')
		self.presentacion_extra.save(update_fields=['peso_por_caja'])
		heavy_item = PedidoItem.objects.create(
			pedido=self.pedido,
			presentacion=self.presentacion_extra,
			cantidad_solicitada=1,
			cantidad_reservada_inventario=1,
			cantidad=1,
			precio=Decimal('6.00'),
			subtotal=Decimal('6.00'),
		)
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		self.client.force_login(self.selector)

		response = self.client.get(reverse('selector_picking_detail', args=[self.pedido.id]))

		self.assertEqual(response.status_code, 200)
		self.assertEqual(
			[row['id'] for row in response.context['item_rows']],
			[heavy_item.id, self.item.id],
		)

	def test_selector_save_requires_and_persists_pallets_quantity(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		self.client.force_login(self.selector)

		missing = self.client.post(reverse('selector_picking_detail', args=[self.pedido.id]), {
			f'cantidad_real_{self.item.id}': '2',
			f'linea_revisada_{self.item.id}': 'on',
			'nota_seleccionador': 'Todo correcto',
			'nota_seleccionador_resuelta': 'on',
		})
		self.assertEqual(missing.status_code, 200)
		self.assertContains(missing, 'Pallets quantity is required', html=False)
		self.pedido.refresh_from_db()
		self.assertIsNone(self.pedido.cantidad_pallets)

		ok = self.client.post(reverse('selector_picking_detail', args=[self.pedido.id]), {
			'cantidad_pallets': '2.5',
			f'cantidad_real_{self.item.id}': '2',
			f'linea_revisada_{self.item.id}': 'on',
			'nota_seleccionador': 'Todo correcto',
			'nota_seleccionador_resuelta': 'on',
		})
		self.assertEqual(ok.status_code, 302)
		self.pedido.refresh_from_db()
		self.assertEqual(self.pedido.cantidad_pallets, Decimal('2.50'))
		self.assertEqual(self.pedido.estado, 'VERIFICADO_AJUSTADO')

	def test_selector_must_review_every_line_before_saving(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		self.client.force_login(self.selector)

		response = self.client.post(reverse('selector_picking_detail', args=[self.pedido.id]), {
			'cantidad_pallets': '1.5',
			f'cantidad_real_{self.item.id}': '2',
			'nota_seleccionador': 'Todo correcto',
			'nota_seleccionador_resuelta': 'on',
		})

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Check every product line in the Reviewed column', html=False)
		self.pedido.refresh_from_db()
		self.assertNotEqual(self.pedido.estado, 'VERIFICADO_AJUSTADO')

	def test_selector_reedit_only_requires_review_for_changed_lines(self):
		second_item = PedidoItem.objects.create(
			pedido=self.pedido,
			presentacion=self.presentacion_extra,
			cantidad_solicitada=1,
			cantidad_reservada_inventario=1,
			cantidad=1,
			precio=Decimal('6.00'),
			subtotal=Decimal('6.00'),
		)
		extra_stock = StockPresentacion.objects.get(presentacion=self.presentacion_extra)
		extra_stock.stock_reservado += 1
		extra_stock.stock_disponible = extra_stock.stock_fisico - extra_stock.stock_reservado
		extra_stock.save(update_fields=['stock_reservado', 'stock_disponible', 'actualizado_en'])
		self.pedido.total = Decimal('30.00')
		self.pedido.save(update_fields=['total'])

		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		guardar_verificacion_picking(
			pedido=self.pedido,
			seleccionador=self.selector,
			cantidades_reales={self.item.id: 2, second_item.id: 1},
			nota='Primera verifica',
			nota_resuelta=True,
		)
		self.client.force_login(self.selector)

		missing_changed_review = self.client.post(reverse('selector_picking_detail', args=[self.pedido.id]), {
			'cantidad_pallets': '1.5',
			f'presentacion_{self.item.id}': str(self.presentacion.id),
			f'cantidad_real_{self.item.id}': '1',
			f'presentacion_{second_item.id}': str(self.presentacion_extra.id),
			f'cantidad_real_{second_item.id}': '1',
			'nota_seleccionador': 'Ajuste un item',
			'nota_seleccionador_resuelta': 'on',
		})
		self.assertEqual(missing_changed_review.status_code, 200)
		self.assertContains(
			missing_changed_review,
			'Check Reviewed only for each line you changed or added before saving.',
			html=False,
		)

		response = self.client.post(reverse('selector_picking_detail', args=[self.pedido.id]), {
			'cantidad_pallets': '1.5',
			f'presentacion_{self.item.id}': str(self.presentacion.id),
			f'cantidad_real_{self.item.id}': '1',
			f'presentacion_{second_item.id}': str(self.presentacion_extra.id),
			f'cantidad_real_{second_item.id}': '1',
			'nota_seleccionador': 'Ajuste un item',
			'nota_seleccionador_resuelta': 'on',
			f'linea_revisada_{self.item.id}': 'on',
		})

		self.assertEqual(response.status_code, 302)
		self.item.refresh_from_db()
		second_item.refresh_from_db()
		self.assertEqual(self.item.cantidad, 1)
		self.assertEqual(second_item.cantidad, 1)

	def test_selector_detail_disables_picker_approval_when_physical_stock_is_insufficient(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		self.client.force_login(self.selector)
		StockPresentacion.objects.filter(presentacion=self.presentacion).update(stock_fisico=0)

		response = self.client.get(reverse('selector_picking_detail', args=[self.pedido.id]))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Available stock: 0 CS')
		self.assertContains(response, 'text-danger')
		self.assertContains(response, 'name="nota_seleccionador_resuelta"', html=False)
		self.assertNotContains(response, 'disabled>', html=False)
		self.assertContains(response, 'badge bg-success', html=False)

		response = self.client.post(reverse('selector_picking_detail', args=[self.pedido.id]), {
			'cantidad_pallets': '1.5',
			f'cantidad_real_{self.item.id}': '2',
			'nota_seleccionador': 'Sin stock fisico',
			f'linea_revisada_{self.item.id}': 'on',
		})

		self.assertEqual(response.status_code, 302)
		self.pedido.refresh_from_db()
		self.assertTrue(self.pedido.picking_bloqueado)

	def test_selector_can_save_zero_quantity_when_item_will_not_ship(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		self.client.force_login(self.selector)
		StockPresentacion.objects.filter(presentacion=self.presentacion).update(stock_fisico=0)

		response = self.client.post(reverse('selector_picking_detail', args=[self.pedido.id]), {
			'cantidad_pallets': '1.5',
			f'cantidad_real_{self.item.id}': '0',
			'nota_seleccionador': '',
			'nota_seleccionador_resuelta': 'on',
			f'linea_revisada_{self.item.id}': 'on',
		})

		self.assertEqual(response.status_code, 302)
		self.assertEqual(response.url, reverse('selector_picking_list'))
		self.pedido.refresh_from_db()
		self.item.refresh_from_db()
		self.assertEqual(self.item.cantidad, 0)
		self.assertEqual(self.item.subtotal, Decimal('0.00'))
		self.assertEqual(self.item.cantidad_inventario_aplicada, 0)
		self.assertFalse(self.pedido.picking_bloqueado)
		self.assertEqual(self.pedido.total, Decimal('0.00'))

	def test_picking_ticket_items_are_sorted_alphabetically(self):
		categoria = Categoria.objects.get(nombre='Categoria test')
		marca = Marca.objects.get(nombre='Marca test')
		producto_z = Producto.objects.create(nombre='Zulu Product', categoria=categoria, marca=marca, activo=True)
		producto_a = Producto.objects.create(nombre='Alpha Product', categoria=categoria, marca=marca, activo=True)
		presentacion_z = Presentacion.objects.create(
			producto=producto_z,
			nombre='Caja',
			unidades=1,
			tipo_contenido='caja',
			costo=Decimal('10.00'),
			precio_1=Decimal('12.00'),
		)
		presentacion_a = Presentacion.objects.create(
			producto=producto_a,
			nombre='Caja',
			unidades=1,
			tipo_contenido='caja',
			costo=Decimal('10.00'),
			precio_1=Decimal('12.00'),
		)
		registrar_entrada_manual(presentacion=presentacion_z, cantidad=5, observacion='Z stock')
		registrar_entrada_manual(presentacion=presentacion_a, cantidad=5, observacion='A stock')
		PedidoItem.objects.create(
			pedido=self.pedido,
			presentacion=presentacion_z,
			cantidad_solicitada=1,
			cantidad=1,
			precio=Decimal('12.00'),
			subtotal=Decimal('12.00'),
		)
		PedidoItem.objects.create(
			pedido=self.pedido,
			presentacion=presentacion_a,
			cantidad_solicitada=1,
			cantidad=1,
			precio=Decimal('12.00'),
			subtotal=Decimal('12.00'),
		)

		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_picking_ticket', args=[self.pedido.id]))
		content = response.content.decode()

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Date')
		self.assertContains(response, 'Received date')
		self.assertContains(response, format_local_datetime(self.pedido.creada_en))
		self.assertLess(content.index('Alpha Product'), content.index('Producto test'))
		self.assertLess(content.index('Producto test'), content.index('Zulu Product'))

		pdf_response = self.client.get(reverse('backoffice_picking_pdf', args=[self.pedido.id]))
		self.assertEqual(pdf_response.status_code, 200)
		self.assertEqual(pdf_response['Content-Type'], 'application/pdf')
		self.assertGreater(len(pdf_response.content), 500)
		from config.core.datetime_formats import format_local_date
		expected_date = format_local_date(self.pedido.creada_en)
		self.assertTrue(expected_date)
		# HTML ticket already asserts Date; PDF must be a non-empty branded document.
		self.assertIn(b'%PDF', pdf_response.content[:8])

	def test_backoffice_detail_can_add_multiple_products_in_one_save(self):
		self.client.force_login(self.backoffice)
		response = self.client.post(reverse('backoffice_pedido_detalle', args=[self.pedido.id]), {
			'estado': self.pedido.estado,
			'nota_backoffice': 'Multi add',
			f'cantidad_{self.item.id}': str(self.item.cantidad),
			f'precio_{self.item.id}': str(self.item.precio),
			'presentacion_nueva[]': [str(self.presentacion_unidad.id), str(self.presentacion_extra.id)],
			'cantidad_nueva[]': ['2', '3'],
			'precio_nuevo[]': ['3.50', '6.00'],
		})

		self.assertEqual(response.status_code, 302)
		self.assertTrue(
			PedidoItem.objects.filter(
				pedido=self.pedido,
				presentacion=self.presentacion_unidad,
				cantidad=2,
				precio=Decimal('3.50'),
			).exists()
		)
		self.assertTrue(
			PedidoItem.objects.filter(
				pedido=self.pedido,
				presentacion=self.presentacion_extra,
				cantidad=3,
				precio=Decimal('6.00'),
			).exists()
		)

	def test_backoffice_detail_shows_add_product_button(self):
		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_pedido_detalle', args=[self.pedido.id]))
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'id="addPendingProductBtn"', html=False)
		self.assertContains(response, 'id="pedidoItemsTableBody"', html=False)
		self.assertContains(response, 'Use Add to queue several products first')

	def test_backoffice_cannot_move_blocked_order_forward(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		StockPresentacion.objects.filter(presentacion=self.presentacion).update(stock_fisico=0)
		guardar_verificacion_picking(
			pedido=self.pedido,
			seleccionador=self.selector,
			cantidades_reales={self.item.id: 2},
			nota='Revisar diferencia de inventario.',
			nota_resuelta=False,
		)

		self.client.force_login(self.backoffice)
		response = self.client.post(reverse('backoffice_pedido_detalle', args=[self.pedido.id]), {
			'estado': 'DESPACHADO',
			'nota_backoffice': 'Intento de despacho',
		})

		self.assertEqual(response.status_code, 302)
		self.pedido.refresh_from_db()
		self.assertEqual(self.pedido.estado, 'VERIFICADO_AJUSTADO')

	def test_backoffice_detail_shows_explicit_picker_shortage_alert(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		StockPresentacion.objects.filter(presentacion=self.presentacion).update(stock_fisico=0)
		guardar_verificacion_picking(
			pedido=self.pedido,
			seleccionador=self.selector,
			cantidades_reales={self.item.id: 2},
			nota='Sin stock fisico en bodega.',
			nota_resuelta=False,
		)

		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_pedido_detalle', args=[self.pedido.id]))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Picker reported physical stock shortage')
		self.assertContains(response, 'This order stays blocked until BackOffice reviews the shortage reported during picking.')
		self.assertContains(response, 'BackOffice action required: the picker reported insufficient physical stock for one or more items.')
		self.assertContains(response, 'Insufficient stock')
		self.assertContains(response, 'Set Quantity to 0 on those lines before unlocking the order.')
		self.assertContains(response, f'data-pedido-item-row="{self.item.id}"', html=False)
		self.assertContains(response, 'data-stock-shortage="true"', html=False)

	def test_backoffice_detail_shows_suggested_resale_inputs_by_percentage_and_value(self):
		self.pedido.estado = 'VERIFICADO_AJUSTADO'
		self.pedido.save(update_fields=['estado', 'actualizada_en'])
		self.client.force_login(self.backoffice)

		response = self.client.get(reverse('backoffice_pedido_detalle', args=[self.pedido.id]))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Customer unit cost')
		self.assertContains(response, f'name="suggested_margin_percentage_{self.item.id}"', html=False)
		self.assertContains(response, f'name="suggested_unit_price_{self.item.id}"', html=False)
		self.assertContains(response, 'Profit %')
		self.assertContains(response, 'value="30.00"', html=False)

	def test_backoffice_detail_renders_presentation_options_for_each_item(self):
		self.pedido.estado = 'VERIFICADO_AJUSTADO'
		self.pedido.save(update_fields=['estado', 'actualizada_en'])
		self.client.force_login(self.backoffice)

		response = self.client.get(reverse('backoffice_pedido_detalle', args=[self.pedido.id]))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, f'name="presentacion_{self.item.id}"', html=False)
		self.assertContains(response, f'<option value="{self.presentacion.id}" selected>{self.presentacion.nombre}</option>', html=False)
		self.assertContains(response, f'value="{self.presentacion_unidad.id}"', html=False)
		self.assertContains(response, f'>{self.presentacion_unidad.nombre}</option>', html=False)

	def test_backoffice_detail_includes_searchable_select_assets(self):
		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_pedido_detalle', args=[self.pedido.id]))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'tom-select.complete.min.js')
		self.assertContains(response, 'searchable-selects.js')
		self.assertContains(response, 'buscadorProductoPedido')
		self.assertContains(response, 'pedido_detalle_product_search.js')
		self.assertContains(response, 'id="precioNuevoPedido"', html=False)
		self.assertContains(response, 'id="precioNuevoPedidoPreset"', html=False)
		self.assertContains(response, 'name="precio_nuevo"', html=False)
		self.assertContains(response, 'name="presentacion_nueva"', html=False)
		self.assertContains(response, 'bulkPriceTierSelect')
		self.assertContains(response, 'applyBulkPriceTierButton')
		self.assertContains(response, 'Apply to all products')
		self.assertContains(response, 'bulkDiscountPresetSelect')
		self.assertContains(response, 'applyBulkDiscountButton')
		self.assertContains(response, 'Apply discount to all products')
		self.assertContains(response, 'pedido-item-price-preset')
		self.assertContains(response, 'pedido-item-discount-preset')
		self.assertContains(response, 'configurar-descuentos')
		self.assertContains(response, 'pedido-presentation-price-map')

	def _create_pedido_customer_invoice(self, *, created_at, quantity, price):
		sale_date = timezone.localtime(created_at).date()
		pedido = Pedido.objects.create(
			cliente=self.cliente,
			vendedor=self.backoffice,
			origen='VENDEDOR',
			estado='INVOICE_GENERADA',
			total=Decimal(str(price)) * Decimal(str(quantity)),
		)
		Pedido.objects.filter(id=pedido.id).update(creada_en=created_at, actualizada_en=created_at)
		pedido_item = PedidoItem.objects.create(
			pedido=pedido,
			presentacion=self.presentacion,
			cantidad_solicitada=quantity,
			cantidad=quantity,
			precio=Decimal(str(price)),
			subtotal=Decimal(str(price)) * Decimal(str(quantity)),
		)
		invoice = Invoice.objects.create(
			pedido=pedido,
			cliente=self.cliente,
			metodo_entrega='CUSTOMER_PICK_UP',
			estado='GENERADA',
			subtotal=Decimal(str(price)) * Decimal(str(quantity)),
			total_neto=Decimal(str(price)) * Decimal(str(quantity)),
			fecha_documento=sale_date,
		)
		Invoice.objects.filter(id=invoice.id).update(creada_en=created_at, actualizada_en=created_at)
		InvoiceItem.objects.create(
			invoice=invoice,
			pedido_item=pedido_item,
			presentacion=self.presentacion,
			producto_nombre=self.presentacion.producto.nombre,
			presentacion_nombre=self.presentacion.nombre,
			cantidad_facturada=quantity,
			precio_unitario=Decimal(str(price)),
			subtotal=Decimal(str(price)) * Decimal(str(quantity)),
		)
		return invoice

	def test_backoffice_detail_shows_only_last_two_invoice_sale_prices(self):
		now = timezone.now()
		self._create_pedido_customer_invoice(created_at=now - timedelta(days=1), quantity=5, price='37.00')
		self._create_pedido_customer_invoice(created_at=now - timedelta(days=8), quantity=2, price='36.50')
		self._create_pedido_customer_invoice(created_at=now - timedelta(days=15), quantity=4, price='35.75')
		self.presentacion.qb_price = Decimal('42.99')
		self.presentacion.save(update_fields=['qb_price'])

		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_pedido_detalle', args=[self.pedido.id]))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, '37.00')
		self.assertContains(response, '36.50')
		self.assertNotContains(response, '35.75')
		self.assertContains(response, 'data-price-key="invoice_sale_1"', html=False)
		self.assertContains(response, 'data-price-key="invoice_sale_2"', html=False)
		self.assertContains(response, 'Most recent sale price')
		self.assertContains(response, 'data-price-key="precio_1"', html=False)
		self.assertContains(response, 'data-price-key="precio_5"', html=False)
		self.assertContains(response, 'data-price-key="qb_price"', html=False)
		self.assertContains(response, 'QB-PRICE')
		self.assertContains(response, 'Assign one price to all products')

	def test_resolve_invoice_sale_reference_date_uses_fecha_documento(self):
		sale_date = timezone.localdate() - timedelta(days=3)
		invoice = Invoice(fecha_documento=sale_date, creada_en=timezone.now())
		self.assertEqual(resolve_invoice_sale_reference_date(invoice), sale_date)

	def test_backoffice_search_presentaciones_returns_matching_products(self):
		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_buscar_presentaciones'), {
			'q': 'Producto test',
			'pedido_id': self.pedido.id,
		})

		self.assertEqual(response.status_code, 200)
		payload = response.json()
		self.assertTrue(any(result['id'] == self.presentacion.id for result in payload['results']))
		self.assertTrue(any('Producto test' in result['label'] for result in payload['results']))
		matched = next(result for result in payload['results'] if result['id'] == self.presentacion.id)
		self.presentacion.refresh_from_db()
		self.assertEqual(len(matched['prices']), 5)
		self.assertEqual(matched['prices'][0]['key'], 'precio_1')
		self.assertEqual(matched['prices'][0]['value'], format(self.presentacion.precio_1, '.2f'))
		self.assertIn('default_price_key', matched)
		self.assertEqual(matched['default_price_key'], 'precio_1')
		self.assertEqual(matched['cost'], '10.00')

		self.presentacion.qb_price = Decimal('42.99')
		self.presentacion.save(update_fields=['qb_price'])
		response = self.client.get(reverse('backoffice_buscar_presentaciones'), {
			'q': 'Producto test',
			'pedido_id': self.pedido.id,
		})
		matched = next(result for result in response.json()['results'] if result['id'] == self.presentacion.id)
		self.assertEqual(len(matched['prices']), 6)
		self.assertEqual(matched['prices'][-1]['key'], 'qb_price')
		self.assertEqual(matched['prices'][-1]['value'], '42.99')

	def test_backoffice_search_uses_live_tier_prices_when_stored_prices_are_stale(self):
		from config.productos.models import ConfiguracionLandedCost, ConfiguracionPrecios

		configuracion = ConfiguracionPrecios.obtener()
		configuracion.porcentaje_1 = Decimal('12')
		configuracion.porcentaje_2 = Decimal('15')
		configuracion.porcentaje_3 = Decimal('20')
		configuracion.porcentaje_4 = Decimal('25')
		configuracion.porcentaje_5 = Decimal('30')
		configuracion.save()
		landed = ConfiguracionLandedCost.obtener()
		landed.valor = Decimal('0.00')
		landed.save(update_fields=['valor'])

		# Simulate Sabriton-style case cost with old unit-era Price 1-5 still in DB.
		Presentacion.objects.filter(pk=self.presentacion.pk).update(
			costo=Decimal('21.99'),
			qb_price=Decimal('44.99'),
			precio_1=Decimal('1.59'),
			precio_2=Decimal('1.65'),
			precio_3=Decimal('1.75'),
			precio_4=Decimal('1.87'),
			precio_5=Decimal('2.00'),
		)

		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_buscar_presentaciones'), {
			'q': 'Producto test',
			'pedido_id': self.pedido.id,
		})
		matched = next(result for result in response.json()['results'] if result['id'] == self.presentacion.id)
		price_by_key = {option['key']: option['value'] for option in matched['prices']}

		self.assertEqual(price_by_key['precio_1'], '24.99')
		self.assertEqual(price_by_key['qb_price'], '44.99')
		self.assertNotEqual(price_by_key['precio_1'], '1.59')

		self.presentacion.refresh_from_db()
		self.assertEqual(self.presentacion.precio_1, Decimal('24.99'))

	def test_backoffice_detail_shows_real_cost_for_backoffice_and_admin(self):
		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_pedido_detalle', args=[self.pedido.id]))
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'pedido-item-real-cost')
		self.assertContains(response, 'Real cost: $10.00')
		self.assertContains(response, 'pedido-presentation-cost-map')
		self.assertContains(response, 'data-show-cost="true"', html=False)

		admin_user = Usuario.objects.create_user(username='admin-cost', password='secret123', role='admin')
		self.client.force_login(admin_user)
		response = self.client.get(reverse('backoffice_pedido_detalle', args=[self.pedido.id]))
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Real cost: $10.00')

	def test_backoffice_detail_hides_real_cost_for_non_backoffice_roles(self):
		vendedor = Usuario.objects.create_user(
			username='vendor-cost',
			password='secret123',
			role='vendedor',
			permission_overrides={'backoffice.orders.view': True, 'backoffice.orders.manage': True},
		)
		self.client.force_login(vendedor)
		response = self.client.get(reverse('backoffice_pedido_detalle', args=[self.pedido.id]))
		self.assertEqual(response.status_code, 200)
		self.assertNotContains(response, 'Real cost: $10.00')
		self.assertNotContains(response, 'pedido-presentation-cost-map')
		self.assertContains(response, 'data-show-cost="false"', html=False)

	def test_backoffice_search_hides_cost_for_non_backoffice_roles(self):
		vendedor = Usuario.objects.create_user(
			username='vendor-search-cost',
			password='secret123',
			role='vendedor',
			permission_overrides={'backoffice.orders.view': True},
		)
		self.client.force_login(vendedor)
		response = self.client.get(reverse('backoffice_buscar_presentaciones'), {
			'q': 'Producto test',
			'pedido_id': self.pedido.id,
		})
		self.assertEqual(response.status_code, 200)
		matched = next(result for result in response.json()['results'] if result['id'] == self.presentacion.id)
		self.assertNotIn('cost', matched)

	def test_invoice_manage_without_orders_view_can_search_presentaciones(self):
		driver = Usuario.objects.create_user(
			username='driver-invoice-search',
			password='secret123',
			role='driver',
			permission_overrides={
				'backoffice.invoices.view': True,
				'backoffice.invoices.manage': True,
				'backoffice.orders.view': False,
			},
		)
		self.client.force_login(driver)
		response = self.client.get(reverse('backoffice_buscar_presentaciones'), {
			'q': 'Producto test',
			'cliente_id': str(self.cliente.id),
		})
		self.assertEqual(response.status_code, 200)
		payload = response.json()
		matched = next(result for result in payload['results'] if result['id'] == self.presentacion.id)
		self.assertTrue(matched['prices'])
		self.assertNotIn('cost', matched)

	def test_searchable_selects_script_uses_dropdown_input_plugin(self):
		from pathlib import Path

		js_path = Path(settings.BASE_DIR) / 'static' / 'js' / 'searchable-selects.js'
		content = js_path.read_text(encoding='utf-8')
		self.assertIn('dropdown_input', content)
		self.assertIn('buildSubstringScoreFunction', content)

	def test_backoffice_can_add_product_with_manual_price(self):
		self.pedido.estado = 'RECIBIDO'
		self.pedido.save(update_fields=['estado', 'actualizada_en'])

		self.client.force_login(self.backoffice)
		response = self.client.post(reverse('backoffice_pedido_detalle', args=[self.pedido.id]), {
			'estado': 'RECIBIDO',
			'presentacion_nueva': str(self.presentacion_extra.id),
			'cantidad_nueva': '2',
			'precio_nuevo': '7.25',
			f'cantidad_{self.item.id}': '2',
			f'precio_{self.item.id}': '12.00',
		})

		self.assertEqual(response.status_code, 302)
		nuevo_item = PedidoItem.objects.get(pedido=self.pedido, presentacion=self.presentacion_extra)
		self.assertEqual(nuevo_item.precio, Decimal('7.25'))
		self.assertEqual(nuevo_item.cantidad, 2)

	def test_backoffice_can_add_product_without_available_stock(self):
		self.pedido.estado = 'RECIBIDO'
		self.pedido.save(update_fields=['estado', 'actualizada_en'])
		StockPresentacion.objects.filter(presentacion=self.presentacion_extra).update(
			stock_fisico=0,
			stock_reservado=0,
			stock_disponible=0,
		)

		self.client.force_login(self.backoffice)
		response = self.client.post(reverse('backoffice_pedido_detalle', args=[self.pedido.id]), {
			'estado': 'RECIBIDO',
			'presentacion_nueva': str(self.presentacion_extra.id),
			'cantidad_nueva': '3',
			'precio_nuevo': '6.00',
			f'cantidad_{self.item.id}': '2',
			f'precio_{self.item.id}': '12.00',
		})

		self.assertEqual(response.status_code, 302)
		nuevo_item = PedidoItem.objects.get(pedido=self.pedido, presentacion=self.presentacion_extra)
		self.assertEqual(nuevo_item.cantidad, 3)
		self.assertEqual(nuevo_item.cantidad_reservada_inventario, 0)
		self.pedido.refresh_from_db()
		self.assertEqual(self.pedido.total, Decimal('42.00'))

	def test_void_pedido_does_not_change_stock(self):
		stock = StockPresentacion.objects.get(presentacion=self.presentacion)
		stock_before = (stock.stock_fisico, stock.stock_reservado, stock.stock_disponible)
		self.pedido.estado = 'RECIBIDO'
		self.pedido.save(update_fields=['estado', 'actualizada_en'])

		self.client.force_login(self.backoffice)
		response = self.client.post(reverse('backoffice_pedido_void', args=[self.pedido.id]))

		self.assertEqual(response.status_code, 302)
		self.pedido.refresh_from_db()
		self.assertEqual(self.pedido.estado, 'CANCELADO')
		stock.refresh_from_db()
		self.assertEqual((stock.stock_fisico, stock.stock_reservado, stock.stock_disponible), stock_before)

	def test_pedido_detail_shows_send_quote_to_customer_controls(self):
		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_pedido_detalle', args=[self.pedido.id]))
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Send quote to customer')
		self.assertContains(response, 'Send email with prices')
		self.assertContains(response, reverse('backoffice_enviar_pedido_cliente', args=[self.pedido.id]))

	@patch('config.pedidos.views.notificar_cliente_pedido')
	def test_backoffice_can_send_order_quote_email_with_and_without_prices(self, mock_notify):
		mock_notify.return_value = True
		self.client.force_login(self.backoffice)

		with_prices = self.client.post(
			reverse('backoffice_enviar_pedido_cliente', args=[self.pedido.id]),
			{'enviar_correo_con_precios': '1'},
		)
		self.assertEqual(with_prices.status_code, 302)
		mock_notify.assert_called_with(self.pedido, include_prices=True)

		without_prices = self.client.post(
			reverse('backoffice_enviar_pedido_cliente', args=[self.pedido.id]),
			{'enviar_correo_con_precios': '0'},
		)
		self.assertEqual(without_prices.status_code, 302)
		mock_notify.assert_called_with(self.pedido, include_prices=False)

	def test_delete_pedido_removes_record_without_changing_stock(self):
		stock = StockPresentacion.objects.get(presentacion=self.presentacion)
		stock_before = (stock.stock_fisico, stock.stock_reservado, stock.stock_disponible)
		pedido_id = self.pedido.id
		self.item.cantidad_inventario_aplicada = 0
		self.item.cantidad_reservada_inventario = 0
		self.item.save(update_fields=['cantidad_inventario_aplicada', 'cantidad_reservada_inventario'])

		self.client.force_login(self.backoffice)
		response = self.client.post(reverse('backoffice_pedido_delete', args=[pedido_id]))

		self.assertEqual(response.status_code, 302)
		self.assertFalse(Pedido.objects.filter(id=pedido_id).exists())
		stock.refresh_from_db()
		self.assertEqual((stock.stock_fisico, stock.stock_reservado, stock.stock_disponible), stock_before)

	def test_backoffice_can_edit_quantities_after_verified_picking(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		guardar_verificacion_picking(
			pedido=self.pedido,
			seleccionador=self.selector,
			cantidades_reales={self.item.id: 2},
			nota='Verificado',
			nota_resuelta=True,
		)

		self.client.force_login(self.backoffice)
		response = self.client.post(reverse('backoffice_pedido_detalle', args=[self.pedido.id]), {
			'estado': 'VERIFICADO_AJUSTADO',
			'nota_backoffice': 'Ajuste manual posterior',
			f'cantidad_{self.item.id}': '1',
			f'precio_{self.item.id}': '12.00',
		})

		self.assertEqual(response.status_code, 302)
		self.item.refresh_from_db()
		self.pedido.refresh_from_db()
		self.assertEqual(self.item.cantidad, 1)
		self.assertEqual(self.item.cantidad_solicitada, 2)
		self.assertEqual(self.item.cantidad_inventario_aplicada, 1)
		self.assertEqual(self.pedido.total, Decimal('12.00'))

	def test_backoffice_can_set_quantity_to_zero_before_invoice_generation(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		guardar_verificacion_picking(
			pedido=self.pedido,
			seleccionador=self.selector,
			cantidades_reales={self.item.id: 2},
			nota='Verificado',
			nota_resuelta=True,
		)

		self.client.force_login(self.backoffice)
		response = self.client.post(reverse('backoffice_pedido_detalle', args=[self.pedido.id]), {
			'estado': 'VERIFICADO_AJUSTADO',
			'nota_backoffice': 'No despachar esta linea',
			f'cantidad_{self.item.id}': '0',
			f'precio_{self.item.id}': '12.00',
		})

		self.assertEqual(response.status_code, 302)
		self.item.refresh_from_db()
		self.pedido.refresh_from_db()
		self.assertEqual(self.item.cantidad, 0)
		self.assertEqual(self.item.cantidad_solicitada, 2)
		self.assertEqual(self.item.cantidad_inventario_aplicada, 0)
		self.assertEqual(self.item.subtotal, Decimal('0.00'))
		self.assertEqual(self.pedido.total, Decimal('0.00'))

	def test_backoffice_zero_quantity_preserves_requested_qty_after_stock_shortage(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		StockPresentacion.objects.filter(presentacion=self.presentacion).update(stock_fisico=0, stock_disponible=0)
		guardar_verificacion_picking(
			pedido=self.pedido,
			seleccionador=self.selector,
			cantidades_reales={self.item.id: 2},
			nota='Sin stock fisico',
			nota_resuelta=False,
		)

		self.client.force_login(self.backoffice)
		response = self.client.post(reverse('backoffice_pedido_detalle', args=[self.pedido.id]), {
			'estado': 'VERIFICADO_AJUSTADO',
			'nota_backoffice': 'No despachar por falta de stock',
			f'cantidad_{self.item.id}': '0',
			f'precio_{self.item.id}': '12.00',
		})

		self.assertEqual(response.status_code, 302)
		self.item.refresh_from_db()
		self.assertEqual(self.item.cantidad, 0)
		self.assertEqual(self.item.cantidad_solicitada, 2)
		self.assertEqual(self.item.cantidad_inventario_aplicada, 0)

	def test_backoffice_can_delete_picker_added_item_after_verified_picking(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		guardar_verificacion_picking(
			pedido=self.pedido,
			seleccionador=self.selector,
			cantidades_reales={self.item.id: 2},
			nota='Con agregado',
			nota_resuelta=True,
			additional_items=[{'presentacion_id': self.presentacion_extra.id, 'cantidad': 1}],
		)
		nuevo_item = PedidoItem.objects.get(pedido=self.pedido, presentacion=self.presentacion_extra)

		self.client.force_login(self.backoffice)
		response = self.client.post(reverse('backoffice_pedido_detalle', args=[self.pedido.id]), {
			'estado': 'VERIFICADO_AJUSTADO',
			'nota_backoffice': 'Eliminar agregado picker',
			f'cantidad_{self.item.id}': '2',
			f'precio_{self.item.id}': '12.00',
			f'cantidad_{nuevo_item.id}': '1',
			f'precio_{nuevo_item.id}': '6.00',
			f'eliminar_{nuevo_item.id}': 'on',
		})

		self.assertEqual(response.status_code, 302)
		self.assertFalse(PedidoItem.objects.filter(id=nuevo_item.id).exists())

	def test_backoffice_can_change_presentation_after_verified_picking(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		guardar_verificacion_picking(
			pedido=self.pedido,
			seleccionador=self.selector,
			cantidades_reales={self.item.id: 2},
			nota='Verificado',
			nota_resuelta=True,
		)

		self.client.force_login(self.backoffice)
		response = self.client.post(reverse('backoffice_pedido_detalle', args=[self.pedido.id]), {
			'estado': 'VERIFICADO_AJUSTADO',
			'nota_backoffice': 'Cambio de presentacion posterior',
			f'presentacion_{self.item.id}': str(self.presentacion_unidad.id),
			f'cantidad_{self.item.id}': '2',
			f'precio_{self.item.id}': '12.00',
		})

		self.assertEqual(response.status_code, 302)
		self.item.refresh_from_db()
		self.pedido.refresh_from_db()
		self.assertEqual(self.item.presentacion, self.presentacion_unidad)
		self.assertEqual(self.item.cantidad_inventario_aplicada, 2)
		self.assertEqual(self.pedido.total, Decimal('24.00'))

		get_response = self.client.get(reverse('backoffice_pedido_detalle', args=[self.pedido.id]))
		self.assertContains(get_response, f'name="presentacion_{self.item.id}"', html=False)
		self.assertContains(get_response, 'This line was modified during picking. Do you want to delete it anyway?', html=False)

	def test_backoffice_dashboard_loads_successfully(self):
		self.client.force_login(self.backoffice)

		response = self.client.get(reverse('backoffice_dashboard'))

		self.assertEqual(response.status_code, 200)

	def test_backoffice_order_list_defaults_to_pending_orders(self):
		in_progress_order = Pedido.objects.create(
			cliente=self.cliente,
			origen='CLIENTE',
			estado='EN_GESTION',
			total=Decimal('15.00'),
		)
		completed_order = Pedido.objects.create(
			cliente=self.cliente,
			origen='CLIENTE',
			estado='DESPACHADO',
			total=Decimal('18.00'),
		)
		cancelled_order = Pedido.objects.create(
			cliente=self.cliente,
			origen='CLIENTE',
			estado='CANCELADO',
			total=Decimal('21.00'),
		)

		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_pedidos'))
		visible_ids = [row.source_id for row in response.context['dispatch_orders'] if row.record_type == 'order']

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Pending orders')
		self.assertEqual(visible_ids, [self.pedido.id])
		self.assertNotIn(in_progress_order.id, visible_ids)
		self.assertNotIn(completed_order.id, visible_ids)
		self.assertNotIn(cancelled_order.id, visible_ids)

	def test_backoffice_order_list_can_filter_in_progress_completed_and_cancelled(self):
		in_progress_order = Pedido.objects.create(
			cliente=self.cliente,
			origen='CLIENTE',
			estado='PARA_VERIFICAR',
			total=Decimal('15.00'),
		)
		completed_order = Pedido.objects.create(
			cliente=self.cliente,
			origen='CLIENTE',
			estado='DESPACHADO',
			total=Decimal('18.00'),
		)
		cancelled_order = Pedido.objects.create(
			cliente=self.cliente,
			origen='CLIENTE',
			estado='CANCELADO',
			total=Decimal('21.00'),
		)

		self.client.force_login(self.backoffice)

		in_progress_response = self.client.get(reverse('backoffice_pedidos'), {'view': 'sent-to-picking'})
		self.assertContains(in_progress_response, 'Sent to picking')
		self.assertEqual(
			[row.source_id for row in in_progress_response.context['dispatch_orders'] if row.record_type == 'order'],
			[in_progress_order.id],
		)

		completed_response = self.client.get(reverse('backoffice_pedidos'), {'view': 'completed'})
		self.assertContains(completed_response, 'Completed orders')
		self.assertEqual(
			[row.source_id for row in completed_response.context['dispatch_orders'] if row.record_type == 'order'],
			[completed_order.id],
		)

		cancelled_response = self.client.get(reverse('backoffice_pedidos'), {'view': 'cancelled'})
		self.assertContains(cancelled_response, 'Cancelled orders')
		self.assertEqual(
			[row.source_id for row in cancelled_response.context['dispatch_orders'] if row.record_type == 'order'],
			[cancelled_order.id],
		)

	@patch('config.pedidos.views.BACKOFFICE_PEDIDOS_PAGE_SIZE', 2)
	def test_backoffice_order_list_paginates_filtered_orders(self, _page_size):
		for index in range(3):
			Pedido.objects.create(
				cliente=self.cliente,
				origen='CLIENTE',
				estado='EN_GESTION',
				total=Decimal(f'{10 + index}.00'),
			)

		self.client.force_login(self.backoffice)
		first_page = self.client.get(reverse('backoffice_pedidos'), {'view': 'purchase-order'})
		second_page = self.client.get(reverse('backoffice_pedidos'), {'view': 'purchase-order', 'page': 2})

		self.assertEqual(len(list(first_page.context['dispatch_orders'])), 2)
		self.assertContains(first_page, 'page=2"')
		self.assertContains(first_page, 'Showing 1-2 of 3 orders')
		self.assertEqual(len(list(second_page.context['dispatch_orders'])), 1)
		self.assertContains(second_page, 'aria-current="page"')

	def test_backoffice_order_list_excludes_quickbooks_imported_pedidos(self):
		imported_pedido = Pedido.objects.create(
			cliente=self.cliente,
			origen='BACKOFFICE',
			canal_toma='QUICKBOOKS_IMPORT',
			estado='INVOICE_GENERADA',
			total=Decimal('99.00'),
		)
		in_progress_order = Pedido.objects.create(
			cliente=self.cliente,
			origen='CLIENTE',
			estado='EN_GESTION',
			total=Decimal('15.00'),
		)

		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_pedidos'), {'view': 'purchase-order'})
		visible_ids = [row.source_id for row in response.context['dispatch_orders'] if row.record_type == 'order']

		self.assertEqual(visible_ids, [in_progress_order.id])
		self.assertNotIn(imported_pedido.id, visible_ids)
		self.assertEqual(response.context['stage_counts']['purchase-order'], 1)

	def test_backoffice_order_list_can_search_within_active_tab(self):
		target_order = Pedido.objects.create(
			cliente=self.cliente,
			origen='CLIENTE',
			estado='EN_GESTION',
			total=Decimal('15.00'),
		)
		Pedido.objects.create(
			cliente=self.cliente,
			origen='CLIENTE',
			estado='EN_GESTION',
			total=Decimal('18.00'),
		)

		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_pedidos'), {
			'view': 'purchase-order',
			'q': str(target_order.id),
		})

		self.assertEqual(response.status_code, 200)
		self.assertEqual(
			[row.source_id for row in response.context['dispatch_orders'] if row.record_type == 'order'],
			[target_order.id],
		)


class PedidoEditLockTests(TestCase):
	def setUp(self):
		self.backoffice_one = Usuario.objects.create_user(
			username='backoffice-one',
			password='secret123',
			role='backoffice',
			first_name='Alice',
		)
		self.backoffice_two = Usuario.objects.create_user(
			username='backoffice-two',
			password='secret123',
			role='backoffice',
			first_name='Bob',
		)
		customer_user = Usuario.objects.create_user(
			username='customer-lock',
			password='secret123',
			role='cliente',
			email='customer-lock@example.com',
		)
		self.cliente = Cliente.objects.create(
			usuario=customer_user,
			nombre_empresa='Cliente Lock',
			telefono='5551234567',
			direccion='123 Main St',
			ciudad='Marietta',
			estado='GA',
			codigo_postal='30062',
			pais='USA',
			sales_tax_number='TAX-LOCK',
			certificado_tax=SimpleUploadedFile('certificado.txt', b'certificado'),
			aprobado=True,
		)
		categoria = Categoria.objects.create(nombre='Categoria lock')
		marca = Marca.objects.create(nombre='Marca lock')
		producto = Producto.objects.create(nombre='Producto lock', categoria=categoria, marca=marca, activo=True)
		self.presentacion = Presentacion.objects.create(
			producto=producto,
			nombre='Caja',
			unidades=1,
			tipo_contenido='caja',
			costo=Decimal('10.00'),
			precio_1=Decimal('12.00'),
		)
		self.pedido = Pedido.objects.create(
			cliente=self.cliente,
			origen='CLIENTE',
			estado='RECIBIDO',
			total=Decimal('24.00'),
		)
		self.item = PedidoItem.objects.create(
			pedido=self.pedido,
			presentacion=self.presentacion,
			cantidad_solicitada=2,
			cantidad=2,
			precio=Decimal('12.00'),
			subtotal=Decimal('24.00'),
		)

	def test_second_backoffice_user_sees_read_only_when_first_is_editing(self):
		client_one = Client()
		client_one.force_login(self.backoffice_one)
		first_response = client_one.get(reverse('backoffice_pedido_detalle', args=[self.pedido.id]))

		self.assertEqual(first_response.status_code, 200)
		self.assertTrue(first_response.context['pedido_edit_holds_lock'])
		self.assertFalse(first_response.context['pedido_form_disabled'])

		client_two = Client()
		client_two.force_login(self.backoffice_two)
		blocked_response = client_two.get(reverse('backoffice_pedido_detalle', args=[self.pedido.id]))

		self.assertEqual(blocked_response.status_code, 200)
		self.assertTrue(blocked_response.context['pedido_edit_blocked'])
		self.assertEqual(blocked_response.context['pedido_edit_blocked_by'], 'Alice')
		self.assertTrue(blocked_response.context['pedido_form_disabled'])
		self.assertContains(blocked_response, 'currently being edited by Alice')

	def test_lock_released_after_save_allows_second_user(self):
		client_one = Client()
		client_one.force_login(self.backoffice_one)
		client_one.get(reverse('backoffice_pedido_detalle', args=[self.pedido.id]))

		save_response = client_one.post(reverse('backoffice_pedido_detalle', args=[self.pedido.id]), {
			'estado': 'RECIBIDO',
			'nota_backoffice': 'Guardado por Alice',
			f'cantidad_{self.item.id}': '2',
			f'precio_{self.item.id}': '12.00',
		})
		self.assertEqual(save_response.status_code, 302)
		self.assertFalse(PedidoEditLock.objects.filter(pedido=self.pedido).exists())

		client_two = Client()
		client_two.force_login(self.backoffice_two)
		second_response = client_two.get(reverse('backoffice_pedido_detalle', args=[self.pedido.id]))

		self.assertEqual(second_response.status_code, 200)
		self.assertTrue(second_response.context['pedido_edit_holds_lock'])
		self.assertFalse(second_response.context['pedido_edit_blocked'])

	def test_stale_lock_can_be_taken_by_another_user(self):
		acquire_pedido_edit_lock(pedido=self.pedido, user=self.backoffice_one)
		lock = PedidoEditLock.objects.get(pedido=self.pedido)
		lock.last_seen_at = timezone.now() - PEDIDO_EDIT_LOCK_TIMEOUT - timedelta(seconds=1)
		lock.save(update_fields=['last_seen_at'])

		client_two = Client()
		client_two.force_login(self.backoffice_two)
		response = client_two.get(reverse('backoffice_pedido_detalle', args=[self.pedido.id]))

		self.assertEqual(response.status_code, 200)
		self.assertTrue(response.context['pedido_edit_holds_lock'])
		lock.refresh_from_db()
		self.assertEqual(lock.locked_by_id, self.backoffice_two.id)

	def test_second_user_cannot_post_while_order_is_locked(self):
		client_one = Client()
		client_one.force_login(self.backoffice_one)
		client_one.get(reverse('backoffice_pedido_detalle', args=[self.pedido.id]))

		client_two = Client()
		client_two.force_login(self.backoffice_two)
		response = client_two.post(reverse('backoffice_pedido_detalle', args=[self.pedido.id]), {
			'estado': 'EN_GESTION',
			'nota_backoffice': 'Intento bloqueado',
			f'cantidad_{self.item.id}': '2',
			f'precio_{self.item.id}': '12.00',
		})

		self.assertEqual(response.status_code, 302)
		self.pedido.refresh_from_db()
		self.assertEqual(self.pedido.estado, 'RECIBIDO')
		self.assertNotEqual(self.pedido.nota_backoffice, 'Intento bloqueado')

	def test_edit_lock_ping_refreshes_active_lock(self):
		client_one = Client()
		client_one.force_login(self.backoffice_one)
		client_one.get(reverse('backoffice_pedido_detalle', args=[self.pedido.id]))
		lock = PedidoEditLock.objects.get(pedido=self.pedido)
		original_seen_at = lock.last_seen_at

		ping_response = client_one.post(reverse('backoffice_pedido_edit_lock_ping', args=[self.pedido.id]))
		lock.refresh_from_db()

		self.assertEqual(ping_response.status_code, 200)
		self.assertGreater(lock.last_seen_at, original_seen_at)

	def test_edit_lock_ping_reacquires_after_release(self):
		client_one = Client()
		client_one.force_login(self.backoffice_one)
		client_one.get(reverse('backoffice_pedido_detalle', args=[self.pedido.id]))
		release_response = client_one.post(reverse('backoffice_pedido_edit_lock_release', args=[self.pedido.id]))
		self.assertEqual(release_response.status_code, 200)
		self.assertFalse(PedidoEditLock.objects.filter(pedido=self.pedido).exists())

		ping_response = client_one.post(reverse('backoffice_pedido_edit_lock_ping', args=[self.pedido.id]))

		self.assertEqual(ping_response.status_code, 200)
		lock = PedidoEditLock.objects.get(pedido=self.pedido)
		self.assertEqual(lock.locked_by_id, self.backoffice_one.id)

	def test_edit_lock_release_removes_lock(self):
		client_one = Client()
		client_one.force_login(self.backoffice_one)
		client_one.get(reverse('backoffice_pedido_detalle', args=[self.pedido.id]))

		release_response = client_one.post(reverse('backoffice_pedido_edit_lock_release', args=[self.pedido.id]))

		self.assertEqual(release_response.status_code, 200)
		self.assertFalse(PedidoEditLock.objects.filter(pedido=self.pedido).exists())

	def test_edit_lock_release_succeeds_after_pedido_is_deleted(self):
		client_one = Client()
		client_one.force_login(self.backoffice_one)
		client_one.get(reverse('backoffice_pedido_detalle', args=[self.pedido.id]))
		pedido_id = self.pedido.id
		self.pedido.delete()

		release_response = client_one.post(reverse('backoffice_pedido_edit_lock_release', args=[pedido_id]))

		self.assertEqual(release_response.status_code, 200)
		self.assertEqual(release_response.json(), {'ok': True})

	def test_delete_pedido_succeeds_while_user_holds_edit_lock(self):
		client_one = Client()
		client_one.force_login(self.backoffice_one)
		client_one.get(reverse('backoffice_pedido_detalle', args=[self.pedido.id]))
		pedido_id = self.pedido.id

		delete_response = client_one.post(reverse('backoffice_pedido_delete', args=[pedido_id]))

		self.assertEqual(delete_response.status_code, 302)
		self.assertEqual(delete_response.url, reverse('backoffice_pedidos'))
		self.assertFalse(Pedido.objects.filter(id=pedido_id).exists())
		self.assertFalse(PedidoEditLock.objects.filter(pedido_id=pedido_id).exists())


class PedidoItemDiscountTests(TestCase):
	def setUp(self):
		self.backoffice = Usuario.objects.create_user(username='backoffice-discount', password='secret123', role='backoffice')
		customer_user = Usuario.objects.create_user(username='customer-discount', password='secret123', role='cliente', email='customer-discount@example.com')
		self.cliente = Cliente.objects.create(
			usuario=customer_user,
			nombre_empresa='Cliente Discount',
			telefono='5551234567',
			direccion='123 Main St',
			ciudad='Marietta',
			estado='GA',
			codigo_postal='30062',
			pais='USA',
			sales_tax_number='TAX-DISC',
			certificado_tax=SimpleUploadedFile('certificado.txt', b'certificado'),
			aprobado=True,
		)
		categoria = Categoria.objects.create(nombre='Categoria discount')
		marca = Marca.objects.create(nombre='Marca discount')
		producto = Producto.objects.create(nombre='Producto discount', categoria=categoria, marca=marca, activo=True)
		self.presentacion = Presentacion.objects.create(
			producto=producto,
			nombre='Caja',
			unidades=1,
			tipo_contenido='caja',
			costo=Decimal('10.00'),
			precio_1=Decimal('12.00'),
		)
		self.pedido = Pedido.objects.create(cliente=self.cliente, origen='VENDEDOR', estado='RECIBIDO', total=Decimal('24.00'))
		self.item = PedidoItem.objects.create(
			pedido=self.pedido,
			presentacion=self.presentacion,
			cantidad_solicitada=2,
			cantidad=2,
			precio=Decimal('12.00'),
			subtotal=Decimal('24.00'),
		)

	def test_backoffice_can_apply_dollar_discount_to_order_item(self):
		self.client.force_login(self.backoffice)
		response = self.client.post(reverse('backoffice_pedido_detalle', args=[self.pedido.id]), {
			'estado': 'RECIBIDO',
			f'cantidad_{self.item.id}': '2',
			f'precio_{self.item.id}': '12.00',
			f'descuento_aplicado_{self.item.id}': 'on',
			f'descuento_monto_{self.item.id}': '2.00',
		})

		self.assertEqual(response.status_code, 302)
		self.item.refresh_from_db()
		self.pedido.refresh_from_db()
		self.assertTrue(self.item.descuento_aplicado)
		self.assertEqual(self.item.descuento_monto, Decimal('2.00'))
		self.assertEqual(self.item.precio_unitario_neto, Decimal('10.00'))
		self.assertEqual(self.item.subtotal, Decimal('20.00'))
		self.assertEqual(self.pedido.total, Decimal('20.00'))

	def test_backoffice_detail_selects_matching_discount_preset_for_saved_amount(self):
		configuracion = ConfiguracionDescuentos.obtener()
		configuracion.descuento_2 = Decimal('0.50')
		configuracion.save()

		self.item.descuento_aplicado = True
		self.item.descuento_monto = Decimal('0.50')
		self.item.subtotal = Decimal('23.00')
		self.item.save(update_fields=['descuento_aplicado', 'descuento_monto', 'subtotal'])

		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_pedido_detalle', args=[self.pedido.id]))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'data-discount-key="descuento_2" selected', html=False)

	def test_invoice_pricing_section_reflects_saved_dollar_discount(self):
		self.item.descuento_aplicado = True
		self.item.descuento_monto = Decimal('2.00')
		self.item.subtotal = Decimal('20.00')
		self.item.save(update_fields=['descuento_aplicado', 'descuento_monto', 'subtotal'])
		self.pedido.estado = 'VERIFICADO_AJUSTADO'
		self.pedido.total = Decimal('20.00')
		self.pedido.save(update_fields=['estado', 'total'])

		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_pedido_detalle', args=[self.pedido.id]))

		self.assertEqual(response.status_code, 200)
		rows = response.context['invoice_suggested_price_rows']
		self.assertEqual(len(rows), 1)
		self.assertTrue(rows[0]['descuento_aplicado'])
		self.assertEqual(rows[0]['descuento_monto'], '2.00')
		self.assertEqual(rows[0]['final_unit_value'], '10.00')
		self.assertEqual(rows[0]['line_subtotal_value'], '20.00')
		self.assertContains(response, '$-2.00')
		self.assertContains(response, '$10.00')
		self.assertContains(response, '$20.00')

	def test_generate_invoice_uses_saved_dollar_discount_from_order_line(self):
		from config.facturacion.services import generar_invoice_desde_picking

		self.item.descuento_aplicado = True
		self.item.descuento_monto = Decimal('2.00')
		self.item.precio = Decimal('12.00')
		self.item.cantidad = 2
		self.item.subtotal = Decimal('20.00')
		self.item.save(update_fields=['descuento_aplicado', 'descuento_monto', 'precio', 'cantidad', 'subtotal'])
		self.pedido.estado = 'VERIFICADO_AJUSTADO'
		self.pedido.total = Decimal('20.00')
		self.pedido.save(update_fields=['estado', 'total'])

		invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
			line_discounts={self.item.id: Decimal('0')},
		)
		item = invoice.items.get()

		self.assertEqual(item.descuento_monto_unitario, Decimal('2.00'))
		self.assertEqual(item.precio_unitario, Decimal('10.00'))
		self.assertEqual(item.subtotal, Decimal('20.00'))


class PartialOrderFlowTests(TestCase):
	def setUp(self):
		self.backoffice = Usuario.objects.create_user(username='bo-partial', password='secret123', role='backoffice')
		self.selector = Usuario.objects.create_user(username='sel-partial', password='secret123', role='seleccionador')
		self.customer_user = Usuario.objects.create_user(
			username='cust-partial',
			password='secret123',
			role='cliente',
			email='partial@example.com',
		)
		self.cliente = Cliente.objects.create(
			usuario=self.customer_user,
			nombre_empresa='Partial Customer',
			telefono='5550001111',
			direccion='1 Test St',
			ciudad='Marietta',
			estado='GA',
			codigo_postal='30062',
			pais='USA',
			sales_tax_number='TAX-P',
			certificado_tax=SimpleUploadedFile('certificado.txt', b'certificado'),
			aprobado=True,
		)
		categoria = Categoria.objects.create(nombre='Cat partial')
		marca = Marca.objects.create(nombre='Marca partial')
		producto_a = Producto.objects.create(nombre='Coca Cola', categoria=categoria, marca=marca, activo=True)
		producto_b = Producto.objects.create(nombre='Jarritos', categoria=categoria, marca=marca, activo=True)
		self.presentacion_a = Presentacion.objects.create(
			producto=producto_a,
			nombre='Caja',
			unidades=1,
			tipo_contenido='caja',
			costo=Decimal('10.00'),
			precio_1=Decimal('30.00'),
		)
		self.presentacion_b = Presentacion.objects.create(
			producto=producto_b,
			nombre='Caja',
			unidades=1,
			tipo_contenido='caja',
			costo=Decimal('8.00'),
			precio_1=Decimal('20.00'),
		)
		registrar_entrada_manual(presentacion=self.presentacion_a, cantidad=200, observacion='Stock A')
		registrar_entrada_manual(presentacion=self.presentacion_b, cantidad=200, observacion='Stock B')

		from config.pedidos.services import crear_pedido_desde_items

		self.pedido = crear_pedido_desde_items(
			cliente=self.cliente,
			origen='BACKOFFICE',
			vendedor=None,
			reservar_inventario=True,
			items_payload=[
				{'presentacion': self.presentacion_a, 'cantidad': 60, 'precio': Decimal('30.00')},
				{'presentacion': self.presentacion_b, 'cantidad': 120, 'precio': Decimal('20.00')},
			],
		)
		self.item_a = self.pedido.items.get(presentacion=self.presentacion_a)
		self.item_b = self.pedido.items.get(presentacion=self.presentacion_b)

	def test_create_partial_order_reduces_root_and_sets_display(self):
		from config.pedidos.services import crear_pedido_parcial

		parcial = crear_pedido_parcial(
			pedido=self.pedido,
			lineas_payload=[{'item_id': self.item_a.id, 'cantidad': 30}],
			usuario=self.backoffice,
		)

		self.pedido.refresh_from_db()
		self.item_a.refresh_from_db()
		self.item_b.refresh_from_db()

		self.assertTrue(parcial.es_parcial)
		self.assertEqual(parcial.pedido_raiz_id, self.pedido.id)
		self.assertEqual(parcial.indice_parcial, 1)
		self.assertEqual(parcial.numero_display, f'{self.pedido.id}-P1')
		self.assertEqual(self.pedido.numero_display, str(self.pedido.id))
		self.assertEqual(self.item_a.cantidad, 30)
		self.assertEqual(self.item_a.cantidad_solicitada, 60)
		self.assertEqual(self.item_b.cantidad, 120)
		self.assertEqual(parcial.items.count(), 1)
		child = parcial.items.get()
		self.assertEqual(child.cantidad, 30)
		self.assertEqual(child.cantidad_solicitada, 30)
		self.assertEqual(child.item_origen_id, self.item_a.id)

	def test_create_partial_with_full_line_quantity_deletes_source_item(self):
		from config.pedidos.services import crear_pedido_parcial

		item_a_id = self.item_a.id
		parcial = crear_pedido_parcial(
			pedido=self.pedido,
			lineas_payload=[{'item_id': item_a_id, 'cantidad': 60}],
			usuario=self.backoffice,
		)

		self.pedido.refresh_from_db()
		self.item_b.refresh_from_db()

		self.assertFalse(PedidoItem.objects.filter(pk=item_a_id).exists())
		self.assertEqual(self.pedido.items.count(), 1)
		self.assertEqual(self.item_b.cantidad, 120)
		self.assertEqual(parcial.items.count(), 1)
		child = parcial.items.get()
		self.assertEqual(child.cantidad, 60)
		self.assertEqual(child.cantidad_solicitada, 60)
		self.assertIsNone(child.item_origen_id)

	def test_second_partial_uses_p2_index(self):
		from config.pedidos.services import crear_pedido_parcial

		crear_pedido_parcial(
			pedido=self.pedido,
			lineas_payload=[{'item_id': self.item_a.id, 'cantidad': 20}],
			usuario=self.backoffice,
		)
		parcial_2 = crear_pedido_parcial(
			pedido=self.pedido,
			lineas_payload=[{'item_id': self.item_a.id, 'cantidad': 10}],
			usuario=self.backoffice,
		)

		self.assertEqual(parcial_2.indice_parcial, 2)
		self.assertEqual(parcial_2.numero_display, f'{self.pedido.id}-P2')
		self.item_a.refresh_from_db()
		self.assertEqual(self.item_a.cantidad, 30)

	def test_rejects_quantity_above_pending(self):
		from config.pedidos.services import crear_pedido_parcial

		with self.assertRaises(ValidationError):
			crear_pedido_parcial(
				pedido=self.pedido,
				lineas_payload=[{'item_id': self.item_a.id, 'cantidad': 61}],
				usuario=self.backoffice,
			)

	def test_rejects_empty_partial(self):
		from config.pedidos.services import crear_pedido_parcial

		with self.assertRaises(ValidationError):
			crear_pedido_parcial(pedido=self.pedido, lineas_payload=[], usuario=self.backoffice)

	def test_rejects_partial_when_invoice_exists(self):
		from config.pedidos.services import crear_pedido_parcial

		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		guardar_verificacion_picking(
			pedido=self.pedido,
			seleccionador=self.selector,
			cantidades_reales={self.item_a.id: 60, self.item_b.id: 120},
			nota='OK',
			nota_resuelta=True,
		)
		generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
		)
		self.pedido.refresh_from_db()

		with self.assertRaises(ValidationError):
			crear_pedido_parcial(
				pedido=self.pedido,
				lineas_payload=[{'item_id': self.item_a.id, 'cantidad': 10}],
				usuario=self.backoffice,
			)

	def test_partial_can_be_assigned_to_picker(self):
		from config.pedidos.services import crear_pedido_parcial

		parcial = crear_pedido_parcial(
			pedido=self.pedido,
			lineas_payload=[{'item_id': self.item_a.id, 'cantidad': 30}],
			usuario=self.backoffice,
		)
		asignar_picking_a_seleccionador(pedido=parcial, seleccionador=self.selector)
		parcial.refresh_from_db()
		self.assertEqual(parcial.estado, 'PARA_VERIFICAR')
		self.assertEqual(parcial.seleccionador_id, self.selector.id)

	def test_backoffice_partial_confirm_flow(self):
		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_pedido_partial', args=[self.pedido.id]))
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Create Partial Order')

		response = self.client.post(
			reverse('backoffice_pedido_partial', args=[self.pedido.id]),
			{
				f'select_{self.item_a.id}': '1',
				f'qty_{self.item_a.id}': '30',
			},
		)
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Confirm Partial Order')

		response = self.client.post(
			reverse('backoffice_pedido_partial_confirm', args=[self.pedido.id]),
			{
				f'select_{self.item_a.id}': '1',
				f'qty_{self.item_a.id}': '30',
			},
		)
		parcial = Pedido.objects.filter(pedido_raiz=self.pedido).get()
		self.assertRedirects(response, reverse('backoffice_pedido_detalle', args=[parcial.id]))
		self.assertEqual(parcial.numero_display, f'{self.pedido.id}-P1')
