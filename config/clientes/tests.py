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
from config.clientes.balance_summary import (
    AR_STATUS_DUE_SOON,
    AR_STATUS_NO_DEBT,
    AR_STATUS_OUTSTANDING,
    AR_STATUS_OVERDUE,
    SEVERITY_DUE_SOON,
    SEVERITY_NONE,
    SEVERITY_OVERDUE_HARD,
    SEVERITY_OVERDUE_SOFT,
    build_customer_balance_summary,
    build_customers_receivables_summary,
    expand_clientes_for_list_display,
    filter_clientes_queryset_by_receivables,
)
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

    def _create_open_invoice(self, *, amount, created_at, cliente=None, qb_due_date=None):
        cliente = cliente or self.cliente
        pedido = Pedido.objects.create(
            cliente=cliente,
            origen='CLIENTE',
            estado='INVOICE_GENERADA',
            total=amount,
        )
        invoice = Invoice.objects.create(
            pedido=pedido,
            cliente=cliente,
            metodo_entrega='CUSTOMER_PICK_UP',
            subtotal=amount,
            total_neto=amount,
            saldo_cliente=amount,
        )
        update_fields = {'creada_en': created_at}
        if qb_due_date is not None:
            update_fields['qb_due_date'] = qb_due_date
        Invoice.objects.filter(pk=invoice.pk).update(**update_fields)
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
    def test_excludes_quickbooks_paid_invoices_from_balance(self, mock_timezone):
        mock_timezone.localdate.return_value = date(2026, 7, 6)
        mock_timezone.make_aware.side_effect = timezone.make_aware

        paid_invoice = self._create_open_invoice(
            amount=Decimal('500.00'),
            created_at=timezone.make_aware(datetime(2026, 6, 1, 12, 0, 0)),
        )
        Invoice.objects.filter(pk=paid_invoice.pk).update(
            estado='GENERADA', qb_payment_status='PAID'
        )
        self._create_open_invoice(
            amount=Decimal('80.00'),
            created_at=timezone.make_aware(datetime(2026, 6, 20, 12, 0, 0)),
        )

        summary = build_customer_balance_summary(self.cliente, today=date(2026, 7, 6))

        self.assertEqual(summary.total_open_balance, Decimal('80.00'))
        self.assertEqual(len(summary.overdue_lines), 1)

    @patch('config.clientes.balance_summary.timezone')
    def test_summary_exposes_overdue_and_current_line_groups(self, mock_timezone):
        mock_timezone.localdate.return_value = date(2026, 7, 6)
        mock_timezone.make_aware.side_effect = timezone.make_aware

        self._create_open_invoice(
            amount=Decimal('100.00'),
            created_at=timezone.make_aware(datetime(2026, 6, 20, 12, 0, 0)),
        )
        self._create_open_invoice(
            amount=Decimal('50.00'),
            created_at=timezone.make_aware(datetime(2026, 7, 6, 10, 0, 0)),
        )

        summary = build_customer_balance_summary(self.cliente, today=date(2026, 7, 6))

        self.assertEqual(summary.overdue_count, 1)
        self.assertEqual(summary.current_count, 1)
        self.assertGreater(summary.max_aging_days, 0)
        self.assertTrue(all(line.is_overdue for line in summary.overdue_lines))
        self.assertTrue(all(not line.is_overdue for line in summary.current_lines))

    @patch('config.clientes.balance_summary.timezone')
    def test_expand_clientes_creates_one_row_per_invoice(self, mock_timezone):
        mock_timezone.localdate.return_value = date(2026, 7, 6)
        mock_timezone.make_aware.side_effect = timezone.make_aware

        self._create_open_invoice(
            amount=Decimal('100.00'),
            created_at=timezone.make_aware(datetime(2026, 6, 20, 12, 0, 0)),
        )
        self._create_open_invoice(
            amount=Decimal('50.00'),
            created_at=timezone.make_aware(datetime(2026, 7, 6, 10, 0, 0)),
        )

        self.cliente.balance_summary = build_customer_balance_summary(self.cliente, today=date(2026, 7, 6))
        rows = expand_clientes_for_list_display([self.cliente])

        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[0].is_primary)
        self.assertFalse(rows[1].is_primary)
        self.assertIsNotNone(rows[0].line.invoice_number)
        self.assertIsNotNone(rows[1].line.invoice_number)
        self.assertGreater(rows[0].line.aging_days, 0)

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

    @patch('config.clientes.balance_summary.timezone')
    def test_expand_clientes_can_limit_invoice_rows_per_customer(self, mock_timezone):
        mock_timezone.localdate.return_value = date(2026, 7, 6)
        mock_timezone.make_aware.side_effect = timezone.make_aware

        for index in range(3):
            self._create_open_invoice(
                amount=Decimal('10.00') + Decimal(index),
                created_at=timezone.make_aware(datetime(2026, 6, 1, 12, index, 0)),
            )

        self.cliente.balance_summary = build_customer_balance_summary(self.cliente, today=date(2026, 7, 6))
        rows = expand_clientes_for_list_display([self.cliente], max_lines_per_customer=2)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].hidden_line_count, 1)
        self.assertEqual(rows[1].hidden_line_count, 0)


