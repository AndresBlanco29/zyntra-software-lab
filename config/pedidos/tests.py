from decimal import Decimal
from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from config.clientes.models import Cliente
from config.facturacion.services import generar_invoice_desde_picking
from config.inventario.models import StockPresentacion
from config.inventario.services import registrar_entrada_manual
from config.notificaciones.models import Notificacion
from config.pedidos.models import Pedido, PedidoEditLock, PedidoItem
from config.pedidos.services import (
	PEDIDO_EDIT_LOCK_TIMEOUT,
	acquire_pedido_edit_lock,
	asignar_picking_a_seleccionador,
	evaluar_stock_fisico_verificacion_picking,
	guardar_verificacion_picking,
	resolver_bloqueo_picking_desde_backoffice,
)
from config.productos.models import Categoria, ConfiguracionDescuentos, Marca, Presentacion, Producto
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
			f'linea_revisada_{self.item.id}': 'on',
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
		self.assertEqual(self.item.cantidad_inventario_aplicada, 2)

		detail_response = self.client.get(reverse('backoffice_pedido_detalle', args=[self.pedido.id]))
		self.assertContains(detail_response, 'name="metodo_entrega"', html=False)

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

	def test_selector_post_with_stock_error_preserves_typed_quantities_and_note(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		self.client.force_login(self.selector)
		StockPresentacion.objects.filter(presentacion=self.presentacion).update(stock_fisico=0)

		response = self.client.post(reverse('selector_picking_detail', args=[self.pedido.id]), {
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
		self.assertContains(response, 'Reviewed')

	def test_selector_must_review_every_line_before_saving(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		self.client.force_login(self.selector)

		response = self.client.post(reverse('selector_picking_detail', args=[self.pedido.id]), {
			f'cantidad_real_{self.item.id}': '2',
			'nota_seleccionador': 'Todo correcto',
			'nota_seleccionador_resuelta': 'on',
		})

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Check every product line in the Reviewed column', html=False)
		self.pedido.refresh_from_db()
		self.assertNotEqual(self.pedido.estado, 'VERIFICADO_AJUSTADO')

	def test_selector_detail_disables_picker_approval_when_physical_stock_is_insufficient(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
		self.client.force_login(self.selector)
		StockPresentacion.objects.filter(presentacion=self.presentacion).update(stock_fisico=0)

		response = self.client.get(reverse('selector_picking_detail', args=[self.pedido.id]))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'name="nota_seleccionador_resuelta"', html=False)
		self.assertNotContains(response, 'disabled>', html=False)
		self.assertContains(response, 'badge bg-success', html=False)

		response = self.client.post(reverse('selector_picking_detail', args=[self.pedido.id]), {
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
		self.assertEqual(len(matched['prices']), 5)
		self.assertIn('default_price_key', matched)

	def test_searchable_selects_script_uses_dropdown_input_plugin(self):
		from pathlib import Path

		js_path = Path(settings.BASE_DIR) / 'static' / 'js' / 'searchable-selects.js'
		content = js_path.read_text(encoding='utf-8')
		self.assertIn('dropdown_input', content)

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

		in_progress_response = self.client.get(reverse('backoffice_pedidos'), {'view': 'in-progress'})
		self.assertContains(in_progress_response, 'Orders in progress')
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
		first_page = self.client.get(reverse('backoffice_pedidos'), {'view': 'in-progress'})
		second_page = self.client.get(reverse('backoffice_pedidos'), {'view': 'in-progress', 'page': 2})

		self.assertEqual(len(list(first_page.context['dispatch_orders'])), 2)
		self.assertContains(first_page, 'Page 1 of')
		self.assertContains(first_page, 'Showing 1-2 of 3 orders')
		self.assertEqual(len(list(second_page.context['dispatch_orders'])), 1)
		self.assertContains(second_page, 'Page 2 of 2')


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
