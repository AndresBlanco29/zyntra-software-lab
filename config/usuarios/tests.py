import re

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from config.clientes.models import Cliente
from config.facturacion.models import Invoice, NotaAjuste
from config.pedidos.models import Pedido
from config.usuarios.models import Usuario
from config.usuarios.permissions import get_redirect_url_for_user
from config.usuarios.schema_repair import _backfill_field_values, _build_relaxed_field


class InternalPermissionTests(TestCase):
	def test_backoffice_role_gets_default_permissions(self):
		user = Usuario.objects.create_user(
			username='backoffice-default',
			password='secret123',
			role='backoffice',
		)

		self.assertTrue(user.has_internal_permission('backoffice.dashboard.view'))
		self.assertTrue(user.has_internal_permission('backoffice.quotes.manage'))
		self.assertTrue(user.has_internal_permission('backoffice.reports.view'))
		self.assertFalse(user.has_internal_permission('admin.products.view'))

	def test_permission_overrides_can_grant_and_revoke_access(self):
		user = Usuario.objects.create_user(
			username='backoffice-custom',
			password='secret123',
			role='backoffice',
			permission_overrides={
				'backoffice.quotes.manage': False,
				'admin.products.view': True,
			},
		)

		self.assertTrue(user.has_internal_permission('backoffice.quotes.view'))
		self.assertFalse(user.has_internal_permission('backoffice.quotes.manage'))
		self.assertTrue(user.has_internal_permission('admin.products.view'))

	def test_delegated_products_permission_allows_admin_products_page(self):
		user = Usuario.objects.create_user(
			username='backoffice-products',
			password='secret123',
			role='backoffice',
			permission_overrides={
				'admin.products.view': True,
			},
		)
		self.client.force_login(user)

		response = self.client.get(reverse('lista_productos'))

		self.assertEqual(response.status_code, 200)

	def test_user_without_products_permission_is_redirected(self):
		user = Usuario.objects.create_user(
			username='vendor-basic',
			password='secret123',
			role='vendedor',
		)
		self.client.force_login(user)

		response = self.client.get(reverse('lista_productos'))

		self.assertEqual(response.status_code, 302)
		self.assertEqual(response.url, reverse('vendedores_clientes'))

	def test_selector_role_gets_default_permissions_and_redirect(self):
		user = Usuario.objects.create_user(
			username='selector-default',
			password='secret123',
			role='seleccionador',
		)

		self.assertTrue(user.has_internal_permission('selector.picking.view'))
		self.assertTrue(user.has_internal_permission('selector.picking.manage'))
		self.assertEqual(get_redirect_url_for_user(user), reverse('selector_picking_list'))

	def test_driver_role_gets_default_permissions_and_redirect(self):
		user = Usuario.objects.create_user(
			username='driver-default',
			password='secret123',
			role='driver',
		)

		self.assertTrue(user.has_internal_permission('driver.delivery.view'))
		self.assertTrue(user.has_internal_permission('driver.delivery.manage'))
		self.assertEqual(get_redirect_url_for_user(user), reverse('driver_delivery_list'))

	def test_backoffice_without_vendor_permissions_does_not_render_sales_menu(self):
		user = Usuario.objects.create_user(
			username='backoffice-no-vendor-menu',
			password='secret123',
			role='backoffice',
		)
		self.client.force_login(user)

		response = self.client.get(reverse('backoffice_dashboard'))

		self.assertEqual(response.status_code, 200)
		self.assertNotContains(response, f'href="{reverse("crear_cliente")}"', html=False)
		self.assertNotContains(response, f'href="{reverse("vendedores_clientes")}"', html=False)
		self.assertNotContains(response, f'href="{reverse("tomar_pedido")}"', html=False)
		self.assertContains(response, f'href="{reverse("reportes_dashboard")}"', html=False)

	def test_backoffice_with_vendor_permissions_renders_sales_menu(self):
		user = Usuario.objects.create_user(
			username='backoffice-with-vendor-menu',
			password='secret123',
			role='backoffice',
			permission_overrides={
				'vendor.customers.manage': True,
				'vendor.orders.manage': True,
			},
		)
		self.client.force_login(user)

		response = self.client.get(reverse('backoffice_dashboard'))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, f'href="{reverse("crear_cliente")}"', html=False)
		self.assertContains(response, f'href="{reverse("vendedores_clientes")}"', html=False)
		self.assertContains(response, f'href="{reverse("tomar_pedido")}"', html=False)

	def test_vendor_with_backoffice_permissions_renders_operations_menu(self):
		user = Usuario.objects.create_user(
			username='vendor-with-backoffice-menu',
			password='secret123',
			role='vendedor',
			permission_overrides={
				'backoffice.dashboard.view': True,
				'backoffice.orders.manage': True,
			},
		)
		self.client.force_login(user)

		response = self.client.get(reverse('backoffice_dashboard'))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, f'href="{reverse("backoffice_dashboard")}"', html=False)
		self.assertContains(response, f'href="{reverse("backoffice_pedidos")}"', html=False)
		self.assertContains(response, f'href="{reverse("backoffice_inventory_list")}"', html=False)

	def test_driver_with_vendor_permissions_renders_only_granted_sales_links(self):
		user = Usuario.objects.create_user(
			username='driver-with-vendor-menu',
			password='secret123',
			role='driver',
			permission_overrides={
				'vendor.customers.view': True,
				'vendor.orders.view': True,
			},
		)
		self.client.force_login(user)

		response = self.client.get(reverse('driver_delivery_list'))

		self.assertEqual(response.status_code, 200)
		self.assertNotContains(response, f'href="{reverse("crear_cliente")}"', html=False)
		self.assertContains(response, f'href="{reverse("vendedores_clientes")}"', html=False)
		self.assertContains(response, f'href="{reverse("tomar_pedido")}"', html=False)

	def test_backoffice_with_admin_products_permission_renders_admin_products_link(self):
		user = Usuario.objects.create_user(
			username='backoffice-with-admin-products-menu',
			password='secret123',
			role='backoffice',
			permission_overrides={
				'admin.products.view': True,
			},
		)
		self.client.force_login(user)

		response = self.client.get(reverse('backoffice_dashboard'))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, f'href="{reverse("lista_productos")}"', html=False)
		self.assertNotContains(response, f'href="{reverse("panel_admin")}"', html=False)


