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
from config.productos.models import (
	Categoria,
	ConfiguracionDescuentos,
	Marca,
	Presentacion,
	Producto,
	Promocion,
	PromocionEscala,
	PromocionProducto,
)
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
				'redirect_url': reverse('vendedor_home'),
			},
		)
		item = pedido.items.get()
		self.assertEqual(item.cantidad_reservada_inventario, 0)
		self.assertEqual(item.cantidad_inventario_aplicada, 0)

	def test_vendor_can_create_quotation_from_take_quote_flow(self):
		from config.cotizaciones.models import Cotizacion

		self.client.force_login(self.vendor)
		self.client.get(reverse('tomar_cotizacion'))
		session = self.client.session
		session['cliente_id'] = self.customer.id
		session['pedido'] = {
			'1': {
				'presentacion_id': str(self.presentacion.id),
				'producto_id': self.presentacion.producto_id,
				'nombre': self.presentacion.producto.nombre,
				'presentacion_nombre': self.presentacion.nombre,
				'precio': 233.0,
				'cantidad': 2,
				'descuento_aplicado': True,
				'descuento_monto': 10.0,
			}
		}
		session.save()

		response = self.client.post(
			reverse('crear_cotizacion_desde_toma'),
			{'nota': 'Quote for weekend'},
		)
		self.assertEqual(response.status_code, 200)
		payload = response.json()
		self.assertTrue(payload['success'])
		cotizacion = Cotizacion.objects.get(id=payload['cotizacion_id'])
		self.assertEqual(cotizacion.estado, 'BORRADOR')
		self.assertTrue(cotizacion.backoffice_pricing_confirmed)
		self.assertEqual(cotizacion.vendedor_id, self.vendor.id)
		self.assertEqual(cotizacion.nota_cliente, 'Quote for weekend')
		item = cotizacion.items.get()
		self.assertEqual(item.cantidad, 2)
		self.assertTrue(item.descuento_aplicado)
		self.assertIn(str(cotizacion.id), payload['redirect_url'])
		self.assertIn('saved=1', payload['redirect_url'])

		detail = self.client.get(payload['redirect_url'])
		self.assertEqual(detail.status_code, 200)
		self.assertTrue(detail.context['can_send_customer_quote'])
		self.assertFalse(detail.context['can_generate_backoffice_order'])

	def test_backoffice_can_create_quotation_from_take_quote_flow(self):
		from config.cotizaciones.models import Cotizacion

		self.client.force_login(self.backoffice)
		self.client.get(reverse('tomar_cotizacion'))
		session = self.client.session
		session['cliente_id'] = self.customer.id
		session['pedido'] = {
			'1': {
				'presentacion_id': str(self.presentacion.id),
				'producto_id': self.presentacion.producto_id,
				'nombre': self.presentacion.producto.nombre,
				'presentacion_nombre': self.presentacion.nombre,
				'precio': 200.0,
				'cantidad': 1,
			}
		}
		session.save()

		response = self.client.post(reverse('crear_cotizacion_desde_toma'))
		self.assertEqual(response.status_code, 200)
		payload = response.json()
		cotizacion = Cotizacion.objects.get(id=payload['cotizacion_id'])
		self.assertIsNone(cotizacion.vendedor_id)
		detail = self.client.get(payload['redirect_url'])
		self.assertEqual(detail.status_code, 200)
		self.assertTrue(detail.context['can_generate_backoffice_order'])

	def test_vendor_cannot_view_unassigned_customer_quote(self):
		from config.cotizaciones.models import Cotizacion
		from config.cotizaciones.services import crear_cotizacion_desde_items

		other_vendor = Usuario.objects.create_user(
			username='vendor-other-quote',
			password='secret123',
			role='vendedor',
		)
		other_user = Usuario.objects.create_user(
			username='customer-other-quote',
			password='secret123',
			role='cliente',
		)
		other_customer = Cliente.objects.create(
			usuario=other_user,
			nombre_empresa='Other Quote Customer',
			telefono='5559998888',
			direccion='9 Other St',
			ciudad='Atlanta',
			estado='GA',
			codigo_postal='30301',
			pais='USA',
			sales_tax_number='TX-OTHER-QUOTE',
			certificado_tax='certificados/test.pdf',
			aprobado=True,
			vendedor_asignado=other_vendor,
		)
		ClienteVendedorAsignacion.objects.create(cliente=other_customer, vendedor=other_vendor)
		cotizacion = crear_cotizacion_desde_items(
			cliente=other_customer,
			creado_por=other_vendor,
			items_payload=[
				{'presentacion': self.presentacion, 'cantidad': 1, 'precio': Decimal('10.00')},
			],
		)

		self.client.force_login(self.vendor)
		response = self.client.get(reverse('backoffice_cotizacion_detalle', args=[cotizacion.id]))
		self.assertEqual(response.status_code, 302)
		self.assertEqual(Cotizacion.objects.filter(id=cotizacion.id).count(), 1)

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

	def test_guardar_nota_pedido_preserves_spaces_while_typing(self):
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
			{'nota': 'Leave at  back door '},
		)
		self.assertEqual(save_response.status_code, 200)
		self.assertTrue(save_response.json()['success'])
		self.assertEqual(save_response.json()['nota'], 'Leave at  back door ')

		summary = self.client.get(reverse('ver_pedido'))
		self.assertContains(summary, 'Leave at  back door ', html=False)

		send_response = self.client.post(
			reverse('enviar_pedido'),
			{'tipo_orden': 'telefono', 'nota': 'Leave at  back door '},
		)
		self.assertEqual(send_response.status_code, 200)
		self.assertTrue(send_response.json()['success'])
		pedido = Pedido.objects.get()
		self.assertEqual(pedido.nota_cliente, 'Leave at  back door')
		self.assertNotIn('pedido_nota', self.client.session)

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
		self.assertContains(response, 'page=2"')
		self.assertContains(response, 'aria-current="page"')

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
		self.assertContains(response, 'page=2"')
		self.assertContains(response, 'aria-current="page"')
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
		self.assertContains(response, 'catalog-search-sticky')

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
		self.assertContains(response, 'page=2"')
		self.assertContains(response, 'aria-current="page"')

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
		from config.clientes.assignment import ensure_cliente_assigned_to_vendedor

		ensure_cliente_assigned_to_vendedor(cliente=self.customer, vendedor=self.vendor)
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
		self.customer.refresh_from_db()
		self.assertEqual(self.customer_user.username, 'lilasmarket')
		self.assertTrue(self.customer_user.has_usable_password())
		self.assertTrue(self.customer_user.check_password('TempAccess123!'))
		self.assertEqual(self.customer.web_access_password, 'TempAccess123!')

	def test_admin_can_view_stored_customer_web_password(self):
		self.customer_user.set_password('TempAccess123!')
		self.customer_user.save(update_fields=['password'])
		self.customer.web_access_password = 'TempAccess123!'
		self.customer.save(update_fields=['web_access_password'])

		admin = Usuario.objects.create_user(
			username='admin-access-view',
			password='pass',
			role='admin',
			is_staff=True,
		)
		self.client.force_login(admin)
		response = self.client.get(reverse('obtener_acceso_cliente', args=[self.customer.id]))

		self.assertEqual(response.status_code, 200)
		payload = response.json()
		self.assertTrue(payload['success'])
		self.assertEqual(payload['username'], self.customer_user.username)
		self.assertEqual(payload['password'], 'TempAccess123!')
		self.assertTrue(payload['password_available'])

	def test_vendor_cannot_view_stored_customer_web_password(self):
		self.customer_user.set_password('TempAccess123!')
		self.customer_user.save(update_fields=['password'])
		self.customer.web_access_password = 'TempAccess123!'
		self.customer.save(update_fields=['web_access_password'])

		self.client.force_login(self.vendor)
		response = self.client.get(reverse('obtener_acceso_cliente', args=[self.customer.id]))
		self.assertEqual(response.status_code, 403)

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
		self.assertContains(response, 'Create Quote')
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


