import gzip
import json
import tempfile
import time
from decimal import Decimal
from io import StringIO
from pathlib import Path
from datetime import date
from datetime import timedelta
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

from django.contrib.messages import get_messages
from django.core.management import call_command
from django.core.management.base import CommandError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from config.cotizaciones.models import Cotizacion
from config.clientes.models import Cliente
from config.facturacion.models import Invoice, NotaAjuste
from config.facturacion.services import generar_invoice_desde_picking
from config.integrations.backups import _backup_modified_time
from config.integrations.quickbooks.client import QuickBooksAPIError
from config.integrations.quickbooks.services import get_connection
from config.integrations.models import QuickBooksConnection, QuickBooksImportConflict
from config.integrations.quickbooks.sync import (
    _build_customer_display_name,
    _build_customer_payload,
    _build_item_name,
    _build_item_payload,
    _convert_linked_item_to_inventory,
    _derive_quickbooks_invoice_status,
    _enrich_quickbooks_item_payload,
    _extract_quickbooks_item_cost,
    _extract_quickbooks_customer_company_name,
    _get_inventory_start_date,
    _normalize_inventory_start_date_if_needed,
    _parse_quickbooks_presentation,
    _prepare_inventory_item_for_txn_date,
    _resolve_item_payload_name,
    _resolve_quickbooks_item_category_and_brand,
    _strip_ltg_customer_export_prefix,
    _quickbooks_payload_active,
    import_quickbooks_credit_memo_record,
    import_quickbooks_customer_record,
    import_quickbooks_invoice_record,
    import_quickbooks_item_record,
    import_quickbooks_items,
    pull_quickbooks_items_to_local,
    refresh_linked_quickbooks_items,
    _resolve_quickbooks_item_active,
    _resolve_item_import_force_full,
    refresh_linked_quickbooks_invoice_status,
)
from config.inventario.models import StockPresentacion
from config.pedidos.models import Pedido, PedidoItem
from config.productos.models import Categoria, Marca, Presentacion, Producto
from config.usuarios.models import Usuario


class QuickBooksPresentationParsingTests(TestCase):
    def test_ltg_export_name_preserves_presentation(self):
        product, presentation, tipo, units = _parse_quickbooks_presentation({
            'Id': '1',
            'Name': 'LTG Item 12 - Jarritos Mango - Pallet',
        })

        self.assertEqual(product, 'Jarritos Mango')
        self.assertEqual(presentation, 'Pallet')
        self.assertEqual(tipo, 'pallet')
        self.assertEqual(units, 1)

    def test_product_dash_packaging_suffix_parses_case_and_units(self):
        product, presentation, tipo, units = _parse_quickbooks_presentation({
            'Id': '2',
            'Name': 'Jarritos Mango - Case 24',
        })

        self.assertEqual(product, 'Jarritos Mango')
        self.assertEqual(presentation, 'Case 24')
        self.assertEqual(tipo, 'caja')
        self.assertEqual(units, 24)

    def test_ltg_description_pipe_format_restores_tipo_contenido(self):
        product, presentation, tipo, units = _parse_quickbooks_presentation({
            'Id': '3',
            'Name': 'Imported Salsa Bottle',
            'Description': 'Catalog item | Caja 12 | caja',
        })

        self.assertEqual(product, 'Imported Salsa Bottle')
        self.assertEqual(presentation, 'Caja 12')
        self.assertEqual(tipo, 'caja')
        self.assertEqual(units, 12)

    def test_unit_of_measure_ref_is_used_when_available(self):
        product, presentation, tipo, units = _parse_quickbooks_presentation({
            'Id': '4',
            'Name': 'Jarritos Mango',
            'UnitOfMeasureRef': {'name': 'Box 12'},
        })

        self.assertEqual(product, 'Jarritos Mango')
        self.assertEqual(presentation, 'Box 12')
        self.assertEqual(tipo, 'caja')
        self.assertEqual(units, 12)

    def test_product_name_slash_pattern_sets_case_units(self):
        product, presentation, tipo, units = _parse_quickbooks_presentation({
            'Id': '5',
            'Name': '123 DETERGENT MAXI EFECTO COLOR 4/4.65 LT',
        })

        self.assertEqual(product, '123 DETERGENT MAXI EFECTO COLOR 4/4.65 LT')
        self.assertEqual(presentation, 'Caja')
        self.assertEqual(units, 4)
        self.assertEqual(tipo, '4.65 LT')

    def test_generic_unit_defaults_to_case_for_quickbooks_import(self):
        product, presentation, tipo, units = _parse_quickbooks_presentation({
            'Id': '6',
            'Name': 'BOUNCE CHECK',
        })

        self.assertEqual(product, 'BOUNCE CHECK')
        self.assertEqual(presentation, 'Caja')
        self.assertEqual(tipo, 'caja')
        self.assertEqual(units, 1)

    def test_double_slash_packaging_pattern_defaults_to_case(self):
        product, presentation, tipo, units = _parse_quickbooks_presentation({
            'Id': '7',
            'Name': 'BOING TRIANGULO ASSORTED 3/6//6.76OZ',
        })

        self.assertEqual(product, 'BOING TRIANGULO ASSORTED 3/6//6.76OZ')
        self.assertEqual(presentation, 'Caja')
        self.assertEqual(units, 6)
        self.assertEqual(tipo, '6.76 OZ')


class QuickBooksCustomerNamingTests(TestCase):
    def test_strip_ltg_customer_export_prefix(self):
        self.assertEqual(
            _strip_ltg_customer_export_prefix('LTG Customer 238 - (BHM) MI TIERRA'),
            '(BHM) MI TIERRA',
        )

    def test_build_customer_display_name_uses_company_name_without_prefix(self):
        cliente = Cliente(nombre_empresa='(BHM) MI TIERRA')
        self.assertEqual(_build_customer_display_name(cliente), '(BHM) MI TIERRA')

    def test_build_customer_payload_preserves_remote_quickbooks_name(self):
        cliente = Cliente(nombre_empresa='(BHM) MI TIERRA')
        payload = _build_customer_payload(
            cliente,
            remote_payload={
                'Id': '701',
                'DisplayName': '(BHM) MI TIERRA',
                'CompanyName': '(BHM) MI TIERRA',
            },
        )
        self.assertEqual(payload['DisplayName'], '(BHM) MI TIERRA')
        self.assertEqual(payload['CompanyName'], '(BHM) MI TIERRA')

    def test_import_strips_ltg_prefix_from_customer_name(self):
        company_name = _extract_quickbooks_customer_company_name({
            'DisplayName': 'LTG Customer 238 - (BHM) MI TIERRA',
        })
        self.assertEqual(company_name, '(BHM) MI TIERRA')


class QuickBooksItemPayloadTests(TestCase):
    def setUp(self):
        categoria = Categoria.objects.create(nombre='QB payload category')
        marca = Marca.objects.create(nombre='QB payload brand')
        producto = Producto.objects.create(nombre='QB payload product', categoria=categoria, marca=marca, activo=True)
        self.presentacion = Presentacion.objects.create(
            producto=producto,
            nombre='Caja',
            unidades=1,
            tipo_contenido='caja',
            costo=Decimal('6.00'),
            precio_3=Decimal('12.00'),
        )
        StockPresentacion.objects.create(presentacion=self.presentacion, stock_fisico=18, stock_disponible=18)

    def test_build_item_name_uses_product_name_without_ltg_prefix(self):
        self.assertEqual(_build_item_name(self.presentacion), 'QB payload product')

    def test_build_item_payload_preserves_remote_quickbooks_name(self):
        payload = _build_item_payload(
            self.presentacion,
            client=Mock(),
            remote_payload={
                'Id': '1062',
                'Type': 'Inventory',
                'Name': 'MILAGRO CORN TORTILLA 16/36CT',
            },
        )

        self.assertEqual(payload['Name'], 'MILAGRO CORN TORTILLA 16/36CT')

    def test_resolve_item_payload_name_falls_back_to_local_product_name(self):
        self.assertEqual(
            _resolve_item_payload_name(self.presentacion),
            'QB payload product',
        )

    @override_settings(QUICKBOOKS_USE_INVENTORY_ITEMS=True)
    @patch('config.integrations.quickbooks.sync._get_default_asset_account_ref', return_value={'value': '81', 'name': 'Inventory Asset'})
    @patch('config.integrations.quickbooks.sync._get_default_expense_account_ref', return_value={'value': '80', 'name': 'COGS'})
    @patch('config.integrations.quickbooks.sync._get_inventory_income_account_ref', return_value={'value': '79', 'name': 'Sales of Product Income'})
    def test_build_item_payload_converts_linked_noninventory_to_inventory(self, *_mocks):
        payload = _build_item_payload(
            self.presentacion,
            client=Mock(),
            income_account_ref={'value': '55', 'name': 'Services Income'},
            remote_payload={
                'Id': '1062',
                'Type': 'NonInventory',
                'IncomeAccountRef': {'value': '55', 'name': 'Services Income'},
            },
        )

        self.assertEqual(payload['Type'], 'Inventory')
        self.assertTrue(payload['TrackQtyOnHand'])
        self.assertEqual(payload['QtyOnHand'], 18)
        self.assertEqual(payload['IncomeAccountRef']['value'], '79')
        self.assertEqual(payload['AssetAccountRef']['value'], '81')
        self.assertEqual(payload['ExpenseAccountRef']['value'], '80')

    def test_get_inventory_start_date_defaults_to_early_date(self):
        self.assertEqual(_get_inventory_start_date(), date(2015, 1, 1))
        self.assertEqual(_get_inventory_start_date(txn_date=date(2024, 3, 15)), date(2015, 1, 1))

    @override_settings(QUICKBOOKS_INVENTORY_START_DATE='2020-06-01')
    def test_get_inventory_start_date_respects_setting_and_txn_date(self):
        self.assertEqual(_get_inventory_start_date(), date(2020, 6, 1))
        self.assertEqual(_get_inventory_start_date(txn_date=date(2019, 1, 1)), date(2019, 1, 1))

    @override_settings(QUICKBOOKS_USE_INVENTORY_ITEMS=True)
    @patch('config.integrations.quickbooks.sync._get_default_asset_account_ref', return_value={'value': '81', 'name': 'Inventory Asset'})
    @patch('config.integrations.quickbooks.sync._get_default_expense_account_ref', return_value={'value': '80', 'name': 'COGS'})
    @patch('config.integrations.quickbooks.sync._get_inventory_income_account_ref', return_value={'value': '79', 'name': 'Sales of Product Income'})
    def test_build_item_payload_uses_early_inventory_start_date(self, *_mocks):
        payload = _build_item_payload(self.presentacion, client=Mock())

        self.assertEqual(payload['InvStartDate'], '2015-01-01')

    def test_prepare_inventory_item_for_txn_date_recreates_late_start_item(self):
        presentacion = Mock(pk=12, quickbooks_id='1062')
        client = Mock()
        client.read_entity.return_value = {
            'Id': '1062',
            'SyncToken': '4',
            'Type': 'Inventory',
            'InvStartDate': '2026-06-22',
            'IncomeAccountRef': {'value': '79', 'name': 'Sales of Product Income'},
        }
        recreated = {'Id': '2001', 'SyncToken': '0', 'Type': 'Inventory', 'InvStartDate': '2015-01-01'}

        with patch('config.integrations.quickbooks.sync._recreate_presentacion_as_inventory_item', return_value=recreated) as mock_recreate, \
             patch('config.integrations.quickbooks.sync._build_item_payload', return_value={'Type': 'Inventory', 'InvStartDate': '2015-01-01'}) as mock_build, \
             patch('config.integrations.quickbooks.sync._mark_synced') as mock_mark_synced:
            _prepare_inventory_item_for_txn_date(
                client=client,
                presentacion=presentacion,
                txn_date=date(2026, 5, 1),
            )

        mock_build.assert_called_once()
        mock_recreate.assert_called_once()
        mock_mark_synced.assert_called_once_with(presentacion, '2001')

    @override_settings(QUICKBOOKS_USE_INVENTORY_ITEMS=True)
    @patch('config.integrations.quickbooks.sync._recreate_presentacion_as_inventory_item')
    @patch('config.integrations.quickbooks.sync._build_item_payload')
    def test_normalize_inventory_start_date_if_needed_recreates_item(self, mock_build, mock_recreate):
        presentacion = Mock(pk=12)
        existing = {
            'Id': '1062',
            'SyncToken': '4',
            'Type': 'Inventory',
            'InvStartDate': '2026-06-22',
        }
        mock_build.return_value = {'Type': 'Inventory', 'InvStartDate': '2015-01-01'}
        mock_recreate.return_value = {'Id': '2001', 'Type': 'Inventory', 'InvStartDate': '2015-01-01'}

        with patch('config.integrations.quickbooks.sync._mark_synced') as mock_mark_synced:
            result = _normalize_inventory_start_date_if_needed(presentacion, existing, client=Mock())

        self.assertEqual(result['Id'], '2001')
        mock_recreate.assert_called_once()
        mock_mark_synced.assert_called_once_with(presentacion, '2001')

    @override_settings(QUICKBOOKS_USE_INVENTORY_ITEMS=True)
    @patch('config.integrations.quickbooks.sync._get_default_asset_account_ref', return_value={'value': '81', 'name': 'Inventory Asset'})
    @patch('config.integrations.quickbooks.sync._get_default_expense_account_ref', return_value={'value': '80', 'name': 'COGS'})
    @patch('config.integrations.quickbooks.sync._get_inventory_income_account_ref', return_value={'value': '79', 'name': 'Sales of Product Income'})
    def test_build_item_payload_preserves_inventory_income_account_on_update(self, *_mocks):
        payload = _build_item_payload(
            self.presentacion,
            client=Mock(),
            income_account_ref={'value': '79', 'name': 'Sales of Product Income'},
            remote_payload={
                'Id': '1062',
                'Type': 'Inventory',
                'IncomeAccountRef': {'value': '79', 'name': 'Sales of Product Income'},
            },
        )

        self.assertEqual(payload['Type'], 'Inventory')
        self.assertEqual(payload['IncomeAccountRef']['value'], '79')

    @override_settings(QUICKBOOKS_USE_INVENTORY_ITEMS=False)
    @patch('config.integrations.quickbooks.sync._get_default_income_account_ref', return_value={'value': '79', 'name': 'Sales'})
    def test_build_item_payload_preserves_noninventory_when_setting_disabled(self, *_mocks):
        payload = _build_item_payload(
            self.presentacion,
            client=Mock(),
            remote_payload={
                'Id': '1062',
                'Type': 'NonInventory',
                'IncomeAccountRef': {'value': '79', 'name': 'Sales'},
            },
        )

        self.assertEqual(payload['Type'], 'NonInventory')
        self.assertNotIn('QtyOnHand', payload)

    @override_settings(QUICKBOOKS_USE_INVENTORY_ITEMS=True)
    @patch('config.integrations.quickbooks.sync._get_default_asset_account_ref', return_value={'value': '81', 'name': 'Inventory Asset'})
    @patch('config.integrations.quickbooks.sync._get_default_expense_account_ref', return_value={'value': '80', 'name': 'COGS'})
    @patch('config.integrations.quickbooks.sync._get_inventory_income_account_ref', return_value={'value': '79', 'name': 'Sales of Product Income'})
    def test_convert_linked_item_to_inventory_falls_back_to_recreate(self, *_mocks):
        existing = {
            'Id': '1062',
            'SyncToken': '3',
            'Type': 'NonInventory',
            'Name': 'MILAGRO CORN TORTILLA 16/36CT',
            'IncomeAccountRef': {'value': '79', 'name': 'Sales'},
        }
        desired_payload = _build_item_payload(self.presentacion, client=Mock(), remote_payload=existing)
        self.assertEqual(desired_payload['Name'], 'MILAGRO CORN TORTILLA 16/36CT')
        client = Mock()
        client.update_item.side_effect = QuickBooksAPIError('QuickBooks API request failed: conversion rejected')
        client.create_item.return_value = {'Id': '2001', 'SyncToken': '0', 'Type': 'Inventory', 'QtyOnHand': 18}
        client.find_by_id.return_value = {'Id': '2001', 'SyncToken': '0', 'Type': 'Inventory', 'QtyOnHand': 18}

        updated = _convert_linked_item_to_inventory(self.presentacion, existing, desired_payload, client=client)

        self.assertEqual(updated['Id'], '2001')
        client.create_item.assert_called_once()
        self.assertEqual(client.create_item.call_args.args[0]['Type'], 'Inventory')