class InternalUserAdminViewTests(TestCase):
	def setUp(self):
		self.admin_user = Usuario.objects.create_user(
			username='admin-internal-users',
			password='secret123',
			role='admin',
		)
		self.client.force_login(self.admin_user)

	def test_generic_create_form_starts_without_preselected_role(self):
		response = self.client.get(reverse('crear_usuario_interno'))

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.context['selected_role'], '')
		self.assertContains(response, 'Select a role')
		self.assertContains(response, 'id="internalUserPassword"', html=False)
		self.assertContains(response, 'id="toggleInternalUserPassword"', html=False)
		self.assertContains(response, 'Show password')
		self.assertFalse(
			any(
				permission['checked']
				for section in response.context['permission_sections']
				for permission in section['permissions']
			)
		)

	def test_post_without_role_does_not_create_vendor_user(self):
		response = self.client.post(
			reverse('crear_usuario_interno'),
			{
				'nombre': 'Driver',
				'apellido': 'Without Role',
				'username': 'driver-without-role',
				'email': 'driver-without-role@example.com',
				'password': 'secret123',
				'telefono': '5551234',
			},
			follow=True,
		)

		self.assertEqual(response.status_code, 200)
		self.assertFalse(Usuario.objects.filter(username='driver-without-role').exists())
		messages = list(response.context['messages'])
		self.assertTrue(any('Select a role for the internal user.' in str(message) for message in messages))


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class PasswordResetFlowTests(TestCase):
	def setUp(self):
		self.user = Usuario.objects.create_user(
			username='cliente-reset',
			email='cliente-reset@example.com',
			password='ClaveAnterior123!',
			role='cliente',
			is_active=True,
		)

	def test_password_reset_request_sends_email(self):
		response = self.client.post(
			reverse('password_reset'),
			{'email': self.user.email},
		)

		self.assertRedirects(response, reverse('password_reset_done'))
		self.assertEqual(len(mail.outbox), 1)
		email = mail.outbox[0]
		self.assertIn(self.user.email, email.to)
		self.assertIn('show_login=1&auth_view=password_reset_confirm', email.body)

	def test_password_can_be_reset_from_email_link(self):
		self.client.post(reverse('password_reset'), {'email': self.user.email})
		self.assertEqual(len(mail.outbox), 1)

		body = mail.outbox[0].body
		match = re.search(r'uidb64=(?P<uidb64>[^&\s]+)&token=(?P<token>[^\s]+)', body)
		self.assertIsNotNone(match)
		uidb64 = match.group('uidb64')
		token = match.group('token')

		response = self.client.get(reverse('password_reset_confirm_modal', args=[uidb64, token]))
		self.assertEqual(response.status_code, 200)

		post_response = self.client.post(
			reverse('password_reset_confirm_modal', args=[uidb64, token]),
			{
				'new_password1': 'NuevaClaveSegura123!',
				'new_password2': 'NuevaClaveSegura123!',
			},
			HTTP_X_REQUESTED_WITH='XMLHttpRequest',
		)

		self.assertEqual(post_response.status_code, 200)
		self.assertIn('Your password has been updated', post_response.json()['html'])
		self.user.refresh_from_db()
		self.assertTrue(self.user.check_password('NuevaClaveSegura123!'))
		self.assertTrue(self.client.login(username=self.user.username, password='NuevaClaveSegura123!'))

	def test_unknown_email_does_not_send_email(self):
		response = self.client.post(
			reverse('password_reset'),
			{'email': 'desconocido@example.com'},
		)

		self.assertRedirects(response, reverse('password_reset_done'))
		self.assertEqual(len(mail.outbox), 0)

	def test_password_reset_modal_get_returns_partial_form(self):
		response = self.client.get(
			reverse('password_reset_modal'),
			HTTP_X_REQUESTED_WITH='XMLHttpRequest',
		)

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'passwordResetModalForm')

	def test_password_reset_modal_post_sends_email_and_returns_success_html(self):
		response = self.client.post(
			reverse('password_reset_modal'),
			{'email': self.user.email},
			HTTP_X_REQUESTED_WITH='XMLHttpRequest',
		)

		self.assertEqual(response.status_code, 200)
		self.assertJSONEqual(
			response.content,
			{
				'success': True,
				'html': response.json()['html'],
			},
		)
		self.assertIn('Check your email', response.json()['html'])
		self.assertEqual(len(mail.outbox), 1)


