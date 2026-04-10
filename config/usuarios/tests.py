from django.test import TestCase
from django.urls import reverse

from config.usuarios.models import Usuario


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
