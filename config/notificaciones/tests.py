from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase
from django.urls import reverse

from config.clientes.models import Cliente
from config.cotizaciones.models import Cotizacion
from config.notificaciones.alerts import get_urgent_workspace_alerts
from config.notificaciones.context_processors import workspace_urgent_alerts
from config.pedidos.models import Pedido
from config.usuarios.models import Usuario


class WorkspaceUrgentAlertsTests(TestCase):
	def setUp(self):
		self.backoffice = Usuario.objects.create_user(username='backoffice', password='secret123', role='backoffice')
		self.customer_user = Usuario.objects.create_user(
			username='customer',
			password='secret123',
			role='cliente',
			email='customer@example.com',
		)
		self.cliente = Cliente.objects.create(
			usuario=self.customer_user,
			nombre_empresa='Cliente Demo',
			telefono='5551234567',
			direccion='123 Main St',
			ciudad='Marietta',
			estado='GA',
			codigo_postal='30062',
			pais='USA',
			sales_tax_number='TAX-123',
			certificado_tax=SimpleUploadedFile('certificado.txt', b'certificado'),
			aprobado=True,
		)

	def test_customer_user_does_not_receive_workspace_alerts(self):
		self.assertIsNone(get_urgent_workspace_alerts(self.customer_user))

	def test_backoffice_user_receives_customer_quote_alerts_only(self):
		Cotizacion.objects.create(cliente=self.cliente, estado='ENVIADA')
		Cotizacion.objects.create(cliente=self.cliente, estado='LISTA_PARA_CONFIRMACION')
		Cotizacion.objects.create(cliente=self.cliente, estado='CONFIRMADA_CLIENTE')
		Pedido.objects.create(cliente=self.cliente, origen='CLIENTE', estado='RECIBIDO')

		alerts = get_urgent_workspace_alerts(self.backoffice)

		self.assertIsNotNone(alerts)
		self.assertEqual(alerts['total_count'], 3)
		self.assertEqual(len(alerts['summary_items']), 3)
		self.assertEqual(len(alerts['recent_quotes']), 3)
		self.assertEqual(alerts['summary_items'][0]['label'], 'Pending review')
		self.assertEqual(alerts['summary_items'][0]['count'], 1)
		self.assertEqual(alerts['summary_items'][1]['label'], 'Waiting for customer')
		self.assertEqual(alerts['summary_items'][1]['count'], 1)
		self.assertEqual(alerts['summary_items'][2]['label'], 'Confirmed, not finished')
		self.assertEqual(alerts['summary_items'][2]['count'], 1)

	def test_context_processor_injects_alerts_for_internal_users(self):
		request = RequestFactory().get('/')
		request.user = self.backoffice

		context = workspace_urgent_alerts(request)

		self.assertIn('workspace_urgent_alerts', context)
		self.assertIsNotNone(context['workspace_urgent_alerts'])

	def test_backoffice_dashboard_includes_navbar_quotes_button(self):
		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_dashboard'))

		self.assertContains(response, 'navbar-urgent-alerts')
		self.assertContains(response, 'Quotes')
		self.assertContains(response, 'Customer quotes')
