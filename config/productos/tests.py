from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from config.clientes.models import Cliente
from config.productos.models import Categoria, ConfiguracionDescuentos, ConfiguracionPrecios, Marca, Presentacion, Producto
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


class ConfiguracionDescuentosTests(TestCase):
	def setUp(self):
		self.admin = Usuario.objects.create_user(username='admin-discounts', password='secret123', role='admin')

	def test_obtener_creates_default_discount_presets(self):
		configuracion = ConfiguracionDescuentos.obtener()

		self.assertEqual(configuracion.descuento_1, Decimal('0.25'))
		self.assertEqual(configuracion.descuento_2, Decimal('0.50'))
		self.assertEqual(len(configuracion.opciones_activas()), 10)

	def test_opciones_activas_skips_zero_amounts(self):
		configuracion = ConfiguracionDescuentos.obtener()
		configuracion.descuento_3 = Decimal('0.00')
		configuracion.descuento_4 = Decimal('0.00')
		configuracion.save()

		options = configuracion.opciones_activas()
		self.assertEqual(len(options), 8)
		self.assertEqual(options[0]['key'], 'descuento_1')
		self.assertEqual(options[0]['value'], '0.25')

	def test_configurar_descuentos_rejects_negative_values(self):
		self.client.force_login(self.admin)

		response = self.client.post(reverse('configurar_descuentos'), {
			'descuento_1': '-0.50',
			'descuento_2': '0.50',
			'descuento_3': '0.75',
			'descuento_4': '1.00',
			'descuento_5': '1.50',
			'descuento_6': '2.00',
			'descuento_7': '2.50',
			'descuento_8': '3.00',
			'descuento_9': '4.00',
			'descuento_10': '5.00',
		})

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Discount amounts must be zero or greater.')
		configuracion = ConfiguracionDescuentos.obtener()
		self.assertEqual(configuracion.descuento_1, Decimal('0.25'))

	def test_configurar_descuentos_rounds_values_to_two_decimals(self):
		self.client.force_login(self.admin)

		response = self.client.post(reverse('configurar_descuentos'), {
			'descuento_1': '0.255',
			'descuento_2': '0.50',
			'descuento_3': '0.00',
			'descuento_4': '1.00',
			'descuento_5': '1.50',
			'descuento_6': '2.00',
			'descuento_7': '2.50',
			'descuento_8': '3.00',
			'descuento_9': '4.00',
			'descuento_10': '5.00',
		})

		self.assertEqual(response.status_code, 302)
		configuracion = ConfiguracionDescuentos.obtener()
		self.assertEqual(configuracion.descuento_1, Decimal('0.26'))
		self.assertEqual(configuracion.descuento_3, Decimal('0.00'))
		self.assertEqual(len(configuracion.opciones_activas()), 9)


