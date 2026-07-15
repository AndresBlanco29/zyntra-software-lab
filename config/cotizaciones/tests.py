from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from config.clientes.models import Cliente
from config.cotizaciones.models import Cotizacion, CotizacionItem
from config.productos.models import Categoria, Marca, Presentacion, Producto
from config.usuarios.models import Usuario


class BackofficeQuotesHubTests(TestCase):
	def setUp(self):
		self.admin = Usuario.objects.create_user(username='admin-quotes-hub', password='secret123', role='admin')
		self.backoffice = Usuario.objects.create_user(username='bo-quotes-hub', password='secret123', role='backoffice')
		self.vendedor = Usuario.objects.create_user(username='vendor-quotes-hub', password='secret123', role='vendedor')
		self.cliente_user = Usuario.objects.create_user(username='cliente-quotes-hub', password='secret123', role='cliente')
		self.cliente = Cliente.objects.create(
			usuario=self.cliente_user,
			nombre_empresa='Quotes Hub Customer',
			telefono='5550001111',
			direccion='100 Test St',
			ciudad='Dallas',
			estado='TX',
			codigo_postal='75001',
			pais='USA',
		)
		categoria = Categoria.objects.create(nombre='Quotes Cat')
		marca = Marca.objects.create(nombre='Quotes Brand')
		producto = Producto.objects.create(nombre='Quote Product', categoria=categoria, marca=marca)
		self.presentacion = Presentacion.objects.create(
			producto=producto,
			nombre='Caja',
			unidades=12,
			tipo_contenido='unidades',
			costo=Decimal('10.00'),
		)
		self.quote = Cotizacion.objects.create(
			cliente=self.cliente,
			vendedor=self.vendedor,
			estado='BORRADOR',
			total=Decimal('25.00'),
			backoffice_pricing_confirmed=True,
		)
		CotizacionItem.objects.create(
			cotizacion=self.quote,
			presentacion=self.presentacion,
			cantidad=1,
			precio=Decimal('25.00'),
			subtotal=Decimal('25.00'),
		)

	def test_quotes_menu_removed_from_customers_and_listed_under_commercial(self):
		self.client.force_login(self.admin)
		response = self.client.get(reverse('backoffice_cotizaciones'))
		self.assertEqual(response.status_code, 200)
		# Quotes hub page itself
		self.assertContains(response, 'Create quote')
		self.assertContains(response, 'Search by quote #, customer, or status')
		# Sidebar rendered via base: Create Quote link to tomar_cotizacion should not appear as Customers item
		html = response.content.decode('utf-8')
		self.assertIn('href="/cotizaciones/backoffice/"', html.replace("'", '"') or html)
		# The old Customers nav item label for create should not be present as a dedicated Customers link
		self.assertNotRegex(
			html,
			r'Customers &amp; Sales[\s\S]*?Create Quote',
		)
		self.assertRegex(
			html,
			r'Orders[\s\S]*?Quotes',
		)

	def test_quotes_list_search_and_create_link(self):
		self.cliente.nombre_empresa = 'Alpha Unique Quotes Customer'
		self.cliente.save(update_fields=['nombre_empresa'])
		other_user = Usuario.objects.create_user(username='cliente-quotes-other', password='secret123', role='cliente')
		other_cliente = Cliente.objects.create(
			usuario=other_user,
			nombre_empresa='Beta Unique Quotes Customer',
			telefono='5550002222',
			direccion='200 Test St',
			ciudad='Dallas',
			estado='TX',
			codigo_postal='75001',
			pais='USA',
		)
		other = Cotizacion.objects.create(
			cliente=other_cliente,
			estado='LISTA_PARA_CONFIRMACION',
			total=Decimal('40.00'),
		)
		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_cotizaciones'), {'q': 'Alpha Unique'})
		self.assertEqual(response.status_code, 200)
		self.assertEqual(list(response.context['cotizaciones']), [self.quote])
		self.assertEqual(response.context['search_query'], 'Alpha Unique')
		self.assertContains(response, 'Alpha Unique Quotes Customer')
		self.assertContains(response, reverse('tomar_cotizacion'))
		listed_ids = {c.id for c in response.context['cotizaciones']}
		self.assertIn(self.quote.id, listed_ids)
		self.assertNotIn(other.id, listed_ids)

	def test_quotes_list_confirmed_filter_shows_generate_order(self):
		self.quote.estado = 'CONFIRMADA_CLIENTE'
		self.quote.save(update_fields=['estado'])
		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_cotizaciones'), {'view': 'confirmed'})
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, f'#{self.quote.id}')
		self.assertContains(response, 'Generate order')
		self.assertContains(response, reverse('generar_pedido_desde_cotizacion', args=[self.quote.id]))
