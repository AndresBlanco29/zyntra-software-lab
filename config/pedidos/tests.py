from decimal import Decimal

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
		)
		registrar_entrada_manual(presentacion=self.presentacion, cantidad=10, observacion='Initial stock')

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

		with self.assertRaises(ValidationError):
			guardar_verificacion_picking(
				pedido=self.pedido,
				seleccionador=self.selector,
				cantidades_reales={self.item.id: 1},
				nota='   ',
				nota_resuelta=False,
			)

	def test_verification_updates_quantities_and_blocks_when_unresolved(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)

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
		self.assertTrue(Notificacion.objects.filter(titulo__icontains='verification completed').exists())

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
		self.assertNotContains(response, f'#{other_order.id}')

		detail_response = self.client.get(reverse('selector_picking_detail', args=[self.pedido.id]))
		self.assertEqual(detail_response.status_code, 200)
		self.assertNotContains(detail_response, '12.00')

		other_detail_response = self.client.get(reverse('selector_picking_detail', args=[other_order.id]))
		self.assertEqual(other_detail_response.status_code, 404)

		other_item.refresh_from_db()

	def test_backoffice_cannot_move_blocked_order_forward(self):
		asignar_picking_a_seleccionador(pedido=self.pedido, seleccionador=self.selector)
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
