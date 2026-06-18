from decimal import Decimal
from unittest.mock import patch

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from config.clientes.models import Cliente
from config.inventario.models import StockPresentacion
from config.inventario.services import registrar_entrada_manual
from config.notificaciones.models import Notificacion
from config.pedidos.models import Pedido, PedidoItem
from config.pedidos.services import asignar_picking_a_seleccionador, guardar_verificacion_picking
from config.productos.models import Categoria, Marca, Presentacion, Producto
from config.usuarios.models import Usuario


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

	def test_assigning_picking_sets_selector_and_notifies(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)

		self.pedido.refresh_from_db()
		self.item.refresh_from_db()

		self.assertEqual(self.pedido.estado, 'PARA_VERIFICAR')
		self.assertEqual(self.pedido.seleccionador, self.selector)
		self.assertEqual(self.item.cantidad_solicitada, 2)
		self.assertTrue(Notificacion.objects.filter(usuario=self.selector, titulo__icontains='picking').exists())

	def test_verification_requires_note(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		StockPresentacion.objects.filter(presentacion=self.presentacion).update(stock_fisico=0)

		with self.assertRaises(ValidationError):
			guardar_verificacion_picking(
				pedido=self.pedido,
				seleccionador=self.selector,
				cantidades_reales={self.item.id: 1},
				nota='   ',
				nota_resuelta=False,
			)

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
		self.assertEqual(stock.stock_reservado, 2)
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

	def test_selector_post_verification_redirects_to_assigned_list(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		self.client.force_login(self.selector)

		response = self.client.post(reverse('selector_picking_detail', args=[self.pedido.id]), {
			f'cantidad_real_{self.item.id}': '2',
			'nota_seleccionador': 'Todo correcto',
			'nota_seleccionador_resuelta': 'on',
		})

		self.assertEqual(response.status_code, 302)
		self.assertEqual(response.url, reverse('selector_picking_list'))

	def test_selector_can_change_unit_of_measure_during_picking(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		self.client.force_login(self.selector)

		response = self.client.post(reverse('selector_picking_detail', args=[self.pedido.id]), {
			f'presentacion_{self.item.id}': str(self.presentacion_unidad.id),
			f'cantidad_real_{self.item.id}': '2',
			'nota_seleccionador': 'Cambio de U/M en picking',
			'nota_seleccionador_resuelta': 'on',
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
			f'presentacion_{self.item.id}': str(self.presentacion.id),
			f'cantidad_real_{self.item.id}': '2',
			'presentacion_nueva': str(self.presentacion_extra.id),
			'cantidad_nueva': '1',
			'nota_seleccionador': 'Agregado por picker',
			'nota_seleccionador_resuelta': 'on',
		})

		self.assertEqual(response.status_code, 302)
		nuevo_item = PedidoItem.objects.get(pedido=self.pedido, presentacion=self.presentacion_extra)
		self.assertTrue(nuevo_item.selector_added_by_picker)
		self.assertEqual(nuevo_item.cantidad, 1)
		self.assertEqual(nuevo_item.cantidad_inventario_aplicada, 1)

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

	def test_selector_post_with_stock_error_preserves_typed_quantities_and_note(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		self.client.force_login(self.selector)
		StockPresentacion.objects.filter(presentacion=self.presentacion).update(stock_fisico=0)

		response = self.client.post(reverse('selector_picking_detail', args=[self.pedido.id]), {
			f'cantidad_real_{self.item.id}': '2',
			'nota_seleccionador': 'Mantener cantidad digitada',
			'nota_seleccionador_resuelta': 'on',
		})

		self.assertEqual(response.status_code, 302)
		self.assertEqual(response.url, reverse('selector_picking_list'))
		self.pedido.refresh_from_db()
		self.item.refresh_from_db()
		self.assertTrue(self.pedido.picking_bloqueado)
		self.assertEqual(self.pedido.nota_seleccionador, 'Mantener cantidad digitada')
		self.assertFalse(self.pedido.nota_seleccionador_resuelta)
		self.assertEqual(self.item.cantidad, 2)

	def test_selector_detail_disables_picker_approval_when_physical_stock_is_insufficient(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		self.client.force_login(self.selector)
		StockPresentacion.objects.filter(presentacion=self.presentacion).update(stock_fisico=0)

		response = self.client.get(reverse('selector_picking_detail', args=[self.pedido.id]))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Physical stock is insufficient. A note is required and the order will remain blocked for BackOffice review.', html=False)
		self.assertContains(response, 'name="nota_seleccionador_resuelta"', html=False)
		self.assertContains(response, 'disabled>', html=False)
		self.assertContains(response, 'badge bg-danger', html=False)

	def test_selector_can_save_zero_quantity_when_item_will_not_ship(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		self.client.force_login(self.selector)
		StockPresentacion.objects.filter(presentacion=self.presentacion).update(stock_fisico=0)

		response = self.client.post(reverse('selector_picking_detail', args=[self.pedido.id]), {
			f'cantidad_real_{self.item.id}': '0',
			'nota_seleccionador': '',
			'nota_seleccionador_resuelta': 'on',
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
		visible_ids = [pedido.id for pedido in response.context['pedidos']]

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Pending Orders')
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

		in_progress_response = self.client.get(reverse('backoffice_pedidos'), {'view': 'in-progress'})
		self.assertContains(in_progress_response, 'Orders in progress')
		self.assertEqual([pedido.id for pedido in in_progress_response.context['pedidos']], [in_progress_order.id])

		completed_response = self.client.get(reverse('backoffice_pedidos'), {'view': 'completed'})
		self.assertContains(completed_response, 'Completed Orders')
		self.assertEqual([pedido.id for pedido in completed_response.context['pedidos']], [completed_order.id])

		cancelled_response = self.client.get(reverse('backoffice_pedidos'), {'view': 'cancelled'})
		self.assertContains(cancelled_response, 'Cancelled Orders')
		self.assertEqual([pedido.id for pedido in cancelled_response.context['pedidos']], [cancelled_order.id])

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
		first_page = self.client.get(reverse('backoffice_pedidos'), {'view': 'in-progress'})
		second_page = self.client.get(reverse('backoffice_pedidos'), {'view': 'in-progress', 'page': 2})

		self.assertEqual(len(list(first_page.context['pedidos'])), 2)
		self.assertContains(first_page, 'Page 1 of')
		self.assertContains(first_page, 'Showing 1-2 of 3 orders')
		self.assertEqual(len(list(second_page.context['pedidos'])), 1)
		self.assertContains(second_page, 'Page 2 of 2')
