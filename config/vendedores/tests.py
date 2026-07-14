import json
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from config.clientes.models import Cliente, ClienteVendedorAsignacion
from config.facturacion.models import Invoice, InvoiceItem, NotaAjuste
from config.facturacion.services import crear_nota_ajuste, generar_invoice_desde_picking
from config.inventario.services import registrar_entrada_manual
from config.pedidos.models import Pedido, PedidoItem
from config.productos.models import Categoria, ConfiguracionDescuentos, Marca, Presentacion, Producto
from config.usuarios.models import Usuario
from config.usuarios.permissions import get_redirect_url_for_user


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
			aprobado=True,
			vendedor_asignado=self.vendor,
		)
		ClienteVendedorAsignacion.objects.get_or_create(
			cliente=self.customer,
			vendedor=self.vendor,
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
			fecha_documento=timezone.localtime(created_at).date(),
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

	def test_enviar_pedido_saves_order_comment(self):
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

		response = self.client.post(
			reverse('enviar_pedido'),
			{'tipo_orden': 'telefono', 'nota': 'Leave at back door'},
		)

		self.assertEqual(response.status_code, 200)
		self.assertTrue(response.json()['success'])
		pedido = Pedido.objects.get()
		self.assertEqual(pedido.nota_cliente, 'Leave at back door')

	def test_order_summary_shows_comment_field_first(self):
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

		response = self.client.get(reverse('ver_pedido'))
		self.assertEqual(response.status_code, 200)
		content = response.content.decode()
		comment_pos = content.find('id="pedidoNotaCliente"')
		customer_pos = content.find('class="cliente-box')
		self.assertGreater(comment_pos, 0)
		self.assertGreater(customer_pos, comment_pos)
		self.assertContains(response, 'pedidoVolverCatalogoBtn')
		self.assertContains(response, 'pedidoVolverCatalogoDesdeNotaBtn')
		self.assertContains(response, 'keep adding')

	def test_guardar_nota_pedido_persists_until_send(self):
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

		save_response = self.client.post(
			reverse('guardar_nota_pedido'),
			{'nota': 'Call before delivery'},
		)
		self.assertEqual(save_response.status_code, 200)
		self.assertTrue(save_response.json()['success'])

		summary = self.client.get(reverse('ver_pedido'))
		self.assertContains(summary, 'Call before delivery')

		send_response = self.client.post(
			reverse('enviar_pedido'),
			{'tipo_orden': 'telefono'},
		)
		self.assertEqual(send_response.status_code, 200)
		self.assertTrue(send_response.json()['success'])
		pedido = Pedido.objects.get()
		self.assertEqual(pedido.nota_cliente, 'Call before delivery')

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
		self.assertContains(summary_response, 'PC2 ·', html=False)

	def test_ver_pedido_shows_bulk_price_and_discount_controls(self):
		self.client.force_login(self.vendor)
		session = self.client.session
		session['cliente_id'] = self.customer.id
		session['pedido'] = {
			'item-1': {
				'producto_id': self.presentacion.producto_id,
				'presentacion_id': self.presentacion.id,
				'nombre': self.presentacion.producto.nombre,
				'cantidad': 1,
				'precio': float(self.presentacion.precio_1),
				'precio_key': 'precio_1',
			}
		}
		session.save()

		response = self.client.get(reverse('ver_pedido'))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Assign one price tier to all products')
		self.assertContains(response, 'Apply to all products')
		self.assertContains(response, 'Assign one preset discount to all products')
		self.assertContains(response, 'Apply discount to all products')
		self.assertContains(response, 'option value="precio_1"')
		self.assertContains(response, 'discount-toggle-box', html=False)
		self.assertContains(response, 'descuento-preset', html=False)
		self.assertContains(response, 'Manual discount')
		self.assertContains(response, 'precio-resumen-manual', html=False)
		self.assertContains(response, 'Manual price')
		self.assertContains(response, 'PC1 ·', html=False)
		self.assertContains(response, 'order-type-panel', html=False)
		self.assertContains(response, 'id="tipoOrdenPersonal"', html=False)
		self.assertContains(response, 'id="tipoOrdenTelefono"', html=False)

	def test_ver_pedido_selects_matching_discount_preset_for_saved_amount(self):
		self.client.force_login(self.vendor)
		session = self.client.session
		session['cliente_id'] = self.customer.id
		session['pedido'] = {
			'item-1': {
				'producto_id': self.presentacion.producto_id,
				'presentacion_id': self.presentacion.id,
				'nombre': self.presentacion.producto.nombre,
				'cantidad': 1,
				'precio': float(self.presentacion.precio_1),
				'precio_key': 'precio_1',
				'descuento_aplicado': True,
				'descuento_monto': 0.50,
			}
		}
		session.save()

		response = self.client.get(reverse('ver_pedido'))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'data-discount-key="descuento_2" selected', html=False)

	def test_ver_pedido_shows_full_sidebar_for_backoffice_user(self):
		self.client.force_login(self.backoffice)
		session = self.client.session
		session['cliente_id'] = self.customer.id
		session['pedido'] = {
			'item-1': {
				'producto_id': self.presentacion.producto_id,
				'presentacion_id': self.presentacion.id,
				'nombre': self.presentacion.producto.nombre,
				'cantidad': 1,
				'precio': float(self.presentacion.precio_1),
				'precio_key': 'precio_1',
			}
		}
		session.save()

		response = self.client.get(reverse('ver_pedido'))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Commercial')
		self.assertContains(response, 'Inventory')
		self.assertContains(response, 'panelSidebar')

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
		self.assertContains(response, 'catalogoSearchButton')
		self.assertContains(response, 'catalogoSearchClear')
		self.assertContains(response, 'catalog-filter-bar')

	def test_catalogo_vendedor_search_preserves_trailing_space_in_input(self):
		self.client.force_login(self.vendor)
		response = self.client.get(
			reverse('catalogo_vendedor', args=[self.customer.id]),
			{'q': 'coca '},
		)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.context['filter_q'], 'coca ')
		self.assertContains(response, 'value="coca "', html=False)

	def test_tomar_pedido_search_preserves_trailing_space_in_input(self):
		self.client.force_login(self.vendor)
		response = self.client.get(reverse('tomar_pedido'), {'q': 'alex '})

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.context['filter_q'], 'alex ')
		self.assertContains(response, 'value="alex "', html=False)

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
		self.assertNotContains(response, '4 @ $35.75')
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
		self.backoffice = Usuario.objects.create_user(
			username='backoffice-edit-client',
			password='secret123',
			role='backoffice',
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
		self.assertContains(response, '<th>Phone</th>', html=True)
		self.assertContains(response, '<th>Due balance (overdue)</th>', html=True)
		self.assertContains(response, '<th>Aging</th>', html=True)
		self.assertContains(response, '<th>Not yet due</th>', html=True)
		self.assertIn('clientes', response.context)
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

	def test_admin_can_change_customer_username(self):
		self.client.force_login(self.admin)

		response = self.client.post(
			reverse('editar_cliente'),
			data=json.dumps({
				'cliente_id': self.customer.id,
				'empresa': self.customer.nombre_empresa,
				'correo': self.customer_user.email or 'cliente@test.com',
				'telefono': '5551234567',
				'direccion': self.customer.direccion,
				'ciudad': self.customer.ciudad,
				'estado': self.customer.estado,
				'codigo_postal': self.customer.codigo_postal,
				'pais': self.customer.pais or 'USA',
				'manual_location': True,
				'username': 'quikstop',
			}),
			content_type='application/json',
		)

		self.assertEqual(response.status_code, 200)
		self.customer_user.refresh_from_db()
		self.assertEqual(self.customer_user.username, 'quikstop')

	def test_backoffice_can_change_customer_username(self):
		self.client.force_login(self.backoffice)

		response = self.client.post(
			reverse('editar_cliente'),
			data=json.dumps({
				'cliente_id': self.customer.id,
				'empresa': self.customer.nombre_empresa,
				'correo': self.customer_user.email or 'cliente@test.com',
				'telefono': '5551234567',
				'direccion': self.customer.direccion,
				'ciudad': self.customer.ciudad,
				'estado': self.customer.estado,
				'codigo_postal': self.customer.codigo_postal,
				'pais': self.customer.pais or 'USA',
				'manual_location': True,
				'username': 'quikstop',
			}),
			content_type='application/json',
		)

		self.assertEqual(response.status_code, 200)
		self.customer_user.refresh_from_db()
		self.assertEqual(self.customer_user.username, 'quikstop')

	def test_vendor_cannot_change_customer_username_via_edit(self):
		original_username = self.customer_user.username
		self.client.force_login(self.vendor)

		response = self.client.post(
			reverse('editar_cliente'),
			data=json.dumps({
				'cliente_id': self.customer.id,
				'empresa': self.customer.nombre_empresa,
				'correo': self.customer_user.email or 'cliente@test.com',
				'telefono': '5551234567',
				'direccion': self.customer.direccion,
				'ciudad': self.customer.ciudad,
				'estado': self.customer.estado,
				'codigo_postal': self.customer.codigo_postal,
				'pais': self.customer.pais or 'USA',
				'manual_location': True,
				'username': 'quikstop',
			}),
			content_type='application/json',
		)

		self.assertEqual(response.status_code, 200)
		self.customer_user.refresh_from_db()
		self.assertEqual(self.customer_user.username, original_username)

	def test_username_conflict_message_names_other_customer(self):
		other_user = Usuario.objects.create_user(
			username='quikstop',
			password='secret123',
			role='cliente',
			email='other@test.com',
		)
		Cliente.objects.create(
			usuario=other_user,
			nombre_empresa='Other Quik Stop',
			telefono='5559998888',
			direccion='999 Other St',
			ciudad='Atlanta',
			estado='Georgia',
			codigo_postal='30301',
			pais='USA',
			sales_tax_number='TX-OTHER-1',
			certificado_tax='certificados/test.pdf',
		)
		self.client.force_login(self.admin)

		response = self.client.post(
			reverse('editar_cliente'),
			data=json.dumps({
				'cliente_id': self.customer.id,
				'empresa': self.customer.nombre_empresa,
				'correo': self.customer_user.email or 'cliente@test.com',
				'telefono': '5551234567',
				'direccion': self.customer.direccion,
				'ciudad': self.customer.ciudad,
				'estado': self.customer.estado,
				'codigo_postal': self.customer.codigo_postal,
				'pais': self.customer.pais or 'USA',
				'manual_location': True,
				'username': 'quikstop',
			}),
			content_type='application/json',
		)

		self.assertEqual(response.status_code, 400)
		payload = response.json()
		self.assertFalse(payload['success'])
		self.assertIn('Other Quik Stop', payload['message'])
		self.assertIn('quikstop', payload['message'])
		self.customer_user.refresh_from_db()
		self.assertEqual(self.customer_user.username, 'customer-edit-client')

	def test_username_conflict_releases_placeholder_account(self):
		placeholder = Usuario.objects.create_user(
			username='quikstop',
			password='secret123',
			role='cliente',
			email='placeholder@test.com',
		)
		placeholder.set_unusable_password()
		placeholder.save(update_fields=['password'])
		Cliente.objects.create(
			usuario=placeholder,
			nombre_empresa='Placeholder Quik',
			telefono='5557776666',
			direccion='111 Place St',
			ciudad='Atlanta',
			estado='Georgia',
			codigo_postal='30301',
			pais='USA',
			sales_tax_number='TX-PLACE-1',
			certificado_tax='certificados/test.pdf',
		)
		self.client.force_login(self.admin)

		response = self.client.post(
			reverse('editar_cliente'),
			data=json.dumps({
				'cliente_id': self.customer.id,
				'empresa': self.customer.nombre_empresa,
				'correo': self.customer_user.email or 'cliente@test.com',
				'telefono': '5551234567',
				'direccion': self.customer.direccion,
				'ciudad': self.customer.ciudad,
				'estado': self.customer.estado,
				'codigo_postal': self.customer.codigo_postal,
				'pais': self.customer.pais or 'USA',
				'manual_location': True,
				'username': 'quikstop',
			}),
			content_type='application/json',
		)

		self.assertEqual(response.status_code, 200)
		self.customer_user.refresh_from_db()
		placeholder.refresh_from_db()
		self.assertEqual(self.customer_user.username, 'quikstop')
		self.assertTrue(placeholder.username.startswith(f'released-{placeholder.pk}-'))

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

	def test_edit_customer_accepts_formatted_phone_number(self):
		self.client.force_login(self.admin)

		response = self.client.post(
			reverse('editar_cliente'),
			data=json.dumps({
				'cliente_id': self.customer.id,
				'empresa': self.customer.nombre_empresa,
				'correo': self.customer_user.email,
				'telefono': '(706) 263-7500',
				'direccion': self.customer.direccion,
				'ciudad': self.customer.ciudad,
				'estado': self.customer.estado,
				'codigo_postal': self.customer.codigo_postal,
				'pais': self.customer.pais,
				'manual_location': True,
			}),
			content_type='application/json',
		)

		self.assertEqual(response.status_code, 200)
		self.customer.refresh_from_db()
		self.assertEqual(self.customer.telefono, '7062637500')

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

	def test_vendor_can_configure_ach_net7_payment_terms(self):
		self.client.force_login(self.vendor)

		response = self.client.post(
			reverse('configurar_terminos_cliente'),
			data=json.dumps({
				'cliente_id': self.customer.id,
				'terminos_pago': 'ACH_NET7',
			}),
			content_type='application/json',
		)

		self.assertEqual(response.status_code, 200)
		payload = response.json()
		self.assertTrue(payload['success'])
		self.assertEqual(payload['terminos_pago'], 'ACH_NET7')
		self.assertEqual(payload['terminos_pago_label'], 'ACH NET 7')
		self.customer.refresh_from_db()
		self.assertEqual(self.customer.terminos_pago, Cliente.PAYMENT_TERMS_ACH_NET7)
		self.assertEqual(self.customer.get_payment_terms_due_days(), 7)

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

	def test_customer_list_shows_credit_limit_button(self):
		self.client.force_login(self.vendor)

		response = self.client.get(reverse('vendedores_clientes'))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Credit limit')
		self.assertContains(response, 'abrirModalLimiteCreditoCliente')

	def test_vendor_can_configure_customer_credit_limit(self):
		self.client.force_login(self.vendor)

		response = self.client.post(
			reverse('configurar_limite_credito_cliente'),
			data=json.dumps({
				'cliente_id': self.customer.id,
				'credit_limit': '2000.00',
			}),
			content_type='application/json',
		)

		self.assertEqual(response.status_code, 200)
		payload = response.json()
		self.assertTrue(payload['success'])
		self.customer.refresh_from_db()
		self.assertEqual(self.customer.credit_limit, Decimal('2000.00'))
		self.assertEqual(payload['remaining_limit'], '2000.00')


class TakeOrderDraftPersistenceTests(TestCase):
	def setUp(self):
		self.vendor = Usuario.objects.create_user(
			username='vendor-draft-test',
			password='secret123',
			role='vendedor',
		)
		self.customer_user = Usuario.objects.create_user(
			username='customer-draft-test',
			password='secret123',
			role='cliente',
		)
		self.customer = Cliente.objects.create(
			usuario=self.customer_user,
			nombre_empresa='Cliente Draft Test',
			telefono='5559876543',
			direccion='456 Draft St',
			ciudad='Atlanta',
			estado='GA',
			codigo_postal='30301',
			pais='USA',
			sales_tax_number='TX-DRAFT-1',
			certificado_tax='certificados/test.pdf',
		)

	def test_draft_survives_save_and_reload(self):
		from config.vendedores.drafts import load_draft_cart, save_draft_cart
		from config.vendedores.models import TakeOrderDraft

		cart = {
			'12': {
				'presentacion_id': '12',
				'producto_id': 1,
				'nombre': 'Tortilla',
				'presentacion_nombre': 'Case',
				'precio': 10.5,
				'cantidad': 3,
			}
		}
		save_draft_cart(vendedor=self.vendor, cliente_id=self.customer.id, cart=cart)
		loaded = load_draft_cart(vendedor=self.vendor, cliente_id=self.customer.id)
		self.assertEqual(loaded['12']['cantidad'], 3)
		self.assertEqual(TakeOrderDraft.objects.filter(vendedor=self.vendor).count(), 1)

	def test_empty_cart_clears_draft(self):
		from config.vendedores.drafts import save_draft_cart
		from config.vendedores.models import TakeOrderDraft

		save_draft_cart(
			vendedor=self.vendor,
			cliente_id=self.customer.id,
			cart={'1': {'cantidad': 1, 'precio': 1}},
		)
		save_draft_cart(vendedor=self.vendor, cliente_id=self.customer.id, cart={})
		self.assertEqual(TakeOrderDraft.objects.filter(vendedor=self.vendor).count(), 0)

	def test_note_keeps_draft_when_cart_empty(self):
		from config.vendedores.drafts import load_draft_nota, save_draft_cart
		from config.vendedores.models import TakeOrderDraft

		save_draft_cart(
			vendedor=self.vendor,
			cliente_id=self.customer.id,
			cart={},
			nota='Hold the order comment',
		)
		self.assertEqual(TakeOrderDraft.objects.filter(vendedor=self.vendor).count(), 1)
		self.assertEqual(
			load_draft_nota(vendedor=self.vendor, cliente_id=self.customer.id),
			'Hold the order comment',
		)


class VendorHomeAndNotesTests(TestCase):
	def setUp(self):
		self.vendor = Usuario.objects.create_user(
			username='vendor-home-notes',
			password='secret123',
			role='vendedor',
		)
		self.other_vendor = Usuario.objects.create_user(
			username='vendor-other-notes',
			password='secret123',
			role='vendedor',
		)
		self.backoffice = Usuario.objects.create_user(
			username='backoffice-vendor-notes',
			password='secret123',
			role='backoffice',
		)
		self.customer_user = Usuario.objects.create_user(
			username='customer-vendor-notes',
			password='secret123',
			role='cliente',
			email='vendor-notes@test.com',
		)
		self.customer = Cliente.objects.create(
			usuario=self.customer_user,
			nombre_empresa='Cliente Vendor Notes',
			telefono='5552223333',
			direccion='100 Vendor St',
			ciudad='Atlanta',
			estado='Georgia',
			codigo_postal='30301',
			pais='USA',
			sales_tax_number='TX-VENDOR-NOTES',
			certificado_tax='certificados/test.pdf',
			aprobado=True,
			vendedor_asignado=self.vendor,
			vendedor_asignado_en=timezone.now(),
		)
		ClienteVendedorAsignacion.objects.get_or_create(cliente=self.customer, vendedor=self.vendor)
		other_user = Usuario.objects.create_user(
			username='customer-other-vendor-notes',
			password='secret123',
			role='cliente',
			email='other-vendor-notes@test.com',
		)
		self.other_customer = Cliente.objects.create(
			usuario=other_user,
			nombre_empresa='Cliente Otro Vendor',
			telefono='5554445555',
			direccion='200 Other St',
			ciudad='Atlanta',
			estado='Georgia',
			codigo_postal='30301',
			pais='USA',
			sales_tax_number='TX-OTHER-VENDOR',
			certificado_tax='certificados/test.pdf',
			aprobado=True,
			vendedor_asignado=self.other_vendor,
			vendedor_asignado_en=timezone.now(),
		)
		ClienteVendedorAsignacion.objects.get_or_create(cliente=self.other_customer, vendedor=self.other_vendor)

		categoria = Categoria.objects.create(nombre='Cat Vendor Notes')
		marca = Marca.objects.create(nombre='Marca Vendor Notes')
		producto = Producto.objects.create(nombre='Producto Vendor Notes', categoria=categoria, marca=marca, activo=True)
		self.presentacion = Presentacion.objects.create(
			producto=producto,
			nombre='Caja',
			unidades=12,
			tipo_contenido='unidades',
			costo=Decimal('10.00'),
			precio_1=Decimal('17.00'),
		)
		registrar_entrada_manual(presentacion=self.presentacion, cantidad=50, observacion='Vendor notes stock')

		self.pedido = Pedido.objects.create(
			cliente=self.customer,
			origen='VENDEDOR',
			vendedor=self.vendor,
			estado='VERIFICADO_AJUSTADO',
			total=Decimal('34.00'),
		)
		PedidoItem.objects.create(
			pedido=self.pedido,
			presentacion=self.presentacion,
			cantidad_solicitada=2,
			cantidad=2,
			cantidad_inventario_aplicada=2,
			precio=Decimal('17.00'),
			subtotal=Decimal('34.00'),
		)
		self.invoice = generar_invoice_desde_picking(
			pedido=self.pedido,
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
		)
		self.invoice_item = self.invoice.items.first()

	def test_vendor_login_redirects_to_home(self):
		self.assertTrue(get_redirect_url_for_user(self.vendor).endswith('/vendedores/'))

	def test_vendor_home_shows_expected_tiles(self):
		self.client.force_login(self.vendor)
		response = self.client.get(reverse('vendedor_home'))
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Take Order')
		self.assertContains(response, 'Customers')
		self.assertContains(response, 'Create Customer')
		self.assertContains(response, 'Credit Memo')
		self.assertContains(response, 'Return')
		self.assertContains(response, 'My Notes')

	def test_vendor_can_create_credit_memo_for_assigned_customer(self):
		self.client.force_login(self.vendor)
		response = self.client.post(
			reverse('vendedor_credit_memo_create') + f'?cliente_id={self.customer.id}&invoice_id={self.invoice.id}',
			data={
				'cliente_id': self.customer.id,
				'invoice_id': self.invoice.id,
				'note_tipo_documento': 'CREDITO',
				'note_tipo_ajuste': 'PRODUCTO',
				'note_tipo_credito': 'CREDIT_DUMP',
				'note_motivo': 'DAMAGE',
				'note_descripcion': 'Vendor credit memo',
				f'note_qty_{self.invoice_item.id}': '1',
				f'note_amount_{self.invoice_item.id}': '17.00',
			},
		)
		self.assertEqual(response.status_code, 302)
		nota = NotaAjuste.objects.get(creada_por=self.vendor, descripcion='Vendor credit memo')
		self.assertEqual(nota.tipo_credito, 'CREDIT_DUMP')
		self.assertEqual(nota.estado, 'BORRADOR')
		self.assertEqual(nota.cliente_id, self.customer.id)

	def test_vendor_can_create_return_for_assigned_customer(self):
		self.client.force_login(self.vendor)
		response = self.client.post(
			reverse('vendedor_return_create') + f'?cliente_id={self.customer.id}&invoice_id={self.invoice.id}',
			data={
				'cliente_id': self.customer.id,
				'invoice_id': self.invoice.id,
				'note_tipo_documento': 'CREDITO',
				'note_tipo_ajuste': 'PRODUCTO',
				'note_tipo_credito': 'CREDIT_RETURN',
				'note_motivo': 'DEFECT',
				'note_descripcion': 'Vendor return',
				f'note_qty_{self.invoice_item.id}': '1',
				f'note_amount_{self.invoice_item.id}': '17.00',
			},
		)
		self.assertEqual(response.status_code, 302)
		nota = NotaAjuste.objects.get(creada_por=self.vendor, descripcion='Vendor return')
		self.assertEqual(nota.tipo_credito, 'CREDIT_RETURN')
		self.assertEqual(nota.estado, 'BORRADOR')

	def test_vendor_cannot_create_note_for_unassigned_customer(self):
		other_pedido = Pedido.objects.create(
			cliente=self.other_customer,
			origen='VENDEDOR',
			vendedor=self.other_vendor,
			estado='VERIFICADO_AJUSTADO',
			total=Decimal('17.00'),
		)
		PedidoItem.objects.create(
			pedido=other_pedido,
			presentacion=self.presentacion,
			cantidad_solicitada=1,
			cantidad=1,
			cantidad_inventario_aplicada=1,
			precio=Decimal('17.00'),
			subtotal=Decimal('17.00'),
		)
		other_invoice = generar_invoice_desde_picking(
			pedido=other_pedido,
			metodo_entrega='CUSTOMER_PICK_UP',
			driver=None,
			usuario=self.backoffice,
		)
		other_item = other_invoice.items.first()

		self.client.force_login(self.vendor)
		response = self.client.post(
			reverse('vendedor_credit_memo_create') + f'?cliente_id={self.other_customer.id}&invoice_id={other_invoice.id}',
			data={
				'cliente_id': self.other_customer.id,
				'invoice_id': other_invoice.id,
				'note_tipo_documento': 'CREDITO',
				'note_tipo_ajuste': 'PRODUCTO',
				'note_tipo_credito': 'CREDIT_DUMP',
				'note_motivo': 'DAMAGE',
				'note_descripcion': 'Should fail',
				f'note_qty_{other_item.id}': '1',
				f'note_amount_{other_item.id}': '17.00',
			},
		)
		self.assertEqual(response.status_code, 404)
		self.assertFalse(NotaAjuste.objects.filter(descripcion='Should fail').exists())

	def test_vendor_notes_list_only_shows_own_notes(self):
		own = crear_nota_ajuste(
			cliente=self.customer,
			invoice=self.invoice,
			tipo_ajuste='PRODUCTO',
			tipo_documento='CREDITO',
			motivo='DAMAGE',
			tipo_credito='CREDIT_DUMP',
			descripcion='Own note',
			usuario=self.vendor,
			items_payload=[{
				'invoice_item': self.invoice_item,
				'cantidad': 1,
				'monto_unitario': Decimal('17.00'),
			}],
		)
		crear_nota_ajuste(
			cliente=self.other_customer,
			invoice=None,
			tipo_ajuste='FINANCIERO',
			tipo_documento='CREDITO',
			motivo='DAMAGE',
			tipo_credito='CREDIT_DUMP',
			descripcion='Other vendor note',
			usuario=self.other_vendor,
			items_payload=[],
			monto=Decimal('10.00'),
		)

		self.client.force_login(self.vendor)
		response = self.client.get(reverse('vendedor_notes_list'))
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Own note')
		self.assertNotContains(response, 'Other vendor note')
		self.assertEqual(own.estado, 'BORRADOR')

	def test_vendor_cannot_approve_note(self):
		nota = crear_nota_ajuste(
			cliente=self.customer,
			invoice=self.invoice,
			tipo_ajuste='PRODUCTO',
			tipo_documento='CREDITO',
			motivo='DAMAGE',
			tipo_credito='CREDIT_DUMP',
			descripcion='Pending approval',
			usuario=self.vendor,
			items_payload=[{
				'invoice_item': self.invoice_item,
				'cantidad': 1,
				'monto_unitario': Decimal('17.00'),
			}],
		)
		self.client.force_login(self.vendor)
		response = self.client.post(reverse('backoffice_invoice_approve_note', args=[nota.id]))
		self.assertIn(response.status_code, {302, 403})
		nota.refresh_from_db()
		self.assertEqual(nota.estado, 'BORRADOR')
