from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from config.clientes.models import Cliente
from config.facturacion.services import aprobar_nota_ajuste, anular_nota_ajuste, crear_nota_ajuste_desde_invoice, generar_invoice_desde_picking
from config.integrations.models import QuickBooksImportConflict
from config.pedidos.models import Pedido, PedidoItem
from config.pedidos.services import crear_pedido_desde_items, eliminar_linea_pedido_desde_backoffice, guardar_verificacion_picking
from config.inventario.services import reservar_stock_para_pedido_items
from config.productos.models import Categoria, Marca, Presentacion, Producto
from config.usuarios.models import Usuario

from config.inventario.availability import availability_snapshot
from config.inventario.models import InventarioMovimiento, StockPresentacion, StockProductoFraccionado
from config.inventario.services import _apply_fractional_inventory_change, _lock_fractional_stock_records, cancelar_pedido_con_inventario, registrar_entrada_manual


class InventarioOperativoTests(TestCase):
	def setUp(self):
		self.backoffice = Usuario.objects.create_user(username='bo-stock', password='secret123', role='backoffice')
		self.selector = Usuario.objects.create_user(username='selector-stock', password='secret123', role='seleccionador')
		self.driver = Usuario.objects.create_user(username='driver-stock', password='secret123', role='driver')
		self.cliente_user = Usuario.objects.create_user(username='cliente-stock', password='secret123', role='cliente')
		self.cliente = Cliente.objects.create(
			usuario=self.cliente_user,
			nombre_empresa='Cliente Stock',
			telefono='5554441212',
			direccion='100 Inventory Ave',
			ciudad='Atlanta',
			estado='GA',
			codigo_postal='30301',
			pais='USA',
			sales_tax_number='TX-INV-1',
			certificado_tax='certificados/test.pdf',
		)
		categoria = Categoria.objects.create(nombre='Categoria Stock')
		marca = Marca.objects.create(nombre='Marca Stock')
		producto = Producto.objects.create(nombre='Producto Stock', categoria=categoria, marca=marca)
		self.presentacion = Presentacion.objects.create(
			producto=producto,
			nombre='Caja',
			unidades=10,
			tipo_contenido='unidades',
			precio_1=Decimal('10.00'),
		)
		registrar_entrada_manual(presentacion=self.presentacion, cantidad=100, observacion='Initial stock', creado_por=self.backoffice)

	def test_backoffice_delete_line_releases_reserved_stock_before_picking(self):
		pedido = crear_pedido_desde_items(
			cliente=self.cliente,
			items_payload=[{'presentacion': self.presentacion, 'cantidad': 1, 'precio': Decimal('10.00')}],
			origen='CLIENTE',
		)
		item = pedido.items.get()
		duplicate_item = PedidoItem.objects.create(
			pedido=pedido,
			presentacion=self.presentacion,
			cantidad_solicitada=1,
			cantidad=1,
			precio=Decimal('10.00'),
			subtotal=Decimal('10.00'),
		)
		reservar_stock_para_pedido_items(pedido=pedido, pedido_items=[duplicate_item], creado_por=self.backoffice)

		ledger = availability_snapshot([self.presentacion.id])[self.presentacion.id]
		self.assertEqual(ledger['quick_inventory'], 100)
		self.assertEqual(ledger['in_orders'], 2)
		self.assertEqual(ledger['available'], 98)

		eliminar_linea_pedido_desde_backoffice(item=duplicate_item, creado_por=self.backoffice)

		stock = StockPresentacion.objects.get(presentacion=self.presentacion)
		item.refresh_from_db()
		ledger = availability_snapshot([self.presentacion.id])[self.presentacion.id]
		self.assertFalse(PedidoItem.objects.filter(id=duplicate_item.id).exists())
		self.assertEqual(stock.stock_fisico, 100)
		self.assertEqual(ledger['in_orders'], 1)
		self.assertEqual(ledger['available'], 99)
		self.assertEqual(item.cantidad_reservada_inventario, 1)

	def test_order_creation_reserves_stock_and_prevents_oversell(self):
		pedido = crear_pedido_desde_items(
			cliente=self.cliente,
			items_payload=[{'presentacion': self.presentacion, 'cantidad': 4, 'precio': Decimal('10.00')}],
			origen='CLIENTE',
		)
		stock = StockPresentacion.objects.get(presentacion=self.presentacion)
		item = pedido.items.get()
		ledger = availability_snapshot([self.presentacion.id])[self.presentacion.id]
		self.assertEqual(stock.stock_fisico, 100)
		self.assertEqual(ledger['in_orders'], 4)
		self.assertEqual(ledger['available'], 96)
		self.assertEqual(item.cantidad_reservada_inventario, 4)

		with self.assertRaises(ValidationError):
			crear_pedido_desde_items(
				cliente=self.cliente,
				items_payload=[{'presentacion': self.presentacion, 'cantidad': 97, 'precio': Decimal('10.00')}],
				origen='CLIENTE',
			)

	def test_customer_overorder_can_be_created_without_stock_reservation(self):
		pedido = crear_pedido_desde_items(
			cliente=self.cliente,
			items_payload=[{'presentacion': self.presentacion, 'cantidad': 15, 'precio': Decimal('10.00')}],
			origen='CLIENTE',
			bypass_stock_check=True,
			reservar_inventario=False,
		)
		stock = StockPresentacion.objects.get(presentacion=self.presentacion)
		item = pedido.items.get()
		ledger = availability_snapshot([self.presentacion.id])[self.presentacion.id]

		self.assertEqual(pedido.total, Decimal('150.00'))
		self.assertEqual(stock.stock_fisico, 100)
		self.assertEqual(ledger['in_orders'], 15)
		self.assertEqual(ledger['available'], 85)
		self.assertEqual(item.cantidad_reservada_inventario, 0)
		self.assertEqual(item.cantidad_inventario_aplicada, 0)

	def test_picking_consumes_real_quantity_and_releases_difference(self):
		pedido = crear_pedido_desde_items(
			cliente=self.cliente,
			items_payload=[{'presentacion': self.presentacion, 'cantidad': 5, 'precio': Decimal('10.00')}],
			origen='CLIENTE',
		)
		pedido.seleccionador = self.selector
		pedido.estado = 'PARA_VERIFICAR'
		pedido.save(update_fields=['seleccionador', 'estado'])
		item = pedido.items.get()

		guardar_verificacion_picking(
			pedido=pedido,
			seleccionador=self.selector,
			cantidades_reales={item.id: 3},
			nota='Diferencia de dos cajas',
			nota_resuelta=True,
		)

		stock = StockPresentacion.objects.get(presentacion=self.presentacion)
		item.refresh_from_db()
		ledger = availability_snapshot([self.presentacion.id])[self.presentacion.id]
		# Dual-ledger: picking does not mutate Quick Inventory; open order qty follows picked amount.
		self.assertEqual(stock.stock_fisico, 100)
		self.assertEqual(ledger['in_orders'], 3)
		self.assertEqual(ledger['available'], 97)
		self.assertEqual(item.cantidad_inventario_aplicada, 3)
		self.assertEqual(item.cantidad_reservada_inventario, 0)

	def test_picking_verification_handles_legacy_unreserved_order_after_manual_restock(self):
		pedido = crear_pedido_desde_items(
			cliente=self.cliente,
			items_payload=[{'presentacion': self.presentacion, 'cantidad': 12, 'precio': Decimal('10.00')}],
			origen='CLIENTE',
			bypass_stock_check=True,
			reservar_inventario=False,
		)
		pedido.seleccionador = self.selector
		pedido.estado = 'PARA_VERIFICAR'
		pedido.save(update_fields=['seleccionador', 'estado'])
		item = pedido.items.get()
		item.cantidad_reservada_inventario = 1
		item.save(update_fields=['cantidad_reservada_inventario'])

		registrar_entrada_manual(presentacion=self.presentacion, cantidad=5, observacion='Restock before verification', creado_por=self.backoffice)

		guardar_verificacion_picking(
			pedido=pedido,
			seleccionador=self.selector,
			cantidades_reales={item.id: 1},
			nota='Stock was replenished before verification.',
			nota_resuelta=True,
		)

		stock = StockPresentacion.objects.get(presentacion=self.presentacion)
		item.refresh_from_db()
		ledger = availability_snapshot([self.presentacion.id])[self.presentacion.id]
		self.assertEqual(stock.stock_fisico, 105)
		self.assertEqual(ledger['in_orders'], 1)
		self.assertEqual(ledger['available'], 104)
		self.assertEqual(item.cantidad_reservada_inventario, 0)
		self.assertEqual(item.cantidad_inventario_aplicada, 1)

	def test_cancelled_order_restores_reserved_and_applied_stock(self):
		pedido = crear_pedido_desde_items(
			cliente=self.cliente,
			items_payload=[{'presentacion': self.presentacion, 'cantidad': 4, 'precio': Decimal('10.00')}],
			origen='CLIENTE',
		)
		item = pedido.items.get()
		pedido.seleccionador = self.selector
		pedido.estado = 'PARA_VERIFICAR'
		pedido.save(update_fields=['seleccionador', 'estado'])
		guardar_verificacion_picking(
			pedido=pedido,
			seleccionador=self.selector,
			cantidades_reales={item.id: 4},
			nota='Todo correcto',
			nota_resuelta=True,
		)

		cancelar_pedido_con_inventario(pedido=pedido, creado_por=self.backoffice)
		pedido.estado = 'CANCELADO'
		pedido.save(update_fields=['estado', 'actualizada_en'])
		stock = StockPresentacion.objects.get(presentacion=self.presentacion)
		ledger = availability_snapshot([self.presentacion.id])[self.presentacion.id]
		self.assertEqual(stock.stock_fisico, 100)
		self.assertEqual(ledger['in_orders'], 0)
		self.assertEqual(ledger['available'], 100)

	def test_selling_one_case_does_not_mutate_quick_inventory(self):
		pedido = crear_pedido_desde_items(
			cliente=self.cliente,
			items_payload=[{'presentacion': self.presentacion, 'cantidad': 1, 'precio': Decimal('10.00')}],
			origen='CLIENTE',
		)
		pedido.seleccionador = self.selector
		pedido.estado = 'PARA_VERIFICAR'
		pedido.save(update_fields=['seleccionador', 'estado'])
		item = pedido.items.get()
		guardar_verificacion_picking(
			pedido=pedido,
			seleccionador=self.selector,
			cantidades_reales={item.id: 1},
			nota='Venta de una caja',
			nota_resuelta=True,
		)
		stock = StockPresentacion.objects.get(presentacion=self.presentacion)
		ledger = availability_snapshot([self.presentacion.id])[self.presentacion.id]
		self.assertEqual(stock.stock_fisico, 100)
		self.assertEqual(ledger['in_orders'], 1)
		self.assertEqual(ledger['available'], 99)

	def test_manual_entry_and_reservation_use_package_counts_when_presentation_has_multiple_units(self):
		self.presentacion.unidades = 8
		self.presentacion.save(update_fields=['unidades'])
		stock = StockPresentacion.objects.get(presentacion=self.presentacion)
		stock.stock_fisico = 0
		stock.stock_reservado = 0
		stock.stock_disponible = 0
		stock.save(update_fields=['stock_fisico', 'stock_reservado', 'stock_disponible', 'actualizado_en'])

		registrar_entrada_manual(presentacion=self.presentacion, cantidad=32, observacion='32 cajas fisicas', creado_por=self.backoffice)
		pedido = crear_pedido_desde_items(
			cliente=self.cliente,
			items_payload=[{'presentacion': self.presentacion, 'cantidad': 20, 'precio': Decimal('10.00')}],
			origen='CLIENTE',
		)

		stock.refresh_from_db()
		ledger = availability_snapshot([self.presentacion.id])[self.presentacion.id]
		self.assertEqual(stock.stock_fisico, 32)
		self.assertEqual(ledger['in_orders'], 20)
		self.assertEqual(ledger['available'], 12)
		self.assertEqual(pedido.items.get().cantidad_reservada_inventario, 20)

	def test_credit_return_and_void_reverse_inventory(self):
		pedido = Pedido.objects.create(cliente=self.cliente, origen='CLIENTE', estado='VERIFICADO_AJUSTADO', total=Decimal('20.00'))
		pedido_item = PedidoItem.objects.create(
			pedido=pedido,
			presentacion=self.presentacion,
			cantidad_solicitada=2,
			cantidad=2,
			cantidad_inventario_aplicada=2,
			precio=Decimal('10.00'),
			subtotal=Decimal('20.00'),
		)
		stock = StockPresentacion.objects.get(presentacion=self.presentacion)
		stock.stock_fisico = 8
		stock.stock_reservado = 0
		stock.stock_disponible = 8
		stock.save()

		invoice = generar_invoice_desde_picking(pedido=pedido, metodo_entrega='RUTA_DRIVER', driver=self.driver, usuario=self.backoffice)
		nota = crear_nota_ajuste_desde_invoice(
			invoice=invoice,
			tipo_documento='CREDITO',
			motivo='DAMAGE',
			tipo_credito='CREDIT_RETURN',
			descripcion='Retorno parcial',
			usuario=self.backoffice,
			items_payload=[{'invoice_item': invoice.items.first(), 'cantidad': 1, 'monto_unitario': Decimal('10.00')}],
		)

		aprobar_nota_ajuste(nota=nota, usuario=self.backoffice)
		stock.refresh_from_db()
		self.assertEqual(stock.stock_fisico, 9)
		self.assertTrue(InventarioMovimiento.objects.filter(nota_ajuste=nota, tipo='ENTRADA_NOTA_CREDITO').exists())

		anular_nota_ajuste(nota=nota)
		stock.refresh_from_db()
		self.assertEqual(stock.stock_fisico, 8)
		self.assertTrue(InventarioMovimiento.objects.filter(nota_ajuste=nota, tipo='REVERSO_NOTA_CREDITO').exists())

	def test_fractional_stock_promotes_into_full_packages_when_threshold_is_reached(self):
		fractional_stock = _lock_fractional_stock_records([(self.presentacion.producto_id, 'unidades')])[(self.presentacion.producto_id, 'unidades')]

		_apply_fractional_inventory_change(stock=fractional_stock, delta_fisico=7, observacion='First partial')
		fractional_stock.refresh_from_db()
		self.assertEqual(fractional_stock.stock_fisico, 7)

		_apply_fractional_inventory_change(stock=fractional_stock, delta_fisico=3, observacion='Second partial')
		fractional_stock.refresh_from_db()
		stock_presentacion = StockPresentacion.objects.get(presentacion=self.presentacion)
		self.assertEqual(fractional_stock.stock_fisico, 0)
		self.assertEqual(stock_presentacion.stock_fisico, 101)

	def test_fractional_stock_deconsolidates_package_when_reversal_needs_content_units(self):
		stock_presentacion = StockPresentacion.objects.get(presentacion=self.presentacion)
		stock_presentacion.stock_fisico = 1
		stock_presentacion.stock_reservado = 0
		stock_presentacion.stock_disponible = 1
		stock_presentacion.save()
		fractional_stock = _lock_fractional_stock_records([(self.presentacion.producto_id, 'unidades')])[(self.presentacion.producto_id, 'unidades')]

		_apply_fractional_inventory_change(stock=fractional_stock, delta_fisico=-5, observacion='Reverse partial')
		fractional_stock.refresh_from_db()
		stock_presentacion.refresh_from_db()
		self.assertEqual(stock_presentacion.stock_fisico, 0)
		self.assertEqual(fractional_stock.stock_fisico, 5)


