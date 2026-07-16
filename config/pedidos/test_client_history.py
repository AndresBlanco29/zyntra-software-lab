from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from config.clientes.models import Cliente
from config.facturacion.models import Invoice, InvoiceItem
from config.pedidos.client_history import (
	list_cliente_favorite_product_ids,
	merge_pedido_into_session_cart,
)
from config.pedidos.models import Pedido, PedidoItem
from config.productos.models import Categoria, Marca, Presentacion, Producto
from config.productos.promotions import aplicar_promocion_en_item_sesion
from config.usuarios.models import Usuario


class ClientPurchaseSuggestionsTests(TestCase):
	def setUp(self):
		self.user = Usuario.objects.create_user(
			username='fav-client',
			password='secret123',
			role='cliente',
			email='fav@example.com',
		)
		self.cliente = Cliente.objects.create(
			usuario=self.user,
			nombre_empresa='Favorite Client Co',
			telefono='5551234000',
			direccion='10 History St',
			ciudad='Atlanta',
			estado='GA',
			codigo_postal='30301',
			pais='USA',
			sales_tax_number='TAX-FAV-1',
			certificado_tax=SimpleUploadedFile('certificado.txt', b'cert'),
			aprobado=True,
			nivel_precio=1,
			estado_revision=Cliente.REVIEW_STATUS_APPROVED,
		)
		categoria = Categoria.objects.create(nombre='Oils')
		marca = Marca.objects.create(nombre='123')
		self.producto_a = Producto.objects.create(
			nombre='Aceite A',
			categoria=categoria,
			marca=marca,
			activo=True,
		)
		self.producto_b = Producto.objects.create(
			nombre='Aceite B',
			categoria=categoria,
			marca=marca,
			activo=True,
		)
		self.presentacion_a = Presentacion.objects.create(
			producto=self.producto_a,
			nombre='CS',
			unidades=12,
			tipo_contenido='caja',
			precio_1=Decimal('20.00'),
		)
		self.presentacion_b = Presentacion.objects.create(
			producto=self.producto_b,
			nombre='CS',
			unidades=6,
			tipo_contenido='caja',
			precio_1=Decimal('15.00'),
		)

	def _make_pedido(self, *, estado='DESPACHADO', total='40.00'):
		return Pedido.objects.create(
			cliente=self.cliente,
			origen='CLIENTE',
			estado=estado,
			total=Decimal(total),
		)

	def test_favorites_rank_from_invoice_history(self):
		pedido_1 = self._make_pedido(total='60.00')
		pedido_2 = self._make_pedido(total='15.00')
		invoice_1 = Invoice.objects.create(
			pedido=pedido_1,
			cliente=self.cliente,
			metodo_entrega='CUSTOMER_PICK_UP',
			subtotal=Decimal('60.00'),
			total_neto=Decimal('60.00'),
		)
		invoice_2 = Invoice.objects.create(
			pedido=pedido_2,
			cliente=self.cliente,
			metodo_entrega='CUSTOMER_PICK_UP',
			subtotal=Decimal('15.00'),
			total_neto=Decimal('15.00'),
		)
		InvoiceItem.objects.create(
			invoice=invoice_1,
			presentacion=self.presentacion_a,
			producto_nombre=self.producto_a.nombre,
			presentacion_nombre=self.presentacion_a.nombre,
			cantidad_facturada=3,
			precio_unitario=Decimal('20.00'),
			subtotal=Decimal('60.00'),
		)
		InvoiceItem.objects.create(
			invoice=invoice_2,
			presentacion=self.presentacion_b,
			producto_nombre=self.producto_b.nombre,
			presentacion_nombre=self.presentacion_b.nombre,
			cantidad_facturada=1,
			precio_unitario=Decimal('15.00'),
			subtotal=Decimal('15.00'),
		)

		ranked = list_cliente_favorite_product_ids(cliente=self.cliente, limit=5)
		self.assertEqual([row['product_id'] for row in ranked], [self.producto_a.id, self.producto_b.id])
		self.assertEqual(ranked[0]['preferred_presentation_id'], self.presentacion_a.id)

	def test_favorites_fallback_to_pedido_items(self):
		pedido = self._make_pedido()
		PedidoItem.objects.create(
			pedido=pedido,
			presentacion=self.presentacion_b,
			cantidad_solicitada=2,
			cantidad=2,
			precio=Decimal('15.00'),
			subtotal=Decimal('30.00'),
		)
		PedidoItem.objects.create(
			pedido=pedido,
			presentacion=self.presentacion_a,
			cantidad_solicitada=1,
			cantidad=1,
			precio=Decimal('20.00'),
			subtotal=Decimal('20.00'),
		)

		ranked = list_cliente_favorite_product_ids(cliente=self.cliente, limit=5)
		self.assertEqual(ranked[0]['product_id'], self.producto_b.id)
		self.assertEqual(ranked[0]['preferred_presentation_id'], self.presentacion_b.id)

	def test_merge_pedido_into_session_cart(self):
		pedido = self._make_pedido()
		PedidoItem.objects.create(
			pedido=pedido,
			presentacion=self.presentacion_a,
			cantidad_solicitada=2,
			cantidad=2,
			precio=Decimal('20.00'),
			subtotal=Decimal('40.00'),
		)
		existing = {
			str(self.presentacion_a.id): {
				'producto_id': self.producto_a.id,
				'presentacion_id': self.presentacion_a.id,
				'nombre': self.producto_a.nombre,
				'cantidad': 1,
				'precio': 20.0,
			}
		}
		carrito, added = merge_pedido_into_session_cart(
			carrito=existing,
			pedido=pedido,
			price_fn=lambda presentacion: Decimal('20.00'),
			promo_fn=aplicar_promocion_en_item_sesion,
		)
		self.assertEqual(added, 1)
		self.assertEqual(carrito[str(self.presentacion_a.id)]['cantidad'], 3)

	def test_order_history_page_lists_newest_first(self):
		older = self._make_pedido(total='10.00')
		newer = self._make_pedido(total='30.00')
		PedidoItem.objects.create(
			pedido=older,
			presentacion=self.presentacion_a,
			cantidad_solicitada=1,
			cantidad=1,
			precio=Decimal('20.00'),
			subtotal=Decimal('20.00'),
		)
		PedidoItem.objects.create(
			pedido=newer,
			presentacion=self.presentacion_b,
			cantidad_solicitada=2,
			cantidad=2,
			precio=Decimal('15.00'),
			subtotal=Decimal('30.00'),
		)

		client = Client()
		client.force_login(self.user)
		response = client.get(reverse('cliente_historial_ordenes'))
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, f'#{newer.numero_display}')
		self.assertContains(response, 'Reorder')
		body = response.content.decode()
		self.assertLess(body.index(f'#{newer.numero_display}'), body.index(f'#{older.numero_display}'))

	def test_reorder_adds_items_to_cart_and_redirects(self):
		pedido = self._make_pedido()
		PedidoItem.objects.create(
			pedido=pedido,
			presentacion=self.presentacion_a,
			cantidad_solicitada=4,
			cantidad=4,
			precio=Decimal('20.00'),
			subtotal=Decimal('80.00'),
		)

		client = Client()
		client.force_login(self.user)
		response = client.post(reverse('cliente_reordenar_pedido', args=[pedido.id]))
		self.assertEqual(response.status_code, 302)
		self.assertEqual(response.url, reverse('ver_cotizacion'))

		session = client.session
		cart = session.get('carrito') or {}
		self.assertIn(str(self.presentacion_a.id), cart)
		self.assertEqual(cart[str(self.presentacion_a.id)]['cantidad'], 4)

	def test_catalog_shows_favorite_products_section(self):
		pedido = self._make_pedido()
		PedidoItem.objects.create(
			pedido=pedido,
			presentacion=self.presentacion_a,
			cantidad_solicitada=1,
			cantidad=1,
			precio=Decimal('20.00'),
			subtotal=Decimal('20.00'),
		)

		client = Client()
		client.force_login(self.user)
		response = client.get(reverse('catalogo'))
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Your Favorite Products')
		self.assertContains(response, self.producto_a.nombre)
