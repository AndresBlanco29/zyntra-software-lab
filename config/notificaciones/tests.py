from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse

from config.clientes.models import Cliente
from config.cotizaciones.models import Cotizacion
from config.notificaciones.alerts import get_urgent_workspace_alerts, mark_dispatch_alerts_seen
from config.notificaciones.context_processors import workspace_urgent_alerts
from config.notificaciones.models import Notificacion, WorkspaceDispatchAlertReadState
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

	def test_backoffice_user_receives_pending_dispatch_alerts_without_in_progress(self):
		Cotizacion.objects.create(cliente=self.cliente, estado='ENVIADA')
		Cotizacion.objects.create(cliente=self.cliente, estado='LISTA_PARA_CONFIRMACION')
		Pedido.objects.create(cliente=self.cliente, origen='CLIENTE', estado='RECIBIDO')
		Pedido.objects.create(cliente=self.cliente, origen='VENDEDOR', estado='EN_GESTION')

		alerts = get_urgent_workspace_alerts(self.backoffice)

		self.assertIsNotNone(alerts)
		self.assertEqual(alerts['total_count'], 4)
		self.assertEqual(len(alerts['summary_items']), 3)
		self.assertEqual(len(alerts['recent_items']), 3)
		self.assertEqual(alerts['summary_items'][0]['label'], 'Pending review')
		self.assertEqual(alerts['summary_items'][0]['count'], 1)
		self.assertEqual(alerts['summary_items'][1]['label'], 'Waiting for customer')
		self.assertEqual(alerts['summary_items'][1]['count'], 1)
		self.assertEqual(alerts['summary_items'][2]['label'], 'Ready to dispatch')
		self.assertEqual(alerts['summary_items'][2]['count'], 1)
		self.assertEqual(alerts['orders_url'], reverse('backoffice_pedidos'))
		self.assertNotIn('In progress', [item['label'] for item in alerts['summary_items']])

	def test_mark_dispatch_alerts_seen_clears_unread_count(self):
		Pedido.objects.create(cliente=self.cliente, origen='CLIENTE', estado='RECIBIDO')
		Notificacion.objects.create(
			tipo='PEDIDO',
			titulo='Nuevo pedido',
			mensaje='Pedido pendiente',
			usuario=self.backoffice,
			leida=False,
		)

		self.assertEqual(get_urgent_workspace_alerts(self.backoffice)['total_count'], 1)

		mark_dispatch_alerts_seen(self.backoffice)

		self.assertEqual(get_urgent_workspace_alerts(self.backoffice)['total_count'], 0)
		self.assertTrue(WorkspaceDispatchAlertReadState.objects.filter(user=self.backoffice).exists())
		self.assertTrue(Notificacion.objects.filter(tipo='PEDIDO', leida=True).exists())

	def test_mark_seen_endpoint_marks_dispatch_alerts_as_read(self):
		Pedido.objects.create(cliente=self.cliente, origen='CLIENTE', estado='RECIBIDO')
		client = Client()
		client.force_login(self.backoffice)

		response = client.post(reverse('mark_dispatch_alerts_seen'))

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json()['unread_count'], 0)
		self.assertEqual(get_urgent_workspace_alerts(self.backoffice)['total_count'], 0)

	def test_context_processor_injects_alerts_for_internal_users(self):
		request = RequestFactory().get('/')
		request.user = self.backoffice

		context = workspace_urgent_alerts(request)

		self.assertIn('workspace_urgent_alerts', context)
		self.assertIsNotNone(context['workspace_urgent_alerts'])

	def test_backoffice_dashboard_includes_navbar_orders_button(self):
		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('backoffice_dashboard'))

		self.assertContains(response, 'navbar-urgent-alerts')
		self.assertContains(response, 'Orders')
		self.assertContains(response, 'Orders for dispatch')
		self.assertContains(response, 'Pending dispatch only')
		self.assertContains(response, 'navbar_urgent_alerts.js')

	def test_new_pending_order_after_mark_seen_counts_as_unread(self):
		mark_dispatch_alerts_seen(self.backoffice)
		Pedido.objects.create(cliente=self.cliente, origen='CLIENTE', estado='RECIBIDO')

		alerts = get_urgent_workspace_alerts(self.backoffice)

		self.assertEqual(alerts['total_count'], 1)
		self.assertTrue(alerts['recent_items'][0]['is_unread'])
