from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.translation import gettext as _

from config.clientes.models import Cliente
from config.cotizaciones.models import Cotizacion, CotizacionItem
from config.cotizaciones.views import (
	_build_order_items_payload_from_quote,
	_default_backoffice_quote_price,
	_quote_item_price_for_customer,
)
from config.productos.models import Categoria, Marca, Presentacion, Producto, Promocion, PromocionEscala
from config.usuarios.models import Usuario


class CustomerRequestPricingTests(TestCase):
	def setUp(self):
		self.cliente_user = Usuario.objects.create_user(
			username='cliente-qb-price',
			password='secret123',
			role='cliente',
			email='cliente-qb@example.com',
		)
		self.cliente = Cliente.objects.create(
			usuario=self.cliente_user,
			nombre_empresa='Cliente QB Price',
			telefono='5551112222',
			direccion='1 Test St',
			ciudad='Atlanta',
			estado='GA',
			codigo_postal='30301',
			pais='USA',
			sales_tax_number='TX-QB-1',
			certificado_tax='certificados/test.pdf',
			estado_revision=Cliente.REVIEW_STATUS_APPROVED,
			nivel_precio=1,
		)
		self.backoffice = Usuario.objects.create_user(
			username='bo-qb-price',
			password='secret123',
			role='backoffice',
		)
		categoria = Categoria.objects.create(nombre='Categoria QB')
		marca = Marca.objects.create(nombre='Marca QB')
		self.producto = Producto.objects.create(nombre='Producto QB', categoria=categoria, marca=marca, activo=True)
		self.presentacion = Presentacion.objects.create(
			producto=self.producto,
			nombre='Caja',
			unidades=1,
			tipo_contenido='caja',
			precio_1=Decimal('0.00'),
			precio_2=Decimal('0.00'),
			precio_3=Decimal('0.00'),
			qb_price=Decimal('18.50'),
		)
		self.gift_producto = Producto.objects.create(
			nombre='Producto Regalo',
			categoria=categoria,
			marca=marca,
			activo=True,
		)
		self.gift_presentacion = Presentacion.objects.create(
			producto=self.gift_producto,
			nombre='Caja',
			unidades=1,
			tipo_contenido='caja',
			precio_1=Decimal('5.00'),
			qb_price=Decimal('5.00'),
		)

	def test_customer_request_price_prefers_quickbooks_catalog_price(self):
		price = _quote_item_price_for_customer(
			cliente=self.cliente,
			presentacion=self.presentacion,
			session_price=0,
		)
		self.assertEqual(price, Decimal('18.50'))

	def test_backoffice_default_heals_zero_price_with_qb_price(self):
		cotizacion = Cotizacion.objects.create(cliente=self.cliente, estado='ENVIADA', total=0)
		item = CotizacionItem.objects.create(
			cotizacion=cotizacion,
			presentacion=self.presentacion,
			cantidad=2,
			precio=Decimal('0.00'),
			subtotal=Decimal('0.00'),
			es_regalo=False,
		)
		self.assertEqual(_default_backoffice_quote_price(item, cotizacion), Decimal('18.50'))

	def test_guardar_cotizacion_persists_qb_price_on_paid_lines(self):
		session = self.client.session
		session['carrito'] = {
			str(self.presentacion.id): {
				'producto_id': self.producto.id,
				'presentacion_id': self.presentacion.id,
				'nombre': self.producto.nombre,
				'cantidad': 3,
				'precio': 0,
			}
		}
		session.save()
		self.client.force_login(self.cliente_user)

		response = self.client.post(reverse('guardar_cotizacion'), {'nota': 'Pedido con precio QB'})
		self.assertEqual(response.status_code, 302)

		cotizacion = Cotizacion.objects.get(cliente=self.cliente)
		item = cotizacion.items.get(presentacion=self.presentacion, es_regalo=False)
		self.assertEqual(item.precio, Decimal('18.50'))

	@override_settings(LANGUAGE_CODE='en')
	def test_free_promo_line_is_locked_and_labeled_in_backoffice_quote(self):
		promo = Promocion.objects.create(
			nombre='Buy paid get gift free',
			alcance=Promocion.ALCANCE_INDIVIDUAL,
			producto=self.producto,
			activa=True,
		)
		PromocionEscala.objects.create(
			promocion=promo,
			cantidad_minima=1,
			tipo_beneficio=PromocionEscala.TIPO_FREE_UNITS,
			unidades_gratis=1,
			presentacion_regalo=self.gift_presentacion,
		)
		cotizacion = Cotizacion.objects.create(cliente=self.cliente, estado='ENVIADA', total=Decimal('18.50'))
		CotizacionItem.objects.create(
			cotizacion=cotizacion,
			presentacion=self.presentacion,
			cantidad=1,
			precio=Decimal('18.50'),
			subtotal=Decimal('18.50'),
			es_regalo=False,
		)

		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_cotizacion_detalle', args=[cotizacion.id]))
		self.assertEqual(response.status_code, 200)
		gift_item = CotizacionItem.objects.get(cotizacion=cotizacion, es_regalo=True)
		rows = response.context['cotizacion_item_rows']
		gift_rows = [row for row in rows if row['item'].es_regalo]
		self.assertEqual(len(gift_rows), 1)
		self.assertEqual(gift_rows[0]['current_price'], Decimal('0.00'))
		self.assertContains(response, _('FREE'))
		self.assertContains(response, _('Free'))
		self.assertContains(response, 'data-free-promo-line="true"', html=False)
		self.assertContains(response, _('Promotional gift. Price and discount cannot be edited.'))
		self.assertNotContains(response, f'id="precio_{gift_item.id}"')

	def test_quote_to_order_payload_keeps_gift_flag_and_qb_price(self):
		cotizacion = Cotizacion.objects.create(cliente=self.cliente, estado='ENVIADA', total=0)
		CotizacionItem.objects.create(
			cotizacion=cotizacion,
			presentacion=self.presentacion,
			cantidad=2,
			precio=Decimal('0.00'),
			subtotal=Decimal('0.00'),
			es_regalo=False,
		)
		CotizacionItem.objects.create(
			cotizacion=cotizacion,
			presentacion=self.gift_presentacion,
			cantidad=1,
			precio=Decimal('0.00'),
			subtotal=Decimal('0.00'),
			descuento_aplicado=True,
			es_regalo=True,
		)

		payload = _build_order_items_payload_from_quote(cotizacion)
		paid = next(row for row in payload if not row['es_regalo'])
		gift = next(row for row in payload if row['es_regalo'])
		self.assertEqual(paid['precio'], Decimal('18.50'))
		self.assertEqual(gift['precio'], Decimal('0.00'))
		self.assertTrue(gift['es_regalo'])
