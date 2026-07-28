from decimal import Decimal

from django.test import TestCase

from config.clientes.models import Cliente
from config.facturacion.models import Invoice, InvoiceItem
from config.inventario.availability import availability_snapshot, presentacion_is_quickbooks_linked
from config.inventario.models import StockPresentacion
from config.pedidos.models import Pedido, PedidoItem
from config.productos.models import Categoria, Marca, Presentacion, Producto
from config.usuarios.models import Usuario


class DualLedgerAvailabilityTests(TestCase):
	def setUp(self):
		self.user = Usuario.objects.create_user(username='avail-bo', password='secret123', role='backoffice')
		self.cliente_user = Usuario.objects.create_user(username='avail-cli', password='secret123', role='cliente')
		self.cliente = Cliente.objects.create(
			usuario=self.cliente_user,
			nombre_empresa='Avail Client',
			telefono='5551112222',
			direccion='1 Test St',
			ciudad='Atlanta',
			estado='GA',
			codigo_postal='30301',
			pais='USA',
			sales_tax_number='TX-AV-1',
			certificado_tax='certificados/test.pdf',
		)
		categoria = Categoria.objects.create(nombre='Drinks Avail')
		marca = Marca.objects.create(nombre='Brand Avail')
		self.producto = Producto.objects.create(
			nombre='Coca Cola Avail',
			categoria=categoria,
			marca=marca,
			quickbooks_id='QB-COKE-AVAIL',
		)
		self.presentacion = Presentacion.objects.create(
			producto=self.producto,
			nombre='CS',
			unidades=24,
			tipo_contenido='unidades',
			precio_1=Decimal('10.00'),
			quickbooks_id='QB-COKE-AVAIL',
		)
		StockPresentacion.objects.create(
			presentacion=self.presentacion,
			stock_fisico=20,
			stock_reservado=0,
			stock_disponible=20,
		)

	def test_available_is_qi_minus_pending_minus_in_orders(self):
		pedido = Pedido.objects.create(
			cliente=self.cliente,
			origen='CLIENTE',
			estado='RECIBIDO',
			total=Decimal('100.00'),
		)
		# Requested qty alone does not reduce Available — only reserved does.
		PedidoItem.objects.create(
			pedido=pedido,
			presentacion=self.presentacion,
			cantidad_solicitada=10,
			cantidad=10,
			cantidad_reservada_inventario=0,
			precio=Decimal('10.00'),
			subtotal=Decimal('100.00'),
		)

		snapshot = availability_snapshot([self.presentacion.id])[self.presentacion.id]
		self.assertEqual(snapshot['quick_inventory'], 20)
		self.assertEqual(snapshot['sales_pending_sync'], 0)
		self.assertEqual(snapshot['in_orders'], 0)
		self.assertEqual(snapshot['available'], 20)

		PedidoItem.objects.filter(pedido=pedido).update(cantidad_reservada_inventario=10)
		snapshot = availability_snapshot([self.presentacion.id])[self.presentacion.id]
		self.assertEqual(snapshot['in_orders'], 10)
		self.assertEqual(snapshot['available'], 10)

	def test_invoicing_moves_qty_from_in_orders_to_pending_sync(self):
		pedido = Pedido.objects.create(
			cliente=self.cliente,
			origen='CLIENTE',
			estado='VERIFICADO_AJUSTADO',
			total=Decimal('100.00'),
		)
		PedidoItem.objects.create(
			pedido=pedido,
			presentacion=self.presentacion,
			cantidad_solicitada=10,
			cantidad=10,
			cantidad_reservada_inventario=10,
			precio=Decimal('10.00'),
			subtotal=Decimal('100.00'),
		)
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
		# Invoice presence removes the line from In Orders; reserved marker is cleared in services.
		PedidoItem.objects.filter(pedido=pedido).update(cantidad_reservada_inventario=0)
		pedido.estado = 'INVOICE_GENERADA'
		pedido.save(update_fields=['estado'])

		snapshot = availability_snapshot([self.presentacion.id])[self.presentacion.id]
		self.assertEqual(snapshot['quick_inventory'], 20)
		self.assertEqual(snapshot['sales_pending_sync'], 10)
		self.assertEqual(snapshot['in_orders'], 0)
		self.assertEqual(snapshot['available'], 10)

	def test_qi_increase_keeps_pending_sync(self):
		pedido = Pedido.objects.create(
			cliente=self.cliente,
			origen='CLIENTE',
			estado='INVOICE_GENERADA',
			total=Decimal('100.00'),
		)
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

		stock = StockPresentacion.objects.get(presentacion=self.presentacion)
		stock.stock_fisico = 35
		stock.save()

		snapshot = availability_snapshot([self.presentacion.id])[self.presentacion.id]
		self.assertEqual(snapshot['quick_inventory'], 35)
		self.assertEqual(snapshot['sales_pending_sync'], 10)
		self.assertEqual(snapshot['available'], 25)

	def test_exported_invoice_drops_pending_sync(self):
		pedido = Pedido.objects.create(
			cliente=self.cliente,
			origen='CLIENTE',
			estado='INVOICE_GENERADA',
			total=Decimal('300.00'),
		)
		invoice_a = Invoice.objects.create(
			pedido=pedido,
			cliente=self.cliente,
			metodo_entrega='CUSTOMER_PICK_UP',
			estado='GENERADA',
			subtotal=Decimal('100.00'),
			total_neto=Decimal('100.00'),
			creada_por=self.user,
			quickbooks_id='QB-INV-A',
			sync_status='SYNCED',
		)
		# Second open order + invoice for remaining pending
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
			invoice=invoice_a,
			presentacion=self.presentacion,
			producto_nombre=self.producto.nombre,
			presentacion_nombre=self.presentacion.nombre,
			cantidad_facturada=10,
			precio_unitario=Decimal('10.00'),
			subtotal=Decimal('100.00'),
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
		stock = StockPresentacion.objects.get(presentacion=self.presentacion)
		stock.stock_fisico = 25
		stock.save()

		snapshot = availability_snapshot([self.presentacion.id])[self.presentacion.id]
		self.assertEqual(snapshot['quick_inventory'], 25)
		self.assertEqual(snapshot['sales_pending_sync'], 20)
		self.assertEqual(snapshot['available'], 5)

	def test_presentacion_is_quickbooks_linked(self):
		self.assertTrue(presentacion_is_quickbooks_linked(self.presentacion))
		self.presentacion.quickbooks_id = ''
		self.presentacion.save(update_fields=['quickbooks_id'])
		self.producto.quickbooks_id = ''
		self.producto.save(update_fields=['quickbooks_id'])
		self.presentacion.refresh_from_db()
		self.assertFalse(presentacion_is_quickbooks_linked(self.presentacion))
