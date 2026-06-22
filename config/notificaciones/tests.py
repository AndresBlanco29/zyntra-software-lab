from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase
from django.urls import reverse

from config.clientes.models import Cliente
from config.cotizaciones.models import Cotizacion
from config.notificaciones.alerts import get_urgent_workspace_alerts
from config.notificaciones.context_processors import workspace_urgent_alerts
from config.notificaciones.models import Notificacion
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

	def test_backoffice_user_receives_aggregated_urgent_alerts(self):
		Pedido.objects.create(cliente=self.cliente, origen='CLIENTE', estado='RECIBIDO')
		Pedido.objects.create(cliente=self.cliente, origen='CLIENTE', estado='LISTO_PARA_PICKING')
		Cotizacion.objects.create(cliente=self.cliente, estado='ENVIADA')
		Notificacion.objects.create(
			tipo='PEDIDO',
			titulo='New order received',
			mensaje='Order #99 is waiting for review.',
			url='/backoffice/pedidos/',
			leida=False,
			usuario=self.backoffice,
		)

		alerts = get_urgent_workspace_alerts(self.backoffice)

		self.assertIsNotNone(alerts)
		self.assertEqual(alerts['total_count'], 4)
		self.assertEqual(len(alerts['summary_items']), 4)
		self.assertEqual(len(alerts['recent_notifications']), 1)
		self.assertEqual(alerts['summary_items'][0]['label'], 'New customer orders')
		self.assertEqual(alerts['summary_items'][0]['count'], 1)
		self.assertEqual(alerts['summary_items'][1]['count'], 1)
		self.assertEqual(alerts['summary_items'][2]['count'], 1)
		self.assertEqual(alerts['summary_items'][3]['count'], 1)

	def test_context_processor_injects_alerts_for_internal_users(self):
		request = RequestFactory().get('/')
		request.user = self.backoffice

		context = workspace_urgent_alerts(request)

		self.assertIn('workspace_urgent_alerts', context)
		self.assertIsNotNone(context['workspace_urgent_alerts'])

	def test_backoffice_dashboard_includes_navbar_requests_button(self):
		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_dashboard'))

		self.assertContains(response, 'navbar-urgent-alerts')
		self.assertContains(response, 'Requests')
		self.assertContains(response, 'id="system-notifications"')
