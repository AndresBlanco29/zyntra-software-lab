from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from config.clientes.models import Cliente
from config.productos.models import Categoria, ConfiguracionPrecios, Marca, Presentacion, Producto
from config.usuarios.models import Usuario


class ConfiguracionPreciosTests(TestCase):
	def setUp(self):
		categoria = Categoria.objects.create(nombre='Bebidas Test')
		marca = Marca.objects.create(nombre='Marca Test')
		producto = Producto.objects.create(nombre='Producto Test', categoria=categoria, marca=marca)
		self.presentacion = Presentacion.objects.create(
			producto=producto,
			nombre='Caja',
			unidades=1,
			tipo_contenido='caja',
			costo=Decimal('100.00'),
		)
		self.admin = Usuario.objects.create_user(username='admin-prices', password='secret123', role='admin')

	def test_presentacion_uses_cost_divided_by_one_minus_percentage(self):
		configuracion = ConfiguracionPrecios.obtener()
		configuracion.porcentaje_1 = Decimal('30')
		configuracion.porcentaje_2 = Decimal('20')
		configuracion.porcentaje_3 = Decimal('10')
		configuracion.porcentaje_4 = Decimal('5')
		configuracion.porcentaje_5 = Decimal('1')
		configuracion.save()

		self.presentacion.save()
		self.presentacion.refresh_from_db()

		self.assertEqual(self.presentacion.precio_1, Decimal('142.86'))
		self.assertEqual(self.presentacion.precio_2, Decimal('125.00'))

	def test_configurar_precios_rejects_percentages_of_100_or_more(self):
		self.client.force_login(self.admin)

		response = self.client.post(reverse('configurar_precios'), {
			'porcentaje_1': '100',
			'porcentaje_2': '20',
			'porcentaje_3': '30',
			'porcentaje_4': '40',
			'porcentaje_5': '50',
		})

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Utility percentages must be less than 100 because the formula is cost / (1 - percentage).')
		configuracion = ConfiguracionPrecios.obtener()
		self.assertEqual(configuracion.porcentaje_1, Decimal('10'))

	def test_configurar_precios_rounds_values_to_two_decimals(self):
		self.client.force_login(self.admin)

		response = self.client.post(reverse('configurar_precios'), {
			'porcentaje_1': '12.3456',
			'porcentaje_2': '13',
			'porcentaje_3': '14.1',
			'porcentaje_4': '15.678',
			'porcentaje_5': '16.999',
		})

		self.assertEqual(response.status_code, 302)
		configuracion = ConfiguracionPrecios.obtener()
		self.assertEqual(configuracion.porcentaje_1, Decimal('12.35'))
		self.assertEqual(configuracion.porcentaje_2, Decimal('13.00'))
		self.assertEqual(configuracion.porcentaje_3, Decimal('14.10'))
		self.assertEqual(configuracion.porcentaje_4, Decimal('15.68'))
		self.assertEqual(configuracion.porcentaje_5, Decimal('17.00'))

		response = self.client.get(reverse('configurar_precios'))
		self.assertContains(response, 'value="12.35"')
		self.assertContains(response, 'value="13.00"')
		self.assertContains(response, 'value="14.10"')
		self.assertContains(response, 'value="15.68"')
		self.assertContains(response, 'value="17.00"')


class CatalogCustomerPriceTierTests(TestCase):
	def setUp(self):
		categoria = Categoria.objects.create(nombre='Snacks Test')
		marca = Marca.objects.create(nombre='Marca Catalogo')
		self.producto = Producto.objects.create(nombre='Totopos', categoria=categoria, marca=marca, activo=True)
		self.presentacion = Presentacion.objects.create(
			producto=self.producto,
			nombre='Caja',
			unidades=12,
			tipo_contenido='caja',
			costo=Decimal('100.00'),
		)
		self.usuario = Usuario.objects.create_user(
			username='cliente-precio',
			password='secret123',
			role='cliente',
			is_active=True,
		)
		Cliente.objects.create(
			usuario=self.usuario,
			nombre_empresa='Cliente Precio LLC',
			telefono='1234567890',
			direccion='123 Main St',
			ciudad='Houston',
			estado='Texas',
			codigo_postal='77001',
			pais='USA',
			sales_tax_number='99887766',
			certificado_tax='certificados/test.pdf',
			declaracion_fiscal_aceptada=True,
			aprobado=True,
			estado_revision=Cliente.REVIEW_STATUS_APPROVED,
			nivel_precio=3,
		)

	def test_presentacion_returns_price_for_assigned_tier(self):
		self.assertEqual(self.presentacion.get_price_for_tier(3), self.presentacion.precio_3)

	def test_catalog_shows_customer_assigned_price(self):
		self.client.force_login(self.usuario)

		response = self.client.get(reverse('catalogo'))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Your price')
		self.assertContains(response, f'data-price="{self.presentacion.precio_3}"')
