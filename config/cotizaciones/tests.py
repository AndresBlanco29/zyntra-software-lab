from decimal import Decimal

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.contrib.messages import get_messages

from config.clientes.models import Cliente
from config.cotizaciones.models import Cotizacion, CotizacionItem
from config.productos.models import Categoria, ConfiguracionPrecios, Marca, Presentacion, Producto
from config.usuarios.models import Usuario


class BackofficeQuotePricingTests(TestCase):
	def setUp(self):
		self.backoffice = Usuario.objects.create_user(username='backoffice-quote-prices', password='secret123', role='backoffice')
		self.customer_user = Usuario.objects.create_user(username='cliente-quote-prices', password='secret123', role='cliente')
		self.cliente = Cliente.objects.create(
			usuario=self.customer_user,
			nombre_empresa='Cliente Cotizacion',
			telefono='5551234567',
			direccion='123 Main St',
			ciudad='Dallas',
			estado='TX',
			codigo_postal='75001',
			pais='USA',
			sales_tax_number='TX-COT-1',
			certificado_tax='certificados/test.pdf',
		)
		categoria = Categoria.objects.create(nombre='Snacks')
		marca = Marca.objects.create(nombre='Marca Cotizacion')
		producto = Producto.objects.create(nombre='Producto Cotizacion', categoria=categoria, marca=marca)

		configuracion = ConfiguracionPrecios.obtener()
		configuracion.porcentaje_1 = Decimal('30')
		configuracion.porcentaje_2 = Decimal('20')
		configuracion.porcentaje_3 = Decimal('10')
		configuracion.porcentaje_4 = Decimal('5')
		configuracion.porcentaje_5 = Decimal('1')
		configuracion.save()

		self.presentacion = Presentacion.objects.create(
			producto=producto,
			nombre='Caja',
			unidades=1,
			tipo_contenido='caja',
			costo=Decimal('100.00'),
		)
		self.cotizacion = Cotizacion.objects.create(cliente=self.cliente, estado='ENVIADA', total=self.presentacion.precio_1)
		CotizacionItem.objects.create(
			cotizacion=self.cotizacion,
			presentacion=self.presentacion,
			cantidad=1,
			precio=self.presentacion.precio_1,
			subtotal=self.presentacion.precio_1,
		)

	def test_backoffice_quote_detail_shows_preset_prices_and_utility(self):
		self.client.force_login(self.backoffice)

		response = self.client.get(reverse('backoffice_cotizacion_detalle', args=[self.cotizacion.id]))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Status: Sent by client', html=False)
		self.assertContains(response, 'Price 1 (30.00%)')
		self.assertContains(response, 'Price 2 (20.00%)')
		self.assertContains(response, 'Manual price')
		self.assertContains(response, 'value="101.01"', html=False)
		self.assertContains(response, 'Price 5 (1.00%) - $101.01')
		self.assertContains(response, 'Utility: 1.00%')
		self.assertContains(response, 'Updated total: <span id="quoteTotalValue">$101.01</span>', html=False)
		self.assertContains(response, 'data-send-ready-initial="false"')
		self.assertContains(response, 'quote-send-email-button" disabled', html=False)

	def test_customer_quote_success_message_is_not_rendered_for_backoffice(self):
		self.client.force_login(self.customer_user)
		session = self.client.session
		session['carrito'] = {
			str(self.presentacion.id): {
				'presentacion_id': self.presentacion.id,
				'cantidad': 2,
				'precio': str(self.presentacion.precio_1),
			}
		}
		session.save()

		response = self.client.post(reverse('guardar_cotizacion'), {'nota': 'Nueva solicitud'})
		self.assertEqual(response.status_code, 302)

		stored_messages = [message.message for message in get_messages(response.wsgi_request)]
		self.assertIn('Your quote request was sent successfully.', stored_messages)

		self.client.logout()
		self.client.force_login(self.backoffice)
		backoffice_response = self.client.get(reverse('backoffice_dashboard'))

		self.assertEqual(backoffice_response.status_code, 200)
		self.assertNotContains(backoffice_response, 'Your quote request was sent successfully.')

	def test_backoffice_cannot_send_quote_without_saving_changes_first(self):
		self.client.force_login(self.backoffice)

		response = self.client.post(reverse('enviar_cotizacion_cliente', args=[self.cotizacion.id]))

		self.assertRedirects(response, reverse('backoffice_cotizacion_detalle', args=[self.cotizacion.id]))
		self.cotizacion.refresh_from_db()
		self.assertFalse(self.cotizacion.correo_enviado)

	def test_backoffice_can_send_quote_after_saving_changes(self):
		self.client.force_login(self.backoffice)

		update_response = self.client.post(reverse('backoffice_cotizacion_detalle', args=[self.cotizacion.id]), {
			f'cantidad_{self.cotizacion.items.first().id}': '2',
			f'precio_{self.cotizacion.items.first().id}': str(self.presentacion.precio_1),
			'nota_backoffice': 'Precio confirmado',
		})

		self.assertRedirects(update_response, f"{reverse('backoffice_cotizacion_detalle', args=[self.cotizacion.id])}?saved=1")
		self.cotizacion.refresh_from_db()
		self.assertTrue(self.cotizacion.backoffice_pricing_confirmed)

		detail_response = self.client.get(f"{reverse('backoffice_cotizacion_detalle', args=[self.cotizacion.id])}?saved=1")
		self.assertContains(detail_response, 'data-send-ready-initial="true"')

		send_response = self.client.post(reverse('enviar_cotizacion_cliente', args=[self.cotizacion.id]))
		self.assertEqual(send_response.status_code, 302)

	def test_backoffice_cannot_open_whatsapp_without_saving_changes_first(self):
		self.client.force_login(self.backoffice)

		response = self.client.get(reverse('abrir_whatsapp_manual_cotizacion', args=[self.cotizacion.id]))

		self.assertRedirects(response, reverse('backoffice_cotizacion_detalle', args=[self.cotizacion.id]))
		self.cotizacion.refresh_from_db()
		self.assertFalse(self.cotizacion.whatsapp_manual_abierto)

	def test_backoffice_cannot_save_quote_with_price_at_one(self):
		self.client.force_login(self.backoffice)
		item = self.cotizacion.items.first()

		response = self.client.post(reverse('backoffice_cotizacion_detalle', args=[self.cotizacion.id]), {
			f'cantidad_{item.id}': '1',
			f'precio_{item.id}': '1.00',
			'nota_backoffice': 'Intento invalido',
		})

		self.assertRedirects(response, reverse('backoffice_cotizacion_detalle', args=[self.cotizacion.id]))
		item.refresh_from_db()
		self.assertEqual(item.precio, self.presentacion.precio_1)

	def test_enviada_status_label_is_sent_by_client(self):
		self.assertEqual(self.cotizacion.get_estado_display(), 'Sent by client')

	def test_backoffice_quote_list_respects_selected_language(self):
		self.client.force_login(self.backoffice)

		english_response = self.client.get(reverse('backoffice_cotizaciones'))

		self.assertEqual(english_response.status_code, 200)
		self.assertContains(english_response, '<title>Quotes BackOffice</title>', html=False)
		self.assertContains(english_response, 'Log out')
		self.assertContains(english_response, 'Are you sure you want to log out?')
		self.assertContains(english_response, 'Sent by client')

		self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = 'es'
		spanish_response = self.client.get(reverse('backoffice_cotizaciones'), HTTP_ACCEPT_LANGUAGE='es')

		self.assertEqual(spanish_response.status_code, 200)
		self.assertContains(spanish_response, '<title>Cotizaciones BackOffice</title>', html=False)
		self.assertContains(spanish_response, 'Cerrar sesión')
		self.assertContains(spanish_response, '¿Estás seguro de salir?')
		self.assertContains(spanish_response, 'Enviada por el cliente')

	def test_backoffice_cannot_save_quote_below_cost(self):
		self.client.force_login(self.backoffice)
		item = self.cotizacion.items.first()

		response = self.client.post(reverse('backoffice_cotizacion_detalle', args=[self.cotizacion.id]), {
			f'cantidad_{item.id}': '1',
			f'precio_{item.id}': '99.99',
			'nota_backoffice': 'Perdida',
		})

		self.assertRedirects(response, reverse('backoffice_cotizacion_detalle', args=[self.cotizacion.id]))
		item.refresh_from_db()
		self.assertEqual(item.precio, self.presentacion.precio_1)
