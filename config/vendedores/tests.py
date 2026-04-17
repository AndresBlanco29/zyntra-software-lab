import json
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


class VendedorEditarClienteTests(TestCase):
	def setUp(self):
		self.vendor = Usuario.objects.create_user(
			username='vendor-edit-client',
			password='secret123',
			role='vendedor',
		)
		self.admin = Usuario.objects.create_user(
			username='admin-edit-client',
			password='secret123',
			role='admin',
		)
		self.customer_user = Usuario.objects.create_user(
			username='customer-edit-client',
			password='secret123',
			role='cliente',
			email='cliente@test.com',
		)
		self.customer = Cliente.objects.create(
			usuario=self.customer_user,
			nombre_empresa='Cliente Editable',
			telefono='5551234567',
			direccion='123 Test St',
			ciudad='Atlanta',
			estado='Georgia',
			codigo_postal='30301',
			pais='USA',
			sales_tax_number='TX-VENDOR-2',
			certificado_tax='certificados/test.pdf',
		)

	def test_admin_can_edit_customer_with_manual_international_location(self):
		self.client.force_login(self.admin)

		response = self.client.post(
			reverse('editar_cliente'),
			data=json.dumps({
				'cliente_id': self.customer.id,
				'empresa': 'Cliente Ruta Colombia',
				'correo': 'rutas@cliente.com',
				'telefono': '3001234567',
				'direccion': 'Cra 45 # 12-34',
				'ciudad': 'Medellin',
				'estado': 'Antioquia',
				'codigo_postal': '050021',
				'pais': 'Colombia',
				'manual_location': True,
			}),
			content_type='application/json',
		)

		self.assertEqual(response.status_code, 200)
		self.assertJSONEqual(response.content, {'success': True, 'message': 'Cliente actualizado correctamente'})
		self.customer.refresh_from_db()
		self.customer_user.refresh_from_db()
		self.assertEqual(self.customer.nombre_empresa, 'Cliente Ruta Colombia')
		self.assertEqual(self.customer.telefono, '3001234567')
		self.assertEqual(self.customer.direccion, 'Cra 45 # 12-34')
		self.assertEqual(self.customer.ciudad, 'Medellin')
		self.assertEqual(self.customer.estado, 'Antioquia')
		self.assertEqual(self.customer.codigo_postal, '050021')
		self.assertEqual(self.customer.pais, 'Colombia')
		self.assertEqual(self.customer_user.email, 'rutas@cliente.com')

	def test_vendor_can_edit_customer_with_valid_usa_selector_location(self):
		self.client.force_login(self.vendor)

		response = self.client.post(
			reverse('editar_cliente'),
			data=json.dumps({
				'cliente_id': self.customer.id,
				'empresa': 'Cliente Dallas',
				'correo': 'dallas@cliente.com',
				'telefono': '2145551234',
				'direccion': '456 Commerce St',
				'ciudad': 'Dallas',
				'estado': 'Texas',
				'codigo_postal': '75201',
				'pais': 'USA',
				'manual_location': False,
			}),
			content_type='application/json',
		)

		self.assertEqual(response.status_code, 200)
		self.customer.refresh_from_db()
		self.assertEqual(self.customer.estado, 'Texas')
		self.assertEqual(self.customer.ciudad, 'Dallas')
		self.assertEqual(self.customer.pais, 'USA')

	def test_edit_customer_rejects_invalid_usa_city_for_selected_state(self):
		self.client.force_login(self.admin)

		response = self.client.post(
			reverse('editar_cliente'),
			data=json.dumps({
				'cliente_id': self.customer.id,
				'empresa': 'Cliente Invalido',
				'correo': 'invalido@cliente.com',
				'telefono': '2145551234',
				'direccion': '789 Test Ave',
				'ciudad': 'Bogota',
				'estado': 'Texas',
				'codigo_postal': '75201',
				'pais': 'USA',
				'manual_location': False,
			}),
			content_type='application/json',
		)

		self.assertEqual(response.status_code, 400)
		self.assertJSONEqual(response.content, {
			'success': False,
			'message': 'Debes seleccionar una ciudad valida para el estado elegido.',
		})
