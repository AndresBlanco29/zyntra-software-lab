from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from config.clientes.models import Cliente
from config.cotizaciones.models import Cotizacion
from config.usuarios.models import Usuario


class ClearCustomersCommandTests(TestCase):
    def setUp(self):
        self.customer_user = Usuario.objects.create_user(
            username='cliente-clear-test',
            password='secret123',
            role='cliente',
        )
        self.customer = Cliente.objects.create(
            usuario=self.customer_user,
            nombre_empresa='Cliente Clear Test',
            telefono='5551234567',
            direccion='123 Test St',
            ciudad='Atlanta',
            estado='Georgia',
            sales_tax_number='TX-1',
            certificado_tax='certificados/test.pdf',
            quickbooks_id='QB-CLEAR-1',
        )
        Cotizacion.objects.create(
            cliente=self.customer,
            vendedor=None,
            estado='BORRADOR',
            total=Decimal('10.00'),
        )

    def test_requires_confirmation_token(self):
        with self.assertRaisesMessage(CommandError, 'CLEAR_CUSTOMERS'):
            call_command('clear_customers', '--confirm=WRONG')

    def test_dry_run_keeps_customers(self):
        out = StringIO()
        call_command('clear_customers', '--confirm=CLEAR_CUSTOMERS', '--dry-run', stdout=out)

        self.assertEqual(Cliente.objects.count(), 1)
        self.assertEqual(Usuario.objects.filter(role='cliente').count(), 1)
        self.assertIn('Dry run only', out.getvalue())

    def test_clears_customers_and_related_sales_records(self):
        out = StringIO()
        call_command('clear_customers', '--confirm=CLEAR_CUSTOMERS', stdout=out)

        self.assertEqual(Cliente.objects.count(), 0)
        self.assertEqual(Usuario.objects.filter(role='cliente').count(), 0)
        self.assertEqual(Cotizacion.objects.count(), 0)
        self.assertIn('Customers cleared', out.getvalue())