class InventarioBackofficeViewsTests(TestCase):
	def setUp(self):
		self.backoffice = Usuario.objects.create_user(username='bo-stock-view', password='secret123', role='backoffice')
		categoria = Categoria.objects.create(nombre='Categoria Vista Stock')
		marca = Marca.objects.create(nombre='Marca Vista Stock')
		producto = Producto.objects.create(nombre='Producto Vista Stock', categoria=categoria, marca=marca)
		self.presentacion = Presentacion.objects.create(
			producto=producto,
			nombre='Caja',
			unidades=20,
			tipo_contenido='unidades',
			precio_1=Decimal('15.00'),
		)
		self.presentacion_sin_stock = Presentacion.objects.create(
			producto=producto,
			nombre='Pallet',
			unidades=30,
			tipo_contenido='cajas',
			precio_1=Decimal('2.00'),
		)
		self.stock_fraccionado = StockProductoFraccionado.objects.create(
			producto=producto,
			contenido='unidades',
			stock_fisico=7,
		)
		registrar_entrada_manual(presentacion=self.presentacion, cantidad=6, observacion='Initial stock', creado_por=self.backoffice)

	def test_inventory_list_view_displays_presentations(self):
		self.client.force_login(self.backoffice)

		response = self.client.get(reverse('backoffice_inventory_list'))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Producto Vista Stock')
		self.assertContains(response, 'CS')
		self.assertContains(response, self.presentacion_sin_stock.nombre)
		self.assertContains(response, 'Internal partial stock')
		self.assertContains(response, 'unidades')
		self.assertContains(response, '7')
		self.assertContains(response, '6 CS')
		self.assertContains(response, '7 unidades')
		self.assertContains(response, 'Quick Inventory')
		self.assertContains(response, 'Sales Pending Sync')
		self.assertContains(response, 'In orders')

	def test_inventory_detail_view_displays_fractional_stock_for_product(self):
		self.client.force_login(self.backoffice)

		response = self.client.get(reverse('backoffice_inventory_detail', args=[self.presentacion.id]))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Internal partial stock for this product')
		self.assertContains(response, 'unidades')
		self.assertContains(response, '7')
		self.assertContains(response, self.presentacion.nombre_traducido)
		self.assertContains(response, 'Package consolidation trace')

	def test_inventory_detail_view_shows_consolidation_trace(self):
		self.client.force_login(self.backoffice)
		InventarioMovimiento.objects.create(
			presentacion=self.presentacion,
			stock=StockPresentacion.objects.get(presentacion=self.presentacion),
			categoria='AJUSTE',
			tipo='CONSOLIDACION_FRACCIONADA',
			cantidad=1,
			delta_fisico=1,
			delta_reservado=0,
			stock_fisico_anterior=6,
			stock_fisico_posterior=7,
			stock_reservado_anterior=0,
			stock_reservado_posterior=0,
			stock_disponible_anterior=6,
			stock_disponible_posterior=7,
			referencia='CRN-TEST',
			observacion='Consolidacion de prueba',
			creado_por=self.backoffice,
		)

		response = self.client.get(reverse('backoffice_inventory_detail', args=[self.presentacion.id]))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Consolidacion de prueba')
		self.assertContains(response, 'Fractional stock consolidation')

	def test_inventory_detail_post_records_manual_entry(self):
		self.client.force_login(self.backoffice)

		response = self.client.post(
			reverse('backoffice_inventory_detail', args=[self.presentacion.id]),
			{
				'action': 'entrada',
				'cantidad': '4',
				'observacion': 'Restock warehouse',
			},
		)

		self.assertEqual(response.status_code, 302)
		stock = StockPresentacion.objects.get(presentacion=self.presentacion)
		self.assertEqual(stock.stock_fisico, 10)
		self.assertEqual(stock.stock_disponible, 10)
		self.assertTrue(
			InventarioMovimiento.objects.filter(
				presentacion=self.presentacion,
				tipo='ENTRADA_MANUAL',
				observacion='Restock warehouse',
			).exists()
		)

	def test_inventory_detail_post_records_adjustment_delta(self):
		self.client.force_login(self.backoffice)

		response = self.client.post(
			reverse('backoffice_inventory_detail', args=[self.presentacion.id]),
			{
				'action': 'ajuste',
				'cantidad': '999',
				'delta_cantidad': '1',
				'observacion': 'Cycle count correction',
			},
		)

		self.assertEqual(response.status_code, 302)
		stock = StockPresentacion.objects.get(presentacion=self.presentacion)
		self.assertEqual(stock.stock_fisico, 7)
		self.assertEqual(stock.stock_disponible, 7)
		self.assertTrue(
			InventarioMovimiento.objects.filter(
				presentacion=self.presentacion,
				tipo='AJUSTE_POSITIVO',
				delta_fisico=1,
				observacion='Cycle count correction',
			).exists()
		)

	def test_inventory_detail_post_does_not_default_empty_entry_quantity_to_one(self):
		self.client.force_login(self.backoffice)

		response = self.client.post(
			reverse('backoffice_inventory_detail', args=[self.presentacion.id]),
			{
				'action': 'entrada',
				'cantidad': '',
				'delta_cantidad': '5',
				'observacion': 'Should fail without quantity',
			},
		)

		self.assertEqual(response.status_code, 302)
		stock = StockPresentacion.objects.get(presentacion=self.presentacion)
		self.assertEqual(stock.stock_fisico, 6)
		self.assertFalse(
			InventarioMovimiento.objects.filter(
				presentacion=self.presentacion,
				observacion='Should fail without quantity',
			).exists()
		)

	def test_inventory_detail_post_requires_observation_for_all_movements(self):
		self.client.force_login(self.backoffice)

		response = self.client.post(
			reverse('backoffice_inventory_detail', args=[self.presentacion.id]),
			{
				'action': 'ajuste',
				'delta_cantidad': '2',
				'observacion': '   ',
			},
		)

		self.assertEqual(response.status_code, 302)
		stock = StockPresentacion.objects.get(presentacion=self.presentacion)
		self.assertEqual(stock.stock_fisico, 6)
		self.assertFalse(
			InventarioMovimiento.objects.filter(
				presentacion=self.presentacion,
				tipo='AJUSTE_POSITIVO',
				delta_fisico=2,
			).exists()
		)


