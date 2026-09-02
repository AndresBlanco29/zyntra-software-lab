"""Minimal tests for Business Overview / Reports Center data sources."""

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from config.reportes.data_sources import (
	build_expenses_snapshot,
	count_orders_excluding_cancelled,
	parse_overview_period,
)
from config.usuarios.models import Usuario


class OverviewPeriodParseTests(SimpleTestCase):
	def setUp(self):
		self.factory = RequestFactory()

	def test_default_period_is_last_30_days(self):
		request = self.factory.get('/reportes/')
		period = parse_overview_period(request)
		self.assertEqual(period['preset'], 'last_30_days')
		self.assertEqual(period['days'], 30)
		self.assertEqual(
			(period['end_date'] - period['start_date']).days + 1,
			period['days'],
		)
		self.assertEqual(
			(period['comparison_end_date'] - period['comparison_start_date']).days + 1,
			period['days'],
		)

	def test_today_and_custom_periods(self):
		today = timezone.localdate()
		request = self.factory.get('/reportes/', {'period': 'today'})
		period = parse_overview_period(request)
		self.assertEqual(period['preset'], 'today')
		self.assertEqual(period['start_date'], today)
		self.assertEqual(period['end_date'], today)

		start = today - timedelta(days=3)
		end = today - timedelta(days=1)
		request = self.factory.get(
			'/reportes/',
			{
				'period': 'custom',
				'start_date': start.isoformat(),
				'end_date': end.isoformat(),
			},
		)
		period = parse_overview_period(request)
		self.assertEqual(period['preset'], 'custom')
		self.assertEqual(period['start_date'], start)
		self.assertEqual(period['end_date'], end)
		self.assertEqual(period['days'], 3)


class ExpensesSnapshotTests(SimpleTestCase):
	def test_expenses_module_is_not_available(self):
		snapshot = build_expenses_snapshot()
		self.assertFalse(snapshot['available'])
		self.assertEqual(snapshot['label'], 'N/A')


class ValuedInventoryAvailabilityTests(SimpleTestCase):
	@patch('config.reportes.data_sources.availability_snapshot')
	@patch('config.reportes.data_sources.resolve_effective_cost')
	@patch('config.reportes.data_sources.Presentacion.objects')
	def test_valued_inventory_uses_availability_not_stock_disponible(
		self,
		presentacion_objects,
		resolve_cost,
		availability,
	):
		from config.reportes.data_sources import build_valued_inventory

		presentacion = SimpleNamespace(
			id=11,
			nombre='Case',
			producto=SimpleNamespace(
				nombre='Demo Flour',
				marca=SimpleNamespace(nombre='Demo Brand'),
			),
		)
		qs = MagicMock()
		qs.select_related.return_value = qs
		qs.filter.return_value = qs
		qs.order_by.return_value = [presentacion]
		presentacion_objects.select_related.return_value = qs
		availability.return_value = {
			11: {
				'available': 4,
				'quick_inventory': 10,
				'active_manual_adjustments': 0,
				'sales_pending_sync': 3,
				'in_orders': 3,
			}
		}
		resolve_cost.return_value = Decimal('2.50')

		snapshot = build_valued_inventory()

		availability.assert_called_once()
		self.assertEqual(snapshot['with_stock'], 1)
		self.assertEqual(snapshot['out_of_stock'], 0)
		self.assertEqual(snapshot['inventory_value'], Decimal('10.00'))
		self.assertEqual(snapshot['rows'][0]['available'], 4)
		self.assertTrue(callable(snapshot['stagnant_value']))


class CancelledOrdersHelperTests(SimpleTestCase):
	@patch('config.reportes.data_sources.orders_queryset_for_period')
	def test_count_orders_excludes_cancelled(self, orders_qs_for_period):
		qs = MagicMock()
		qs.count.return_value = 2
		orders_qs_for_period.return_value = qs
		period = {
			'start_datetime': timezone.now() - timedelta(days=1),
			'end_datetime': timezone.now(),
		}
		self.assertEqual(count_orders_excluding_cancelled(period), 2)
		orders_qs_for_period.assert_called_once_with(period, exclude_cancelled=True)


class BusinessOverviewPageTests(TestCase):
	def setUp(self):
		self.backoffice = Usuario.objects.create_user(
			username='backoffice-overview',
			password='secret123',
			role='backoffice',
		)

	def test_overview_page_ok_with_permission(self):
		self.client.force_login(self.backoffice)
		response = self.client.get(reverse('reportes_dashboard'))
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Business Overview')
		self.assertIn('kpis', response.context)
		self.assertIn('report_nav', response.context)

	def test_overview_requires_permission(self):
		vendor = Usuario.objects.create_user(
			username='vendor-no-overview',
			password='secret123',
			role='vendedor',
		)
		self.client.force_login(vendor)
		response = self.client.get(reverse('reportes_dashboard'))
		self.assertEqual(response.status_code, 302)
