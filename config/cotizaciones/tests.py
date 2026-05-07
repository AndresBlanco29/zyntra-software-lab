from decimal import Decimal

from django.conf import settings
from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.contrib.messages import get_messages

from config.clientes.models import Cliente
from config.cotizaciones.models import Cotizacion, CotizacionItem
from config.pedidos.models import Pedido
from config.productos.models import Categoria, ConfiguracionPrecios, Marca, Presentacion, Producto
from config.usuarios.models import Usuario


class BackofficeQuotePricingTests(TestCase):
	def setUp(self):
		self.backoffice = Usuario.objects.create_user(
			username='backoffice-quote-prices',
			password='secret123',
			role='backoffice',
			email='backoffice-quotes@example.com',
		)
		self.customer_user = Usuario.objects.create_user(
			username='cliente-quote-prices',
			password='secret123',
			role='cliente',
			email='cliente-quotes@example.com',
		)
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
		self.assertContains(response, 'value="142.86"', html=False)
		self.assertContains(response, 'Price 5 (1.00%) - $101.01')
		self.assertContains(response, 'Utility: 30.00%')
		self.assertContains(response, 'Updated total: <span id="quoteTotalValue">$142.86</span>', html=False)
		self.assertContains(response, 'data-send-ready-initial="false"')
		self.assertContains(response, 'quote-send-email-button" disabled', html=False)
		self.assertContains(response, 'Assign one customer price tier to all requested products')
		self.assertContains(response, 'Apply to all products')
		self.assertContains(response, 'option value="precio_1"')
		self.assertContains(response, 'option value="precio_5"')
		self.assertContains(response, 'data-price-key="precio_1"', html=False)

	def test_backoffice_quote_detail_uses_customer_assigned_price_when_request_arrives(self):
		self.cliente.estado_revision = Cliente.REVIEW_STATUS_APPROVED
		self.cliente.nivel_precio = 3
		self.cliente.save(update_fields=['estado_revision', 'nivel_precio'])

		assigned_price = self.presentacion.get_price_for_tier(self.cliente.get_nivel_precio_normalizado())
		self.cotizacion.total = assigned_price
		self.cotizacion.save(update_fields=['total'])
		item = self.cotizacion.items.first()
		item.precio = assigned_price
		item.subtotal = assigned_price
		item.save(update_fields=['precio', 'subtotal'])

		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_cotizacion_detalle', args=[self.cotizacion.id]))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Price 3 (10.00%) - $111.11')
		self.assertContains(response, 'value="111.11"', html=False)
		self.assertContains(response, 'Updated total: <span id="quoteTotalValue">$111.11</span>', html=False)

	def test_guardar_cotizacion_persists_customer_assigned_price_for_backoffice(self):
		self.cliente.estado_revision = Cliente.REVIEW_STATUS_APPROVED
		self.cliente.nivel_precio = 3
		self.cliente.save(update_fields=['estado_revision', 'nivel_precio'])

		self.client.force_login(self.customer_user)
		session = self.client.session
		session['carrito'] = {
			str(self.presentacion.producto.id): {
				'presentacion_id': self.presentacion.id,
				'cantidad': 2,
				'precio': str(self.presentacion.precio_1),
			}
		}
		session.save()

		response = self.client.post(reverse('guardar_cotizacion'), {'nota': 'Usar precio asignado'})

		self.assertRedirects(response, reverse('catalogo'))
		created_quote = Cotizacion.objects.exclude(id=self.cotizacion.id).latest('id')
		created_item = created_quote.items.get()
		assigned_price = self.presentacion.get_price_for_tier(3)

		self.assertEqual(created_item.precio, assigned_price)
		self.assertEqual(created_item.subtotal, assigned_price * 2)
		self.assertEqual(created_quote.total, assigned_price * 2)

	def test_customer_quote_success_message_is_not_rendered_for_backoffice(self):
		self.cliente.estado_revision = Cliente.REVIEW_STATUS_APPROVED
		self.cliente.nivel_precio = 3
		self.cliente.save(update_fields=['estado_revision', 'nivel_precio'])

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
		self.assertIn('Your order request was sent successfully.', stored_messages)

		self.client.logout()
		self.client.force_login(self.backoffice)
		backoffice_response = self.client.get(reverse('backoffice_dashboard'))

		self.assertEqual(backoffice_response.status_code, 200)
		self.assertNotContains(backoffice_response, 'Your order request was sent successfully.')

	def test_customer_quote_success_message_is_cleared_on_admin_login(self):
		self.cliente.estado_revision = Cliente.REVIEW_STATUS_APPROVED
		self.cliente.nivel_precio = 3
		self.cliente.save(update_fields=['estado_revision', 'nivel_precio'])

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

		self.client.get(reverse('logout'))
		admin_response = self.client.post(
			reverse('login'),
			{
				'username': self.backoffice.username,
				'password': 'secret123',
			},
			follow=True,
		)

		self.assertEqual(admin_response.status_code, 200)
		self.assertNotContains(admin_response, 'Your order request was sent successfully.')

	def test_guardar_cotizacion_allows_customer_without_assigned_prices(self):
		self.cliente.estado_revision = Cliente.REVIEW_STATUS_APPROVED
		self.cliente.nivel_precio = Cliente.PRICE_TIER_UNASSIGNED
		self.cliente.save(update_fields=['estado_revision', 'nivel_precio'])

		self.client.force_login(self.customer_user)
		session = self.client.session
		session['carrito'] = {
			str(self.presentacion.producto.id): {
				'presentacion_id': self.presentacion.id,
				'cantidad': 1,
				'precio': str(0),
			}
		}
		session.save()

		response = self.client.post(reverse('guardar_cotizacion'), {'nota': 'Sin precios'}, follow=True)

		self.assertRedirects(response, reverse('catalogo'))
		self.assertContains(response, 'agregar-btn')
		created_quote = Cotizacion.objects.exclude(id=self.cotizacion.id).latest('id')
		created_item = created_quote.items.get()
		self.assertEqual(created_item.precio, Decimal('0'))
		self.assertEqual(created_item.subtotal, Decimal('0'))
		self.assertEqual(created_quote.total, Decimal('0'))

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

	def test_backoffice_quote_list_defaults_to_pending_filters(self):
		ready_quote = Cotizacion.objects.create(cliente=self.cliente, estado='LISTA_PARA_CONFIRMACION', total=Decimal('25.00'))
		CotizacionItem.objects.create(
			cotizacion=ready_quote,
			presentacion=self.presentacion,
			cantidad=1,
			precio=Decimal('25.00'),
			subtotal=Decimal('25.00'),
		)
		confirmed_quote = Cotizacion.objects.create(cliente=self.cliente, estado='CONFIRMADA_CLIENTE', total=Decimal('35.00'))
		CotizacionItem.objects.create(
			cotizacion=confirmed_quote,
			presentacion=self.presentacion,
			cantidad=1,
			precio=Decimal('35.00'),
			subtotal=Decimal('35.00'),
		)
		cancelled_quote = Cotizacion.objects.create(cliente=self.cliente, estado='CANCELADA_CLIENTE', total=Decimal('15.00'))
		CotizacionItem.objects.create(
			cotizacion=cancelled_quote,
			presentacion=self.presentacion,
			cantidad=1,
			precio=Decimal('15.00'),
			subtotal=Decimal('15.00'),
		)
		processed_quote = Cotizacion.objects.create(cliente=self.cliente, estado='APROBADA', total=Decimal('45.00'))
		CotizacionItem.objects.create(
			cotizacion=processed_quote,
			presentacion=self.presentacion,
			cantidad=1,
			precio=Decimal('45.00'),
			subtotal=Decimal('45.00'),
		)

		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_cotizaciones'))
		visible_ids = [cotizacion.id for cotizacion in response.context['cotizaciones']]

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Pending Quotes')
		self.assertEqual(visible_ids, [ready_quote.id, self.cotizacion.id])

	def test_backoffice_quote_list_can_filter_confirmed_cancelled_and_processed(self):
		confirmed_quote = Cotizacion.objects.create(cliente=self.cliente, estado='CONFIRMADA_CLIENTE', total=Decimal('35.00'))
		CotizacionItem.objects.create(
			cotizacion=confirmed_quote,
			presentacion=self.presentacion,
			cantidad=1,
			precio=Decimal('35.00'),
			subtotal=Decimal('35.00'),
		)
		cancelled_quote = Cotizacion.objects.create(cliente=self.cliente, estado='CANCELADA_CLIENTE', total=Decimal('15.00'))
		CotizacionItem.objects.create(
			cotizacion=cancelled_quote,
			presentacion=self.presentacion,
			cantidad=1,
			precio=Decimal('15.00'),
			subtotal=Decimal('15.00'),
		)
		processed_quote = Cotizacion.objects.create(cliente=self.cliente, estado='RECHAZADA', total=Decimal('45.00'))
		CotizacionItem.objects.create(
			cotizacion=processed_quote,
			presentacion=self.presentacion,
			cantidad=1,
			precio=Decimal('45.00'),
			subtotal=Decimal('45.00'),
		)

		self.client.force_login(self.backoffice)

		confirmed_response = self.client.get(reverse('backoffice_cotizaciones'), {'view': 'confirmed'})
		confirmed_ids = [cotizacion.id for cotizacion in confirmed_response.context['cotizaciones']]
		self.assertContains(confirmed_response, 'Confirmed Quotes')
		self.assertEqual(confirmed_ids, [confirmed_quote.id])

		cancelled_response = self.client.get(reverse('backoffice_cotizaciones'), {'view': 'cancelled'})
		cancelled_ids = [cotizacion.id for cotizacion in cancelled_response.context['cotizaciones']]
		self.assertContains(cancelled_response, 'Cancelled Quotes')
		self.assertEqual(cancelled_ids, [cancelled_quote.id])

		processed_response = self.client.get(reverse('backoffice_cotizaciones'), {'view': 'processed'})
		processed_ids = [cotizacion.id for cotizacion in processed_response.context['cotizaciones']]
		self.assertContains(processed_response, 'Processed Quotes')
		self.assertEqual(processed_ids, [processed_quote.id])

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

	def test_backoffice_can_generate_purchase_order_from_quote_and_notify_customer(self):
		self.client.force_login(self.backoffice)
		item = self.cotizacion.items.first()

		update_response = self.client.post(reverse('backoffice_cotizacion_detalle', args=[self.cotizacion.id]), {
			f'cantidad_{item.id}': '2',
			f'precio_{item.id}': '142.86',
			'nota_backoffice': 'Cliente confirmo por telefono',
		})

		self.assertRedirects(update_response, f"{reverse('backoffice_cotizacion_detalle', args=[self.cotizacion.id])}?saved=1")

		response = self.client.post(reverse('generar_pedido_desde_cotizacion', args=[self.cotizacion.id]))

		pedido = Pedido.objects.get(cotizacion=self.cotizacion)
		self.assertRedirects(response, reverse('backoffice_pedido_detalle', args=[pedido.id]))
		self.cotizacion.refresh_from_db()
		self.assertEqual(self.cotizacion.estado, 'CONFIRMADA_CLIENTE')
		self.assertEqual(self.cotizacion.total, pedido.total)
		self.assertEqual(pedido.origen, 'CLIENTE')
		self.assertEqual(pedido.canal_toma, 'backoffice')
		self.assertTrue(pedido.acepta_terminos)
		self.assertEqual(len(mail.outbox), 2)
		self.assertIn('Purchase order in process', mail.outbox[-1].subject)
		self.assertIn('generated successfully', mail.outbox[-1].body)

	def test_backoffice_cannot_generate_duplicate_purchase_order_from_quote(self):
		pedido = Pedido.objects.create(
			cliente=self.cliente,
			vendedor=self.cotizacion.vendedor,
			cotizacion=self.cotizacion,
			origen='CLIENTE',
			canal_toma='portal',
			estado='RECIBIDO',
			total=Decimal('25.00'),
		)
		self.client.force_login(self.backoffice)

		response = self.client.post(reverse('generar_pedido_desde_cotizacion', args=[self.cotizacion.id]))

		self.assertRedirects(response, reverse('backoffice_pedido_detalle', args=[pedido.id]))
		self.assertEqual(Pedido.objects.filter(cotizacion=self.cotizacion).count(), 1)


