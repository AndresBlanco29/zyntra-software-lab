from types import SimpleNamespace

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from config.core.workflow_badges import build_delivery_workflow_badge, build_order_workflow_badge
from config.core.pagination import get_quick_jump_pages
from config.usuarios.models import Usuario


class QuickJumpPaginationTests(TestCase):
	def test_page_one_offers_ten_fifteen_twenty_and_twenty_five(self):
		self.assertEqual(get_quick_jump_pages(1, 30), [10, 15, 20, 25])

	def test_jump_pages_stay_ahead_of_current_page(self):
		self.assertEqual(get_quick_jump_pages(12, 30), [20, 25, 30])

	def test_jump_pages_respect_total_pages(self):
		self.assertEqual(get_quick_jump_pages(1, 18), [10, 15])

	def test_no_jump_pages_on_last_page(self):
		self.assertEqual(get_quick_jump_pages(20, 20), [])


class BackofficeSpanishTranslationsTests(TestCase):
	def setUp(self):
		self.backoffice = Usuario.objects.create_user(
			username='backoffice-i18n',
			password='secret123',
			role='backoffice',
		)

	def test_backoffice_pages_render_in_spanish_when_language_is_selected(self):
		self.client.force_login(self.backoffice)
		self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = 'es'

		live_response = self.client.get(reverse('backoffice_live_drivers'), HTTP_ACCEPT_LANGUAGE='es')
		invoices_response = self.client.get(reverse('backoffice_invoices_list'), HTTP_ACCEPT_LANGUAGE='es')
		inventory_response = self.client.get(reverse('backoffice_inventory_list'), HTTP_ACCEPT_LANGUAGE='es')

		self.assertEqual(live_response.status_code, 200)
		self.assertContains(live_response, 'Conductores en vivo')
		self.assertContains(live_response, 'Monitorea cada conductor actualmente en ruta y observa su vehículo moverse en tiempo real.', html=False)
		self.assertContains(live_response, 'Conductores activos')
		self.assertContains(live_response, 'Actualización automática cada 10 segundos', html=False)

		self.assertEqual(invoices_response.status_code, 200)
		self.assertContains(invoices_response, '<title>Facturas</title>', html=False)
		self.assertContains(invoices_response, 'Generadas a partir de cantidades verificadas en picking.', html=False)
		self.assertContains(invoices_response, 'Método de entrega', html=False)
		self.assertContains(invoices_response, 'Despacho')

		self.assertEqual(inventory_response.status_code, 200)
		self.assertContains(inventory_response, '<title>Inventario operativo</title>', html=False)
		self.assertContains(inventory_response, 'Revisa el stock físico, reservado y disponible por presentación.', html=False)
		self.assertContains(inventory_response, 'Buscar producto o presentación', html=False)
		self.assertContains(inventory_response, 'Stock físico', html=False)
		self.assertContains(inventory_response, 'Stock reservado', html=False)
		self.assertContains(inventory_response, 'Sin stock')
		self.assertContains(inventory_response, 'Inventario')


class DefaultEnglishLocaleTests(TestCase):
	def test_home_defaults_to_english_without_explicit_language_choice(self):
		response = self.client.get('/', HTTP_ACCEPT_LANGUAGE='es-MX,es;q=0.9,en;q=0.8')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'lang="en"')
		self.assertContains(response, 'About Us')
		self.assertNotContains(response, 'Quienes Somos')

	def test_explicit_spanish_cookie_still_renders_spanish(self):
		self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = 'es'
		response = self.client.get('/', HTTP_ACCEPT_LANGUAGE='en-US,en;q=0.9')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Quienes Somos')


class WorkflowBadgeTests(TestCase):
	def test_vendor_orders_use_vendor_to_backoffice_transition_badge(self):
		badge = build_order_workflow_badge(SimpleNamespace(estado='RECIBIDO', origen='VENDEDOR', invoice=None))

		self.assertEqual(badge['kind'], 'split')
		self.assertEqual(badge['sender_role'], 'vendedor')
		self.assertEqual(badge['receiver_role'], 'backoffice')

	def test_route_deliveries_use_backoffice_to_driver_transition_badge(self):
		badge = build_delivery_workflow_badge(SimpleNamespace(estado='ASIGNADA'))

		self.assertEqual(badge['kind'], 'split')
		self.assertEqual(badge['sender_role'], 'backoffice')
		self.assertEqual(badge['receiver_role'], 'driver')