class AdminProductosListTests(TestCase):
	def setUp(self):
		self.admin = Usuario.objects.create_user(username='admin-products', password='secret123', role='admin')
		self.categoria = Categoria.objects.create(nombre='General Test')
		self.marca = Marca.objects.create(nombre='Marca Admin Test')
		for index in range(55):
			Producto.objects.create(
				nombre=f'Producto Admin {index:03d}',
				categoria=self.categoria,
				marca=self.marca,
				codigo_barras=f'BAR{index:05d}',
			)

	def test_lista_productos_paginates_results(self):
		self.client.force_login(self.admin)

		response = self.client.get(reverse('lista_productos'))

		self.assertEqual(response.status_code, 200)
		self.assertEqual(len(response.context['productos']), 50)
		self.assertEqual(response.context['page_obj'].paginator.count, 55)
		self.assertContains(response, 'page=2"')
		self.assertContains(response, 'aria-current="page"')

	def test_lista_productos_search_filters_on_server(self):
		self.client.force_login(self.admin)

		response = self.client.get(reverse('lista_productos'), {'q': 'BAR00012'})

		self.assertEqual(response.status_code, 200)
		self.assertEqual(len(response.context['productos']), 1)
		self.assertContains(response, 'Producto Admin 012')


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
		self.usuario_sin_precios = Usuario.objects.create_user(
			username='cliente-sin-precio',
			password='secret123',
			role='cliente',
			is_active=True,
		)
		Cliente.objects.create(
			usuario=self.usuario_sin_precios,
			nombre_empresa='Cliente Sin Precio LLC',
			telefono='1234567891',
			direccion='456 Main St',
			ciudad='Houston',
			estado='Texas',
			codigo_postal='77002',
			pais='USA',
			sales_tax_number='11223344',
			certificado_tax='certificados/test-no-price.pdf',
			declaracion_fiscal_aceptada=True,
			aprobado=True,
			estado_revision=Cliente.REVIEW_STATUS_APPROVED,
			nivel_precio=Cliente.PRICE_TIER_UNASSIGNED,
		)

	def test_presentacion_returns_price_for_assigned_tier(self):
		self.assertEqual(self.presentacion.get_price_for_tier(3), self.presentacion.precio_3)

	def test_get_price_for_tier_recalculates_from_cost_when_stored_prices_are_stale(self):
		configuracion = ConfiguracionPrecios.obtener()
		configuracion.porcentaje_1 = Decimal('12')
		configuracion.save()

		Presentacion.objects.filter(pk=self.presentacion.pk).update(
			costo=Decimal('12.49'),
			precio_1=Decimal('14.99'),
		)
		self.presentacion.refresh_from_db()

		self.assertEqual(self.presentacion.get_price_for_tier(1), Decimal('14.19'))

	def test_catalog_shows_customer_assigned_price(self):
		self.client.force_login(self.usuario)

		response = self.client.get(reverse('catalogo'))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Your price')
		self.assertContains(response, f'data-price="{self.presentacion.precio_3}"')

	def test_catalog_shows_recalculated_tier_price_when_quickbooks_price_is_stale(self):
		configuracion = ConfiguracionPrecios.obtener()
		configuracion.porcentaje_1 = Decimal('12')
		configuracion.save()

		self.usuario.cliente.nivel_precio = 1
		self.usuario.cliente.save(update_fields=['nivel_precio'])

		Presentacion.objects.filter(pk=self.presentacion.pk).update(
			costo=Decimal('12.49'),
			precio_1=Decimal('14.99'),
		)

		self.client.force_login(self.usuario)
		response = self.client.get(reverse('catalogo'))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, '14.19')
		self.assertNotContains(response, '14.99')

	def test_catalog_hides_prices_when_customer_has_no_assigned_tier(self):
		self.client.force_login(self.usuario_sin_precios)

		response = self.client.get(reverse('catalogo'))

		self.assertEqual(response.status_code, 200)
		self.assertNotContains(response, 'Your price')
		self.assertNotContains(response, 'Assigned tier')
		self.assertContains(response, 'agregar-btn')
		self.assertNotContains(response, 'Pending price assignment')

	def test_catalog_lists_products_in_alphabetical_order(self):
		self.producto.nombre = 'Zebra Chips'
		self.producto.save(update_fields=['nombre'])
		otro_producto = Producto.objects.create(
			nombre='Alpha Snacks',
			categoria=self.producto.categoria,
			marca=self.producto.marca,
			activo=True,
		)
		Presentacion.objects.create(
			producto=otro_producto,
			nombre='Caja',
			unidades=1,
			tipo_contenido='caja',
			costo=Decimal('10.00'),
		)

		response = self.client.get(reverse('catalogo'))

		self.assertEqual(response.status_code, 200)
		names = [producto.nombre for producto in response.context['productos']]
		self.assertEqual(names[0], 'Alpha Snacks')
		self.assertEqual(names[1], 'Zebra Chips')

	def test_catalog_paginates_products(self):
		for index in range(55):
			producto = Producto.objects.create(
				nombre=f'Catalog Page {index:03d}',
				categoria=self.producto.categoria,
				marca=self.producto.marca,
				activo=True,
			)
			Presentacion.objects.create(
				producto=producto,
				nombre='Caja',
				unidades=1,
				tipo_contenido='caja',
				costo=Decimal('10.00'),
			)

		self.client.force_login(self.usuario)
		response = self.client.get(reverse('catalogo'))

		self.assertEqual(response.status_code, 200)
		self.assertEqual(len(response.context['productos']), 50)
		self.assertEqual(response.context['page_obj'].paginator.count, 56)
		self.assertContains(response, 'page=2"')
		self.assertContains(response, 'aria-current="page"')

	@patch('config.productos.views.CATALOGO_PAGE_SIZE', 5)
	def test_catalog_shows_visible_page_window_links(self):
		for index in range(99):
			producto = Producto.objects.create(
				nombre=f'Catalog Jump {index:03d}',
				categoria=self.producto.categoria,
				marca=self.producto.marca,
				activo=True,
			)
			Presentacion.objects.create(
				producto=producto,
				nombre='Caja',
				unidades=1,
				tipo_contenido='caja',
				costo=Decimal('10.00'),
			)

		self.client.force_login(self.usuario)
		response = self.client.get(reverse('catalogo'))

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.context['page_obj'].paginator.num_pages, 20)
		self.assertContains(response, 'page=2"')
		self.assertContains(response, 'page=3"')
		self.assertContains(response, 'page=4"')
		self.assertContains(response, 'page=5"')
		self.assertContains(response, 'page=20"')
		self.assertNotContains(response, 'Page 1 of')

	@patch('config.productos.views.CATALOGO_PAGE_SIZE', 5)
	@patch('config.productos.views.load_cliente_favorite_productos', return_value=[])
	def test_catalog_hides_favorites_after_first_page(self, mock_favorites):
		mock_favorites.return_value = [self.producto]
		for index in range(9):
			producto = Producto.objects.create(
				nombre=f'Catalog Fav Page {index:03d}',
				categoria=self.producto.categoria,
				marca=self.producto.marca,
				activo=True,
			)
			Presentacion.objects.create(
				producto=producto,
				nombre='Caja',
				unidades=1,
				tipo_contenido='caja',
				costo=Decimal('10.00'),
			)

		self.client.force_login(self.usuario)
		first_page = self.client.get(reverse('catalogo'))
		second_page = self.client.get(reverse('catalogo'), {'page': 2})

		self.assertEqual(first_page.status_code, 200)
		self.assertEqual(second_page.status_code, 200)
		self.assertTrue(first_page.context['productos_favoritos'])
		self.assertContains(first_page, 'Your Favorite Products')
		self.assertEqual(second_page.context['productos_favoritos'], [])
		self.assertNotContains(second_page, 'Your Favorite Products')
		self.assertEqual(mock_favorites.call_count, 1)