class InventoryListFilterTests(TestCase):
	def setUp(self):
		self.backoffice = Usuario.objects.create_user(username='bo-inv-filters', password='secret123', role='backoffice')
		categoria = Categoria.objects.create(nombre='Categoria Filtros')
		marca = Marca.objects.create(nombre='Marca Filtros')

		def _presentacion(nombre, qty):
			producto = Producto.objects.create(nombre=nombre, categoria=categoria, marca=marca, activo=True)
			presentacion = Presentacion.objects.create(
				producto=producto,
				nombre='CS',
				unidades=1,
				tipo_contenido='caja',
				precio_1=Decimal('10.00'),
			)
			if qty:
				registrar_entrada_manual(presentacion=presentacion, cantidad=qty, observacion='Seed stock', creado_por=self.backoffice)
			return presentacion

		self.out_of_stock = _presentacion('Producto Sin Stock', 0)
		self.low_stock = _presentacion('Producto Low Stock', 3)
		self.in_stock = _presentacion('Producto En Stock', 20)

	def test_inventory_list_supports_out_of_stock_filter(self):
		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_inventory_list'), {'stock': 'out'})
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Producto Sin Stock')
		self.assertNotContains(response, 'Producto Low Stock')
		self.assertNotContains(response, 'Producto En Stock')

	def test_inventory_list_supports_low_stock_filter(self):
		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_inventory_list'), {'stock': 'low'})
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Producto Low Stock')
		self.assertNotContains(response, 'Producto Sin Stock')
		self.assertNotContains(response, 'Producto En Stock')

	def test_inventory_list_supports_in_stock_filter(self):
		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_inventory_list'), {'stock': 'in'})
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Producto En Stock')
		self.assertNotContains(response, 'Producto Sin Stock')
		self.assertNotContains(response, 'Producto Low Stock')


