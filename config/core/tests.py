from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from config.usuarios.models import Usuario


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