class ProductPresentationFormTests(TestCase):
	def setUp(self):
		self.admin = Usuario.objects.create_user(username='admin-presentations', password='secret123', role='admin')
		self.categoria = Categoria.objects.create(nombre='Bebidas Admin')
		self.marca = Marca.objects.create(nombre='Marca Admin')

	def test_parse_packaging_from_name_api_detects_lt_pattern(self):
		self.client.force_login(self.admin)
		response = self.client.get(
			reverse('parse_packaging_from_name'),
			{'nombre': 'AGUA 12/16.9 LT'},
		)

		self.assertEqual(response.status_code, 200)
		payload = response.json()
		self.assertTrue(payload['ok'])
		self.assertEqual(payload['defaults']['units_per_case'], 12)

	def test_parse_packaging_from_name_api_rejects_generic_name(self):
		self.client.force_login(self.admin)
		response = self.client.get(
			reverse('parse_packaging_from_name'),
			{'nombre': 'VARIOS'},
		)

		self.assertEqual(response.status_code, 200)
		payload = response.json()
		self.assertFalse(payload['ok'])

	def test_crear_producto_saves_new_presentations_with_bracket_field_names(self):
		self.client.force_login(self.admin)
		response = self.client.post(reverse('crear_producto'), {
			'nombre': 'VELA 6 CT',
			'nombre_en': '',
			'codigo_barras': '7500000000001',
			'categoria': str(self.categoria.id),
			'marca': str(self.marca.id),
			'activo': 'on',
			'presentacion_nueva_nombre[]': ['Caja'],
			'presentacion_nueva_tipo_contenido[]': ['piezas'],
			'presentacion_nueva_unidades[]': ['6'],
			'presentacion_nueva_costo[]': ['12.50'],
			'presentacion_nueva_stock[]': ['25'],
		})

		self.assertEqual(response.status_code, 302)
		producto = Producto.objects.get(codigo_barras='7500000000001')
		presentacion = producto.presentaciones.get()
		self.assertEqual(presentacion.nombre, 'Caja')
		self.assertEqual(presentacion.unidades, 6)
		self.assertEqual(presentacion.stock_operativo.stock_fisico, 25)

	def test_crear_and_editar_producto_save_pallet_fields(self):
		self.client.force_login(self.admin)
		create_response = self.client.post(reverse('crear_producto'), {
			'nombre': 'TORTILLA 12 CT',
			'nombre_en': '',
			'codigo_barras': '7500000000099',
			'categoria': str(self.categoria.id),
			'marca': str(self.marca.id),
			'activo': 'on',
			'presentacion_nueva_nombre[]': ['Caja'],
			'presentacion_nueva_tipo_contenido[]': ['piezas'],
			'presentacion_nueva_unidades[]': ['12'],
			'presentacion_nueva_costo[]': ['20.00'],
			'presentacion_nueva_stock[]': ['0'],
			'presentacion_nueva_pallet_tie[]': ['8'],
			'presentacion_nueva_pallet_high[]': ['6'],
		})
		self.assertEqual(create_response.status_code, 302)
		producto = Producto.objects.get(codigo_barras='7500000000099')
		presentacion = producto.presentaciones.get()
		self.assertEqual(presentacion.pallet_tie, 8)
		self.assertEqual(presentacion.pallet_high, 6)
		self.assertEqual(presentacion.pallet_quantity, 48)

		edit_response = self.client.post(reverse('editar_producto', args=[producto.id]), {
			'nombre': producto.nombre,
			'nombre_en': '',
			'codigo_barras': producto.codigo_barras,
			'categoria': str(self.categoria.id),
			'marca': str(self.marca.id),
			'activo': 'on',
			f'presentacion_nombre_{presentacion.id}': 'Caja',
			f'tipo_contenido_{presentacion.id}': 'piezas',
			f'unidades_{presentacion.id}': '12',
			f'costo_{presentacion.id}': '20.00',
			f'pallet_tie_{presentacion.id}': '10',
			f'pallet_high_{presentacion.id}': '5',
		})
		self.assertEqual(edit_response.status_code, 302)
		presentacion.refresh_from_db()
		self.assertEqual(presentacion.pallet_tie, 10)
		self.assertEqual(presentacion.pallet_high, 5)
		self.assertEqual(presentacion.pallet_quantity, 50)

		get_response = self.client.get(reverse('editar_producto', args=[producto.id]))
		self.assertEqual(get_response.status_code, 200)
		self.assertContains(get_response, 'Pallet tie')
		self.assertContains(get_response, 'Pallet high')
		self.assertContains(get_response, 'Pallet quantity')
		self.assertContains(get_response, 'name="pallet_tie_%s"' % presentacion.id)
		self.assertContains(get_response, 'value="10"')
		self.assertContains(get_response, 'value="5"')
		self.assertContains(get_response, 'value="50"')

	def test_editar_producto_normalizes_placeholder_barcode_to_null(self):
		producto = Producto.objects.create(
			nombre='Editable Product',
			nombre_en='',
			categoria=self.categoria,
			marca=self.marca,
			codigo_barras='TEMP-EDIT',
			activo=True,
		)
		Producto.objects.filter(pk=producto.pk).update(codigo_barras='None')
		producto.refresh_from_db()
		self.assertEqual(producto.codigo_barras, 'None')

		presentacion = Presentacion.objects.create(
			producto=producto,
			nombre='Caja',
			unidades=12,
			tipo_contenido='unidades',
			costo=Decimal('10.00'),
		)
		self.client.force_login(self.admin)
		response = self.client.post(reverse('editar_producto', args=[producto.id]), {
			'nombre': 'Editable Product Updated',
			'nombre_en': '',
			'codigo_barras': 'None',
			'categoria': str(self.categoria.id),
			'marca': str(self.marca.id),
			'activo': 'on',
			f'presentacion_nombre_{presentacion.id}': 'Caja',
			f'tipo_contenido_{presentacion.id}': 'unidades',
			f'unidades_{presentacion.id}': '12',
			f'costo_{presentacion.id}': '10.00',
			f'pallet_tie_{presentacion.id}': '8',
			f'pallet_high_{presentacion.id}': '5',
		})
		self.assertEqual(response.status_code, 302)
		producto.refresh_from_db()
		self.assertEqual(producto.nombre, 'Editable Product Updated')
		self.assertIsNone(producto.codigo_barras)
		self.assertEqual(producto.presentaciones.get().pallet_quantity, 40)