class QuickBooksItemCostSyncTests(TestCase):
    @patch('config.integrations.quickbooks.sync._fetch_quickbooks_item_payload')
    def test_enrich_fetches_purchase_cost_when_list_payload_only_has_qty_on_hand(self, mock_fetch):
        mock_fetch.return_value = {
            'Id': 'QB-OIL-1',
            'Name': 'ACEITE 123 CANOLA OLI 12/1 LT',
            'Type': 'Inventory',
            'PurchaseCost': 35.99,
            'QtyOnHand': 0,
        }
        result = _enrich_quickbooks_item_payload({
            'Id': 'QB-OIL-1',
            'Name': 'ACEITE 123 CANOLA OLI 12/1 LT',
            'Type': 'Inventory',
            'QtyOnHand': 0,
        })
        self.assertEqual(_extract_quickbooks_item_cost(result), Decimal('35.99'))
        mock_fetch.assert_called_once()

    @patch('config.integrations.quickbooks.sync.import_quickbooks_item_record')
    @patch('config.integrations.quickbooks.sync._fetch_quickbooks_items_map')
    def test_refresh_linked_items_uses_full_item_payload_for_cost(self, mock_fetch_map, mock_import):
        categoria = Categoria.objects.create(nombre='Aceites')
        marca = Marca.objects.create(nombre='123')
        producto = Producto.objects.create(
            nombre='ACEITE 123 CANOLA OLI 12/1 LT',
            categoria=categoria,
            marca=marca,
        )
        Presentacion.objects.create(
            producto=producto,
            nombre='Unit',
            unidades=1,
            tipo_contenido='unidad',
            costo=Decimal('33.00'),
            quickbooks_id='QB-OIL-1',
        )
        mock_fetch_map.return_value = {
            'QB-OIL-1': {
                'Id': 'QB-OIL-1',
                'Name': 'ACEITE 123 CANOLA OLI 12/1 LT',
                'Type': 'Inventory',
                'PurchaseCost': 35.99,
                'QtyOnHand': 0,
            }
        }
        mock_import.return_value = {'ok': True, 'action': 'updated'}

        refresh_linked_quickbooks_items()

        mock_fetch_map.assert_called_once()
        imported_payload = mock_import.call_args[0][0]
        self.assertEqual(_extract_quickbooks_item_cost(imported_payload), Decimal('35.99'))


class QuickBooksLinkedItemUpdateTests(TestCase):
    @patch('config.integrations.quickbooks.sync._save_quickbooks_item_image', return_value=False)
    @patch('config.integrations.quickbooks.sync._enrich_quickbooks_item_payload', side_effect=lambda payload, **kwargs: payload)
    def test_linked_update_preserves_local_presentation(self, _mock_enrich, _mock_image):
        categoria = Categoria.objects.create(nombre='Aceites')
        marca = Marca.objects.create(nombre='123')
        producto = Producto.objects.create(
            nombre='Old Product Name',
            categoria=categoria,
            marca=marca,
            codigo_barras='OLD-SKU',
        )
        presentacion = Presentacion.objects.create(
            producto=producto,
            nombre='Caja',
            nombre_en='Box',
            unidades=12,
            tipo_contenido='caja',
            tipo_contenido_en='box',
            costo=Decimal('10.00'),
            quickbooks_id='QB-LINKED-1',
        )

        result = import_quickbooks_item_record({
            'Id': 'QB-LINKED-1',
            'Name': 'ACEITE 123 CANOLA OLI 12/1 LT',
            'Description': 'Updated from QuickBooks',
            'Sku': '012005000596',
            'Type': 'Inventory',
            'PurchaseCost': 35.99,
            'QtyOnHand': 5,
            'Active': True,
        })

        presentacion.refresh_from_db()
        producto.refresh_from_db()
        self.assertEqual(result['action'], 'updated')
        self.assertEqual(presentacion.nombre, 'Caja')
        self.assertEqual(presentacion.nombre_en, 'Box')
        self.assertEqual(presentacion.unidades, 12)
        self.assertEqual(presentacion.tipo_contenido, 'caja')
        self.assertEqual(presentacion.tipo_contenido_en, 'box')
        self.assertEqual(presentacion.costo, Decimal('35.99'))
        self.assertEqual(producto.nombre, 'ACEITE 123 CANOLA OLI 12/1 LT')
        self.assertEqual(producto.descripcion, 'Updated from QuickBooks')
        self.assertEqual(producto.codigo_barras, '012005000596')


class QuickBooksItemImportModeTests(TestCase):
    def test_force_full_when_no_linked_catalog_items(self):
        self.assertTrue(_resolve_item_import_force_full(False))

    def test_incremental_when_catalog_already_linked(self):
        producto = Producto.objects.create(nombre='Existing', quickbooks_id='QB-1')
        Presentacion.objects.create(producto=producto, nombre='Unit', quickbooks_id='QB-1')
        self.assertFalse(_resolve_item_import_force_full(False))

    @patch('config.integrations.quickbooks.sync.import_quickbooks_items')
    @patch('config.integrations.quickbooks.sync.QuickBooksAPIClient')
    def test_pull_items_auto_uses_full_import_on_empty_catalog(self, mock_client_cls, mock_import_items):
        mock_client = mock_client_cls.return_value
        mock_client.connection.get_sync_cursor.return_value = '2026-01-01T00:00:00Z'
        mock_import_items.return_value = {'created_count': 1000, 'latest_updated_at': None}

        result = pull_quickbooks_items_to_local(max_results=None)

        self.assertTrue(result['force_full'])
        self.assertFalse(result['incremental'])
        mock_import_items.assert_called_once()
        self.assertIsNone(mock_import_items.call_args.kwargs.get('updated_after'))


class QuickBooksCategoryBrandImportTests(TestCase):
    def test_resolve_returns_none_when_quickbooks_has_no_category_or_brand(self):
        category, brand = _resolve_quickbooks_item_category_and_brand({
            'Id': 'QB-PLAIN',
            'Name': 'Plain Product',
            'Type': 'Inventory',
        })
        self.assertIsNone(category)
        self.assertIsNone(brand)

    @patch('config.integrations.quickbooks.sync._save_quickbooks_item_image', return_value=False)
    @patch('config.integrations.quickbooks.sync._enrich_quickbooks_item_payload', side_effect=lambda payload, **kwargs: payload)
    def test_new_import_without_qb_category_or_brand_stays_empty(self, _mock_enrich, _mock_image):
        result = import_quickbooks_item_record({
            'Id': 'QB-PLAIN-NEW',
            'Name': 'Plain Product',
            'Type': 'Inventory',
            'Active': True,
        })
        self.assertEqual(result['action'], 'created')
        presentacion = Presentacion.objects.get(quickbooks_id='QB-PLAIN-NEW')
        self.assertIsNone(presentacion.producto.categoria)
        self.assertIsNone(presentacion.producto.marca)

    @patch('config.integrations.quickbooks.sync._save_quickbooks_item_image', return_value=False)
    @patch('config.integrations.quickbooks.sync._enrich_quickbooks_item_payload', side_effect=lambda payload, **kwargs: payload)
    def test_linked_update_clears_placeholder_and_preserves_real_local_category(self, _mock_enrich, _mock_image):
        real_category = Categoria.objects.create(nombre='GROCERY')
        real_brand = Marca.objects.create(nombre='123')
        placeholder_category = Categoria.objects.create(nombre='QuickBooks Imported')
        placeholder_brand = Marca.objects.create(nombre='QuickBooks Imported')
        producto = Producto.objects.create(
            nombre='Local Product',
            categoria=real_category,
            marca=real_brand,
            quickbooks_id='QB-LOCAL-1',
        )
        presentacion = Presentacion.objects.create(
            producto=producto,
            nombre='Unit',
            quickbooks_id='QB-LOCAL-1',
        )

        result = import_quickbooks_item_record({
            'Id': 'QB-LOCAL-1',
            'Name': 'Local Product Updated',
            'Type': 'Inventory',
            'Active': True,
        })

        producto.refresh_from_db()
        self.assertEqual(result['action'], 'updated')
        self.assertEqual(producto.categoria.nombre, 'GROCERY')
        self.assertEqual(producto.marca.nombre, '123')
        self.assertNotEqual(producto.categoria_id, placeholder_category.id)
        self.assertNotEqual(producto.marca_id, placeholder_brand.id)

    @patch('config.integrations.quickbooks.sync._save_quickbooks_item_image', return_value=False)
    @patch('config.integrations.quickbooks.sync._enrich_quickbooks_item_payload', side_effect=lambda payload, **kwargs: payload)
    def test_linked_update_clears_quickbooks_imported_placeholder(self, _mock_enrich, _mock_image):
        placeholder_category = Categoria.objects.create(nombre='QuickBooks Imported')
        placeholder_brand = Marca.objects.create(nombre='QuickBooks Imported')
        producto = Producto.objects.create(
            nombre='Imported Product',
            categoria=placeholder_category,
            marca=placeholder_brand,
            quickbooks_id='QB-PLACEHOLDER-1',
        )
        presentacion = Presentacion.objects.create(
            producto=producto,
            nombre='Unit',
            quickbooks_id='QB-PLACEHOLDER-1',
        )

        result = import_quickbooks_item_record({
            'Id': 'QB-PLACEHOLDER-1',
            'Name': 'Imported Product',
            'Type': 'Inventory',
            'Active': True,
        })

        producto.refresh_from_db()
        self.assertEqual(result['action'], 'updated')
        self.assertIsNone(producto.categoria)
        self.assertIsNone(producto.marca)