class SignedStockSchemaTests(TestCase):
	def test_out_of_range_stock_error_detection(self):
		from config.inventario.signed_stock import is_out_of_range_stock_error

		self.assertTrue(
			is_out_of_range_stock_error(
				'(1264, "Out of range value for column \'stock_fisico\' at row 1")'
			)
		)
		self.assertFalse(is_out_of_range_stock_error('other database error'))

	def test_ensure_signed_stock_columns_skips_inside_atomic_block(self):
		from django.db import connection

		from config.inventario.signed_stock import ensure_signed_stock_columns

		# TestCase wraps each test in atomic; DDL must be skipped here.
		self.assertTrue(connection.in_atomic_block)
		self.assertFalse(ensure_signed_stock_columns(force=True))

	def test_negative_quick_inventory_reduces_available(self):
		categoria = Categoria.objects.create(nombre='Neg QI Cat')
		marca = Marca.objects.create(nombre='Neg QI Brand')
		producto = Producto.objects.create(nombre='Neg QI Product', categoria=categoria, marca=marca)
		presentacion = Presentacion.objects.create(
			producto=producto,
			nombre='CS',
			unidades=24,
			tipo_contenido='unidades',
		)
		StockPresentacion.objects.create(
			presentacion=presentacion,
			stock_fisico=-4,
			stock_reservado=0,
			stock_disponible=-4,
		)

		snapshot = availability_snapshot([presentacion.id])[presentacion.id]
		self.assertEqual(snapshot['quick_inventory'], -4)
		self.assertEqual(snapshot['available'], -4)
