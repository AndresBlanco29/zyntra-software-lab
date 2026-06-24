import json
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from config.clientes.models import Cliente
from config.facturacion.models import Invoice, InvoiceItem
from config.pedidos.models import Pedido, PedidoItem
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
		self.backoffice = Usuario.objects.create_user(
			username='backoffice-order-test',
			password='secret123',
			role='backoffice',
		)

	def _create_customer_invoice(self, *, created_at, quantity, price):
		pedido = Pedido.objects.create(
			cliente=self.customer,
			vendedor=self.vendor,
			origen='VENDEDOR',
			estado='INVOICE_GENERADA',
			total=Decimal(str(price)) * Decimal(str(quantity)),
		)
		Pedido.objects.filter(id=pedido.id).update(creada_en=created_at, actualizada_en=created_at)
		pedido.refresh_from_db()
		pedido_item = PedidoItem.objects.create(
			pedido=pedido,
			presentacion=self.presentacion,
			cantidad_solicitada=quantity,
			cantidad=quantity,
			precio=Decimal(str(price)),
			subtotal=Decimal(str(price)) * Decimal(str(quantity)),
		)
		invoice = Invoice.objects.create(
			pedido=pedido,
			cliente=self.customer,
			metodo_entrega='CUSTOMER_PICK_UP',
			estado='GENERADA',
			subtotal=Decimal(str(price)) * Decimal(str(quantity)),
			total_neto=Decimal(str(price)) * Decimal(str(quantity)),
		)
		Invoice.objects.filter(id=invoice.id).update(creada_en=created_at, actualizada_en=created_at)
		invoice.refresh_from_db()
		InvoiceItem.objects.create(
			invoice=invoice,
			pedido_item=pedido_item,
			presentacion=self.presentacion,
			producto_nombre=self.presentacion.producto.nombre,
			presentacion_nombre=self.presentacion.nombre,
			cantidad_facturada=quantity,
			precio_unitario=Decimal(str(price)),
			subtotal=Decimal(str(price)) * Decimal(str(quantity)),
		)
		return invoice

	def _create_customer_order(self, *, created_at, quantity, price):
		pedido = Pedido.objects.create(
			cliente=self.customer,
			vendedor=self.vendor,
			origen='VENDEDOR',
			estado='RECIBIDO',
			total=Decimal(str(price)) * Decimal(str(quantity)),
		)
		Pedido.objects.filter(id=pedido.id).update(creada_en=created_at, actualizada_en=created_at)
		pedido.refresh_from_db()
		PedidoItem.objects.create(
			pedido=pedido,
			presentacion=self.presentacion,
			cantidad_solicitada=quantity,
			cantidad=quantity,
			precio=Decimal(str(price)),
			subtotal=Decimal(str(price)) * Decimal(str(quantity)),
		)
		return pedido

	def test_enviar_pedido_allows_order_without_available_stock(self):
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

		self.assertEqual(response.status_code, 200)
		pedido = Pedido.objects.get()
		self.assertJSONEqual(
			response.content,
			{
				'success': True,
				'pedido_id': pedido.id,
			},
		)
		item = pedido.items.get()
		self.assertEqual(item.cantidad_reservada_inventario, 0)
		self.assertEqual(item.cantidad_inventario_aplicada, 0)

	def test_agregar_producto_pedido_preserves_selected_price_tier_in_order_summary(self):
		self.client.force_login(self.vendor)
		session = self.client.session
		session['cliente_id'] = self.customer.id
		session.save()

		response = self.client.post(reverse('agregar_producto_pedido'), {
			'presentacion_id': self.presentacion.id,
			'cantidad': 2,
			'precio': str(self.presentacion.precio_2),
			'precio_key': 'precio_2',
		})

		self.assertEqual(response.status_code, 200)
		self.assertTrue(response.json()['success'])

		summary_response = self.client.get(reverse('ver_pedido'))
		self.assertEqual(summary_response.status_code, 200)
		self.assertContains(summary_response, 'data-price-key="precio_2" selected', html=False)
		self.assertContains(summary_response, f'Precio 2 - ${self.presentacion.precio_2}', html=False)

	def test_tomar_pedido_paginates_approved_customers(self):
		self.customer.aprobado = True
		self.customer.save(update_fields=['aprobado'])
		for index in range(55):
			user = Usuario.objects.create_user(
				username=f'order-customer-{index}',
				password='secret123',
				role='cliente',
			)
			Cliente.objects.create(
				usuario=user,
				nombre_empresa=f'Cliente Pedido {index:03d}',
				telefono='5551234567',
				direccion='123 Test St',
				ciudad='Atlanta',
				estado='GA',
				codigo_postal='30301',
				pais='USA',
				sales_tax_number=f'TX-ORDER-{index:03d}',
				certificado_tax='certificados/test.pdf',
				aprobado=True,
			)

		self.client.force_login(self.vendor)
		response = self.client.get(reverse('tomar_pedido'))

		self.assertEqual(response.status_code, 200)
		self.assertEqual(len(response.context['clientes']), 50)
		self.assertEqual(response.context['page_obj'].paginator.count, 56)
		self.assertContains(response, 'Page 1 of 2')

	def test_catalogo_vendedor_paginates_products_alphabetically(self):
		categoria = Categoria.objects.create(nombre='Catalog Pagination')
		marca = Marca.objects.create(nombre='Marca Pagination')
		for index in range(55):
			Producto.objects.create(
				nombre=f'Zeta Producto {index:03d}',
				categoria=categoria,
				marca=marca,
				activo=True,
			)

		self.client.force_login(self.vendor)
		response = self.client.get(reverse('catalogo_vendedor', args=[self.customer.id]))

		self.assertEqual(response.status_code, 200)
		self.assertEqual(len(response.context['productos']), 50)
		self.assertEqual(response.context['page_obj'].paginator.count, 56)
		self.assertContains(response, 'Page 1 of 2')
		self.assertContains(response, 'Coca-Colaaaaaaaa')
		product_names = [producto.nombre for producto in response.context['productos']]
		self.assertEqual(product_names, sorted(product_names))

	def test_catalogo_vendedor_search_filters_on_server(self):
		categoria = Categoria.objects.create(nombre='Catalog Search')
		marca = Marca.objects.create(nombre='Marca Search')
		Producto.objects.create(
			nombre='Unique Catalog Search Product',
			categoria=categoria,
			marca=marca,
			activo=True,
		)

		self.client.force_login(self.vendor)
		response = self.client.get(
			reverse('catalogo_vendedor', args=[self.customer.id]),
			{'q': 'Unique Catalog Search'},
		)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(len(response.context['productos']), 1)
		self.assertContains(response, 'Unique Catalog Search Product')

	def test_catalogo_vendedor_shows_bulk_price_tier_selector(self):
		self.client.force_login(self.vendor)
		response = self.client.get(reverse('catalogo_vendedor', args=[self.customer.id]))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Assign one price tier to all products')
		self.assertContains(response, 'Apply to all products')
		self.assertContains(response, 'option value="precio_1"')
		self.assertContains(response, 'option value="precio_5"')
		self.assertContains(response, f'data-cliente-id="{self.customer.id}"', html=False)

	def test_catalogo_vendedor_shows_recent_customer_order_history(self):
		now = timezone.now()
		self._create_customer_invoice(created_at=now - timezone.timedelta(days=1), quantity=5, price='37.00')
		self._create_customer_invoice(created_at=now - timezone.timedelta(days=8), quantity=2, price='36.50')
		self._create_customer_invoice(created_at=now - timezone.timedelta(days=15), quantity=4, price='35.75')
		self._create_customer_invoice(created_at=now - timezone.timedelta(days=22), quantity=7, price='34.10')

		self.client.force_login(self.vendor)
		response = self.client.get(reverse('catalogo_vendedor', args=[self.customer.id]))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Historial')
		self.assertContains(response, '5 @ $37.00')
		self.assertContains(response, '2 @ $36.50')
		self.assertContains(response, '4 @ $35.75')
		self.assertNotContains(response, '7 @ $34.10')

	def test_catalogo_vendedor_ignores_unbilled_sales_orders(self):
		now = timezone.now()
		self._create_customer_order(created_at=now - timezone.timedelta(days=1), quantity=60, price='14.99')

		self.client.force_login(self.vendor)
		response = self.client.get(reverse('catalogo_vendedor', args=[self.customer.id]))

		self.assertEqual(response.status_code, 200)
		self.assertNotContains(response, '60 @ $14.99')

	def test_backoffice_can_access_order_taking_catalog(self):
		self.client.force_login(self.backoffice)

		response = self.client.get(reverse('catalogo_vendedor', args=[self.customer.id]))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Take Order')

	def test_backoffice_can_access_take_order_customer_selector(self):
		self.customer.aprobado = True
		self.customer.save(update_fields=['aprobado'])
		self.client.force_login(self.backoffice)

		response = self.client.get(reverse('tomar_pedido'))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, self.customer.nombre_empresa)


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
			balance=Decimal('239.00'),
		)

	def test_customer_list_paginates_results(self):
		for index in range(55):
			user = Usuario.objects.create_user(
				username=f'customer-page-{index}',
				password='secret123',
				role='cliente',
			)
			Cliente.objects.create(
				usuario=user,
				nombre_empresa=f'Cliente Paginado {index:03d}',
				telefono='5551234567',
				direccion='123 Test St',
				ciudad='Atlanta',
				estado='Georgia',
				codigo_postal='30301',
				pais='USA',
				sales_tax_number=f'TX-PAGE-{index:03d}',
				certificado_tax='certificados/test.pdf',
			)

		self.client.force_login(self.vendor)
		response = self.client.get(reverse('vendedores_clientes'))

		self.assertEqual(response.status_code, 200)
		self.assertEqual(len(response.context['clientes']), 50)
		self.assertEqual(response.context['page_obj'].paginator.count, 56)
		self.assertContains(response, 'Page 1 of 2')

	def test_customer_list_search_filters_on_server(self):
		self.client.force_login(self.vendor)

		response = self.client.get(reverse('vendedores_clientes'), {'q': 'Cliente Editable'})

		self.assertEqual(response.status_code, 200)
		self.assertEqual(len(response.context['clientes']), 1)
		self.assertContains(response, 'Cliente Editable')

	def test_customer_list_matches_quickbooks_style_columns(self):
		self.customer_user.first_name = 'Imported QB Contact'
		self.customer_user.last_name = ''
		self.customer_user.save(update_fields=['first_name', 'last_name'])
		self.client.force_login(self.vendor)

		response = self.client.get(reverse('vendedores_clientes'))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, '<th>Name</th>', html=True)
		self.assertContains(response, '<th>Company Name</th>', html=True)
		self.assertContains(response, '<th>PHONE</th>', html=True)
		self.assertContains(response, '<th>Balance</th>', html=True)
		self.assertContains(response, 'Imported QB Contact')
		self.assertContains(response, 'Cliente Editable')
		self.assertContains(response, '$239.00')

	def test_pending_access_button_shows_for_imported_customer_without_password(self):
		self.customer_user.set_unusable_password()
		self.customer_user.save(update_fields=['password'])
		self.client.force_login(self.vendor)

		response = self.client.get(reverse('vendedores_clientes'))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Acceso pendiente')

	def test_vendor_can_configure_web_access_for_imported_customer(self):
		self.customer_user.set_unusable_password()
		self.customer_user.save(update_fields=['password'])
		self.client.force_login(self.vendor)

		response = self.client.post(
			reverse('configurar_acceso_cliente'),
			data=json.dumps({
				'cliente_id': self.customer.id,
				'username': 'lilasmarket',
				'password': 'TempAccess123!',
				'password_confirm': 'TempAccess123!',
			}),
			content_type='application/json',
		)

		self.assertEqual(response.status_code, 200)
		self.assertJSONEqual(response.content, {'success': True, 'message': 'Web access configured successfully.'})
		self.customer_user.refresh_from_db()
		self.assertEqual(self.customer_user.username, 'lilasmarket')
		self.assertTrue(self.customer_user.has_usable_password())
		self.assertTrue(self.customer_user.check_password('TempAccess123!'))

	def test_configure_web_access_rejects_mismatched_passwords(self):
		self.customer_user.set_unusable_password()
		self.customer_user.save(update_fields=['password'])
		self.client.force_login(self.vendor)

		response = self.client.post(
			reverse('configurar_acceso_cliente'),
			data=json.dumps({
				'cliente_id': self.customer.id,
				'username': 'lilasmarket',
				'password': 'TempAccess123!',
				'password_confirm': 'OtherPassword123!',
			}),
			content_type='application/json',
		)

		self.assertEqual(response.status_code, 400)
		self.assertJSONEqual(response.content, {'success': False, 'message': 'Passwords do not match.'})

	def test_configure_web_access_rejects_password_without_special_character(self):
		self.customer_user.set_unusable_password()
		self.customer_user.save(update_fields=['password'])
		self.client.force_login(self.vendor)

		response = self.client.post(
			reverse('configurar_acceso_cliente'),
			data=json.dumps({
				'cliente_id': self.customer.id,
				'username': 'lilasmarket',
				'password': 'TempAccess123',
				'password_confirm': 'TempAccess123',
			}),
			content_type='application/json',
		)

		self.assertEqual(response.status_code, 400)
		self.assertJSONEqual(
			response.content,
			{'success': False, 'message': 'Password must include at least one special character.'},
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

	def test_customer_list_shows_terms_button(self):
		self.client.force_login(self.vendor)

		response = self.client.get(reverse('vendedores_clientes'))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Terms')
		self.assertContains(response, 'abrirModalTerminosCliente')

	def test_vendor_can_configure_customer_payment_terms(self):
		self.client.force_login(self.vendor)

		response = self.client.post(
			reverse('configurar_terminos_cliente'),
			data=json.dumps({
				'cliente_id': self.customer.id,
				'terminos_pago': 'NET14',
			}),
			content_type='application/json',
		)

		self.assertEqual(response.status_code, 200)
		payload = response.json()
		self.assertTrue(payload['success'])
		self.assertEqual(payload['terminos_pago'], 'NET14')
		self.assertEqual(payload['terminos_pago_label'], 'NET14')
		self.customer.refresh_from_db()
		self.assertEqual(self.customer.terminos_pago, Cliente.PAYMENT_TERMS_NET14)

	def test_configure_payment_terms_rejects_invalid_value(self):
		self.client.force_login(self.vendor)

		response = self.client.post(
			reverse('configurar_terminos_cliente'),
			data=json.dumps({
				'cliente_id': self.customer.id,
				'terminos_pago': 'NET30',
			}),
			content_type='application/json',
		)

		self.assertEqual(response.status_code, 400)
		payload = response.json()
		self.assertFalse(payload['success'])
