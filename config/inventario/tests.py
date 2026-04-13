from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from config.clientes.models import Cliente
from config.facturacion.services import aprobar_nota_ajuste, anular_nota_ajuste, crear_nota_ajuste_desde_invoice, generar_invoice_desde_picking
from config.pedidos.models import Pedido, PedidoItem
from config.pedidos.services import crear_pedido_desde_items, guardar_verificacion_picking
from config.productos.models import Categoria, Marca, Presentacion, Producto
from config.usuarios.models import Usuario

from .models import InventarioMovimiento, StockPresentacion
from .services import cancelar_pedido_con_inventario, registrar_entrada_manual


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
			unidades=1,
			tipo_contenido='caja',
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


class InventarioBackofficeViewsTests(TestCase):
	def setUp(self):
		self.backoffice = Usuario.objects.create_user(username='bo-stock-view', password='secret123', role='backoffice')
		categoria = Categoria.objects.create(nombre='Categoria Vista Stock')
		marca = Marca.objects.create(nombre='Marca Vista Stock')
		producto = Producto.objects.create(nombre='Producto Vista Stock', categoria=categoria, marca=marca)
		self.presentacion = Presentacion.objects.create(
			producto=producto,
			nombre='Caja',
			unidades=1,
			tipo_contenido='caja',
			precio_1=Decimal('15.00'),
		)
		self.presentacion_sin_stock = Presentacion.objects.create(
			producto=producto,
			nombre='Unidad',
			unidades=1,
			tipo_contenido='unidad',
			precio_1=Decimal('2.00'),
		)
		registrar_entrada_manual(presentacion=self.presentacion, cantidad=6, observacion='Initial stock', creado_por=self.backoffice)

	def test_inventory_list_view_displays_presentations(self):
		self.client.force_login(self.backoffice)

		response = self.client.get(reverse('backoffice_inventory_list'))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Producto Vista Stock')
		self.assertContains(response, self.presentacion.nombre_traducido)
		self.assertContains(response, self.presentacion_sin_stock.nombre_traducido)

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
