from django.test import TestCase
from django.urls import reverse

from config.usuarios.models import Usuario
from config.usuarios.permissions import get_redirect_url_for_user


class InternalPermissionTests(TestCase):
	def test_backoffice_role_gets_default_permissions(self):
		user = Usuario.objects.create_user(
			username='backoffice-default',
			password='secret123',
			role='backoffice',
		)

		self.assertTrue(user.has_internal_permission('backoffice.dashboard.view'))
		self.assertTrue(user.has_internal_permission('backoffice.quotes.manage'))
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
