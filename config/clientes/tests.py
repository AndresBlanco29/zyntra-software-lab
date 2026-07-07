from decimal import Decimal
from io import StringIO
from datetime import date, datetime
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from config.clientes.assignment import (
    assign_all_approved_clientes_to_vendedor,
    filter_clientes_for_vendedor,
    sync_vendedor_cliente_assignments,
)
from config.clientes.balance_summary import build_customer_balance_summary
from config.clientes.models import Cliente
from config.clientes.phone import normalize_stored_phone_number
from config.cotizaciones.models import Cotizacion
from config.facturacion.models import Invoice
from config.pedidos.models import Pedido
from config.usuarios.models import Usuario


def _create_customer(*, company_name, approved=False):
    user = Usuario.objects.create_user(
        username=f'cliente-{company_name.lower().replace(" ", "-")}',
        password='secret123',
        role='cliente',
    )
    return Cliente.objects.create(
        usuario=user,
        nombre_empresa=company_name,
        telefono='5551234567',
        direccion='123 Test St',
        ciudad='Atlanta',
        estado='Georgia',
        sales_tax_number='TX-1',
        certificado_tax='certificados/test.pdf',
        aprobado=approved,
    )


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


class CustomerVendorAssignmentTests(TestCase):
    def setUp(self):
        self.admin = Usuario.objects.create_user(
            username='admin-assign',
            password='secret123',
            role='admin',
        )
        self.backoffice = Usuario.objects.create_user(
            username='backoffice-assign',
            password='secret123',
            role='backoffice',
        )
        self.vendedor = Usuario.objects.create_user(
            username='vendedor-assign',
            password='secret123',
            role='vendedor',
            first_name='Ana',
            last_name='Vendor',
        )
        self.other_vendedor = Usuario.objects.create_user(
            username='vendedor-other',
            password='secret123',
            role='vendedor',
        )
        self.cliente_a = _create_customer(company_name='Cliente A', approved=True)
        self.cliente_b = _create_customer(company_name='Cliente B', approved=True)
        self.cliente_pending = _create_customer(company_name='Cliente Pending', approved=False)

    def test_sync_assigns_and_unassigns_selected_customers(self):
        self.cliente_a.vendedor_asignado = self.other_vendedor
        self.cliente_a.save(update_fields=['vendedor_asignado'])

        result = sync_vendedor_cliente_assignments(
            vendedor=self.vendedor,
            selected_cliente_ids=[self.cliente_a.id, self.cliente_b.id],
            assigned_by=self.admin,
        )

        self.cliente_a.refresh_from_db()
        self.cliente_b.refresh_from_db()
        self.assertEqual(result['assigned_count'], 2)
        self.assertEqual(self.cliente_a.vendedor_asignado, self.vendedor)
        self.assertEqual(self.cliente_b.vendedor_asignado, self.vendedor)

        result = sync_vendedor_cliente_assignments(
            vendedor=self.vendedor,
            selected_cliente_ids=[self.cliente_b.id],
            assigned_by=self.admin,
        )
        self.cliente_a.refresh_from_db()
        self.assertEqual(result['unassigned_count'], 1)
        self.assertIsNone(self.cliente_a.vendedor_asignado)

    def test_assign_all_only_updates_approved_customers(self):
        count = assign_all_approved_clientes_to_vendedor(
            vendedor=self.vendedor,
            assigned_by=self.admin,
        )
        self.assertEqual(count, 2)
        self.cliente_pending.refresh_from_db()
        self.assertIsNone(self.cliente_pending.vendedor_asignado)

    def test_filter_clientes_for_vendedor_limits_vendor_visibility(self):
        self.cliente_a.vendedor_asignado = self.vendedor
        self.cliente_a.save(update_fields=['vendedor_asignado'])

        visible = list(
            filter_clientes_for_vendedor(Cliente.objects.all(), self.vendedor).values_list('id', flat=True)
        )
        self.assertEqual(visible, [self.cliente_a.id])

        admin_visible = filter_clientes_for_vendedor(Cliente.objects.all(), self.admin).count()
        self.assertEqual(admin_visible, 3)

    def test_backoffice_can_open_assignment_page(self):
        self.client.force_login(self.backoffice)
        response = self.client.get(reverse('lista_asignacion_clientes_vendedores'))
        self.assertEqual(response.status_code, 200)

    def test_vendedor_cannot_open_assignment_page(self):
        self.client.force_login(self.vendedor)
        response = self.client.get(reverse('lista_asignacion_clientes_vendedores'))
        self.assertEqual(response.status_code, 302)


