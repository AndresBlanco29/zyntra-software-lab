"""Unit tests for invoice export terms + payment helpers (mocked QuickBooks client)."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

from django.test import SimpleTestCase
from django.utils import timezone

from config.integrations.quickbooks.sync import (
	_build_invoice_payment_terms_payload,
	_local_invoice_paid_amount,
	_preferred_term_name_for_local,
	_remote_invoice_open_balance,
	_resolve_or_create_sales_term_ref,
	_sync_invoice_payment_if_needed,
	_sync_result,
)


class InvoiceExportTermsHelpersTests(SimpleTestCase):
	def test_preferred_term_names(self):
		self.assertEqual(_preferred_term_name_for_local('NET7'), 'Net 7')
		self.assertEqual(_preferred_term_name_for_local('COD'), 'COD')
		self.assertEqual(_preferred_term_name_for_local(''), '')

	def test_resolve_existing_sales_term_ref(self):
		client = MagicMock()
		client._escape_query_value.side_effect = lambda value: value
		client.query.return_value = {'Term': [{'Id': '12', 'Name': 'Net 7', 'DueDays': 7}]}

		ref = _resolve_or_create_sales_term_ref(client, 'NET7')

		self.assertEqual(ref, {'value': '12', 'name': 'Net 7'})
		client.create_entity.assert_not_called()

	def test_create_sales_term_when_missing(self):
		client = MagicMock()
		client._escape_query_value.side_effect = lambda value: value
		client.query.return_value = {'Term': []}
		client.create_entity.return_value = {'Id': '99', 'Name': 'Net 14', 'DueDays': 14}

		ref = _resolve_or_create_sales_term_ref(client, 'NET14')

		self.assertEqual(ref['value'], '99')
		client.create_entity.assert_called_once_with('Term', {'Name': 'Net 14', 'DueDays': 14})

	def test_build_terms_payload_includes_due_date_and_term(self):
		client = MagicMock()
		client._escape_query_value.side_effect = lambda value: value
		client.query.return_value = {'Term': [{'Id': '7', 'Name': 'Net 7', 'DueDays': 7}]}
		base = date(2026, 7, 10)
		cliente = SimpleNamespace(
			terminos_pago='NET7',
			get_payment_due_date=lambda base_date: base_date + timedelta(days=7),
		)
		invoice = SimpleNamespace(
			numero='INV-1',
			pk=1,
			cliente=cliente,
			creada_en=timezone.make_aware(datetime(2026, 7, 10, 12, 0, 0)),
			delivery=SimpleNamespace(estimated_delivery_at=None),
		)

		payload = _build_invoice_payment_terms_payload(invoice=invoice, client=client)

		self.assertEqual(payload.get('DueDate'), '2026-07-17')
		self.assertEqual(payload.get('SalesTermRef', {}).get('value'), '7')

	def test_terms_payload_survives_term_lookup_errors(self):
		client = MagicMock()
		client._escape_query_value.side_effect = lambda value: value
		client.query.side_effect = RuntimeError('QB down')
		cliente = SimpleNamespace(
			terminos_pago='NET7',
			get_payment_due_date=lambda base_date: base_date + timedelta(days=7),
		)
		invoice = SimpleNamespace(
			numero='INV-2',
			pk=2,
			cliente=cliente,
			creada_en=timezone.make_aware(datetime(2026, 7, 10, 12, 0, 0)),
			delivery=SimpleNamespace(estimated_delivery_at=None),
		)

		payload = _build_invoice_payment_terms_payload(invoice=invoice, client=client)

		self.assertEqual(payload.get('DueDate'), '2026-07-17')
		self.assertNotIn('SalesTermRef', payload)


class InvoiceExportPaymentHelpersTests(SimpleTestCase):
	def test_local_paid_amount_only_when_delivery_paid(self):
		unpaid = SimpleNamespace(delivery=SimpleNamespace(estado_pago='NO_PAGADO', monto_pagado=Decimal('10.00')))
		paid = SimpleNamespace(delivery=SimpleNamespace(estado_pago='PAGADO', monto_pagado=Decimal('25.50')))
		self.assertEqual(_local_invoice_paid_amount(unpaid), Decimal('0.00'))
		self.assertEqual(_local_invoice_paid_amount(paid), Decimal('25.50'))

	def test_remote_open_balance_prefers_balance(self):
		self.assertEqual(
			_remote_invoice_open_balance({'Balance': '12.00', 'TotalAmt': '99.00'}),
			Decimal('12.00'),
		)
		self.assertEqual(
			_remote_invoice_open_balance({'TotalAmt': '40.00'}),
			Decimal('40.00'),
		)

	def test_payment_created_for_paid_invoice(self):
		client = MagicMock()
		client._escape_query_value.side_effect = lambda value: value
		client.query.return_value = {'Account': [{'Id': '4', 'Name': 'Undeposited Funds'}]}
		client.create_payment.return_value = {'Id': '500', 'TotalAmt': 50}
		invoice = SimpleNamespace(
			numero='INV-PAID',
			pk=10,
			quickbooks_id='',
			qb_payment_status='',
			creada_en=timezone.now(),
			delivery=SimpleNamespace(
				estado_pago='PAGADO',
				monto_pagado=Decimal('50.00'),
				metodo_pago='CASH',
				delivered_at=timezone.now(),
				get_metodo_pago_display=lambda: 'Cash',
			),
			save=MagicMock(),
		)
		remote = {'Id': '88', 'Balance': 50, 'TotalAmt': 50}

		result = _sync_invoice_payment_if_needed(
			client=client,
			invoice=invoice,
			remote_invoice=remote,
			customer_quickbooks_id='33',
		)

		self.assertEqual(result['action'], 'created')
		self.assertEqual(result['quickbooks_id'], '500')
		client.create_payment.assert_called_once()
		payment_payload = client.create_payment.call_args.args[0]
		self.assertEqual(payment_payload['TotalAmt'], 50.0)
		self.assertEqual(payment_payload['Line'][0]['LinkedTxn'][0]['TxnId'], '88')
		self.assertEqual(invoice.qb_payment_status, 'PAID')

	def test_payment_soft_fails_without_raising(self):
		from config.integrations.quickbooks.client import QuickBooksAPIError

		client = MagicMock()
		client._escape_query_value.side_effect = lambda value: value
		client.query.return_value = {'Account': [{'Id': '4', 'Name': 'Undeposited Funds'}]}
		client.create_payment.side_effect = QuickBooksAPIError('boom')
		invoice = SimpleNamespace(
			numero='INV-FAIL',
			pk=11,
			quickbooks_id='88',
			qb_payment_status='',
			creada_en=timezone.now(),
			delivery=SimpleNamespace(
				estado_pago='PAGADO',
				monto_pagado=Decimal('20.00'),
				metodo_pago='',
				delivered_at=None,
				get_metodo_pago_display=lambda: '',
			),
			save=MagicMock(),
		)

		result = _sync_invoice_payment_if_needed(
			client=client,
			invoice=invoice,
			remote_invoice={'Id': '88', 'Balance': 20},
			customer_quickbooks_id='33',
		)

		self.assertEqual(result['action'], 'failed')
		self.assertIn('boom', result['error'])

	def test_payment_skipped_when_unpaid(self):
		client = MagicMock()
		invoice = SimpleNamespace(
			delivery=SimpleNamespace(estado_pago='NO_PAGADO', monto_pagado=Decimal('0.00')),
		)
		result = _sync_invoice_payment_if_needed(
			client=client,
			invoice=invoice,
			remote_invoice={'Id': '1', 'Balance': 10},
			customer_quickbooks_id='2',
		)
		self.assertIsNone(result)
		client.create_payment.assert_not_called()

	def test_sync_result_keeps_extra_payment_key(self):
		result = _sync_result(
			entity='Invoice',
			action='created',
			payload={'Id': '1'},
			payment={'action': 'created'},
		)
		self.assertEqual(result['quickbooks_id'], '1')
		self.assertEqual(result['payment']['action'], 'created')
