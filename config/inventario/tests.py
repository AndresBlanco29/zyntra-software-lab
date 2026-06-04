from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from config.clientes.models import Cliente
from config.facturacion.services import aprobar_nota_ajuste, anular_nota_ajuste, crear_nota_ajuste_desde_invoice, generar_invoice_desde_picking
from config.integrations.models import QuickBooksImportConflict
from config.pedidos.models import Pedido, PedidoItem
from config.pedidos.services import crear_pedido_desde_items, guardar_verificacion_picking
from config.productos.models import Categoria, Marca, Presentacion, Producto
from config.usuarios.models import Usuario

from config.inventario.models import CompraProveedor, InventarioMovimiento, Proveedor, StockPresentacion, StockProductoFraccionado
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
		registrar_entrada_manual(presentacion=self.presentacion, cantidad=10, observacion='Initial stock', creado_por=self.backoffice)

	def test_order_creation_reserves_stock_and_prevents_oversell(self):
		pedido = crear_pedido_desde_items(
			cliente=self.cliente,
			items_payload=[{'presentacion': self.presentacion, 'cantidad': 4, 'precio': Decimal('10.00')}],
			origen='CLIENTE',
		)
		stock = StockPresentacion.objects.get(presentacion=self.presentacion)
		item = pedido.items.get()
		self.assertEqual(stock.stock_fisico, 10)
		self.assertEqual(stock.stock_reservado, 4)
		self.assertEqual(stock.stock_disponible, 6)
		self.assertEqual(item.cantidad_reservada_inventario, 4)

		with self.assertRaises(ValidationError):
			crear_pedido_desde_items(
				cliente=self.cliente,
				items_payload=[{'presentacion': self.presentacion, 'cantidad': 7, 'precio': Decimal('10.00')}],
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

		self.assertEqual(pedido.total, Decimal('150.00'))
		self.assertEqual(stock.stock_fisico, 10)
		self.assertEqual(stock.stock_reservado, 0)
		self.assertEqual(stock.stock_disponible, 10)
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
		self.assertEqual(stock.stock_fisico, 7)
		self.assertEqual(stock.stock_reservado, 0)
		self.assertEqual(stock.stock_disponible, 7)
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
		item.cantidad_reservada_inventario = 12
		item.save(update_fields=['cantidad_reservada_inventario'])

		registrar_entrada_manual(presentacion=self.presentacion, cantidad=5, observacion='Restock before verification', creado_por=self.backoffice)

		guardar_verificacion_picking(
			pedido=pedido,
			seleccionador=self.selector,
			cantidades_reales={item.id: 12},
			nota='Stock was replenished before verification.',
			nota_resuelta=True,
		)

		stock = StockPresentacion.objects.get(presentacion=self.presentacion)
		item.refresh_from_db()
		self.assertEqual(stock.stock_fisico, 3)
		self.assertEqual(stock.stock_reservado, 0)
		self.assertEqual(stock.stock_disponible, 3)
		self.assertEqual(item.cantidad_reservada_inventario, 0)
		self.assertEqual(item.cantidad_inventario_aplicada, 12)

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
		stock = StockPresentacion.objects.get(presentacion=self.presentacion)
		self.assertEqual(stock.stock_fisico, 10)
		self.assertEqual(stock.stock_reservado, 0)
		self.assertEqual(stock.stock_disponible, 10)

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
		self.assertEqual(stock_presentacion.stock_fisico, 11)

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
		self.assertContains(response, self.presentacion.nombre_traducido)
		self.assertContains(response, self.presentacion_sin_stock.nombre_traducido)
		self.assertContains(response, 'Internal partial stock')
		self.assertContains(response, 'unidades')
		self.assertContains(response, '7')
		self.assertContains(response, '6 box + 7 unidades')

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

	def test_purchase_order_list_post_creates_draft(self):
		self.client.force_login(self.backoffice)

		response = self.client.post(
			reverse('backoffice_supplier_purchase_list'),
			{
				'proveedor_nombre': 'Proveedor Nuevo',
				'fecha_compra': '2026-05-20',
				'bill_number': 'SUP-200',
				'presentacion_id': [str(self.presentacion.id), ''],
				'cantidad': ['5', ''],
				'costo_unitario': ['7.80', ''],
				'descripcion': ['Reposicion semanal', ''],
			},
		)

		compra = CompraProveedor.objects.get(proveedor_nombre='Proveedor Nuevo')
		self.assertEqual(response.status_code, 302)
		self.assertEqual(compra.estado, CompraProveedor.STATUS_DRAFT)
		self.assertEqual(compra.total, Decimal('39.00'))
		self.assertTrue(compra.po_number.startswith('PO-'))
		self.assertEqual(compra.lineas.count(), 1)

	def test_purchase_order_list_post_uses_selected_supplier_catalog_profile(self):
		self.client.force_login(self.backoffice)
		supplier = Proveedor.objects.create(
			nombre='Proveedor Catalogo',
			email='catalogo@example.com',
			telefono='5551230000',
			company_name='Catalog Company',
		)

		response = self.client.post(
			reverse('backoffice_supplier_purchase_list'),
			{
				'proveedor_id': str(supplier.id),
				'fecha_compra': '2026-05-20',
				'bill_number': 'SUP-201',
				'presentacion_id': [str(self.presentacion.id), ''],
				'cantidad': ['2', ''],
				'costo_unitario': ['7.50', ''],
				'descripcion': ['Pedido desde catalogo', ''],
			},
		)

		compra = CompraProveedor.objects.get(bill_number='SUP-201')
		self.assertEqual(response.status_code, 302)
		self.assertEqual(compra.proveedor, supplier)
		self.assertEqual(compra.proveedor_nombre, 'Proveedor Catalogo')
		self.assertEqual(compra.proveedor_email, 'catalogo@example.com')
		self.assertEqual(compra.proveedor_telefono, '5551230000')

	def test_purchase_order_list_view_includes_supplier_autofill_metadata(self):
		self.client.force_login(self.backoffice)
		Proveedor.objects.create(
			nombre='Proveedor JS',
			email='js@example.com',
			telefono='5558889999',
		)

		response = self.client.get(reverse('backoffice_supplier_purchase_list'))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'id="purchase-supplier-select"', html=False)
		self.assertContains(response, 'data-email="js@example.com"', html=False)
		self.assertContains(response, 'data-phone="5558889999"', html=False)
		self.assertContains(response, 'id="purchase-order-lines"', html=False)
		self.assertContains(response, 'id="purchase-order-add-line"', html=False)
		self.assertContains(response, 'id="purchase-order-line-template"', html=False)

	def test_purchase_order_list_view_separates_imported_quickbooks_bills(self):
		self.client.force_login(self.backoffice)
		local_purchase = CompraProveedor.objects.create(
			proveedor_nombre='Proveedor Local PO',
			bill_number='PO-LOCAL-1',
			fecha_compra='2026-05-20',
			estado=CompraProveedor.STATUS_DRAFT,
			creado_por=self.backoffice,
			total=Decimal('25.00'),
		)
		imported_bill = CompraProveedor.objects.create(
			proveedor_nombre='Proveedor QB Bill',
			bill_number='BILL-QB-1',
			fecha_compra='2026-05-21',
			estado=CompraProveedor.STATUS_RECEIVED,
			quickbooks_id='QB-BILL-1',
			sync_status='SYNCED',
			total=Decimal('86.44'),
		)
		QuickBooksImportConflict.objects.create(
			entity_type='BILL',
			quickbooks_id='QB-BILL-CONFLICT-1',
			display_name='Conflict Bill',
			reason='QuickBooks Bill does not contain importable item-based expense lines.',
		)

		response = self.client.get(reverse('backoffice_supplier_purchase_list'))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Imported Bills from QuickBooks')
		self.assertContains(response, 'BILL-QB-1')
		self.assertContains(response, 'Proveedor QB Bill')
		self.assertContains(response, 'Open bill conflicts (1)')
		self.assertContains(response, reverse('quickbooks_import_conflicts'))
		self.assertContains(response, 'PO-LOCAL-1')

	def test_purchase_order_detail_post_updates_linked_supplier_snapshot(self):
		self.client.force_login(self.backoffice)
		original_supplier = Proveedor.objects.create(nombre='Proveedor Original', email='old@example.com', telefono='5550000001')
		replacement_supplier = Proveedor.objects.create(nombre='Proveedor Nuevo Link', email='new@example.com', telefono='5550000002')
		compra = CompraProveedor.objects.create(
			proveedor=original_supplier,
			proveedor_nombre=original_supplier.nombre,
			proveedor_email=original_supplier.email,
			proveedor_telefono=original_supplier.telefono,
			fecha_compra='2026-05-20',
			creado_por=self.backoffice,
		)

		response = self.client.post(
			reverse('backoffice_supplier_purchase_detail', args=[compra.id]),
			{'action': 'update_supplier_link', 'proveedor_id': str(replacement_supplier.id)},
		)

		compra.refresh_from_db()
		self.assertEqual(response.status_code, 302)
		self.assertEqual(compra.proveedor, replacement_supplier)
		self.assertEqual(compra.proveedor_nombre, 'Proveedor Nuevo Link')
		self.assertEqual(compra.proveedor_email, 'new@example.com')
		self.assertEqual(compra.proveedor_telefono, '5550000002')

	def test_supplier_list_view_displays_import_controls_and_local_suppliers(self):
		self.client.force_login(self.backoffice)
		Proveedor.objects.create(
			nombre='Proveedor Importado',
			email='proveedor@example.com',
			telefono='5551112222',
			company_name='Vendor Company',
			balance=Decimal('562.50'),
			quickbooks_id='QB-VENDOR-1',
			sync_status='SYNCED',
		)

		response = self.client.get(reverse('backoffice_supplier_list'))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Suppliers Center')
		self.assertContains(response, 'Proveedor Importado')
		self.assertContains(response, '<th>PHONE</th>', html=True)
		self.assertContains(response, '<th>EMAIL</th>', html=True)
		self.assertContains(response, '<th>Balance</th>', html=True)
		self.assertContains(response, '5551112222')
		self.assertContains(response, 'proveedor@example.com')
		self.assertContains(response, '$562.50')
		self.assertContains(response, reverse('quickbooks_import_vendors_to_local'))
		self.assertContains(response, 'QB-VENDOR-1')

	def test_supplier_list_filters_active_and_sync_state(self):
		self.client.force_login(self.backoffice)
		Proveedor.objects.create(nombre='Proveedor Vinculado', activo=True, quickbooks_id='QB-VENDOR-10', sync_status='SYNCED')
		Proveedor.objects.create(nombre='Proveedor Local Inactivo', activo=False, sync_status='PENDING')

		response = self.client.get(reverse('backoffice_supplier_list'), {'status': 'inactive', 'sync': 'local'})

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Proveedor Local Inactivo')
		self.assertNotContains(response, 'Proveedor Vinculado')

	def test_supplier_detail_post_updates_supplier_profile(self):
		self.client.force_login(self.backoffice)
		supplier = Proveedor.objects.create(
			nombre='Proveedor Editar',
			email='old@example.com',
			telefono='5550001111',
			company_name='Old Company',
			notas='Nota vieja',
			activo=True,
		)

		response = self.client.post(
			reverse('backoffice_supplier_detail', args=[supplier.id]),
			{
				'nombre': 'Proveedor Editado',
				'email': 'nuevo@example.com',
				'telefono': '5553334444',
				'company_name': 'New Company',
				'notas': 'Perfil actualizado',
			},
		)

		supplier.refresh_from_db()
		self.assertEqual(response.status_code, 302)
		self.assertEqual(supplier.nombre, 'Proveedor Editado')
		self.assertEqual(supplier.email, 'nuevo@example.com')
		self.assertEqual(supplier.telefono, '5553334444')
		self.assertEqual(supplier.company_name, 'New Company')
		self.assertEqual(supplier.notas, 'Perfil actualizado')
		self.assertFalse(supplier.activo)

	def test_purchase_order_receive_loads_inventory_once(self):
		self.client.force_login(self.backoffice)
		compra = CompraProveedor.objects.create(
			proveedor_nombre='Proveedor Recibo',
			fecha_compra='2026-05-20',
			creado_por=self.backoffice,
		)
		compra.lineas.create(
			presentacion=self.presentacion,
			cantidad=3,
			costo_unitario=Decimal('6.50'),
			descripcion='Ingreso confirmado',
		)
		compra.recalcular_totales(save=True)

		first_response = self.client.post(reverse('backoffice_supplier_purchase_receive', args=[compra.id]))
		second_response = self.client.post(reverse('backoffice_supplier_purchase_receive', args=[compra.id]))

		compra.refresh_from_db()
		stock = StockPresentacion.objects.get(presentacion=self.presentacion)
		self.assertEqual(first_response.status_code, 302)
		self.assertEqual(second_response.status_code, 302)
		self.assertEqual(compra.estado, CompraProveedor.STATUS_RECEIVED)
		self.assertTrue(compra.inventory_applied)
		self.assertEqual(stock.stock_fisico, 9)
		self.assertEqual(InventarioMovimiento.objects.filter(referencia=f'SUPPLIER-PURCHASE-{compra.id}').count(), 1)