class VendedorComboTests(TestCase):
	def setUp(self):
		self.vendor = Usuario.objects.create_user(
			username='vendor-combo-test', password='secret123', role='vendedor',
		)
		self.customer_user = Usuario.objects.create_user(
			username='customer-combo-test', password='secret123', role='cliente',
		)
		self.customer = Cliente.objects.create(
			usuario=self.customer_user,
			nombre_empresa='Cliente Combo Test',
			telefono='5551234567',
			direccion='123 Test St',
			ciudad='Atlanta', estado='GA', codigo_postal='30301', pais='USA',
			sales_tax_number='TX-COMBO-V', certificado_tax='certificados/test.pdf',
			aprobado=True, nivel_precio=1, vendedor_asignado=self.vendor,
		)
		ClienteVendedorAsignacion.objects.get_or_create(cliente=self.customer, vendedor=self.vendor)

		categoria = Categoria.objects.create(nombre='Combo Bebidas')
		marca = Marca.objects.create(nombre='Combo Marca V')
		self.producto_a = Producto.objects.create(nombre='Jarrito A', categoria=categoria, marca=marca)
		self.producto_b = Producto.objects.create(nombre='Jarrito B', categoria=categoria, marca=marca)
		self.producto_c = Producto.objects.create(nombre='Jarrito C', categoria=categoria, marca=marca)
		self.pres_a = Presentacion.objects.create(producto=self.producto_a, nombre='Case A', unidades=1, tipo_contenido='caja', precio_1=Decimal('20.00'))
		self.pres_b = Presentacion.objects.create(producto=self.producto_b, nombre='Case B', unidades=1, tipo_contenido='caja', precio_1=Decimal('20.00'))
		self.pres_c = Presentacion.objects.create(producto=self.producto_c, nombre='Case C', unidades=1, tipo_contenido='caja', precio_1=Decimal('20.00'))

		from django.utils import timezone
		from datetime import timedelta

		self.promo = Promocion.objects.create(
			nombre='Combo Vendedor', descripcion='Buy 10 mixed, 10% off',
			alcance=Promocion.ALCANCE_GRUPO, producto=self.producto_a, activa=True,
			fecha_fin=timezone.now() + timedelta(hours=15),
		)
		PromocionProducto.objects.create(promocion=self.promo, producto=self.producto_a)
		PromocionProducto.objects.create(promocion=self.promo, producto=self.producto_b)
		PromocionProducto.objects.create(promocion=self.promo, producto=self.producto_c)
		PromocionEscala.objects.create(
			promocion=self.promo, cantidad_minima=10,
			tipo_beneficio=PromocionEscala.TIPO_PERCENT, valor_beneficio=Decimal('10'),
		)

	def test_combo_pedido_miembros_returns_members_with_tier_price(self):
		self.client.force_login(self.vendor)
		session = self.client.session
		session['cliente_id'] = self.customer.id
		session.save()

		response = self.client.get(reverse('combo_pedido_miembros', args=[self.promo.id]))
		self.assertEqual(response.status_code, 200)
		data = response.json()
		self.assertEqual(data['minimum'], 10)
		self.assertEqual(len(data['miembros']), 3)
		for miembro in data['miembros']:
			pres = miembro['presentaciones'][0]
			self.assertEqual(pres['precio'], 20.0)
			self.assertEqual(pres['precio_key'], 'precio_1')

	def test_combo_pedido_miembros_includes_free_gift_metadata(self):
		gift_promo = Promocion.objects.create(
			nombre='Combo Free Gift',
			alcance=Promocion.ALCANCE_GRUPO,
			activa=True,
		)
		PromocionProducto.objects.create(promocion=gift_promo, producto=self.producto_a)
		PromocionProducto.objects.create(promocion=gift_promo, producto=self.producto_b)
		PromocionEscala.objects.create(
			promocion=gift_promo,
			cantidad_minima=20,
			tipo_beneficio=PromocionEscala.TIPO_PERCENT,
			valor_beneficio=Decimal('5'),
		)
		PromocionEscala.objects.create(
			promocion=gift_promo,
			cantidad_minima=100,
			tipo_beneficio=PromocionEscala.TIPO_FREE_UNITS,
			unidades_gratis=1,
			presentacion_regalo=self.pres_b,
		)
		self.client.force_login(self.vendor)
		session = self.client.session
		session['cliente_id'] = self.customer.id
		session.save()

		response = self.client.get(reverse('combo_pedido_miembros', args=[gift_promo.id]))
		self.assertEqual(response.status_code, 200)
		data = response.json()
		free_tier = next(row for row in data['escalas'] if row['minimo'] == 100)
		self.assertEqual(free_tier['tipo_beneficio'], PromocionEscala.TIPO_FREE_UNITS)
		self.assertEqual(free_tier['unidades_gratis'], 1)
		self.assertIsNotNone(free_tier['regalo'])
		self.assertEqual(free_tier['regalo']['presentacion_id'], self.pres_b.id)
		self.assertEqual(free_tier['regalo']['producto_id'], self.producto_b.id)

	def test_distributed_combo_with_free_gift_adds_regalo_line_to_cart(self):
		self.promo.activa = False
		self.promo.save(update_fields=['activa'])
		gift_promo = Promocion.objects.create(
			nombre='Combo Free Gift Cart',
			alcance=Promocion.ALCANCE_GRUPO,
			activa=True,
		)
		PromocionProducto.objects.create(promocion=gift_promo, producto=self.producto_a)
		PromocionProducto.objects.create(promocion=gift_promo, producto=self.producto_b)
		PromocionProducto.objects.create(promocion=gift_promo, producto=self.producto_c)
		PromocionEscala.objects.create(
			promocion=gift_promo,
			cantidad_minima=100,
			tipo_beneficio=PromocionEscala.TIPO_FREE_UNITS,
			unidades_gratis=1,
			presentacion_regalo=self.pres_b,
		)
		self.client.force_login(self.vendor)
		session = self.client.session
		session['cliente_id'] = self.customer.id
		session.save()

		for pres, cantidad in [(self.pres_a, 34), (self.pres_b, 33), (self.pres_c, 33)]:
			resp = self.client.post(reverse('agregar_producto_pedido'), {
				'presentacion_id': pres.id,
				'cantidad': cantidad,
				'precio': str(pres.precio_1),
				'precio_key': 'precio_1',
			})
			self.assertEqual(resp.status_code, 200)

		pedido = self.client.session['pedido']
		gift_lines = [item for item in pedido.values() if item.get('es_regalo')]
		self.assertEqual(len(gift_lines), 1)
		self.assertEqual(gift_lines[0]['presentacion_id'], self.pres_b.id)
		self.assertEqual(gift_lines[0]['cantidad'], 1)
		self.assertEqual(float(gift_lines[0]['precio']), 0.0)

	def test_combo_pedido_miembros_rejects_individual_promo(self):
		individual = Promocion.objects.create(nombre='Solo V', producto=self.producto_a, activa=True)
		PromocionEscala.objects.create(promocion=individual, cantidad_minima=5, valor_beneficio=Decimal('10'))
		self.client.force_login(self.vendor)
		session = self.client.session
		session['cliente_id'] = self.customer.id
		session.save()
		response = self.client.get(reverse('combo_pedido_miembros', args=[individual.id]))
		self.assertEqual(response.status_code, 404)

	def test_distributed_combo_applies_discount_in_vendor_order(self):
		self.client.force_login(self.vendor)
		session = self.client.session
		session['cliente_id'] = self.customer.id
		session.save()

		for pres, cantidad in [(self.pres_a, 5), (self.pres_b, 3), (self.pres_c, 2)]:
			resp = self.client.post(reverse('agregar_producto_pedido'), {
				'presentacion_id': pres.id,
				'cantidad': cantidad,
				'precio': str(pres.precio_1),
				'precio_key': 'precio_1',
			})
			self.assertEqual(resp.status_code, 200)

		summary = self.client.get(reverse('ver_pedido'))
		self.assertEqual(summary.status_code, 200)

		pedido = self.client.session['pedido']
		self.assertEqual(len(pedido), 3)
		for item in pedido.values():
			self.assertTrue(item.get('descuento_aplicado'), item.get('nombre'))
			self.assertEqual(float(item.get('descuento_monto')), 2.0)

	def test_combo_card_rendered_in_vendor_catalog(self):
		self.client.force_login(self.vendor)
		session = self.client.session
		session['cliente_id'] = self.customer.id
		session.save()
		response = self.client.get(reverse('catalogo_vendedor', args=[self.customer.id]))
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'combos-section')
		self.assertContains(response, 'combo-card')
		self.assertContains(response, 'Combo Vendedor')
		self.assertContains(response, 'js-combo-add-btn')
		self.assertContains(response, 'id="comboModal"')
		self.assertContains(response, 'js-promo-countdown')
		self.assertContains(response, 'promo_countdown.js')
		self.assertContains(response, 'data-countdown-prefix')

	def test_vendor_catalog_shows_promo_banner_before_favorites(self):
		from django.utils import timezone
		from datetime import timedelta
		from config.productos.models import PromocionEscala

		individual = Promocion.objects.create(
			nombre='Promo Individual Banner',
			producto=self.producto_a,
			activa=True,
			fecha_fin=timezone.now() + timedelta(days=1),
		)
		PromocionEscala.objects.create(
			promocion=individual,
			cantidad_minima=5,
			valor_beneficio=Decimal('10'),
		)

		self.client.force_login(self.vendor)
		session = self.client.session
		session['cliente_id'] = self.customer.id
		session.save()
		response = self.client.get(reverse('catalogo_vendedor', args=[self.customer.id]))
		self.assertEqual(response.status_code, 200)
		content = response.content.decode()
		self.assertIn('promo-welcome-banner', content)
		self.assertIn('promoWelcomeTitle', content)
		self.assertIn('promociones=1', content)
		banner_pos = content.find('promo-welcome-banner')
		favorites_pos = content.find('favorites-section')
		combos_pos = content.find('combos-section')
		self.assertGreater(banner_pos, -1)
		self.assertGreater(combos_pos, banner_pos)
		if favorites_pos != -1:
			self.assertGreater(favorites_pos, banner_pos)
			self.assertGreater(combos_pos, favorites_pos)
