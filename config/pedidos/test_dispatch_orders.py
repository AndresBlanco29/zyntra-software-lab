from decimal import Decimal

from django.test import TestCase

from config.clientes.models import Cliente
from config.facturacion.models import Delivery, Invoice
from config.facturacion.services import eliminar_invoice, ensure_delivery_for_invoice, generar_invoice_desde_picking
from config.inventario.services import registrar_entrada_manual
from config.pedidos.dispatch_orders import build_dispatch_order_page, get_dispatch_order_counts
from config.pedidos.models import Pedido, PedidoItem
from config.productos.models import Categoria, Marca, Presentacion, Producto
from config.usuarios.models import Usuario


class DispatchOrderClassificationTests(TestCase):
	def setUp(self):
		self.backoffice = Usuario.objects.create_user(username='bo-dispatch', password='secret123', role='backoffice')
		self.driver = Usuario.objects.create_user(username='driver-dispatch', password='secret123', role='driver')
		self.cliente_user = Usuario.objects.create_user(username='cliente-dispatch', password='secret123', role='cliente')
		self.cliente = Cliente.objects.create(
			usuario=self.cliente_user,
			nombre_empresa='PRUEBA DISPATCH',
			telefono='5550002222',
			direccion='100 Main St',
			ciudad='Dallas',
			estado='TX',
			codigo_postal='75001',
			pais='USA',
			sales_tax_number='TX-DISPATCH',
			certificado_tax='certificados/test.pdf',
		)
		categoria = Categoria.objects.create(nombre='Dispatch')
		marca = Marca.objects.create(nombre='Marca Dispatch')
		producto = Producto.objects.create(nombre='Producto Dispatch', categoria=categoria, marca=marca)
		self.presentacion = Presentacion.objects.create(
			producto=producto,
			nombre='Caja',
			unidades=1,
			tipo_contenido='caja',
			precio_1=Decimal('10.00'),
		)
		registrar_entrada_manual(presentacion=self.presentacion, cantidad=20, observacion='Seed stock')

	def _create_verified_pedido(self):
		pedido = Pedido.objects.create(
			cliente=self.cliente,
			origen='VENDEDOR',
			estado='VERIFICADO_AJUSTADO',
			total=Decimal('122.34'),
		)
		PedidoItem.objects.create(
			pedido=pedido,
			presentacion=self.presentacion,
			cantidad_solicitada=2,
			cantidad=2,
			cantidad_inventario_aplicada=2,
			precio=Decimal('61.17'),
			subtotal=Decimal('122.34'),
		)
		return pedido

	def _order_ids_for_view(self, view_mode):
		_, page_obj = build_dispatch_order_page(view_mode=view_mode, page_number=1, page_size=50)
		return [row.source_id for row in page_obj if row.record_type == 'order']

	def test_deleted_invoice_order_moves_to_cancelled_tab(self):
		pedido = self._create_verified_pedido()
		invoice = generar_invoice_desde_picking(
			pedido=pedido,
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
		)
		eliminar_invoice(invoice=invoice)
		pedido.refresh_from_db()

		self.assertEqual(pedido.estado, 'CANCELADO')
		self.assertNotIn(pedido.id, self._order_ids_for_view('in-progress'))
		self.assertIn(pedido.id, self._order_ids_for_view('cancelled'))

	def test_driver_completed_delivery_moves_order_to_completed_tab(self):
		pedido = self._create_verified_pedido()
		invoice = generar_invoice_desde_picking(
			pedido=pedido,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			usuario=self.backoffice,
		)
		delivery = ensure_delivery_for_invoice(invoice)
		delivery.estado = 'ENTREGADA_PAGADA'
		delivery.save(update_fields=['estado', 'updated_at'])
		pedido.estado = 'INVOICE_GENERADA'
		pedido.save(update_fields=['estado', 'actualizada_en'])

		self.assertNotIn(pedido.id, self._order_ids_for_view('in-progress'))
		self.assertIn(pedido.id, self._order_ids_for_view('completed'))

	def test_dispatch_counts_reflect_cancelled_and_completed_buckets(self):
		cancelled_pedido = self._create_verified_pedido()
		cancelled_invoice = generar_invoice_desde_picking(
			pedido=cancelled_pedido,
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
		)
		eliminar_invoice(invoice=cancelled_invoice)

		completed_pedido = self._create_verified_pedido()
		completed_invoice = Invoice.objects.create(
			pedido=completed_pedido,
			cliente=self.cliente,
			metodo_entrega='RUTA_DRIVER',
			driver=self.driver,
			subtotal=completed_pedido.total,
			total_neto=completed_pedido.total,
			saldo_cliente=completed_pedido.total,
			creada_por=self.backoffice,
		)
		completed_pedido.estado = 'DESPACHADO'
		completed_pedido.save(update_fields=['estado', 'actualizada_en'])
		Delivery.objects.create(
			invoice=completed_invoice,
			driver=self.driver,
			estado='ENTREGADA_PAGADA',
			delivery_address=self.cliente.direccion,
			delivery_city=self.cliente.ciudad,
			delivery_state=self.cliente.estado,
			delivery_postal_code=self.cliente.codigo_postal,
			delivery_country=self.cliente.pais,
		)

		counts = get_dispatch_order_counts()
		self.assertGreaterEqual(counts['cancelled_count'], 1)
		self.assertGreaterEqual(counts['completed_count'], 1)
