from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from config.clientes.models import Cliente
from config.facturacion.models import Invoice, InvoiceItem
from config.inventario.availability import availability_snapshot
from config.inventario.models import StockPresentacion
from config.inventario.services import (
	aplicar_verificacion_picking_inventario,
	liberar_reserva_inventario_pedido,
	reservar_cantidades_verificacion_picking,
	reservar_stock_para_pedido_items,
)
from config.integrations.quickbooks.sync import _sync_stock_from_quickbooks_item
from config.pedidos.models import Pedido, PedidoItem
from config.pedidos.services import crear_pedido_desde_items
from config.productos.models import Categoria, Marca, Presentacion, Producto
from config.usuarios.models import Usuario


class DualLedgerEndToEndTests(TestCase):
	def setUp(self):
		self.user = Usuario.objects.create_user(username='dual-bo', password='secret123', role='backoffice')
		self.cliente_user = Usuario.objects.create_user(username='dual-cli', password='secret123', role='cliente')
		self.cliente = Cliente.objects.create(
			usuario=self.cliente_user,
			nombre_empresa='Dual Client',
			telefono='5553334444',
			direccion='2 Dual St',
			ciudad='Atlanta',
			estado='GA',
			codigo_postal='30301',
			pais='USA',
			sales_tax_number='TX-DUAL-1',
			certificado_tax='certificados/test.pdf',
		)
		categoria = Categoria.objects.create(nombre='Dual Cat')
		marca = Marca.objects.create(nombre='Dual Brand')
		self.producto = Producto.objects.create(
			nombre='Dual Coke',
			categoria=categoria,
			marca=marca,
			quickbooks_id='QB-DUAL-COKE',
		)
		self.presentacion = Presentacion.objects.create(
			producto=self.producto,
			nombre='CS',
			unidades=24,
			tipo_contenido='unidades',
			precio_1=Decimal('10.00'),
			quickbooks_id='QB-DUAL-COKE',
		)
		StockPresentacion.objects.create(
			presentacion=self.presentacion,
			stock_fisico=20,
			stock_reservado=0,
			stock_disponible=20,
		)

	def _snapshot(self):
		return availability_snapshot([self.presentacion.id])[self.presentacion.id]

	def test_create_order_does_not_reserve_in_orders(self):
		pedido = crear_pedido_desde_items(
			cliente=self.cliente,
			items_payload=[{'presentacion': self.presentacion, 'cantidad': 130, 'precio': Decimal('10.00')}],
			origen='CLIENTE',
		)
		item = pedido.items.get()
		snap = self._snapshot()
		self.assertEqual(item.cantidad_solicitada, 130)
		self.assertEqual(item.cantidad_reservada_inventario, 0)
		self.assertEqual(snap['quick_inventory'], 20)
		self.assertEqual(snap['in_orders'], 0)
		self.assertEqual(snap['available'], 20)

	def test_picking_keeps_reservation_until_invoice(self):
		pedido = crear_pedido_desde_items(
			cliente=self.cliente,
			items_payload=[{'presentacion': self.presentacion, 'cantidad': 10, 'precio': Decimal('10.00')}],
			origen='CLIENTE',
		)
		item = pedido.items.get()
		before = StockPresentacion.objects.get(presentacion=self.presentacion).stock_fisico
		aplicar_verificacion_picking_inventario(
			pedido=pedido,
			pedido_item_ids=[item.id],
			creado_por=self.user,
		)
		stock = StockPresentacion.objects.get(presentacion=self.presentacion)
		item.refresh_from_db()
		snap = self._snapshot()
		self.assertEqual(stock.stock_fisico, before)
		self.assertEqual(item.cantidad_inventario_aplicada, 10)
		self.assertEqual(item.cantidad_reservada_inventario, 10)
		self.assertEqual(snap['in_orders'], 10)
		self.assertEqual(snap['available'], 10)

	def test_partial_verify_reserves_only_manual_qty(self):
		pedido = crear_pedido_desde_items(
			cliente=self.cliente,
			items_payload=[{'presentacion': self.presentacion, 'cantidad': 130, 'precio': Decimal('10.00')}],
			origen='CLIENTE',
		)
		item = pedido.items.get()
		reservar_cantidades_verificacion_picking(
			pedido=pedido,
			items_qty_map={item.id: 17},
			creado_por=self.user,
		)
		item.refresh_from_db()
		snap = self._snapshot()
		self.assertEqual(item.cantidad_solicitada, 130)
		self.assertEqual(item.cantidad, 17)
		self.assertEqual(item.cantidad_reservada_inventario, 17)
		self.assertEqual(snap['in_orders'], 17)
		self.assertEqual(snap['available'], 3)

	def test_partial_verify_rejects_qty_above_available(self):
		pedido = crear_pedido_desde_items(
			cliente=self.cliente,
			items_payload=[{'presentacion': self.presentacion, 'cantidad': 130, 'precio': Decimal('10.00')}],
			origen='CLIENTE',
		)
		item = pedido.items.get()
		with self.assertRaises(ValidationError):
			reservar_cantidades_verificacion_picking(
				pedido=pedido,
				items_qty_map={item.id: 131},
				creado_por=self.user,
			)
		item.refresh_from_db()
		self.assertEqual(item.cantidad_reservada_inventario, 0)
		self.assertEqual(self._snapshot()['in_orders'], 0)

	def test_verify_allows_qty_above_requested_when_available(self):
		pedido = crear_pedido_desde_items(
			cliente=self.cliente,
			items_payload=[{'presentacion': self.presentacion, 'cantidad': 10, 'precio': Decimal('10.00')}],
			origen='CLIENTE',
		)
		item = pedido.items.get()
		reservar_cantidades_verificacion_picking(
			pedido=pedido,
			items_qty_map={item.id: 15},
			creado_por=self.user,
		)
		item.refresh_from_db()
		snap = self._snapshot()
		self.assertEqual(item.cantidad_solicitada, 10)
		self.assertEqual(item.cantidad, 15)
		self.assertEqual(item.cantidad_reservada_inventario, 15)
		self.assertEqual(snap['in_orders'], 15)
		self.assertEqual(snap['available'], 5)

	def test_full_flow_order_invoice_export_import(self):
		# Create does not touch Available / In Orders
		pedido = crear_pedido_desde_items(
			cliente=self.cliente,
			items_payload=[{'presentacion': self.presentacion, 'cantidad': 10, 'precio': Decimal('10.00')}],
			origen='CLIENTE',
		)
		snap = self._snapshot()
		self.assertEqual(snap['quick_inventory'], 20)
		self.assertEqual(snap['in_orders'], 0)
		self.assertEqual(snap['sales_pending_sync'], 0)
		self.assertEqual(snap['available'], 20)

		# Verify/reserve -> In Orders 10, Available 10
		aplicar_verificacion_picking_inventario(
			pedido=pedido,
			pedido_item_ids=[pedido.items.get().id],
			creado_por=self.user,
		)
		snap = self._snapshot()
		self.assertEqual(snap['in_orders'], 10)
		self.assertEqual(snap['available'], 10)

		# Invoice -> pending 10, in orders 0; QI unchanged
		invoice = Invoice.objects.create(
			pedido=pedido,
			cliente=self.cliente,
			metodo_entrega='CUSTOMER_PICK_UP',
			estado='GENERADA',
			subtotal=Decimal('100.00'),
			total_neto=Decimal('100.00'),
			creada_por=self.user,
		)
		InvoiceItem.objects.create(
			invoice=invoice,
			presentacion=self.presentacion,
			producto_nombre=self.producto.nombre,
			presentacion_nombre=self.presentacion.nombre,
			cantidad_facturada=10,
			precio_unitario=Decimal('10.00'),
			subtotal=Decimal('100.00'),
		)
		liberar_reserva_inventario_pedido(pedido=pedido, creado_por=self.user)
		pedido.estado = 'INVOICE_GENERADA'
		pedido.save(update_fields=['estado'])

		snap = self._snapshot()
		self.assertEqual(snap['quick_inventory'], 20)
		self.assertEqual(snap['sales_pending_sync'], 10)
		self.assertEqual(snap['in_orders'], 0)
		self.assertEqual(snap['available'], 10)

		# QB restock / import QI=35; pending remains 10 -> Available 25
		_sync_stock_from_quickbooks_item(
			self.presentacion,
			{'Type': 'Inventory', 'QtyOnHand': 35},
		)
		snap = self._snapshot()
		self.assertEqual(snap['quick_inventory'], 35)
		self.assertEqual(snap['sales_pending_sync'], 10)
		self.assertEqual(snap['available'], 25)

		# another invoice 20 pending -> Available 5
		pedido_b = Pedido.objects.create(
			cliente=self.cliente,
			origen='CLIENTE',
			estado='INVOICE_GENERADA',
			total=Decimal('200.00'),
		)
		invoice_b = Invoice.objects.create(
			pedido=pedido_b,
			cliente=self.cliente,
			metodo_entrega='CUSTOMER_PICK_UP',
			estado='GENERADA',
			subtotal=Decimal('200.00'),
			total_neto=Decimal('200.00'),
			creada_por=self.user,
		)
		InvoiceItem.objects.create(
			invoice=invoice_b,
			presentacion=self.presentacion,
			producto_nombre=self.producto.nombre,
			presentacion_nombre=self.presentacion.nombre,
			cantidad_facturada=20,
			precio_unitario=Decimal('10.00'),
			subtotal=Decimal('200.00'),
		)
		snap = self._snapshot()
		self.assertEqual(snap['quick_inventory'], 35)
		self.assertEqual(snap['sales_pending_sync'], 30)
		self.assertEqual(snap['available'], 5)

		# export both invoices + import QI=5
		invoice.quickbooks_id = 'QB-INV-1'
		invoice.sync_status = 'SYNCED'
		invoice.save(update_fields=['quickbooks_id', 'sync_status'])
		invoice_b.quickbooks_id = 'QB-INV-2'
		invoice_b.sync_status = 'SYNCED'
		invoice_b.save(update_fields=['quickbooks_id', 'sync_status'])
		_sync_stock_from_quickbooks_item(
			self.presentacion,
			{'Type': 'Inventory', 'QtyOnHand': 5},
		)
		snap = self._snapshot()
		self.assertEqual(snap['quick_inventory'], 5)
		self.assertEqual(snap['sales_pending_sync'], 0)
		self.assertEqual(snap['in_orders'], 0)
		self.assertEqual(snap['available'], 5)

	def test_partial_export_keeps_remaining_pending(self):
		pedido_a = Pedido.objects.create(cliente=self.cliente, origen='CLIENTE', estado='INVOICE_GENERADA', total=Decimal('100'))
		pedido_b = Pedido.objects.create(cliente=self.cliente, origen='CLIENTE', estado='INVOICE_GENERADA', total=Decimal('200'))
		inv_a = Invoice.objects.create(
			pedido=pedido_a, cliente=self.cliente, metodo_entrega='CUSTOMER_PICK_UP',
			estado='GENERADA', subtotal=Decimal('100'), total_neto=Decimal('100'), creada_por=self.user,
		)
		inv_b = Invoice.objects.create(
			pedido=pedido_b, cliente=self.cliente, metodo_entrega='CUSTOMER_PICK_UP',
			estado='GENERADA', subtotal=Decimal('200'), total_neto=Decimal('200'), creada_por=self.user,
		)
		InvoiceItem.objects.create(
			invoice=inv_a, presentacion=self.presentacion, producto_nombre=self.producto.nombre,
			presentacion_nombre='CS', cantidad_facturada=10, precio_unitario=Decimal('10'), subtotal=Decimal('100'),
		)
		InvoiceItem.objects.create(
			invoice=inv_b, presentacion=self.presentacion, producto_nombre=self.producto.nombre,
			presentacion_nombre='CS', cantidad_facturada=20, precio_unitario=Decimal('10'), subtotal=Decimal('200'),
		)
		_sync_stock_from_quickbooks_item(self.presentacion, {'Type': 'Inventory', 'QtyOnHand': 35})

		inv_a.quickbooks_id = 'QB-ONLY-A'
		inv_a.sync_status = 'SYNCED'
		inv_a.save(update_fields=['quickbooks_id', 'sync_status'])
		_sync_stock_from_quickbooks_item(self.presentacion, {'Type': 'Inventory', 'QtyOnHand': 25})

		snap = self._snapshot()
		self.assertEqual(snap['quick_inventory'], 25)
		self.assertEqual(snap['sales_pending_sync'], 20)
		self.assertEqual(snap['available'], 5)

	def test_reservation_helper_does_not_mutate_qi(self):
		pedido = Pedido.objects.create(cliente=self.cliente, origen='CLIENTE', estado='RECIBIDO', total=Decimal('50'))
		item = PedidoItem.objects.create(
			pedido=pedido,
			presentacion=self.presentacion,
			cantidad_solicitada=5,
			cantidad=5,
			precio=Decimal('10'),
			subtotal=Decimal('50'),
		)
		before = StockPresentacion.objects.get(presentacion=self.presentacion)
		before_fisico = before.stock_fisico
		before_reservado = before.stock_reservado
		reservar_stock_para_pedido_items(pedido=pedido, pedido_items=[item], creado_por=self.user)
		after = StockPresentacion.objects.get(presentacion=self.presentacion)
		item.refresh_from_db()
		self.assertEqual(after.stock_fisico, before_fisico)
		self.assertEqual(after.stock_reservado, before_reservado)
		self.assertEqual(item.cantidad_reservada_inventario, 5)