class QuickBooksDeletedRecordImportTests(TestCase):
    def test_active_defaults_to_true_when_missing_or_none(self):
        self.assertTrue(_quickbooks_payload_active({'Id': '1', 'Name': 'X'}))
        self.assertTrue(_quickbooks_payload_active({'Id': '1', 'Name': 'X', 'Active': None}))
        self.assertFalse(_quickbooks_payload_active({'Id': '1', 'Name': 'X', 'Active': False}))

    @patch('config.integrations.quickbooks.sync._fetch_quickbooks_item_payload', return_value=None)
    def test_resolve_item_active_defaults_to_false_when_status_unknown(self, _mock_fetch):
        self.assertFalse(_resolve_quickbooks_item_active({'Id': 'QB-UNKNOWN', 'Name': 'Unknown Status'}))

    @patch('config.integrations.quickbooks.sync.fetch_quickbooks_items')
    @patch('config.integrations.quickbooks.sync.import_quickbooks_item_record')
    def test_incremental_import_does_not_deactivate_products_outside_batch(self, mock_import, mock_fetch):
        producto = Producto.objects.create(nombre='Keep Active', activo=True, quickbooks_id='QB-KEEP-1')
        Presentacion.objects.create(producto=producto, nombre='Unit', quickbooks_id='QB-KEEP-1')

        mock_fetch.return_value = [{'Id': 'QB-OTHER', 'Name': 'Other', 'Type': 'Inventory', 'Active': True}]
        mock_import.return_value = {'ok': True, 'action': 'updated'}

        import_quickbooks_items(max_results=None, updated_after='2026-01-01T00:00:00Z')

        producto.refresh_from_db()
        self.assertTrue(producto.activo)

    @patch('config.integrations.quickbooks.sync._save_quickbooks_item_image', return_value=False)
    @patch('config.integrations.quickbooks.sync._enrich_quickbooks_item_payload', side_effect=lambda payload, **kwargs: payload)
    def test_inactive_item_is_skipped_on_new_import(self, _mock_enrich, _mock_image):
        result = import_quickbooks_item_record({
            'Id': 'QB-INACTIVE-ITEM',
            'Name': 'Inactive Product',
            'Type': 'Inventory',
            'Active': False,
        })
        self.assertEqual(result['action'], 'skipped')
        self.assertFalse(Presentacion.objects.filter(quickbooks_id='QB-INACTIVE-ITEM').exists())

    @patch('config.integrations.quickbooks.sync._save_quickbooks_item_image', return_value=False)
    @patch('config.integrations.quickbooks.sync._enrich_quickbooks_item_payload', side_effect=lambda payload, **kwargs: payload)
    def test_inactive_item_deactivates_existing_linked_product(self, _mock_enrich, _mock_image):
        producto = Producto.objects.create(
            nombre='Was Active',
            activo=True,
            quickbooks_id='QB-INACTIVE-LINKED',
        )
        Presentacion.objects.create(
            producto=producto,
            nombre='Unit',
            quickbooks_id='QB-INACTIVE-LINKED',
        )

        result = import_quickbooks_item_record({
            'Id': 'QB-INACTIVE-LINKED',
            'Name': 'Was Active',
            'Type': 'Inventory',
            'Active': False,
        })

        producto.refresh_from_db()
        self.assertEqual(result['action'], 'updated')
        self.assertFalse(producto.activo)

    def test_inactive_customer_is_skipped(self):
        result = import_quickbooks_customer_record({
            'Id': 'QB-INACTIVE-CUST',
            'DisplayName': 'Inactive Customer LLC',
            'CompanyName': 'Inactive Customer LLC',
            'Active': False,
        })
        self.assertEqual(result['action'], 'skipped')
        self.assertFalse(Cliente.objects.filter(quickbooks_id='QB-INACTIVE-CUST').exists())

    def test_deleted_customer_label_is_skipped(self):
        result = import_quickbooks_customer_record({
            'Id': 'QB-DELETED-CUST',
            'DisplayName': 'Flores Produce (deleted)',
            'CompanyName': 'Flores Produce (deleted)',
            'Active': True,
        })
        self.assertEqual(result['action'], 'skipped')
        self.assertFalse(Cliente.objects.filter(quickbooks_id='QB-DELETED-CUST').exists())

    def test_invoice_with_deleted_customer_ref_is_skipped_without_conflict(self):
        result = import_quickbooks_invoice_record({
            'Id': 'QB-INV-DELETED-CUST',
            'DocNumber': '2001',
            'CustomerRef': {'value': '999', 'name': '(BHM) FLORES PRODUCE (deleted)'},
            'TotalAmt': '50.00',
            'Balance': '50.00',
        })
        self.assertEqual(result['action'], 'skipped')
        self.assertFalse(Invoice.objects.filter(quickbooks_id='QB-INV-DELETED-CUST').exists())
        self.assertFalse(QuickBooksImportConflict.objects.filter(quickbooks_id='QB-INV-DELETED-CUST').exists())

    def test_credit_memo_with_deleted_customer_ref_is_skipped_without_conflict(self):
        result = import_quickbooks_credit_memo_record({
            'Id': 'QB-CM-DELETED-CUST',
            'DocNumber': 'CM-2001',
            'CustomerRef': {'value': '999', 'name': 'Old Customer (eliminado)'},
            'TotalAmt': '10.00',
            'Balance': '0.00',
        })
        self.assertEqual(result['action'], 'skipped')
        self.assertFalse(NotaAjuste.objects.filter(quickbooks_id='QB-CM-DELETED-CUST').exists())
        self.assertFalse(QuickBooksImportConflict.objects.filter(quickbooks_id='QB-CM-DELETED-CUST').exists())


class QuickBooksInvoiceStatusImportTests(TestCase):
    def test_derive_paid_status_when_balance_is_zero(self):
        status, due_date, email_status = _derive_quickbooks_invoice_status({
            'TotalAmt': '1559.52',
            'Balance': '0',
            'DueDate': '2026-06-10',
            'EmailStatus': 'EmailSent',
        })
        self.assertEqual(status, 'PAID')
        self.assertEqual(email_status, 'EMAIL_SENT')

    def test_derive_due_status_with_future_due_date(self):
        due = timezone.localdate() + timedelta(days=6)
        status, due_date, email_status = _derive_quickbooks_invoice_status({
            'TotalAmt': '2399.40',
            'Balance': '2399.40',
            'DueDate': due.isoformat(),
            'EmailStatus': 'NeedToSend',
        })
        self.assertEqual(status, 'DUE')
        self.assertEqual(due_date, due)
        self.assertEqual(email_status, 'NEED_TO_SEND')

    @patch('config.integrations.quickbooks.sync.QuickBooksAPIClient')
    def test_derive_deposited_status_when_linked_payment_is_deposited(self, mock_client_cls):
        mock_client = mock_client_cls.return_value
        mock_client.read_entity.return_value = {
            'Id': 'PAY-1',
            'DepositToAccountRef': {'name': 'Business Checking'},
        }
        status, _, _ = _derive_quickbooks_invoice_status({
            'TotalAmt': '100.00',
            'Balance': '0',
            'LinkedTxn': [{'TxnId': 'PAY-1', 'TxnType': 'Payment'}],
        }, client=mock_client)
        self.assertEqual(status, 'DEPOSITED')

    def test_import_invoice_sets_qb_payment_status(self):
        user = Usuario.objects.create_user(username='qb-status-client', password='secret123', role='cliente')
        cliente = Cliente.objects.create(
            usuario=user,
            nombre_empresa='Status Customer LLC',
            telefono='5550000001',
            direccion='123 Main',
            ciudad='Dallas',
            estado='TX',
            codigo_postal='75001',
            pais='USA',
            sales_tax_number='TX-1',
            certificado_tax='certificados/test.pdf',
        )
        due = timezone.localdate() + timedelta(days=8)
        result = import_quickbooks_invoice_record({
            'Id': 'QB-INV-STATUS-1',
            'DocNumber': 'LU101387',
            'CustomerRef': {'value': 'C-1', 'name': cliente.nombre_empresa},
            'TotalAmt': '2399.40',
            'Balance': '2399.40',
            'DueDate': due.isoformat(),
            'EmailStatus': 'NeedToSend',
        })
        self.assertEqual(result['action'], 'created')
        invoice = Invoice.objects.get(quickbooks_id='QB-INV-STATUS-1')
        self.assertEqual(invoice.qb_payment_status, 'DUE')
        self.assertEqual(invoice.qb_due_date, due)
        self.assertEqual(invoice.qb_email_status, 'NEED_TO_SEND')
        self.assertEqual(invoice.get_qb_payment_status_display_label(), 'Due in 8 days')

    @patch('config.integrations.quickbooks.sync._fetch_quickbooks_invoices_by_ids')
    def test_refresh_linked_invoice_status_updates_unsynced_invoices(self, mock_fetch_invoices):
        user = Usuario.objects.create_user(username='qb-refresh-client', password='secret123', role='cliente')
        cliente = Cliente.objects.create(
            usuario=user,
            nombre_empresa='Refresh Customer LLC',
            telefono='5550000002',
            direccion='456 Main',
            ciudad='Dallas',
            estado='TX',
            codigo_postal='75001',
            pais='USA',
            sales_tax_number='TX-2',
            certificado_tax='certificados/test.pdf',
        )
        due = timezone.localdate() - timedelta(days=9)
        import_quickbooks_invoice_record({
            'Id': 'QB-INV-REFRESH-1',
            'DocNumber': 'LU100902',
            'CustomerRef': {'value': 'C-2', 'name': cliente.nombre_empresa},
            'TotalAmt': '8716.11',
            'Balance': '8716.11',
            'DueDate': due.isoformat(),
            'EmailStatus': 'NeedToSend',
        })
        invoice = Invoice.objects.get(quickbooks_id='QB-INV-REFRESH-1')
        Invoice.objects.filter(pk=invoice.pk).update(qb_payment_status='', qb_email_status='', qb_due_date=None)
        mock_fetch_invoices.return_value = {
            'QB-INV-REFRESH-1': {
                'Id': 'QB-INV-REFRESH-1',
                'DocNumber': 'LU100902',
                'CustomerRef': {'value': 'C-2', 'name': cliente.nombre_empresa},
                'TotalAmt': '8716.11',
                'Balance': '8716.11',
                'DueDate': due.isoformat(),
                'EmailStatus': 'NeedToSend',
            }
        }

        result = refresh_linked_quickbooks_invoice_status()

        invoice.refresh_from_db()
        self.assertEqual(result['updated_count'], 1)
        self.assertEqual(invoice.qb_payment_status, 'OVERDUE')
        self.assertEqual(invoice.qb_due_date, due)
        self.assertEqual(invoice.qb_email_status, 'NEED_TO_SEND')
        self.assertEqual(invoice.get_qb_payment_status_display_label(), 'Overdue 9 days')

    @patch('config.integrations.quickbooks.sync._fetch_quickbooks_invoices_by_ids')
    def test_refresh_linked_invoice_status_skips_settled_invoices_by_default(self, mock_fetch_invoices):
        user = Usuario.objects.create_user(username='qb-refresh-client-2', password='secret123', role='cliente')
        cliente = Cliente.objects.create(
            usuario=user,
            nombre_empresa='Refresh Customer 2 LLC',
            telefono='5550000003',
            direccion='789 Main',
            ciudad='Dallas',
            estado='TX',
            codigo_postal='75001',
            pais='USA',
            sales_tax_number='TX-3',
            certificado_tax='certificados/test.pdf',
        )
        import_quickbooks_invoice_record({
            'Id': 'QB-INV-REFRESH-OPEN',
            'DocNumber': 'LU100903',
            'CustomerRef': {'value': 'C-3', 'name': cliente.nombre_empresa},
            'TotalAmt': '100.00',
            'Balance': '100.00',
            'DueDate': (timezone.localdate() - timedelta(days=2)).isoformat(),
            'EmailStatus': 'NeedToSend',
        })
        import_quickbooks_invoice_record({
            'Id': 'QB-INV-REFRESH-PAID',
            'DocNumber': 'LU100904',
            'CustomerRef': {'value': 'C-3', 'name': cliente.nombre_empresa},
            'TotalAmt': '200.00',
            'Balance': '0.00',
            'DueDate': (timezone.localdate() - timedelta(days=30)).isoformat(),
            'EmailStatus': 'EmailSent',
        })
        paid_invoice = Invoice.objects.get(quickbooks_id='QB-INV-REFRESH-PAID')
        Invoice.objects.filter(pk=paid_invoice.pk).update(qb_payment_status='PAID', qb_email_status='EMAIL_SENT')

        mock_fetch_invoices.return_value = {
            'QB-INV-REFRESH-OPEN': {
                'Id': 'QB-INV-REFRESH-OPEN',
                'DocNumber': 'LU100903',
                'CustomerRef': {'value': 'C-3', 'name': cliente.nombre_empresa},
                'TotalAmt': '100.00',
                'Balance': '100.00',
                'DueDate': (timezone.localdate() - timedelta(days=2)).isoformat(),
                'EmailStatus': 'NeedToSend',
            },
        }

        result = refresh_linked_quickbooks_invoice_status()

        mock_fetch_invoices.assert_called_once()
        fetched_ids = mock_fetch_invoices.call_args.kwargs['invoice_ids']
        self.assertEqual(fetched_ids, ['QB-INV-REFRESH-OPEN'])
        self.assertEqual(result['linked_count'], 1)


