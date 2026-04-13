from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from config.clientes.models import Cliente
from config.pedidos.models import Pedido
from config.productos.models import Categoria, Marca, Presentacion, Producto
from config.usuarios.models import Usuario


class VendedorPedidoTests(TestCase):
	def setUp(self):
		self.vendor = Usuario.objects.create_user(
			username='vendor-order-test',
			password='secret123',
			role='vendedor',
		)
		self.customer_user = Usuario.objects.create_user(
			username='customer-order-test',
			password='secret123',
			role='cliente',
		)
		self.customer = Cliente.objects.create(
			usuario=self.customer_user,
			nombre_empresa='Cliente Vendor Test',
			telefono='5551234567',
			direccion='123 Test St',
			ciudad='Atlanta',
			estado='GA',
			codigo_postal='30301',
			pais='USA',
			sales_tax_number='TX-VENDOR-1',
			certificado_tax='certificados/test.pdf',
		)
		categoria = Categoria.objects.create(nombre='Bebidas')
		marca = Marca.objects.create(nombre='Marca Vendor Test')
		producto = Producto.objects.create(nombre='Coca-Colaaaaaaaa', categoria=categoria, marca=marca)
		self.presentacion = Presentacion.objects.create(
			producto=producto,
			nombre='caja',
			unidades=1,
			tipo_contenido='caja',
			precio_1=Decimal('233.00'),
		)

	def test_enviar_pedido_returns_json_error_when_stock_is_unavailable(self):
		self.client.force_login(self.vendor)
		session = self.client.session
		session['cliente_id'] = self.customer.id
		session['pedido'] = {
			'1': {
				'presentacion_id': str(self.presentacion.id),
				'producto_id': self.presentacion.producto_id,
				'nombre': self.presentacion.producto.nombre,
				'presentacion_nombre': self.presentacion.nombre,
				'precio': 233.0,
				'cantidad': 1,
			}
		}
		session.save()

		response = self.client.post(reverse('enviar_pedido'), {'tipo_orden': 'VISITA'})

		self.assertEqual(response.status_code, 409)
		self.assertEqual(Pedido.objects.count(), 0)
		self.assertJSONEqual(
			response.content,
			{
				'success': False,
				'error': 'Insufficient available stock for Coca-Colaaaaaaaa - caja. Requested 1, available 0.',
			},
		)