class CustomerReceivedQuotesViewTests(TestCase):
	def setUp(self):
		self.customer_user = Usuario.objects.create_user(username='cliente-recibidas', password='secret123', role='cliente')
		self.cliente = Cliente.objects.create(
			usuario=self.customer_user,
			nombre_empresa='Cliente Recibidas',
			telefono='5551234567',
			direccion='123 Main St',
			ciudad='Dallas',
			estado='TX',
			codigo_postal='75001',
			pais='USA',
			sales_tax_number='TX-COT-2',
			certificado_tax='certificados/test.pdf',
		)
		categoria = Categoria.objects.create(nombre='Bebidas')
		marca = Marca.objects.create(nombre='Marca Recibidas')
		producto = Producto.objects.create(nombre='Producto Recibidas', categoria=categoria, marca=marca)
		self.presentacion = Presentacion.objects.create(
			producto=producto,
			nombre='Caja',
			unidades=1,
			tipo_contenido='caja',
			precio_1=Decimal('12.00'),
		)
		self.pending_quote = Cotizacion.objects.create(cliente=self.cliente, estado='LISTA_PARA_CONFIRMACION', total=Decimal('24.00'))
		CotizacionItem.objects.create(cotizacion=self.pending_quote, presentacion=self.presentacion, cantidad=2, precio=Decimal('12.00'), subtotal=Decimal('24.00'))
		self.confirmed_quote = Cotizacion.objects.create(cliente=self.cliente, estado='CONFIRMADA_CLIENTE', total=Decimal('12.00'))
		CotizacionItem.objects.create(cotizacion=self.confirmed_quote, presentacion=self.presentacion, cantidad=1, precio=Decimal('12.00'), subtotal=Decimal('12.00'))
		self.cancelled_quote = Cotizacion.objects.create(cliente=self.cliente, estado='CANCELADA_CLIENTE', total=Decimal('36.00'))
		CotizacionItem.objects.create(cotizacion=self.cancelled_quote, presentacion=self.presentacion, cantidad=3, precio=Decimal('12.00'), subtotal=Decimal('36.00'))
		self.sent_by_client_quote = Cotizacion.objects.create(cliente=self.cliente, estado='ENVIADA', total=Decimal('0.00'))
		CotizacionItem.objects.create(cotizacion=self.sent_by_client_quote, presentacion=self.presentacion, cantidad=1, precio=Decimal('0.00'), subtotal=Decimal('0.00'))

	def test_received_quotes_defaults_to_pending_only(self):
		self.client.force_login(self.customer_user)

		response = self.client.get(reverse('cliente_cotizaciones_recibidas'))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Pending Orders')
		self.assertContains(response, 'Pending: 1')
		self.assertContains(response, f'#{self.pending_quote.id}')
		self.assertNotContains(response, f'#{self.confirmed_quote.id}')
		self.assertNotContains(response, f'#{self.cancelled_quote.id}')
		self.assertNotContains(response, f'#{self.sent_by_client_quote.id}')

	def test_received_quotes_can_filter_confirmed(self):
		self.client.force_login(self.customer_user)

		response = self.client.get(reverse('cliente_cotizaciones_recibidas'), {'view': 'confirmed'})

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Confirmed Orders')
		self.assertContains(response, f'#{self.confirmed_quote.id}')
		self.assertNotContains(response, f'#{self.pending_quote.id}')
		self.assertNotContains(response, f'#{self.cancelled_quote.id}')

	def test_received_quotes_can_filter_cancelled(self):
		self.client.force_login(self.customer_user)

		response = self.client.get(reverse('cliente_cotizaciones_recibidas'), {'view': 'cancelled'})

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Cancelled Orders')
		self.assertContains(response, f'#{self.cancelled_quote.id}')
		self.assertNotContains(response, f'#{self.pending_quote.id}')
		self.assertNotContains(response, f'#{self.confirmed_quote.id}')

	def test_my_quote_page_renders_in_spanish_when_selected(self):
		session = self.client.session
		session['carrito'] = {
			str(self.presentacion.id): {
				'presentacion_id': self.presentacion.id,
				'cantidad': 2,
				'precio': str(self.presentacion.precio_1),
			}
		}
		session.save()
		self.client.force_login(self.customer_user)
		self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = 'es'

		response = self.client.get(reverse('ver_cotizacion'), HTTP_ACCEPT_LANGUAGE='es')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, '<title>Mi pedido</title>', html=False)
		self.assertContains(response, 'Catálogo')
		self.assertContains(response, 'Mi pedido')
		self.assertContains(response, 'Pedidos recibidos')
		self.assertContains(response, 'Revisa los productos antes de enviar tu solicitud', html=False)
		self.assertContains(response, 'Total de productos: 1', html=False)
		self.assertContains(response, 'Resumen del pedido')
		self.assertContains(response, 'Nota opcional')
		self.assertContains(response, 'El pedido se enviará para revisión y validación.', html=False)
		self.assertContains(response, 'Enviar solicitud de pedido')