class RuntimeSchemaRepairTests(TransactionTestCase):
	def setUp(self):
		self.user = Usuario.objects.create_user(
			username='runtime-reset-user',
			email='runtime-reset@example.com',
			password='ClaveAnterior123!',
			role='cliente',
			is_active=True,
		)

	def test_backfill_field_values_populates_note_customer_from_invoice(self):
		creator = Usuario.objects.create_user(
			username='schema-repair-backfill',
			password='secret123',
			role='backoffice',
		)
		customer_user = Usuario.objects.create_user(
			username='schema-repair-customer',
			password='secret123',
			role='cliente',
		)
		cliente = Cliente.objects.create(
			usuario=customer_user,
			nombre_empresa='Schema Repair Customer',
			telefono='5551234567',
			direccion='123 Repair St',
			ciudad='Dallas',
			estado='TX',
			codigo_postal='75001',
			pais='USA',
			sales_tax_number='TX-12345',
			certificado_tax=SimpleUploadedFile('tax.txt', b'test tax certificate'),
		)
		pedido = Pedido.objects.create(
			cliente=cliente,
			origen='CLIENTE',
			estado='INVOICE_GENERADA',
		)
		invoice = Invoice.objects.create(
			pedido=pedido,
			cliente=cliente,
			metodo_entrega='LTG',
			creada_por=creator,
		)
		cliente_field = NotaAjuste._meta.get_field('cliente')
		relaxed_cliente_field = _build_relaxed_field(cliente_field)
		with connection.schema_editor() as schema_editor:
			schema_editor.alter_field(NotaAjuste, cliente_field, relaxed_cliente_field, strict=False)
		note = NotaAjuste.objects.create(
			cliente=cliente,
			invoice=invoice,
			tipo_documento='CREDITO',
			tipo_credito='CREDIT_DUMP',
			motivo='OTHER',
		)

		try:
			NotaAjuste.objects.filter(pk=note.pk).update(cliente_id=None)
			note.refresh_from_db()
			self.assertIsNone(note.cliente_id)

			_backfill_field_values(connection, NotaAjuste, cliente_field)

			note.refresh_from_db()
			self.assertEqual(note.cliente_id, cliente.id)
		finally:
			with connection.schema_editor() as schema_editor:
				schema_editor.alter_field(NotaAjuste, relaxed_cliente_field, cliente_field, strict=False)

	def test_password_reset_confirm_modal_returns_form_for_valid_token(self):
		self.client.post(reverse('password_reset'), {'email': self.user.email})
		body = mail.outbox[0].body
		match = re.search(r'uidb64=(?P<uidb64>[^&\s]+)&token=(?P<token>[^\s]+)', body)
		self.assertIsNotNone(match)

		response = self.client.get(
			reverse('password_reset_confirm_modal', args=[match.group('uidb64'), match.group('token')]),
			HTTP_X_REQUESTED_WITH='XMLHttpRequest',
		)

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'passwordResetConfirmModalForm')


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class CustomerRequestReviewWorkflowTests(TestCase):
	def setUp(self):
		self.admin_user = Usuario.objects.create_user(
			username='admin-customer-review',
			password='secret123',
			role='admin',
			is_active=True,
		)
		self.customer_user = Usuario.objects.create_user(
			username='pending-customer',
			email='pending-customer@example.com',
			password='secret123',
			first_name='Pending',
			last_name='Customer',
			role='cliente',
			is_active=False,
		)
		self.cliente = Cliente.objects.create(
			usuario=self.customer_user,
			nombre_empresa='Pending Foods LLC',
			telefono='1234567890',
			direccion='123 Main St',
			ciudad='Houston',
			estado='Texas',
			codigo_postal='77001',
			pais='USA',
			sales_tax_number='99887766',
			certificado_tax=SimpleUploadedFile('certificado.pdf', b'pdf-bytes', content_type='application/pdf'),
			declaracion_fiscal_aceptada=True,
			estado_revision=Cliente.REVIEW_STATUS_PENDING,
		)
		self.client.force_login(self.admin_user)

	def test_reject_requires_note_and_keeps_request_pending(self):
		response = self.client.post(
			reverse('rechazar_cliente', args=[self.cliente.id]),
			{'view': 'pending', 'nota_rechazo': '   '},
		)

		self.assertEqual(response.status_code, 302)
		self.cliente.refresh_from_db()
		self.customer_user.refresh_from_db()
		self.assertEqual(self.cliente.estado_revision, Cliente.REVIEW_STATUS_PENDING)
		self.assertFalse(self.cliente.nota_rechazo)
		self.assertFalse(self.customer_user.is_active)
		self.assertEqual(len(mail.outbox), 0)

	def test_reject_persists_customer_and_sends_email_with_reason_and_attachment(self):
		reference_image = SimpleUploadedFile('example.png', b'image-bytes', content_type='image/png')

		response = self.client.post(
			reverse('rechazar_cliente', args=[self.cliente.id]),
			{
				'view': 'pending',
				'nota_rechazo': 'Your certificate is missing the state Department of Revenue heading.',
				'adjunto_rechazo': reference_image,
			},
		)

		self.assertEqual(response.status_code, 302)
		self.assertTrue(Cliente.objects.filter(id=self.cliente.id).exists())
		self.assertTrue(Usuario.objects.filter(id=self.customer_user.id).exists())

		self.cliente.refresh_from_db()
		self.customer_user.refresh_from_db()
		self.assertEqual(self.cliente.estado_revision, Cliente.REVIEW_STATUS_REJECTED)
		self.assertEqual(self.cliente.nota_rechazo, 'Your certificate is missing the state Department of Revenue heading.')
		self.assertTrue(bool(self.cliente.adjunto_rechazo))
		self.assertFalse(self.cliente.aprobado)
		self.assertFalse(self.customer_user.is_active)
		self.assertEqual(len(mail.outbox), 1)

		email = mail.outbox[0]
		self.assertIn('needs corrections', email.body)
		self.assertIn('Reason: Your certificate is missing the state Department of Revenue heading.', email.body)
		self.assertIn(str(self.cliente.correction_token), email.body)
		self.assertEqual(len(email.attachments), 1)

	def test_rejected_customer_appears_in_rejected_requests_filter(self):
		self.cliente.estado_revision = Cliente.REVIEW_STATUS_REJECTED
		self.cliente.nota_rechazo = 'Missing certificate page.'
		self.cliente.save(update_fields=['estado_revision', 'nota_rechazo'])

		response = self.client.get(reverse('clientes_pendientes') + '?view=rejected')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Rejected customers')
		self.assertContains(response, self.cliente.nombre_empresa)
		self.assertContains(response, 'Missing certificate page.')

	def test_customer_can_correct_rejected_request_and_resubmit(self):
		self.cliente.estado_revision = Cliente.REVIEW_STATUS_REJECTED
		self.cliente.nota_rechazo = 'Update the sales tax certificate and city.'
		self.cliente.save(update_fields=['estado_revision', 'nota_rechazo'])

		response = self.client.post(
			reverse('corregir_solicitud_cliente', args=[self.cliente.correction_token]),
			{
				'empresa': 'Pending Foods LLC Updated',
				'sales_tax': '11223344',
				'telefono_comercial': '0987654321',
				'direccion': '456 Updated Ave',
				'estado': 'Georgia',
				'ciudad': 'Atlanta',
				'codigo_postal': '30301',
				'pais': 'USA',
			},
		)

		self.assertEqual(response.status_code, 200)
		self.cliente.refresh_from_db()
		self.assertEqual(self.cliente.estado_revision, Cliente.REVIEW_STATUS_PENDING)
		self.assertEqual(self.cliente.nombre_empresa, 'Pending Foods LLC Updated')
		self.assertEqual(self.cliente.sales_tax_number, '11223344')
		self.assertEqual(self.cliente.ciudad, 'Atlanta')
		self.assertContains(response, 'Request resubmitted')

	def test_admin_can_approve_previously_rejected_customer(self):
		self.cliente.estado_revision = Cliente.REVIEW_STATUS_REJECTED
		self.cliente.nota_rechazo = 'Initial rejection note.'
		self.cliente.save(update_fields=['estado_revision', 'nota_rechazo'])

		response = self.client.post(
			reverse('aprobar_cliente', args=[self.cliente.id]),
			{'view': 'rejected', 'nivel_precio': '4'},
		)

		self.assertEqual(response.status_code, 302)
		self.cliente.refresh_from_db()
		self.customer_user.refresh_from_db()
		self.assertEqual(self.cliente.estado_revision, Cliente.REVIEW_STATUS_APPROVED)
		self.assertTrue(self.cliente.aprobado)
		self.assertEqual(self.cliente.nivel_precio, 4)
		self.assertTrue(self.customer_user.is_active)

	def test_admin_can_approve_customer_without_assigning_prices_yet(self):
		response = self.client.post(
			reverse('aprobar_cliente', args=[self.cliente.id]),
			{'view': 'pending', 'nivel_precio': '0'},
		)

		self.assertEqual(response.status_code, 302)
		self.cliente.refresh_from_db()
		self.customer_user.refresh_from_db()
		self.assertEqual(self.cliente.estado_revision, Cliente.REVIEW_STATUS_APPROVED)
		self.assertTrue(self.cliente.aprobado)
		self.assertEqual(self.cliente.nivel_precio, Cliente.PRICE_TIER_UNASSIGNED)
		self.assertTrue(self.customer_user.is_active)

	def test_admin_can_update_customer_pricing_without_sending_approval_email(self):
		self.cliente.estado_revision = Cliente.REVIEW_STATUS_APPROVED
		self.cliente.aprobado = True
		self.cliente.nivel_precio = 2
		self.cliente.save(update_fields=['estado_revision', 'aprobado', 'nivel_precio'])
		mail.outbox = []

		response = self.client.post(
			reverse('actualizar_precio_cliente', args=[self.cliente.id]),
			{'view': 'approved', 'nivel_precio': '4'},
		)

		self.assertEqual(response.status_code, 302)
		self.assertRedirects(response, reverse('clientes_pendientes') + '?view=approved')
		self.cliente.refresh_from_db()
		self.assertEqual(self.cliente.nivel_precio, 4)
		self.assertEqual(len(mail.outbox), 0)

	def test_approved_customers_list_shows_quick_pricing_update_form(self):
		self.cliente.estado_revision = Cliente.REVIEW_STATUS_APPROVED
		self.cliente.aprobado = True
		self.cliente.nivel_precio = 2
		self.cliente.save(update_fields=['estado_revision', 'aprobado', 'nivel_precio'])

		response = self.client.get(reverse('clientes_pendientes') + '?view=approved')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, reverse('actualizar_precio_cliente', args=[self.cliente.id]))
		self.assertContains(response, 'Update customer pricing')
		self.assertContains(response, 'name="nivel_precio"', html=False)

	def test_customer_requests_paginates_approved_list(self):
		self.cliente.estado_revision = Cliente.REVIEW_STATUS_APPROVED
		self.cliente.aprobado = True
		self.cliente.save(update_fields=['estado_revision', 'aprobado'])

		for index in range(55):
			user = Usuario.objects.create_user(
				username=f'approved-customer-{index:03d}',
				password='secret123',
				role='cliente',
				is_active=True,
			)
			Cliente.objects.create(
				usuario=user,
				nombre_empresa=f'Approved Company {index:03d}',
				telefono='1234567890',
				direccion='123 Main St',
				ciudad='Houston',
				estado='Texas',
				codigo_postal='77001',
				pais='USA',
				sales_tax_number=f'9900{index:04d}',
				declaracion_fiscal_aceptada=True,
				estado_revision=Cliente.REVIEW_STATUS_APPROVED,
				aprobado=True,
			)

		response = self.client.get(reverse('clientes_pendientes') + '?view=approved')

		self.assertEqual(response.status_code, 200)
		self.assertEqual(len(response.context['clientes']), 50)
		self.assertEqual(response.context['page_obj'].paginator.count, 56)
		self.assertContains(response, 'Page 1 of 2')

	def test_customer_requests_search_by_company_name(self):
		self.cliente.nombre_empresa = 'Unique Search Foods LLC'
		self.cliente.save(update_fields=['nombre_empresa'])

		other_user = Usuario.objects.create_user(
			username='other-customer',
			password='secret123',
			role='cliente',
			is_active=False,
		)
		Cliente.objects.create(
			usuario=other_user,
			nombre_empresa='Other Market LLC',
			telefono='1234567890',
			direccion='123 Main St',
			ciudad='Houston',
			estado='Texas',
			codigo_postal='77001',
			pais='USA',
			sales_tax_number='88776655',
			declaracion_fiscal_aceptada=True,
			estado_revision=Cliente.REVIEW_STATUS_PENDING,
		)

		response = self.client.get(reverse('clientes_pendientes') + '?view=pending&q=Unique+Search')

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.context['page_obj'].paginator.count, 1)
		self.assertContains(response, 'Unique Search Foods LLC')
		self.assertNotContains(response, 'Other Market LLC')
