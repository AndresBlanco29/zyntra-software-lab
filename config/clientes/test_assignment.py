from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from config.clientes.assignment import (
	assign_all_approved_clientes_to_vendedor,
	filter_clientes_for_vendedor,
	sync_vendedor_cliente_assignments,
)
from config.clientes.models import Cliente, ClienteVendedorAsignacion
from config.usuarios.models import Usuario


class MultiVendorAssignmentTests(TestCase):
	def setUp(self):
		self.admin = Usuario.objects.create_user(username='assign-admin', password='secret123', role='admin')
		self.vendor_a = Usuario.objects.create_user(
			username='vendor-a',
			password='secret123',
			role='vendedor',
			first_name='Jhon',
			last_name='Moncada',
		)
		self.vendor_b = Usuario.objects.create_user(
			username='vendor-b',
			password='secret123',
			role='vendedor',
			first_name='Vendedor',
			last_name='Uno',
		)
		self.customers = []
		for index in range(3):
			user = Usuario.objects.create_user(
				username=f'cust-{index}',
				password='secret123',
				role='cliente',
				email=f'cust{index}@example.com',
			)
			cliente = Cliente.objects.create(
				usuario=user,
				nombre_empresa=f'Customer {index}',
				telefono=f'555000000{index}',
				direccion='1 Main',
				ciudad='Atlanta',
				estado='GA',
				codigo_postal='30301',
				pais='USA',
				sales_tax_number=f'TX-{index}',
				certificado_tax=SimpleUploadedFile(f'c{index}.txt', b'ok'),
				aprobado=True,
			)
			self.customers.append(cliente)

	def test_assign_all_to_second_vendor_keeps_first_vendor_list(self):
		assign_all_approved_clientes_to_vendedor(vendedor=self.vendor_a, assigned_by=self.admin)
		assign_all_approved_clientes_to_vendedor(vendedor=self.vendor_b, assigned_by=self.admin)

		self.assertEqual(ClienteVendedorAsignacion.objects.filter(vendedor=self.vendor_a).count(), 3)
		self.assertEqual(ClienteVendedorAsignacion.objects.filter(vendedor=self.vendor_b).count(), 3)
		self.assertEqual(
			filter_clientes_for_vendedor(Cliente.objects.all(), self.vendor_a).count(),
			3,
		)
		self.assertEqual(
			filter_clientes_for_vendedor(Cliente.objects.all(), self.vendor_b).count(),
			3,
		)

	def test_sync_unassign_only_affects_that_vendor(self):
		sync_vendedor_cliente_assignments(
			vendedor=self.vendor_a,
			selected_cliente_ids=[self.customers[0].id, self.customers[1].id],
			assigned_by=self.admin,
		)
		sync_vendedor_cliente_assignments(
			vendedor=self.vendor_b,
			selected_cliente_ids=[self.customers[0].id, self.customers[2].id],
			assigned_by=self.admin,
		)

		sync_vendedor_cliente_assignments(
			vendedor=self.vendor_b,
			selected_cliente_ids=[self.customers[2].id],
			assigned_by=self.admin,
		)

		self.assertTrue(
			ClienteVendedorAsignacion.objects.filter(
				vendedor=self.vendor_a,
				cliente=self.customers[0],
			).exists()
		)
		self.assertFalse(
			ClienteVendedorAsignacion.objects.filter(
				vendedor=self.vendor_b,
				cliente=self.customers[0],
			).exists()
		)

	def test_assign_page_assign_all_keeps_other_vendor_counts(self):
		self.client.force_login(self.admin)
		response_a = self.client.post(
			reverse('asignar_clientes_vendedor', args=[self.vendor_a.id]),
			{'assign_all': '1'},
		)
		self.assertEqual(response_a.status_code, 302)
		response_b = self.client.post(
			reverse('asignar_clientes_vendedor', args=[self.vendor_b.id]),
			{'assign_all': '1'},
		)
		self.assertEqual(response_b.status_code, 302)

		list_response = self.client.get(reverse('lista_asignacion_clientes_vendedores'))
		self.assertEqual(list_response.status_code, 200)
		vendors = {vendor.username: vendor.assigned_count for vendor in list_response.context['vendedores']}
		self.assertEqual(vendors['vendor-a'], 3)
		self.assertEqual(vendors['vendor-b'], 3)