@override_settings(
    QUICKBOOKS_CLIENT_ID='client-id',
    QUICKBOOKS_CLIENT_SECRET='client-secret',
    QUICKBOOKS_REDIRECT_URI='http://localhost:8000/quickbooks/callback',
    QUICKBOOKS_ENVIRONMENT='sandbox',
    QUICKBOOKS_SCOPES=('com.intuit.quickbooks.accounting',),
    QUICKBOOKS_API_MINOR_VERSION='75',
)
class QuickBooksIntegrationTests(TestCase):
    def setUp(self):
        self.user = Usuario.objects.create_user(
            username='qb-backoffice',
            password='secret123',
            role='backoffice',
        )
        self.client.force_login(self.user)
        self.customer_user = Usuario.objects.create_user(
            username='qb-customer',
            password='secret123',
            role='cliente',
            email='cliente@example.com',
        )
        self.cliente = Cliente.objects.create(
            usuario=self.customer_user,
            nombre_empresa='Cliente QuickBooks',
            telefono='5551239876',
            direccion='123 Main St',
            ciudad='Dallas',
            estado='TX',
            codigo_postal='75001',
            pais='USA',
            sales_tax_number='TX-123',
            certificado_tax='certificados/test.pdf',
            aprobado=True,
        )
        categoria = Categoria.objects.create(nombre='Tortillas')
        marca = Marca.objects.create(nombre='Marca QB')
        producto = Producto.objects.create(nombre='Tortilla 12', categoria=categoria, marca=marca, codigo_barras='7501234567890')
        self.presentacion = Presentacion.objects.create(
            producto=producto,
            nombre='Caja',
            unidades=12,
            tipo_contenido='unidades',
            precio_1=Decimal('15.00'),
            precio_2=Decimal('16.00'),
            precio_3=Decimal('17.00'),
            precio_4=Decimal('18.00'),
            precio_5=Decimal('19.00'),
        )
        pedido = Pedido.objects.create(
            cliente=self.cliente,
            origen='CLIENTE',
            estado='VERIFICADO_AJUSTADO',
            total=Decimal('45.00'),
        )
        PedidoItem.objects.create(
            pedido=pedido,
            presentacion=self.presentacion,
            cantidad_solicitada=3,
            cantidad=3,
            precio=Decimal('15.00'),
            subtotal=Decimal('45.00'),
        )
        self.invoice = generar_invoice_desde_picking(
            pedido=pedido,
            metodo_entrega='CUSTOMER_PICK_UP',
            driver=None,
            usuario=self.user,
        )
        self.adjustment_note = NotaAjuste.objects.create(
            cliente=self.cliente,
            invoice=self.invoice,
            tipo_documento='CREDITO',
            tipo_ajuste='FINANCIERO',
            estado='APROBADA',
            motivo='OTHER',
            tipo_credito='CREDIT_DUMP',
            descripcion='Sandbox credit note',
            monto=Decimal('10.00'),
            total=Decimal('10.00'),
            impacto_saldo=Decimal('10.00'),
        )

    def _activate_connection(self, **overrides):
        connection = QuickBooksConnection.get_solo()
        connection.realm_id = overrides.get('realm_id', '9130357992222806')
        connection.access_token = overrides.get('access_token', 'access-1')
        connection.refresh_token = overrides.get('refresh_token', 'refresh-1')
        connection.access_token_expires_at = overrides.get('access_token_expires_at', timezone.now() + timezone.timedelta(hours=1))
        connection.refresh_token_expires_at = overrides.get('refresh_token_expires_at', timezone.now() + timezone.timedelta(days=30))
        connection.connected_at = overrides.get('connected_at', timezone.now())
        connection.last_refreshed_at = overrides.get('last_refreshed_at', timezone.now())
        connection.last_error = overrides.get('last_error', '')
        connection.save()
        return connection

    def _json_response(self, payload, *, ok=True, status_code=200):
        return Mock(ok=ok, status_code=status_code, json=Mock(return_value=payload), text=str(payload))

    def _binary_response(self, content, *, ok=True, status_code=200, content_type='application/octet-stream'):
        response = Mock(ok=ok, status_code=status_code, content=content)
        response.headers = {'Content-Type': content_type}
        response.text = ''
        response.json.side_effect = ValueError('Binary response does not provide JSON.')
        return response

    def test_oauth_login_redirects_to_quickbooks_and_stores_state(self):
        response = self.client.get(reverse('quickbooks_login'))

        self.assertEqual(response.status_code, 302)
        self.assertIn('appcenter.intuit.com/connect/oauth2', response['Location'])
        self.assertIn('client_id=client-id', response['Location'])
        self.assertIn('redirect_uri=http%3A%2F%2Flocalhost%3A8000%2Fquickbooks%2Fcallback', response['Location'])
        self.assertIn('quickbooks_oauth_state', self.client.session)

    @patch('config.integrations.quickbooks.auth.requests.post')
    def test_callback_persists_tokens_and_realm(self, mock_post):
        session = self.client.session
        session['quickbooks_oauth_state'] = 'state-123'
        session.save()
        mock_post.return_value = Mock(
            ok=True,
            status_code=200,
            json=Mock(return_value={
                'access_token': 'access-1',
                'refresh_token': 'refresh-1',
                'token_type': 'bearer',
                'scope': 'com.intuit.quickbooks.accounting',
                'expires_in': 3600,
                'x_refresh_token_expires_in': 86400,
            }),
        )

        response = self.client.get(
            reverse('quickbooks_callback'),
            {'state': 'state-123', 'code': 'auth-code', 'realmId': '9130357992222806'},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        connection = QuickBooksConnection.get_solo()
        self.assertEqual(connection.realm_id, '9130357992222806')

    @patch('config.integrations.quickbooks.auth.requests.post')
    def test_callback_with_trailing_slash_persists_tokens(self, mock_post):
        session = self.client.session
        session['quickbooks_oauth_state'] = 'state-trailing'
        session.save()
        mock_post.return_value = Mock(
            ok=True,
            status_code=200,
            json=Mock(return_value={
                'access_token': 'access-trailing',
                'refresh_token': 'refresh-trailing',
                'token_type': 'bearer',
                'scope': 'com.intuit.quickbooks.accounting',
                'expires_in': 3600,
                'x_refresh_token_expires_in': 86400,
            }),
        )

        response = self.client.get(
            '/quickbooks/callback/',
            {'state': 'state-trailing', 'code': 'auth-code-trailing', 'realmId': '9130357992222807'},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        connection = QuickBooksConnection.get_solo()
        self.assertEqual(connection.realm_id, '9130357992222807')
        self.assertEqual(connection.access_token, 'access-trailing')
        self.assertEqual(connection.refresh_token, 'refresh-trailing')

    @patch('config.integrations.quickbooks.client.requests.request')
    @patch('config.integrations.quickbooks.auth.requests.post')
    def test_test_connection_refreshes_expired_token(self, mock_post, mock_request):
        self._activate_connection(
            access_token='expired-token',
            refresh_token='refresh-1',
            access_token_expires_at=timezone.now() - timezone.timedelta(minutes=5),
        )
        mock_post.return_value = Mock(
            ok=True,
            status_code=200,
            json=Mock(return_value={
                'access_token': 'access-2',
                'refresh_token': 'refresh-2',
                'token_type': 'bearer',
                'scope': 'com.intuit.quickbooks.accounting',
                'expires_in': 3600,
                'x_refresh_token_expires_in': 86400,
            }),
        )
        mock_request.return_value = Mock(
            ok=True,
            status_code=200,
            json=Mock(return_value={
                'CompanyInfo': {
                    'CompanyName': 'La Tortilla Sandbox',
                    'Id': '1',
                }
            }),
        )

        response = self.client.get(reverse('quickbooks_test_connection'))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['connected'])
        self.assertEqual(payload['company']['CompanyName'], 'La Tortilla Sandbox')
        connection = QuickBooksConnection.get_solo()
        self.assertEqual(connection.access_token, 'access-2')
        self.assertEqual(connection.refresh_token, 'refresh-2')

    @patch('config.integrations.quickbooks.auth.requests.post')
    def test_maintain_quickbooks_connection_refreshes_stale_tokens(self, mock_post):
        self._activate_connection(
            access_token='valid-token',
            refresh_token='refresh-1',
            access_token_expires_at=timezone.now() + timezone.timedelta(hours=1),
        )
        connection = QuickBooksConnection.get_solo()
        connection.last_refreshed_at = timezone.now() - timezone.timedelta(hours=24)
        connection.save(update_fields=['last_refreshed_at', 'updated_at'])
        mock_post.return_value = Mock(
            ok=True,
            status_code=200,
            json=Mock(return_value={
                'access_token': 'access-maintained',
                'refresh_token': 'refresh-maintained',
                'token_type': 'bearer',
                'scope': 'com.intuit.quickbooks.accounting',
                'expires_in': 3600,
                'x_refresh_token_expires_in': 8640000,
            }),
        )

        from config.integrations.quickbooks.services import maintain_quickbooks_connection

        result = maintain_quickbooks_connection()

        self.assertTrue(result['refreshed'])
        connection.refresh_from_db()
        self.assertEqual(connection.access_token, 'access-maintained')
        self.assertEqual(connection.refresh_token, 'refresh-maintained')

    @patch('config.integrations.quickbooks.auth.requests.post')
    def test_transient_refresh_error_does_not_clear_tokens(self, mock_post):
        self._activate_connection(
            access_token='expired-token',
            refresh_token='refresh-1',
            access_token_expires_at=timezone.now() - timezone.timedelta(minutes=5),
        )
        mock_post.side_effect = [
            Mock(
                ok=False,
                status_code=503,
                json=Mock(return_value={'error': 'service_unavailable'}),
                text='service unavailable',
            ),
            Mock(
                ok=True,
                status_code=200,
                json=Mock(return_value={
                    'access_token': 'access-retry',
                    'refresh_token': 'refresh-retry',
                    'token_type': 'bearer',
                    'scope': 'com.intuit.quickbooks.accounting',
                    'expires_in': 3600,
                    'x_refresh_token_expires_in': 8640000,
                }),
            ),
        ]

        from config.integrations.quickbooks.services import ensure_valid_access_token

        connection = ensure_valid_access_token(force_refresh=True)
        self.assertEqual(connection.access_token, 'access-retry')
        self.assertEqual(connection.refresh_token, 'refresh-retry')

    @patch('config.integrations.quickbooks.auth.requests.post')
    def test_quickbooks_center_preview_invalid_refresh_token_marks_connection_inactive(self, mock_post):
        self._activate_connection(
            access_token='expired-token',
            refresh_token='refresh-1',
            access_token_expires_at=timezone.now() - timezone.timedelta(minutes=5),
        )
        mock_post.return_value = Mock(
            ok=False,
            status_code=400,
            json=Mock(return_value={
                'error': 'invalid_grant',
                'error_description': 'Incorrect or invalid refresh token',
            }),
            text='{"error":"invalid_grant"}',
        )

        response = self.client.get(reverse('quickbooks_center'), {'preview': 'credit_memos', 'preview_limit': '8'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['quickbooks_preview_error'], 'QuickBooks connection expired. Reconnect QuickBooks to continue.')
        self.assertFalse(response.context['quickbooks_status']['is_active'])
        connection = QuickBooksConnection.get_solo()
        self.assertEqual(connection.access_token, '')
        self.assertEqual(connection.refresh_token, '')
        self.assertEqual(connection.last_error, 'QuickBooks connection expired. Reconnect QuickBooks to continue.')

    def test_quickbooks_center_admin_shows_organized_navigation_with_sales_and_operations(self):
        admin_user = Usuario.objects.create_user(
            username='qb-admin',
            password='secret123',
            role='admin',
        )
        self.client.force_login(admin_user)

        response = self.client.get(reverse('quickbooks_center'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'href="{reverse("quickbooks_center")}"', count=1, html=False)
        self.assertContains(response, 'Customers & Sales', html=False)
        self.assertContains(response, f'href="{reverse("vendedores_clientes")}"', html=False)
        self.assertContains(response, f'href="{reverse("tomar_pedido")}"', html=False)
        self.assertContains(response, f'href="{reverse("backoffice_dashboard")}"', html=False)

    def test_quickbooks_center_preview_tabs_keep_live_preview_anchor(self):
        response = self.client.get(reverse('quickbooks_center'), {'preview': 'customers', 'preview_limit': '8'})

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('#quickbooks-live-preview', content)

    def test_quickbooks_database_backup_downloads_full_gzipped_dump(self):
        Cotizacion.objects.create(
            cliente=self.cliente,
            vendedor=self.user,
            estado='BORRADOR',
            total=Decimal('45.00'),
        )

        response = self.client.post(reverse('quickbooks_database_backup'))
        self.assertEqual(response.status_code, 302)
        response = self.client.get(response.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/gzip')
        self.assertIn('attachment;', response['Content-Disposition'])
        self.assertIn('.json.gz', response['Content-Disposition'])
        backup_payload = gzip.decompress(b''.join(response.streaming_content)).decode('utf-8')
        backup_rows = json.loads(backup_payload)
        backup_models = {row['model'] for row in backup_rows}
        self.assertIn('productos.producto', backup_models)
        self.assertIn('pedidos.pedido', backup_models)
        self.assertIn('facturacion.invoice', backup_models)
        self.assertIn('facturacion.notaajuste', backup_models)
        self.assertIn('cotizaciones.cotizacion', backup_models)

    def test_database_backups_center_lists_existing_backups_with_redownload_link(self):
        backup_response = self.client.post(reverse('quickbooks_database_backup'))
        self.assertEqual(backup_response.status_code, 302)
        backup_response = self.client.get(backup_response.url)
        backup_name = backup_response['Content-Disposition'].split('filename="', 1)[1].rstrip('"')

        response = self.client.get(reverse('database_backups_center'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, backup_name)
        self.assertContains(response, reverse('quickbooks_database_backup_download', args=[backup_name]))

    def test_database_backups_center_can_save_backup_schedule_preference(self):
        response = self.client.post(reverse('update_backup_schedule_preference'), {'backup_schedule': 'monthly'}, follow=True)

        self.assertEqual(response.status_code, 200)
        connection = get_connection()
        self.assertEqual(connection.sync_state.get('backup_schedule'), 'monthly')
        self.assertContains(response, 'monthly', status_code=200)

    def test_system_backup_uses_saved_backup_schedule_label(self):
        connection = get_connection()
        state = dict(connection.sync_state or {})
        state['backup_schedule'] = 'monthly'
        connection.sync_state = state
        connection.save(update_fields=['sync_state', 'updated_at'])

        response = self.client.post(reverse('system_backup'))
        backup_result = self._wait_for_backup_job(response)
        self.assertIn('ltg-system-backup-monthly-', backup_result.get('backup_name', ''))
        download_response = self.client.get(reverse('system_backup_download', args=[backup_result['backup_name']]))
        self.assertEqual(download_response.status_code, 200)
        self.assertIn('ltg-system-backup-monthly-', download_response['Content-Disposition'])

    def test_backup_database_command_creates_labeled_backup_file(self):
        stdout = StringIO()

        call_command('backup_database', '--label=weekly', '--keep=5', stdout=stdout)

        output = stdout.getvalue()
        self.assertIn('Database backup created: ltg-database-backup-weekly-', output)
        backup_dir = Path(default_storage.location) / 'backups' / 'database'
        self.assertTrue(any(path.name.startswith('ltg-database-backup-weekly-') for path in backup_dir.glob('*.json.gz')))

    @patch('config.integrations.management.commands.run_scheduled_backups.prune_database_backups', return_value=[])
    @patch('config.integrations.management.commands.run_scheduled_backups.create_system_backup_file', return_value=('backups/system/ltg-system-backup-daily-20260520-120000.tar.gz', 'ltg-system-backup-daily-20260520-120000.tar.gz'))
    def test_run_scheduled_backups_creates_daily_backup_when_due(self, mock_create_system_backup, _mock_prune):
        connection = get_connection()
        state = dict(connection.sync_state or {})
        state['backup_schedule'] = 'daily'
        connection.sync_state = state
        connection.save(update_fields=['sync_state', 'updated_at'])
        stdout = StringIO()

        call_command('run_scheduled_backups', '--today=2026-05-20', stdout=stdout)

        self.assertIn('Automatic system backup created: ltg-system-backup-daily-', stdout.getvalue())
        mock_create_system_backup.assert_called_once_with(label='daily')
        connection.refresh_from_db()
        self.assertEqual(connection.sync_state['backup_automation']['system']['last_run_on'], '2026-05-20')

    @patch('config.integrations.management.commands.run_scheduled_backups.create_system_backup_file')
    def test_run_scheduled_backups_skips_weekly_backup_when_today_is_not_monday(self, mock_create_system_backup):
        connection = get_connection()
        state = dict(connection.sync_state or {})
        state['backup_schedule'] = 'weekly'
        connection.sync_state = state
        connection.save(update_fields=['sync_state', 'updated_at'])
        stdout = StringIO()

        call_command('run_scheduled_backups', '--today=2026-05-20', stdout=stdout)

        self.assertIn('Skipped system backup. Configured cadence: weekly.', stdout.getvalue())
        mock_create_system_backup.assert_not_called()

    @patch('config.integrations.management.commands.run_scheduled_backups.create_system_backup_file')
    def test_run_scheduled_backups_skips_duplicate_run_on_same_day(self, mock_create_system_backup):
        connection = get_connection()
        state = dict(connection.sync_state or {})
        state['backup_schedule'] = 'daily'
        state['backup_automation'] = {
            'system': {
                'last_run_on': '2026-05-20',
                'last_backup_name': 'ltg-system-backup-daily-20260520-080000.tar.gz',
                'schedule': 'daily',
            }
        }
        connection.sync_state = state
        connection.save(update_fields=['sync_state', 'updated_at'])
        stdout = StringIO()

        call_command('run_scheduled_backups', '--today=2026-05-20', stdout=stdout)

        self.assertIn('Last successful run: 2026-05-20.', stdout.getvalue())
        mock_create_system_backup.assert_not_called()

    @patch('config.integrations.management.commands.run_scheduled_backups.prune_database_backups', return_value=[])
    @patch('config.integrations.management.commands.run_scheduled_backups.create_system_backup_file', return_value=('backups/system/ltg-system-backup-monthly-20260601-010000.tar.gz', 'ltg-system-backup-monthly-20260601-010000.tar.gz'))
    def test_run_scheduled_backups_creates_monthly_backup_on_first_day(self, mock_create_system_backup, _mock_prune):
        connection = get_connection()
        state = dict(connection.sync_state or {})
        state['backup_schedule'] = 'monthly'
        connection.sync_state = state
        connection.save(update_fields=['sync_state', 'updated_at'])
        stdout = StringIO()

        call_command('run_scheduled_backups', '--today=2026-06-01', stdout=stdout)

        self.assertIn('Automatic system backup created: ltg-system-backup-monthly-', stdout.getvalue())
        mock_create_system_backup.assert_called_once_with(label='monthly')

    def test_backup_system_command_creates_media_inclusive_snapshot(self):
        default_storage.save('invoice-notes/backup-proof.txt', ContentFile(b'backup media proof'))
        stdout = StringIO()

        call_command('backup_system', '--label=full', '--keep=5', stdout=stdout)

        output = stdout.getvalue()
        self.assertIn('System backup created: ltg-system-backup-full-', output)
        backup_dir = Path(default_storage.location) / 'backups' / 'system'
        self.assertTrue(any(path.name.startswith('ltg-system-backup-full-') for path in backup_dir.glob('*.tar.gz')))

    def test_backup_listing_falls_back_to_timestamp_when_storage_has_no_modified_time(self):
        class RemoteLikeStorage:
            def get_modified_time(self, _name):
                raise NotImplementedError()

        modified_at = _backup_modified_time(
            RemoteLikeStorage(),
            'backups/system/ltg-system-backup-prod-20260520-141500.tar.gz',
            'ltg-system-backup-prod-20260520-141500.tar.gz',
        )

        self.assertEqual(modified_at.year, 2026)
        self.assertEqual(modified_at.month, 5)
        self.assertEqual(modified_at.day, 20)

    def _wait_for_restore_job(self, response):
        self.assertEqual(response.status_code, 302)
        job_id = parse_qs(urlparse(response.url).query).get('restore_job', [None])[0]
        self.assertTrue(job_id)
        for _ in range(200):
            status_response = self.client.get(reverse('backup_restore_status', args=[job_id]))
            self.assertEqual(status_response.status_code, 200)
            data = status_response.json()
            if data.get('status') == 'completed':
                return data
            if data.get('status') == 'failed':
                self.fail(data.get('error') or 'Restore job failed.')
            time.sleep(0.05)
        self.fail('Restore job did not complete in time.')

    def _wait_for_backup_job(self, response):
        self.assertEqual(response.status_code, 302)
        job_id = parse_qs(urlparse(response.url).query).get('backup_job', [None])[0]
        self.assertTrue(job_id)
        for _ in range(400):
            status_response = self.client.get(reverse('backup_job_status', args=[job_id]))
            self.assertEqual(status_response.status_code, 200)
            data = status_response.json()
            if data.get('status') == 'completed':
                return data
            if data.get('status') == 'failed':
                self.fail(data.get('error') or 'Backup job failed.')
            time.sleep(0.05)
        self.fail('Backup job did not complete in time.')

    def test_restore_backup_upload_restores_database_from_downloaded_file(self):
        self.client.force_login(self.user)
        backup_response = self.client.post(reverse('quickbooks_database_backup'))
        self.assertEqual(backup_response.status_code, 302)
        backup_response = self.client.get(backup_response.url)
        backup_name = backup_response['Content-Disposition'].split('filename="', 1)[1].rstrip('"')
        backup_bytes = b''.join(backup_response.streaming_content)

        Producto.objects.filter(pk=self.presentacion.producto_id).delete()

        with tempfile.NamedTemporaryFile(suffix='.json.gz', delete=False) as temp_file:
            temp_file.write(backup_bytes)
            temp_path = temp_file.name

        try:
            with open(temp_path, 'rb') as uploaded:
                response = self.client.post(
                    reverse('restore_backup_upload'),
                    {
                        'confirmation': 'RESTORE',
                        'replace_current_data': 'yes',
                        'backup_file': uploaded,
                    },
                )
        finally:
            Path(temp_path).unlink(missing_ok=True)

        restore_result = self._wait_for_restore_job(response)
        self.assertIn(backup_name, restore_result.get('backup_name', ''))
        self.assertTrue(Producto.objects.filter(nombre='Tortilla 12').exists())
        self.assertTrue(
            any(path.name == backup_name for path in (Path(default_storage.location) / 'backups' / 'database').glob('*.json.gz'))
        )

    def test_database_backups_center_shows_manual_backup_and_upload_restore(self):
        response = self.client.get(reverse('database_backups_center'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse('create_database_backup_stored'))
        self.assertContains(response, reverse('restore_backup_upload'))
        self.assertContains(response, reverse('restore_backup_from_center'))
        self.assertContains(response, 'restore-job-overlay')

    def test_database_backups_center_restore_action_replaces_system_from_selected_backup(self):
        self.client.force_login(self.user)
        media_path = 'invoice-notes/ui-restore-proof.txt'
        default_storage.save(media_path, ContentFile(b'ui restore proof'))
        system_backup_dir = Path(default_storage.location) / 'backups' / 'system'
        existing_names = {path.name for path in system_backup_dir.glob('*.tar.gz')}
        call_command('backup_system', '--label=ui-restore')
        created_files = sorted(
            [path for path in system_backup_dir.glob('*.tar.gz') if path.name not in existing_names],
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        backup_name = created_files[0].name

        Producto.objects.filter(pk=self.presentacion.producto_id).delete()
        default_storage.delete(media_path)

        response = self.client.post(
            reverse('restore_backup_from_center'),
            {
                'backup_name': backup_name,
                'confirmation': 'RESTORE',
                'replace_current_data': 'yes',
            },
        )

        restore_result = self._wait_for_restore_job(response)
        self.assertIn(backup_name, restore_result.get('backup_name', ''))
        self.assertTrue(Producto.objects.filter(nombre='Tortilla 12').exists())
        self.assertTrue(default_storage.exists(media_path))

    @override_settings(QUICKBOOKS_IMPORT_ACCOUNTING_DOCUMENTS=True)
    @patch('config.integrations.quickbooks.client.requests.request')
    def test_pull_accounting_documents_matches_local_invoice_and_credit_note(self, mock_request):
        self._activate_connection()
        mock_request.side_effect = [
            self._json_response({'QueryResponse': {'Invoice': [{'Id': 'QB-INV-PULL-1', 'DocNumber': self.invoice.numero, 'CustomerRef': {'name': self.cliente.nombre_empresa}, 'TotalAmt': '45.00', 'Balance': '12.50', 'MetaData': {'LastUpdatedTime': '2026-05-13T10:00:00+00:00'}}]}}),
            self._json_response({'QueryResponse': {'CreditMemo': [{'Id': 'QB-CM-PULL-1', 'DocNumber': self.adjustment_note.numero, 'CustomerRef': {'name': self.cliente.nombre_empresa}, 'TotalAmt': '10.00', 'Balance': '0.00', 'MetaData': {'LastUpdatedTime': '2026-05-13T10:05:00+00:00'}}]}}),
        ]

        response = self.client.post(reverse('quickbooks_import_accounting_documents_to_local'), {'limit': '10'})

        self.assertEqual(response.status_code, 200)
        self.invoice.refresh_from_db()
        self.adjustment_note.refresh_from_db()
        self.assertEqual(self.invoice.quickbooks_id, 'QB-INV-PULL-1')
        self.assertEqual(self.adjustment_note.quickbooks_id, 'QB-CM-PULL-1')
        self.assertEqual(self.invoice.total_neto, Decimal('45.00'))
        self.assertEqual(self.invoice.saldo_cliente, Decimal('12.50'))
        self.assertEqual(self.adjustment_note.total, Decimal('10.00'))
        self.assertEqual(QuickBooksImportConflict.objects.count(), 0)

    @override_settings(QUICKBOOKS_IMPORT_ACCOUNTING_DOCUMENTS=True)
    @patch('config.integrations.quickbooks.client.requests.request')
    def test_pull_accounting_documents_queues_unmatched_conflict(self, mock_request):
        self._activate_connection()
        mock_request.side_effect = [
            self._json_response({'QueryResponse': {'Invoice': [{'Id': 'QB-INV-UNMATCHED', 'DocNumber': 'QB-EXT-1001', 'CustomerRef': {'name': 'External QB Customer'}}]}}),
            self._json_response({'QueryResponse': {'CreditMemo': []}}),
        ]

        response = self.client.post(reverse('quickbooks_import_accounting_documents_to_local'), {'limit': '10'})

        self.assertEqual(response.status_code, 200)
        conflict = QuickBooksImportConflict.objects.get(entity_type='INVOICE', quickbooks_id='QB-INV-UNMATCHED')
        self.assertEqual(conflict.status, 'CONFLICT')
        self.assertEqual(conflict.doc_number, 'QB-EXT-1001')

        view_response = self.client.get(reverse('quickbooks_import_conflicts'))
        self.assertContains(view_response, 'QB-EXT-1001')
        self.assertContains(view_response, 'External QB Customer')

    @override_settings(QUICKBOOKS_IMPORT_ACCOUNTING_DOCUMENTS=True)
    @patch('config.integrations.quickbooks.client.requests.request')
    @patch('config.integrations.quickbooks.client.requests.request')
    def test_pull_accounting_documents_creates_invoice_and_credit_notes_when_customer_exists(self, mock_request):
        self._activate_connection()
        self.cliente.quickbooks_id = 'QB-CUSTOMER-1'
        self.cliente.save(update_fields=['quickbooks_id'])
        self.customer_user.quickbooks_id = 'QB-CUSTOMER-1'
        self.customer_user.save(update_fields=['quickbooks_id'])
        mock_request.side_effect = [
            self._json_response({'QueryResponse': {'Invoice': [{'Id': 'QB-INV-AUTO-1', 'DocNumber': 'QB-INV-1001', 'CustomerRef': {'value': 'QB-CUSTOMER-1', 'name': self.cliente.nombre_empresa}, 'TotalAmt': '45.00', 'Balance': '12.50', 'PrivateNote': 'Imported invoice fallback'}]}}),
            self._json_response({'QueryResponse': {'CreditMemo': [{'Id': 'QB-CM-AUTO-1', 'DocNumber': 'QB-CM-1001', 'CustomerRef': {'value': 'QB-CUSTOMER-1', 'name': self.cliente.nombre_empresa}, 'TotalAmt': '10.00', 'Balance': '0.00', 'PrivateNote': 'Imported credit fallback'}]}}),
        ]

        response = self.client.post(reverse('quickbooks_import_accounting_documents_to_local'), {'limit': '10'})

        self.assertEqual(response.status_code, 200)
        invoice = Invoice.objects.get(quickbooks_id='QB-INV-AUTO-1')
        credit_note = NotaAjuste.objects.get(quickbooks_id='QB-CM-AUTO-1')
        self.assertEqual(invoice.numero, 'QB-INV-1001')
        self.assertEqual(invoice.total_neto, Decimal('45.00'))
        self.assertEqual(invoice.saldo_cliente, Decimal('12.50'))
        self.assertEqual(invoice.cliente, self.cliente)
        self.assertEqual(credit_note.tipo_documento, 'CREDITO')
        self.assertEqual(credit_note.tipo_ajuste, 'FINANCIERO')
        self.assertEqual(credit_note.tipo_credito, 'CREDIT_DUMP')
        self.assertEqual(credit_note.total, Decimal('10.00'))
        self.assertEqual(credit_note.cliente, self.cliente)
        self.assertFalse(QuickBooksImportConflict.objects.filter(quickbooks_id__in=['QB-INV-AUTO-1', 'QB-CM-AUTO-1']).exists())

    @override_settings(QUICKBOOKS_CATALOG_ONLY_MODE=True)
    def test_accounting_import_disabled_by_default(self):
        self._activate_connection()
        response = self.client.post(reverse('quickbooks_import_accounting_documents_to_local'), {'limit': '10'})
        self.assertEqual(response.status_code, 403)

    @override_settings(QUICKBOOKS_CATALOG_ONLY_MODE=True, QUICKBOOKS_IMPORT_ACCOUNTING_DOCUMENTS=True)
    @patch('config.integrations.quickbooks.client.requests.request')
    def test_accounting_import_enabled_when_setting_true(self, mock_request):
        self._activate_connection()
        self.cliente.quickbooks_id = 'QB-CUSTOMER-1'
        self.cliente.save(update_fields=['quickbooks_id'])
        mock_request.side_effect = [
            self._json_response({'QueryResponse': {'Invoice': []}}),
            self._json_response({'QueryResponse': {'CreditMemo': []}}),
        ]

        response = self.client.post(reverse('quickbooks_import_accounting_documents_to_local'), {'limit': '10'})

        self.assertEqual(response.status_code, 200)

    @override_settings(QUICKBOOKS_IMPORT_ACCOUNTING_DOCUMENTS=True)
    @patch('config.integrations.quickbooks.client.requests.request')
    def test_pull_accounting_documents_imports_missing_customer_from_quickbooks(self, mock_request):
        self._activate_connection()
        mock_request.side_effect = [
            self._json_response({'QueryResponse': {'Invoice': [{
                'Id': 'QB-INV-MISSING-CUST',
                'DocNumber': '1061',
                'CustomerRef': {'value': 'QB-CUSTOMER-1061', 'name': 'Customer 1061 LLC'},
                'TotalAmt': '25.00',
                'Balance': '25.00',
            }]}}),
            self._json_response({'QueryResponse': {'CreditMemo': []}}),
            self._json_response({'Customer': {
                'Id': 'QB-CUSTOMER-1061',
                'DisplayName': 'Customer 1061 LLC',
                'CompanyName': 'Customer 1061 LLC',
                'PrimaryEmailAddr': {'Address': 'customer1061@example.com'},
                'PrimaryPhone': {'FreeFormNumber': '5550001061'},
                'BillAddr': {'Line1': '1061 Main', 'City': 'Dallas', 'CountrySubDivisionCode': 'TX', 'PostalCode': '75001', 'Country': 'USA'},
            }}),
        ]

        response = self.client.post(reverse('quickbooks_import_accounting_documents_to_local'), {'limit': '10'})

        self.assertEqual(response.status_code, 200)
        invoice = Invoice.objects.get(quickbooks_id='QB-INV-MISSING-CUST')
        customer = Cliente.objects.get(quickbooks_id='QB-CUSTOMER-1061')
        self.assertEqual(invoice.numero, '1061')
        self.assertEqual(invoice.cliente, customer)
        self.assertEqual(customer.nombre_empresa, 'Customer 1061 LLC')
        self.assertFalse(QuickBooksImportConflict.objects.filter(quickbooks_id='QB-INV-MISSING-CUST').exists())

    @override_settings(QUICKBOOKS_IMPORT_ACCOUNTING_DOCUMENTS=True)
    @patch('config.integrations.quickbooks.client.requests.request')
    def test_pull_accounting_documents_skips_invoice_with_deleted_customer(self, mock_request):
        self._activate_connection()
        mock_request.side_effect = [
            self._json_response({'QueryResponse': {'Invoice': [{
                'Id': 'QB-INV-SKIP-DELETED',
                'DocNumber': '3001',
                'CustomerRef': {'value': 'QB-CUST-DELETED', 'name': '(BHM) FLORES PRODUCE (deleted)'},
                'TotalAmt': '75.00',
                'Balance': '75.00',
            }]}}),
            self._json_response({'QueryResponse': {'CreditMemo': []}}),
        ]

        response = self.client.post(reverse('quickbooks_import_accounting_documents_to_local'), {'limit': '10'})

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Invoice.objects.filter(quickbooks_id='QB-INV-SKIP-DELETED').exists())
        self.assertFalse(QuickBooksImportConflict.objects.filter(quickbooks_id='QB-INV-SKIP-DELETED').exists())

    @patch('config.integrations.quickbooks.client.requests.request')
    def test_import_items_to_local_sets_physical_stock_from_qty_on_hand(self, mock_request):
        self._activate_connection()
        mock_request.return_value = self._json_response({
            'QueryResponse': {
                'Item': [
                    {
                        'Id': 'QB-ITEM-STOCK',
                        'Name': 'cargador fake',
                        'Type': 'Inventory',
                        'Description': 'QuickBooks inventory item',
                        'Sku': 'CHG-FAKE',
                        'UnitPrice': 12.5,
                        'PurchaseCost': 6.0,
                        'QtyOnHand': 18,
                        'Active': True,
                    }
                ]
            }
        })

        response = self.client.post(reverse('quickbooks_import_items_to_local'), {'limit': '10'})

        self.assertEqual(response.status_code, 200)
        presentacion = Presentacion.objects.get(quickbooks_id='QB-ITEM-STOCK')
        stock = StockPresentacion.objects.get(presentacion=presentacion)
        self.assertEqual(stock.stock_fisico, 18)
        self.assertEqual(stock.stock_disponible, 18)

    @override_settings(QUICKBOOKS_CATALOG_ONLY_MODE=True)
    @patch('config.integrations.quickbooks.client.requests.request')
    def test_catalog_only_mode_allows_customer_import(self, mock_request):
        self._activate_connection()
        mock_request.return_value = self._json_response({
            'QueryResponse': {
                'Customer': [
                    {
                        'Id': 'QB-CUST-CATALOG-ONLY',
                        'DisplayName': 'Catalog Only Customer',
                        'CompanyName': 'Catalog Only Customer LLC',
                    }
                ]
            }
        })

        response = self.client.post(reverse('quickbooks_import_customers_to_local'), {'limit': '10'})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Cliente.objects.filter(quickbooks_id='QB-CUST-CATALOG-ONLY').exists())

    @patch('config.integrations.quickbooks.client.requests.request')
    def test_import_customers_skips_inactive_records(self, mock_request):
        self._activate_connection()
        mock_request.return_value = self._json_response({
            'QueryResponse': {
                'Customer': [
                    {
                        'Id': 'QB-CUST-SKIP-INACTIVE',
                        'DisplayName': 'Inactive Skip Customer',
                        'CompanyName': 'Inactive Skip Customer LLC',
                        'Active': False,
                    }
                ]
            }
        })

        response = self.client.post(reverse('quickbooks_import_customers_to_local'), {'limit': '10'})

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Cliente.objects.filter(quickbooks_id='QB-CUST-SKIP-INACTIVE').exists())


@override_settings(
    QUICKBOOKS_CLIENT_ID='client-id',
    QUICKBOOKS_CLIENT_SECRET='client-secret',
    QUICKBOOKS_REDIRECT_URI='http://localhost:8000/quickbooks/callback',
    QUICKBOOKS_ENVIRONMENT='sandbox',
    QUICKBOOKS_SCOPES=('com.intuit.quickbooks.accounting',),
    QUICKBOOKS_API_MINOR_VERSION='75',
)
class DatabaseRestoreCommandTests(QuickBooksIntegrationTests):
    reset_sequences = True

    def _create_backup(self, label=''):
        stdout = StringIO()
        backup_dir = Path(default_storage.location) / 'backups' / 'database'
        existing_names = {path.name for path in backup_dir.glob('*.json.gz')}
        if label:
            call_command('backup_database', f'--label={label}', stdout=stdout)
        else:
            call_command('backup_database', stdout=stdout)
        created_files = sorted(
            [path for path in backup_dir.glob('*.json.gz') if path.name not in existing_names],
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        backup_name = created_files[0].name
        return stdout, backup_name

    @patch('config.integrations.quickbooks.client.requests.request')
    def test_import_customers_returns_preview_data(self, mock_request):
        self._activate_connection()
        mock_request.return_value = self._json_response({
            'QueryResponse': {
                'Customer': [
                    {'Id': '501', 'DisplayName': 'Sandbox Customer'}
                ]
            }
        })

        response = self.client.get(reverse('quickbooks_import_customers'))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['result']['count'], 1)
        self.assertEqual(payload['result']['customers'][0]['Id'], '501')

    @patch('config.integrations.quickbooks.client.requests.request')
    def test_import_customers_to_local_creates_customer_and_user(self, mock_request):
        self._activate_connection()
        mock_request.return_value = self._json_response({
            'QueryResponse': {
                'Customer': [
                    {
                        'Id': 'QB-CUST-1',
                        'DisplayName': 'Imported QB Contact',
                        'CompanyName': 'Imported QB Customer',
                        'PrimaryEmailAddr': {'Address': 'qb-imported@example.com'},
                        'PrimaryPhone': {'FreeFormNumber': '5557771212'},
                        'Balance': '239.00',
                        'BillAddr': {
                            'Line1': '77 Import Ave',
                            'City': 'Houston',
                            'CountrySubDivisionCode': 'TX',
                            'PostalCode': '77001',
                            'Country': 'USA',
                        },
                    }
                ]
            }
        })

        response = self.client.post(reverse('quickbooks_import_customers_to_local'), {'limit': '10'})

        self.assertEqual(response.status_code, 200)
        payload = response.json()['result']
        self.assertEqual(payload['created_count'], 1)
        imported = Cliente.objects.get(quickbooks_id='QB-CUST-1')
        self.assertEqual(imported.nombre_empresa, 'Imported QB Customer')
        self.assertEqual(imported.balance, Decimal('239.00'))
        self.assertEqual(imported.due_balance, Decimal('239.00'))
        self.assertEqual(imported.customer_credit_balance, Decimal('0.00'))
        self.assertEqual(imported.usuario.first_name, 'Imported QB Contact')
        self.assertEqual(imported.usuario.email, 'qb-imported@example.com')
        self.assertEqual(imported.sync_status, 'SYNCED')

    @patch('config.integrations.quickbooks.client.requests.request')
    def test_import_customers_to_local_preserves_negative_quickbooks_credit_balance(self, mock_request):
        self._activate_connection()
        mock_request.return_value = self._json_response({
            'QueryResponse': {
                'Customer': [
                    {
                        'Id': 'QB-CUST-CREDIT',
                        'DisplayName': 'Credit Customer',
                        'CompanyName': 'Credit Customer LLC',
                        'PrimaryEmailAddr': {'Address': 'credit@example.com'},
                        'PrimaryPhone': {'FreeFormNumber': '5557779999'},
                        'Balance': '-125.50',
                        'BillAddr': {
                            'Line1': '88 Credit Ave',
                            'City': 'Houston',
                            'CountrySubDivisionCode': 'TX',
                            'PostalCode': '77002',
                            'Country': 'USA',
                        },
                    }
                ]
            }
        })

        response = self.client.post(reverse('quickbooks_import_customers_to_local'), {'limit': '10'})

        self.assertEqual(response.status_code, 200)
        imported = Cliente.objects.get(quickbooks_id='QB-CUST-CREDIT')
        self.assertEqual(imported.balance, Decimal('-125.50'))
        self.assertEqual(imported.due_balance, Decimal('0.00'))
        self.assertEqual(imported.customer_credit_balance, Decimal('125.50'))

    @patch('config.integrations.quickbooks.client.requests.request')
    def test_import_customers_to_local_without_limit_imports_all_available_pages(self, mock_request):
        self._activate_connection()

        def request_side_effect(method, url, **kwargs):
            parsed_url = urlparse(url)
            if not parsed_url.path.endswith('/query'):
                raise AssertionError(f'Unexpected QuickBooks request: {method} {url}')

            query = kwargs.get('params', {}).get('query', '')
            if 'from Customer' not in query:
                raise AssertionError(f'Unexpected QuickBooks query: {query}')
            if 'startposition 1' in query:
                customers = [
                    {
                        'Id': str(index),
                        'DisplayName': f'Customer {index}',
                        'CompanyName': f'Company {index}',
                        'PrimaryPhone': {'FreeFormNumber': '5550000000'},
                    }
                    for index in range(1, 101)
                ]
                return self._json_response({'QueryResponse': {'Customer': customers}})
            if 'startposition 101' in query:
                return self._json_response({'QueryResponse': {'Customer': [
                    {
                        'Id': '101',
                        'DisplayName': 'Customer 101',
                        'CompanyName': 'Company 101',
                        'PrimaryPhone': {'FreeFormNumber': '5550000101'},
                    }
                ]}})
            return self._json_response({'QueryResponse': {'Customer': []}})

        mock_request.side_effect = request_side_effect

        response = self.client.post(reverse('quickbooks_import_customers_to_local'))

        self.assertEqual(response.status_code, 200)
        payload = response.json()['result']
        self.assertEqual(payload['count'], 101)
        self.assertEqual(payload['created_count'], 101)
        self.assertEqual(Cliente.objects.filter(quickbooks_id__isnull=False).count(), 102)

    @patch('config.integrations.quickbooks.client.requests.request')
    def test_import_items_to_local_creates_product_and_presentation(self, mock_request):
        self._activate_connection()
        def request_side_effect(method, url, **kwargs):
            parsed_url = urlparse(url)
            if parsed_url.path.endswith('/query'):
                query = kwargs.get('params', {}).get('query', '')
                if 'from Item' in query:
                    return self._json_response({
                        'QueryResponse': {
                            'Item': [
                                {
                                    'Id': 'QB-ITEM-1',
                                    'Name': 'Imported Salsa Bottle',
                                    'Description': 'Catalog item from QuickBooks',
                                    'FullyQualifiedName': 'Salsas:La Mexicana:Imported Salsa Bottle',
                                    'ParentRef': {'value': 'PARENT-1', 'name': 'Salsas'},
                                    'Sku': 'QB-SKU-1',
                                    'UnitPrice': 7.5,
                                    'PurchaseCost': 4.25,
                                    'Active': True,
                                }
                            ]
                        }
                    })
                if 'from Attachable' in query:
                    return self._json_response({
                        'QueryResponse': {
                            'Attachable': [
                                {
                                    'Id': 'ATT-ITEM-1',
                                    'FileName': 'salsa.png',
                                    'Category': 'Image',
                                    'ContentType': 'image/png',
                                    'TempDownloadUri': 'https://download.quickbooks.test/salsa.png',
                                }
                            ]
                        }
                    })
            if url == 'https://download.quickbooks.test/salsa.png':
                return self._binary_response(b'qb-image-bytes', content_type='image/png')
            raise AssertionError(f'Unexpected QuickBooks request: {method} {url}')

        mock_request.side_effect = request_side_effect

        response = self.client.post(
            reverse('quickbooks_import_items_to_local'),
            {'limit': '10', 'skip_images': '0'},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()['result']
        self.assertEqual(payload['created_count'], 1, payload)
        presentacion = Presentacion.objects.get(quickbooks_id='QB-ITEM-1')
        self.assertEqual(presentacion.producto.nombre, 'Imported Salsa Bottle')
        self.assertEqual(presentacion.producto.categoria.nombre, 'Salsas')
        self.assertEqual(presentacion.producto.marca.nombre, 'La Mexicana')
        self.assertEqual(presentacion.precio_1, Decimal('4.72'))
        self.assertEqual(presentacion.costo, Decimal('4.25'))
        self.assertEqual(presentacion.sync_status, 'SYNCED')
        self.assertTrue(bool(presentacion.producto.imagen.name))
        self.assertTrue(default_storage.exists(presentacion.producto.imagen.name))

    @patch('config.integrations.quickbooks.client.requests.request')
    def test_import_items_to_local_handles_single_attachable_object(self, mock_request):
        self._activate_connection()

        def request_side_effect(method, url, **kwargs):
            parsed_url = urlparse(url)
            if parsed_url.path.endswith('/query'):
                query = kwargs.get('params', {}).get('query', '')
                if 'from Item' in query:
                    return self._json_response({
                        'QueryResponse': {
                            'Item': [
                                {
                                    'Id': 'QB-ITEM-2',
                                    'Name': 'Imported Chips Bag',
                                    'Description': 'Single attachable image item',
                                    'Sku': 'QB-SKU-2',
                                    'UnitPrice': 3.5,
                                    'PurchaseCost': 2.0,
                                    'Active': True,
                                }
                            ]
                        }
                    })
                if 'from Attachable' in query:
                    return self._json_response({
                        'QueryResponse': {
                            'Attachable': {
                                'Id': 'ATT-ITEM-2',
                                'FileName': 'chips.png',
                                'Category': 'Image',
                                'ContentType': 'image/png',
                                'TempDownloadUri': 'https://download.quickbooks.test/chips.png',
                            }
                        }
                    })
            if url == 'https://download.quickbooks.test/chips.png':
                return self._binary_response(b'qb-image-bytes-single', content_type='image/png')
            raise AssertionError(f'Unexpected QuickBooks request: {method} {url}')

        mock_request.side_effect = request_side_effect

        response = self.client.post(
            reverse('quickbooks_import_items_to_local'),
            {'limit': '10', 'skip_images': '0'},
        )

        self.assertEqual(response.status_code, 200)
        presentacion = Presentacion.objects.get(quickbooks_id='QB-ITEM-2')
        self.assertTrue(bool(presentacion.producto.imagen.name))

    @patch('config.integrations.quickbooks.client.requests.request')
    def test_pull_sync_command_runs_full_pull_summary(self, mock_request):
        self._activate_connection()
        mock_request.side_effect = [
            self._json_response({'QueryResponse': {'Customer': []}}),
            self._json_response({'QueryResponse': {'Item': []}}),
            self._json_response({'QueryResponse': {'Invoice': []}}),
            self._json_response({'QueryResponse': {'CreditMemo': []}}),
            self._json_response({'QueryResponse': {'Bill': []}}),
        ]
        stdout = StringIO()

        call_command('sync_quickbooks_to_local', '--limit=5', stdout=stdout)

        self.assertIn('QuickBooks pull sync complete.', stdout.getvalue())
        self.assertIn('Mode: incremental', stdout.getvalue())

    @patch('config.integrations.quickbooks.client.requests.request')
    def test_catalog_only_sync_command_updates_existing_imported_product_metadata(self, mock_request):
        self._activate_connection()
        import_category = Categoria.objects.create(nombre='QuickBooks Imported')
        import_brand = Marca.objects.create(nombre='QuickBooks Imported')
        self.presentacion.producto.categoria = import_category
        self.presentacion.producto.marca = import_brand
        self.presentacion.producto.quickbooks_id = 'QB-ITEM-1'
        self.presentacion.producto.sync_status = 'SYNCED'
        self.presentacion.producto.save(update_fields=['categoria', 'marca', 'quickbooks_id', 'sync_status'])
        self.presentacion.quickbooks_id = 'QB-ITEM-1'
        self.presentacion.sync_status = 'SYNCED'
        self.presentacion.save(update_fields=['quickbooks_id', 'sync_status'])

        def request_side_effect(method, url, **kwargs):
            parsed_url = urlparse(url)
            if parsed_url.path.endswith('/query'):
                query = kwargs.get('params', {}).get('query', '')
                if 'from Item' in query:
                    return self._json_response({
                        'QueryResponse': {
                            'Item': [
                                {
                                    'Id': 'QB-ITEM-1',
                                    'Name': 'Imported Salsa Bottle',
                                    'Description': 'Catalog item from QuickBooks',
                                    'FullyQualifiedName': 'Salsas:La Mexicana:Imported Salsa Bottle',
                                    'ParentRef': {'value': 'PARENT-1', 'name': 'Salsas'},
                                    'Sku': 'QB-SKU-1',
                                    'UnitPrice': 7.5,
                                    'PurchaseCost': 4.25,
                                    'Active': True,
                                }
                            ]
                        }
                    })
                if 'from Attachable' in query:
                    return self._json_response({'QueryResponse': {'Attachable': []}})
            raise AssertionError(f'Unexpected QuickBooks request: {method} {url}')

        mock_request.side_effect = request_side_effect
        stdout = StringIO()

        call_command('sync_quickbooks_to_local', '--items-only', '--full', '--limit=5', stdout=stdout)

        self.presentacion.refresh_from_db()
        self.assertEqual(self.presentacion.producto.categoria.nombre, 'Salsas')
        self.assertEqual(self.presentacion.producto.marca.nombre, 'La Mexicana')
        self.assertIn('QuickBooks catalog sync complete.', stdout.getvalue())
        self.assertIn('Mode: full', stdout.getvalue())

    @override_settings(QUICKBOOKS_CATALOG_ONLY_MODE=True)
    @patch('config.integrations.quickbooks.client.requests.request')
    def test_catalog_only_mode_allows_pull_sync(self, mock_request):
        self._activate_connection()
        mock_request.side_effect = [
            self._json_response({'QueryResponse': {'Customer': []}}),
            self._json_response({'QueryResponse': {'Item': []}}),
        ]

        response = self.client.post(reverse('quickbooks_pull_sync_to_local'), {'limit': '10'})

        self.assertEqual(response.status_code, 200)

    @override_settings(QUICKBOOKS_CATALOG_ONLY_MODE=True)
    @patch('config.integrations.quickbooks.client.requests.request')
    def test_pull_sync_uses_saved_cursors_for_incremental_queries(self, mock_request):
        connection = self._activate_connection()
        connection.sync_state = {
            'cursors': {
                'quickbooks:customer': '2026-05-13T08:00:00+00:00',
                'quickbooks:item': '2026-05-13T08:00:00+00:00',
                'quickbooks:invoice': '2026-05-13T08:00:00+00:00',
                'quickbooks:credit_memo': '2026-05-13T08:00:00+00:00',
                'quickbooks:bill': '2026-05-13T08:00:00+00:00',
            }
        }
        connection.save(update_fields=['sync_state'])
        mock_request.side_effect = [
            self._json_response({'QueryResponse': {'Customer': []}}),
            self._json_response({'QueryResponse': {'Item': []}}),
        ]

        response = self.client.post(reverse('quickbooks_pull_sync_to_local'), {'limit': '10'})

        self.assertEqual(response.status_code, 200)
        queries = [call.kwargs['params']['query'] for call in mock_request.call_args_list]
        self.assertEqual(len(queries), 2)
        self.assertTrue(all('MetaData.LastUpdatedTime >' in query for query in queries))

    @patch('config.integrations.quickbooks.client.requests.request')
    def test_sync_customer_updates_existing_remote_customer(self, mock_request):
        self._activate_connection()
        self.cliente.quickbooks_id = '701'
        self.cliente.sync_status = 'SYNCED'
        self.cliente.save(update_fields=['quickbooks_id', 'sync_status'])
        mock_request.side_effect = [
            self._json_response({'QueryResponse': {'Customer': [{'Id': '701', 'SyncToken': '3', 'DisplayName': 'Old Name', 'CompanyName': 'Old Name', 'PrintOnCheckName': 'Old Name', 'Active': True}]}}),
            self._json_response({'Customer': {'Id': '701', 'SyncToken': '4', 'DisplayName': 'Old Name'}}),
        ]

        response = self.client.get(reverse('quickbooks_sync_customer', args=[self.cliente.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['result']['action'], 'updated')
        self.assertEqual(mock_request.call_args_list[1].kwargs['params']['operation'], 'update')
        update_payload = mock_request.call_args_list[1].kwargs.get('json') or {}
        display_name = update_payload.get('DisplayName', '')
        self.assertFalse(display_name.startswith('LTG Customer '))

    def test_conflict_link_endpoint_resolves_customer_conflict(self):
        conflict = QuickBooksImportConflict.objects.create(
            entity_type='CUSTOMER',
            quickbooks_id='QB-CUST-LINK-1',
            display_name='Cliente QuickBooks',
            status='CONFLICT',
            payload={
                'Id': 'QB-CUST-LINK-1',
                'DisplayName': 'Cliente QuickBooks',
                'PrimaryEmailAddr': {'Address': 'cliente@example.com'},
                'PrimaryPhone': {'FreeFormNumber': '5551239876'},
            },
            local_model='Cliente',
            local_record_id=self.cliente.id,
        )

        response = self.client.post(
            reverse('quickbooks_import_conflict_link', args=[conflict.id]),
            {'local_record_id': str(self.cliente.id), 'local_model': 'Cliente', 'resolution_note': 'Linked after migration review'},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        conflict.refresh_from_db()
        self.cliente.refresh_from_db()
        self.assertEqual(conflict.status, 'MATCHED')
        self.assertEqual(self.cliente.quickbooks_id, 'QB-CUST-LINK-1')

    def test_conflict_dismiss_endpoint_marks_conflict_as_dismissed(self):
        conflict = QuickBooksImportConflict.objects.create(
            entity_type='ITEM',
            quickbooks_id='QB-ITEM-DISMISS-1',
            display_name='Legacy Item',
            status='CONFLICT',
            payload={'Id': 'QB-ITEM-DISMISS-1'},
        )

        response = self.client.post(
            reverse('quickbooks_import_conflict_dismiss', args=[conflict.id]),
            {'resolution_note': 'Handled outside ERP'},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        conflict.refresh_from_db()
        self.assertEqual(conflict.status, 'DISMISSED')

    def test_conflict_bulk_dismiss_endpoint_marks_selected_conflicts(self):
        first_conflict = QuickBooksImportConflict.objects.create(
            entity_type='INVOICE',
            quickbooks_id='QB-INV-BULK-1',
            display_name='Invoice bulk 1',
            status='CONFLICT',
            payload={'Id': 'QB-INV-BULK-1'},
        )
        second_conflict = QuickBooksImportConflict.objects.create(
            entity_type='INVOICE',
            quickbooks_id='QB-INV-BULK-2',
            display_name='Invoice bulk 2',
            status='CONFLICT',
            payload={'Id': 'QB-INV-BULK-2'},
        )
        matched_conflict = QuickBooksImportConflict.objects.create(
            entity_type='INVOICE',
            quickbooks_id='QB-INV-BULK-3',
            display_name='Invoice bulk 3',
            status='MATCHED',
            payload={'Id': 'QB-INV-BULK-3'},
        )

        response = self.client.post(
            reverse('quickbooks_import_conflicts_bulk_dismiss'),
            {
                'conflict_ids': [str(first_conflict.id), str(second_conflict.id), str(matched_conflict.id)],
                'resolution_note': 'Customer deleted in QuickBooks',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        first_conflict.refresh_from_db()
        second_conflict.refresh_from_db()
        matched_conflict.refresh_from_db()
        self.assertEqual(first_conflict.status, 'DISMISSED')
        self.assertEqual(second_conflict.status, 'DISMISSED')
        self.assertEqual(first_conflict.resolution_note, 'Customer deleted in QuickBooks')
        self.assertEqual(matched_conflict.status, 'MATCHED')

    @patch('config.integrations.quickbooks.client.requests.request')
    def test_import_customers_to_local_redirects_back_to_dashboard_with_summary(self, mock_request):
        self._activate_connection()
        mock_request.return_value = self._json_response({
            'QueryResponse': {
                'Customer': [
                    {
                        'Id': 'QB-CUST-2',
                        'DisplayName': 'Imported Redirect Customer',
                    }
                ]
            }
        })

        response = self.client.post(
            reverse('quickbooks_import_customers_to_local'),
            {'limit': '10', 'redirect_to': '/admin/dashboard/'},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        messages = [message.message for message in get_messages(response.wsgi_request)]
        self.assertTrue(any('Created: 1. Updated: 0. Conflicts: 0. Failed: 0.' in message for message in messages))

    @patch('config.integrations.quickbooks.client.requests.request')
    def test_sync_customer_endpoint_creates_customer_and_marks_local_record(self, mock_request):
        self._activate_connection()
        mock_request.side_effect = [
            self._json_response({'QueryResponse': {}}),
            self._json_response({'Customer': {'Id': '701', 'DisplayName': 'Cliente QuickBooks'}}),
        ]

        response = self.client.get(reverse('quickbooks_sync_customer', args=[self.cliente.pk]))

        self.assertEqual(response.status_code, 200)
        self.cliente.refresh_from_db()
        self.assertEqual(self.cliente.quickbooks_id, '701')
        self.assertEqual(self.cliente.sync_status, 'SYNCED')
        self.assertEqual(response.json()['result']['action'], 'created')

    @patch('config.integrations.quickbooks.client.requests.request')
    def test_sync_product_endpoint_creates_item_and_marks_local_record(self, mock_request):
        self._activate_connection()
        mock_request.side_effect = [
            self._json_response({'QueryResponse': {}}),
            self._json_response({'QueryResponse': {'Account': [{'Id': '79', 'Name': 'Sales of Product Income'}]}}),
            self._json_response({'Item': {'Id': '801', 'Name': 'Tortilla 12'}}),
        ]

        response = self.client.get(reverse('quickbooks_sync_product', args=[self.presentacion.pk]))

        self.assertEqual(response.status_code, 200)
        self.presentacion.refresh_from_db()
        self.assertEqual(self.presentacion.quickbooks_id, '801')
        self.assertEqual(self.presentacion.sync_status, 'SYNCED')
        self.assertEqual(response.json()['result']['action'], 'created')

    @patch('config.integrations.quickbooks.client.requests.request')
    def test_sync_invoice_endpoint_creates_invoice_in_quickbooks(self, mock_request):
        self._activate_connection()
        mock_request.side_effect = [
            self._json_response({'QueryResponse': {}}),
            self._json_response({'Customer': {'Id': '701', 'DisplayName': 'Cliente QuickBooks'}}),
            self._json_response({'QueryResponse': {}}),
            self._json_response({'QueryResponse': {'Account': [{'Id': '79', 'Name': 'Sales of Product Income'}]}}),
            self._json_response({'Item': {'Id': '801', 'Name': 'Tortilla 12'}}),
            self._json_response({'QueryResponse': {}}),
            self._json_response({'Item': {'Id': '801', 'Name': 'Tortilla 12'}}),
            self._json_response({'Invoice': {'Id': '901', 'DocNumber': self.invoice.numero}}),
        ]

        response = self.client.get(reverse('quickbooks_sync_invoice', args=[self.invoice.pk]))

        self.assertEqual(response.status_code, 200)
        payload = response.json()['result']
        self.assertEqual(payload['quickbooks_id'], '901')
        self.assertEqual(payload['entity'], 'Invoice')
        self.assertEqual(response.json()['result']['entity'], 'Invoice')

    @patch('config.integrations.quickbooks.client.requests.request')
    def test_sync_adjustment_note_endpoint_creates_credit_memo(self, mock_request):
        self._activate_connection()
        mock_request.side_effect = [
            self._json_response({'QueryResponse': {}}),
            self._json_response({'Customer': {'Id': '701', 'DisplayName': 'Cliente QuickBooks'}}),
            self._json_response({'QueryResponse': {}}),
            self._json_response({'QueryResponse': {'Account': [{'Id': '79', 'Name': 'Sales of Product Income'}]}}),
            self._json_response({'Item': {'Id': '850', 'Name': 'LTG Adjustment Item'}}),
            self._json_response({'QueryResponse': {}}),
            self._json_response({'CreditMemo': {'Id': '951', 'DocNumber': self.adjustment_note.numero}}),
        ]

        response = self.client.get(reverse('quickbooks_sync_adjustment_note', args=[self.adjustment_note.pk]))

        self.assertEqual(response.status_code, 200)
        self.adjustment_note.refresh_from_db()
        self.assertEqual(self.adjustment_note.quickbooks_id, '951')
        self.assertEqual(self.adjustment_note.sync_status, 'SYNCED')
        self.assertEqual(response.json()['result']['entity'], 'CreditMemo')

    @patch('config.integrations.quickbooks.client.requests.request')
    def test_sync_customers_batch_endpoint_reports_success_and_failure(self, mock_request):
        self._activate_connection()
        second_user = Usuario.objects.create_user(username='qb-batch-client-2', password='secret123', role='cliente')
        second_cliente = Cliente.objects.create(
            usuario=second_user,
            nombre_empresa='Batch Customer 2',
            telefono='5550000099',
            direccion='456 Main',
            ciudad='Dallas',
            estado='TX',
            codigo_postal='75001',
            pais='USA',
            sales_tax_number='TX-99',
            certificado_tax='certificados/test.pdf',
        )
        mock_request.side_effect = [
            self._json_response({'QueryResponse': {}}),
            self._json_response({'Customer': {'Id': '701', 'DisplayName': 'Cliente QuickBooks'}}),
            self._json_response({'QueryResponse': {}}),
            self._json_response({'Fault': {'Error': [{'Message': 'QuickBooks rejected customer'}]}}, ok=False, status_code=400),
        ]

        response = self.client.post(
            reverse('quickbooks_sync_customers_batch'),
            {'ids': [str(self.cliente.pk), str(second_cliente.pk)]},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()['result']
        self.assertEqual(payload['success_count'], 1)
        self.assertEqual(payload['failed_count'], 1)
        self.assertTrue(any(item['ok'] for item in payload['results']))
        self.assertTrue(any(not item['ok'] for item in payload['results']))

    def test_sync_customers_batch_rejects_non_pending_selection(self):
        self._activate_connection()
        response = self.client.post(
            reverse('quickbooks_sync_customers_batch'),
            {'ids': [str(self.cliente.pk), '999999']},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('not pending', response.json()['error'].lower())

    @patch('config.integrations.quickbooks.client.requests.request')
    def test_batch_sync_redirects_back_to_dashboard_with_message(self, mock_request):
        self._activate_connection()
        mock_request.side_effect = [
            self._json_response({'QueryResponse': {}}),
            self._json_response({'Customer': {'Id': '701', 'DisplayName': 'Cliente QuickBooks'}}),
        ]

        response = self.client.post(
            reverse('quickbooks_sync_customers_batch'),
            {'ids': str(self.cliente.pk), 'redirect_to': '/admin/dashboard/'},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        messages = [message.message for message in get_messages(response.wsgi_request)]
        self.assertTrue(any('Succeeded: 1. Failed: 0.' in message for message in messages))