class CustomerPhoneNormalizationTests(TestCase):
    def test_normalize_stored_phone_number_strips_formatting(self):
        self.assertEqual(normalize_stored_phone_number('(706) 263-7500'), '7062637500')
        self.assertEqual(normalize_stored_phone_number('+1 (706) 263-7500'), '7062637500')
        self.assertEqual(normalize_stored_phone_number('7062637500'), '7062637500')

    def test_repair_customer_phones_command_normalizes_existing_records(self):
        user = Usuario.objects.create_user(
            username='cliente-phone-format',
            password='secret123',
            role='cliente',
        )
        cliente = Cliente.objects.create(
            usuario=user,
            nombre_empresa='Cliente Phone Format',
            telefono='(706) 263-7500',
            direccion='123 Main',
            ciudad='Rome',
            estado='GA',
            codigo_postal='30165',
            pais='USA',
            sales_tax_number='GA-1',
            certificado_tax='certificados/test.pdf',
        )

        call_command('repair_customer_phones')

        cliente.refresh_from_db()
        self.assertEqual(cliente.telefono, '7062637500')


class CustomerBalanceSummaryTests(TestCase):
    def setUp(self):
        self.cliente = _create_customer(company_name='Balance Summary', approved=True)
        self.cliente.terminos_pago = Cliente.PAYMENT_TERMS_NET7
        self.cliente.credit_limit = Decimal('3500.00')
        self.cliente.save(update_fields=['terminos_pago', 'credit_limit'])

    def _create_open_invoice(self, *, amount, created_at):
        pedido = Pedido.objects.create(
            cliente=self.cliente,
            origen='CLIENTE',
            estado='INVOICE_GENERADA',
            total=amount,
        )
        invoice = Invoice.objects.create(
            pedido=pedido,
            cliente=self.cliente,
            metodo_entrega='CUSTOMER_PICK_UP',
            subtotal=amount,
            total_neto=amount,
            saldo_cliente=amount,
        )
        Invoice.objects.filter(pk=invoice.pk).update(creada_en=created_at)
        invoice.refresh_from_db()
        return invoice

    @patch('config.clientes.balance_summary.timezone')
    def test_splits_overdue_and_current_balances_with_aging(self, mock_timezone):
        mock_timezone.localdate.return_value = date(2026, 7, 6)
        mock_timezone.make_aware.side_effect = timezone.make_aware

        overdue_invoice = self._create_open_invoice(
            amount=Decimal('100.00'),
            created_at=timezone.make_aware(datetime(2026, 6, 20, 12, 0, 0)),
        )
        current_invoice = self._create_open_invoice(
            amount=Decimal('50.00'),
            created_at=timezone.make_aware(datetime(2026, 7, 6, 10, 0, 0)),
        )

        summary = build_customer_balance_summary(self.cliente, today=date(2026, 7, 6))

        self.assertEqual(summary.overdue_balance, Decimal('100.00'))
        self.assertEqual(summary.current_balance, Decimal('50.00'))
        self.assertEqual(summary.total_open_balance, Decimal('150.00'))
        self.assertEqual(len(summary.lines), 2)
        self.assertTrue(summary.lines[0].is_overdue)
        self.assertGreater(summary.lines[0].aging_days, 0)
        self.assertFalse(summary.lines[1].is_overdue)
        self.assertEqual(summary.lines[1].aging_display, '0')
        self.assertFalse(summary.exceeds_credit_limit)

    @patch('config.clientes.balance_summary.timezone')
    def test_flags_credit_limit_exceeded(self, mock_timezone):
        mock_timezone.localdate.return_value = date(2026, 7, 6)
        mock_timezone.make_aware.side_effect = timezone.make_aware

        self._create_open_invoice(
            amount=Decimal('4000.00'),
            created_at=timezone.make_aware(datetime(2026, 6, 1, 12, 0, 0)),
        )

        summary = build_customer_balance_summary(self.cliente, today=date(2026, 7, 6))

        self.assertTrue(summary.exceeds_credit_limit)
        self.assertEqual(summary.credit_limit_excess, Decimal('500.00'))
