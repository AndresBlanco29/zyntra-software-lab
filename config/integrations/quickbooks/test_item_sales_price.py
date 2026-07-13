"""Unit tests for QuickBooks item sales price → local QB-PRICE mapping."""

from decimal import Decimal
from unittest.mock import MagicMock

from django.test import SimpleTestCase, TestCase

from config.integrations.quickbooks.constants import QUICKBOOKS_SYNC_STATUS_SYNCED
from config.integrations.quickbooks.sync import (
	_extract_quickbooks_item_sales_price,
	_update_presentacion_from_quickbooks,
	import_quickbooks_item_record,
)
from config.productos.models import Categoria, Marca, Presentacion, Producto


class QuickBooksSalesPriceHelpersTests(SimpleTestCase):
	def test_extract_sales_price_from_unit_price(self):
		self.assertEqual(
			_extract_quickbooks_item_sales_price({'UnitPrice': 42.99}),
			Decimal('42.99'),
		)

	def test_extract_sales_price_from_sales_price_alias(self):
		self.assertEqual(
			_extract_quickbooks_item_sales_price({'SalesPrice': '10'}),
			Decimal('10.00'),
		)

	def test_extract_sales_price_missing_returns_none(self):
		self.assertIsNone(_extract_quickbooks_item_sales_price({}))
		self.assertIsNone(_extract_quickbooks_item_sales_price({'PurchaseCost': 5}))


class QuickBooksSalesPriceImportTests(TestCase):
	def setUp(self):
		self.categoria = Categoria.objects.create(nombre='Cleaning')
		self.marca = Marca.objects.create(nombre='Foca')
		self.client_api = MagicMock()

	def test_create_import_stores_qb_price_from_unit_price(self):
		result = import_quickbooks_item_record(
			{
				'Id': 'QB-FOCA-1',
				'Name': 'FOCA (500GRM) 36/1',
				'Type': 'Inventory',
				'UnitPrice': 42.99,
				'PurchaseCost': 34,
				'Active': True,
				'Sku': '005-2729',
			},
			client=self.client_api,
			skip_enrich=True,
			skip_images=True,
		)

		self.assertTrue(result['ok'])
		presentacion = Presentacion.objects.get(quickbooks_id='QB-FOCA-1')
		self.assertEqual(presentacion.qb_price, Decimal('42.99'))
		self.assertEqual(presentacion.costo, Decimal('34.00'))

	def test_update_import_refreshes_qb_price(self):
		producto = Producto.objects.create(
			nombre='FOCA (500GRM) 36/1',
			categoria=self.categoria,
			marca=self.marca,
			quickbooks_id='QB-FOCA-2',
			sync_status=QUICKBOOKS_SYNC_STATUS_SYNCED,
		)
		presentacion = Presentacion.objects.create(
			producto=producto,
			nombre='caja',
			unidades=1,
			tipo_contenido='caja',
			costo=Decimal('34.00'),
			qb_price=Decimal('40.00'),
			quickbooks_id='QB-FOCA-2',
			sync_status=QUICKBOOKS_SYNC_STATUS_SYNCED,
		)

		_update_presentacion_from_quickbooks(
			presentacion,
			quickbooks_id='QB-FOCA-2',
			item_cost=Decimal('34.00'),
			sales_price=Decimal('42.99'),
		)

		presentacion.refresh_from_db()
		self.assertEqual(presentacion.qb_price, Decimal('42.99'))