class CustomersReceivablesFilterTests(TestCase):
    def setUp(self):
        self.today = date(2026, 7, 6)
        self.overdue_customer = _create_customer(company_name='AR Overdue Co', approved=True)
        self.overdue_customer.terminos_pago = Cliente.PAYMENT_TERMS_NET7
        self.overdue_customer.save(update_fields=['terminos_pago'])

        self.due_soon_customer = _create_customer(company_name='AR Due Soon Co', approved=True)
        self.due_soon_customer.terminos_pago = Cliente.PAYMENT_TERMS_NET7
        self.due_soon_customer.save(update_fields=['terminos_pago'])

        self.clean_customer = _create_customer(company_name='AR Clean Co', approved=True)
        self.inactive_customer = _create_customer(company_name='AR Inactive Co', approved=False)

        self._create_invoice(
            self.overdue_customer,
            amount=Decimal('200.00'),
            qb_due_date=date(2026, 6, 20),
        )
        self._create_invoice(
            self.due_soon_customer,
            amount=Decimal('80.00'),
            qb_due_date=date(2026, 7, 10),
        )
        paid = self._create_invoice(
            self.clean_customer,
            amount=Decimal('40.00'),
            qb_due_date=date(2026, 6, 1),
        )
        Invoice.objects.filter(pk=paid.pk).update(qb_payment_status='PAID', saldo_cliente=Decimal('0.00'))

    def _create_invoice(self, cliente, *, amount, qb_due_date):
        pedido = Pedido.objects.create(
            cliente=cliente,
            origen='CLIENTE',
            estado='INVOICE_GENERADA',
            total=amount,
        )
        invoice = Invoice.objects.create(
            pedido=pedido,
            cliente=cliente,
            metodo_entrega='CUSTOMER_PICK_UP',
            subtotal=amount,
            total_neto=amount,
            saldo_cliente=amount,
            qb_due_date=qb_due_date,
        )
        return invoice

    def _qs(self):
        return Cliente.objects.filter(
            id__in=[
                self.overdue_customer.id,
                self.due_soon_customer.id,
                self.clean_customer.id,
                self.inactive_customer.id,
            ]
        ).order_by('id')

    @patch('config.clientes.balance_summary.timezone')
    def test_outstanding_excludes_customers_without_open_balance(self, mock_timezone):
        mock_timezone.localdate.return_value = self.today
        mock_timezone.make_aware.side_effect = timezone.make_aware

        filtered = filter_clientes_queryset_by_receivables(
            self._qs(),
            ar_status=AR_STATUS_OUTSTANDING,
            today=self.today,
        )
        ids = set(filtered.values_list('id', flat=True))

        self.assertEqual(ids, {self.overdue_customer.id, self.due_soon_customer.id})

    @patch('config.clientes.balance_summary.timezone')
    def test_no_debt_excludes_customers_with_open_balance(self, mock_timezone):
        mock_timezone.localdate.return_value = self.today
        mock_timezone.make_aware.side_effect = timezone.make_aware

        filtered = filter_clientes_queryset_by_receivables(
            self._qs(),
            ar_status=AR_STATUS_NO_DEBT,
            today=self.today,
        )
        ids = set(filtered.values_list('id', flat=True))

        self.assertEqual(ids, {self.clean_customer.id, self.inactive_customer.id})

    @patch('config.clientes.balance_summary.timezone')
    def test_overdue_bucket_filters_by_days_past_due(self, mock_timezone):
        mock_timezone.localdate.return_value = self.today
        mock_timezone.make_aware.side_effect = timezone.make_aware

        mid = _create_customer(company_name='AR Mid Overdue', approved=True)
        self._create_invoice(mid, amount=Decimal('50.00'), qb_due_date=date(2026, 6, 28))  # 8 days

        qs = Cliente.objects.filter(id__in=[self.overdue_customer.id, mid.id, self.due_soon_customer.id])
        filtered = filter_clientes_queryset_by_receivables(
            qs,
            ar_status=AR_STATUS_OVERDUE,
            overdue_bucket='8_15',
            today=self.today,
        )
        ids = set(filtered.values_list('id', flat=True))

        # overdue_customer: due 2026-06-20 => 16 days; mid: 8 days; due_soon: not overdue
        self.assertEqual(ids, {mid.id})

    @patch('config.clientes.balance_summary.timezone')
    def test_due_soon_window_filters_by_days_until_due(self, mock_timezone):
        mock_timezone.localdate.return_value = self.today
        mock_timezone.make_aware.side_effect = timezone.make_aware

        later = _create_customer(company_name='AR Later Due', approved=True)
        self._create_invoice(later, amount=Decimal('25.00'), qb_due_date=date(2026, 7, 25))

        qs = Cliente.objects.filter(
            id__in=[self.due_soon_customer.id, later.id, self.overdue_customer.id]
        )
        filtered = filter_clientes_queryset_by_receivables(
            qs,
            ar_status=AR_STATUS_DUE_SOON,
            due_soon_window=7,
            today=self.today,
        )
        ids = set(filtered.values_list('id', flat=True))

        # due_soon: Jul 10 => 4 days; later: 19 days; overdue excluded
        self.assertEqual(ids, {self.due_soon_customer.id})

    @patch('config.clientes.balance_summary.timezone')
    def test_priority_sort_puts_most_overdue_first(self, mock_timezone):
        mock_timezone.localdate.return_value = self.today
        mock_timezone.make_aware.side_effect = timezone.make_aware

        soft = _create_customer(company_name='AR Soft Overdue', approved=True)
        self._create_invoice(soft, amount=Decimal('500.00'), qb_due_date=date(2026, 7, 1))  # 5 days

        qs = Cliente.objects.filter(id__in=[soft.id, self.overdue_customer.id]).order_by('nombre_empresa')
        filtered = list(
            filter_clientes_queryset_by_receivables(
                qs,
                ar_status=AR_STATUS_OUTSTANDING,
                today=self.today,
            ).values_list('id', flat=True)
        )

        self.assertEqual(filtered[0], self.overdue_customer.id)
        self.assertEqual(filtered[1], soft.id)

    @patch('config.clientes.balance_summary.timezone')
    def test_receivables_summary_counts(self, mock_timezone):
        mock_timezone.localdate.return_value = self.today
        mock_timezone.make_aware.side_effect = timezone.make_aware

        summary = build_customers_receivables_summary(self._qs(), today=self.today)

        self.assertEqual(summary.customers_with_balance, 2)
        self.assertEqual(summary.total_outstanding, Decimal('280.00'))
        self.assertEqual(summary.invoices_overdue, 1)
        self.assertEqual(summary.invoices_due_this_week, 1)

    @patch('config.clientes.balance_summary.timezone')
    def test_severity_colors_for_overdue_and_due_soon(self, mock_timezone):
        mock_timezone.localdate.return_value = self.today
        mock_timezone.make_aware.side_effect = timezone.make_aware

        overdue_summary = build_customer_balance_summary(self.overdue_customer, today=self.today)
        due_soon_summary = build_customer_balance_summary(self.due_soon_customer, today=self.today)
        clean_summary = build_customer_balance_summary(self.clean_customer, today=self.today)

        self.assertEqual(overdue_summary.severity, SEVERITY_OVERDUE_SOFT)  # 16 days <= 30
        hard = _create_customer(company_name='AR Hard Overdue', approved=True)
        self._create_invoice(hard, amount=Decimal('10.00'), qb_due_date=date(2026, 5, 1))
        hard_summary = build_customer_balance_summary(hard, today=self.today)
        self.assertEqual(hard_summary.severity, SEVERITY_OVERDUE_HARD)
        self.assertEqual(due_soon_summary.severity, SEVERITY_DUE_SOON)
        self.assertEqual(clean_summary.severity, SEVERITY_NONE)

    def test_customer_list_view_applies_ar_filters_with_search_and_estado(self):
        admin = Usuario.objects.create_user(
            username='admin-ar-filters',
            password='secret123',
            role='admin',
        )
        self.client.force_login(admin)

        with patch(
            'config.clientes.balance_summary.timezone.localdate',
            return_value=self.today,
        ):
            response = self.client.get(
                reverse('vendedores_clientes'),
                {
                    'q': 'AR',
                    'estado': 'activo',
                    'ar_status': 'outstanding',
                },
            )

        self.assertEqual(response.status_code, 200)
        names = {c.nombre_empresa for c in response.context['clientes']}
        self.assertIn('AR Overdue Co', names)
        self.assertIn('AR Due Soon Co', names)
        self.assertNotIn('AR Clean Co', names)
        self.assertNotIn('AR Inactive Co', names)
        self.assertContains(response, 'Receivables Filters')
        self.assertContains(response, 'Customers with balance')
        self.assertEqual(response.context['filter_ar_status'], 'outstanding')
        self.assertEqual(response.context['receivables_summary'].customers_with_balance, 2)
