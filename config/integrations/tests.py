import gzip
import json
import tempfile
from decimal import Decimal
from io import StringIO
from pathlib import Path
from datetime import date
from unittest.mock import Mock, patch
from urllib.parse import urlparse

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
from config.integrations.quickbooks.services import get_connection
from config.integrations.models import QuickBooksConnection, QuickBooksImportConflict
from config.integrations.quickbooks.sync import sync_supplier_purchase
from config.inventario.models import CompraProveedor, CompraProveedorLinea, InventarioMovimiento, Proveedor, StockPresentacion
from config.pedidos.models import Pedido, PedidoItem
from config.productos.models import Categoria, Marca, Presentacion, Producto
from config.usuarios.models import Usuario


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
            metodo_entrega='LTG',
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
        self.assertEqual(connection.access_token, 'access-1')
        self.assertEqual(connection.refresh_token, 'refresh-1')
        self.assertTrue(connection.connected_at)
        messages = [message.message for message in get_messages(response.wsgi_request)]
        self.assertTrue(any('QuickBooks connected successfully.' in message for message in messages))

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

    def test_quickbooks_center_admin_shows_single_admin_navigation_entry(self):
        admin_user = Usuario.objects.create_user(
            username='qb-admin',
            password='secret123',
            role='admin',
        )
        self.client.force_login(admin_user)

        response = self.client.get(reverse('quickbooks_center'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'href="{reverse("quickbooks_center")}"', count=1, html=False)
        self.assertNotContains(response, f'href="{reverse("backoffice_dashboard")}"', html=False)
        self.assertNotContains(response, f'href="{reverse("tomar_pedido")}"', html=False)

    def test_quickbooks_center_preview_tabs_keep_live_preview_anchor(self):
        response = self.client.get(reverse('quickbooks_center'), {'preview': 'customers', 'preview_limit': '8'})

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('#quickbooks-live-preview', content)

    def test_quickbooks_center_exposes_supplier_and_purchase_order_import_actions(self):
        response = self.client.get(reverse('quickbooks_center'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Preview suppliers')
        self.assertContains(response, 'Import suppliers to the app')
        self.assertContains(response, 'Preview purchase orders')
        self.assertContains(response, 'Import purchase orders to the app')

    def test_quickbooks_database_backup_downloads_full_gzipped_dump(self):
        Cotizacion.objects.create(
            cliente=self.cliente,
            vendedor=self.user,
            estado='BORRADOR',
            total=Decimal('45.00'),
        )

        response = self.client.post(reverse('quickbooks_database_backup'))

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

        self.assertEqual(response.status_code, 200)
        self.assertIn('ltg-system-backup-monthly-', response['Content-Disposition'])
        self.assertEqual(response['X-Backup-Schedule'], 'monthly')

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

    def test_restore_backup_upload_restores_database_from_downloaded_file(self):
        self.client.force_login(self.user)
        backup_response = self.client.post(reverse('quickbooks_database_backup'))
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

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Restore completed')
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

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Restore completed')
        self.assertTrue(Producto.objects.filter(nombre='Tortilla 12').exists())
        self.assertTrue(default_storage.exists(media_path))

    @patch('config.integrations.quickbooks.client.requests.request')
    def test_supplier_purchase_endpoint_creates_bill_without_loading_inventory(self, mock_request):
        self._activate_connection()
        self.presentacion.quickbooks_id = 'QB-ITEM-EXISTING'
        self.presentacion.sync_status = 'SYNCED'
        self.presentacion.save(update_fields=['quickbooks_id', 'sync_status'])
        mock_request.side_effect = [
            self._json_response({'QueryResponse': {}}),
            self._json_response({'Vendor': {'Id': 'V-100', 'DisplayName': 'Proveedor Central'}}),
            self._json_response({'QueryResponse': {'Item': [{
                'Id': 'QB-ITEM-EXISTING',
                'SyncToken': '0',
                'Name': f'LTG Item {self.presentacion.pk} - Tortilla 12 - Caja',
                'Type': 'NonInventory',
                'Active': True,
                'Description': 'Caja | unidades',
                'UnitPrice': 15.0,
                'IncomeAccountRef': {'value': '79', 'name': 'Sales of Product Income'},
                'Sku': '7501234567890',
            }]}}),
            self._json_response({'Bill': {'Id': 'B-100', 'VendorRef': {'value': 'V-100'}}}),
        ]

        response = self.client.post(
            reverse('quickbooks_sync_supplier_purchase_create'),
            {
                'proveedor_nombre': 'Proveedor Central',
                'fecha_compra': '2026-05-15',
                'notas': 'Restock run',
                'presentacion_id': [str(self.presentacion.id), ''],
                'cantidad': ['4', ''],
                'costo_unitario': ['8.75', ''],
                'descripcion': ['Compra semanal', ''],
            },
        )

        self.assertEqual(response.status_code, 200)
        compra = CompraProveedor.objects.get(proveedor_nombre='Proveedor Central')
        self.assertEqual(compra.quickbooks_id, 'B-100')
        self.assertEqual(compra.sync_status, 'SYNCED')
        self.assertEqual(compra.estado, CompraProveedor.STATUS_SENT)
        self.assertEqual(compra.total, Decimal('35.00'))
        self.assertFalse(StockPresentacion.objects.filter(presentacion=self.presentacion).exists())
        self.assertFalse(compra.inventory_applied)
        self.assertEqual(InventarioMovimiento.objects.filter(referencia=f'SUPPLIER-PURCHASE-{compra.id}').count(), 0)
        bill_payload = mock_request.call_args_list[-1].kwargs['json']
        self.assertEqual(bill_payload['VendorRef']['value'], 'V-100')
        self.assertEqual(bill_payload['Line'][0]['ItemBasedExpenseLineDetail']['ItemRef']['value'], 'QB-ITEM-EXISTING')
        self.assertEqual(bill_payload['Line'][0]['ItemBasedExpenseLineDetail']['Qty'], 4)

    @patch('config.integrations.quickbooks.client.requests.request')
    def test_supplier_purchase_sync_is_idempotent_without_inventory_receipt(self, mock_request):
        self._activate_connection()
        self.presentacion.quickbooks_id = 'QB-ITEM-EXISTING'
        self.presentacion.sync_status = 'SYNCED'
        self.presentacion.save(update_fields=['quickbooks_id', 'sync_status'])
        compra = CompraProveedor.objects.create(
            proveedor_nombre='Proveedor Reintento',
            fecha_compra=timezone.localdate(),
            creado_por=self.user,
        )
        CompraProveedorLinea.objects.create(
            compra=compra,
            presentacion=self.presentacion,
            cantidad=3,
            costo_unitario=Decimal('7.25'),
            descripcion='Primer intento',
        )
        compra.recalcular_totales(save=True)

        mock_request.side_effect = [
            self._json_response({'QueryResponse': {}}),
            self._json_response({'Vendor': {'Id': 'V-101', 'DisplayName': 'Proveedor Reintento'}}),
            self._json_response({'QueryResponse': {'Item': [{
                'Id': 'QB-ITEM-EXISTING',
                'SyncToken': '0',
                'Name': f'LTG Item {self.presentacion.pk} - Tortilla 12 - Caja',
                'Type': 'NonInventory',
                'Active': True,
                'Description': 'Caja | unidades',
                'UnitPrice': 15.0,
                'IncomeAccountRef': {'value': '79', 'name': 'Sales of Product Income'},
                'Sku': '7501234567890',
            }]}}),
            self._json_response({'Bill': {'Id': 'B-101'}}),
            self._json_response({'QueryResponse': {'Bill': [{'Id': 'B-101', 'SyncToken': '0'}]}}),
        ]

        first = sync_supplier_purchase(compra=compra)
        second = sync_supplier_purchase(compra=compra)

        self.assertEqual(first['action'], 'created')
        self.assertEqual(second['action'], 'existing')
        self.assertFalse(StockPresentacion.objects.filter(presentacion=self.presentacion).exists())
        self.assertEqual(InventarioMovimiento.objects.filter(referencia=f'SUPPLIER-PURCHASE-{compra.id}').count(), 0)

    @patch('config.integrations.quickbooks.client.requests.request')
    def test_import_bills_to_local_creates_supplier_purchase_and_stock(self, mock_request):
        self._activate_connection()
        self.presentacion.quickbooks_id = 'QB-ITEM-EXISTING'
        self.presentacion.sync_status = 'SYNCED'
        self.presentacion.save(update_fields=['quickbooks_id', 'sync_status'])
        mock_request.return_value = self._json_response({
            'QueryResponse': {
                'Bill': [
                    {
                        'Id': 'QB-BILL-1',
                        'DocNumber': 'BILL-001',
                        'TxnDate': '2026-05-15',
                        'DueDate': '2026-05-30',
                        'VendorRef': {'value': 'V-1', 'name': 'Proveedor Importado'},
                        'PrivateNote': 'Imported from QuickBooks',
                        'TotalAmt': '26.25',
                        'Line': [
                            {
                                'Id': '1',
                                'Amount': '26.25',
                                'Description': 'Reposicion de tortilla',
                                'DetailType': 'ItemBasedExpenseLineDetail',
                                'ItemBasedExpenseLineDetail': {
                                    'ItemRef': {'value': 'QB-ITEM-EXISTING', 'name': 'Tortilla Caja'},
                                    'Qty': 3,
                                    'UnitPrice': '8.75',
                                },
                            }
                        ],
                    }
                ]
            }
        })

        response = self.client.post(reverse('quickbooks_import_bills_to_local'), {'limit': '10'})

        self.assertEqual(response.status_code, 200)
        payload = response.json()['result']
        self.assertEqual(payload['created_count'], 1)
        compra = CompraProveedor.objects.get(quickbooks_id='QB-BILL-1')
        stock = StockPresentacion.objects.get(presentacion=self.presentacion)
        self.assertEqual(compra.proveedor_nombre, 'Proveedor Importado')
        self.assertEqual(compra.bill_number, 'BILL-001')
        self.assertEqual(compra.total, Decimal('26.25'))
        self.assertEqual(compra.sync_status, 'SYNCED')
        self.assertEqual(compra.estado, CompraProveedor.STATUS_RECEIVED)
        self.assertTrue(compra.inventory_applied)
        self.assertEqual(compra.lineas.count(), 1)
        self.assertEqual(stock.stock_fisico, 3)
        self.assertEqual(InventarioMovimiento.objects.filter(referencia=f'SUPPLIER-PURCHASE-{compra.id}').count(), 1)

    @patch('config.integrations.quickbooks.client.requests.request')
    def test_import_vendors_to_local_creates_supplier(self, mock_request):
        self._activate_connection()
        mock_request.return_value = self._json_response({
            'QueryResponse': {
                'Vendor': [
                    {
                        'Id': 'QB-VENDOR-1',
                        'DisplayName': 'Imported QB Vendor',
                        'CompanyName': 'Imported QB Vendor LLC',
                        'PrimaryEmailAddr': {'Address': 'vendor@example.com'},
                        'PrimaryPhone': {'FreeFormNumber': '5558881212'},
                        'Balance': '562.50',
                        'Active': True,
                        'Notes': 'Vendor note from QuickBooks',
                    }
                ]
            }
        })

        response = self.client.post(reverse('quickbooks_import_vendors_to_local'), {'limit': '10'})

        self.assertEqual(response.status_code, 200)
        payload = response.json()['result']
        self.assertEqual(payload['created_count'], 1)
        supplier = Proveedor.objects.get(quickbooks_id='QB-VENDOR-1')
        self.assertEqual(supplier.nombre, 'Imported QB Vendor')
        self.assertEqual(supplier.company_name, 'Imported QB Vendor LLC')
        self.assertEqual(supplier.email, 'vendor@example.com')
        self.assertEqual(supplier.telefono, '5558881212')
        self.assertEqual(supplier.balance, Decimal('562.50'))
        self.assertEqual(supplier.sync_status, 'SYNCED')

    @patch('config.integrations.quickbooks.client.requests.request')
    def test_import_purchase_orders_to_local_creates_pending_supplier_purchase_without_inventory(self, mock_request):
        self._activate_connection()
        self.presentacion.quickbooks_id = 'QB-ITEM-PO-1'
        self.presentacion.sync_status = 'SYNCED'
        self.presentacion.save(update_fields=['quickbooks_id', 'sync_status'])
        mock_request.return_value = self._json_response({
            'QueryResponse': {
                'PurchaseOrder': [
                    {
                        'Id': 'QB-PO-1',
                        'DocNumber': 'PO-001',
                        'TxnDate': '2026-05-22',
                        'VendorRef': {'value': 'V-PO-1', 'name': 'QB Purchase Vendor'},
                        'PrivateNote': 'Pending supplier PO',
                        'TotalAmt': '22.50',
                        'Line': [
                            {
                                'Id': '1',
                                'Amount': '22.50',
                                'Description': 'PO import line',
                                'DetailType': 'ItemBasedExpenseLineDetail',
                                'ItemBasedExpenseLineDetail': {
                                    'ItemRef': {'value': 'QB-ITEM-PO-1', 'name': 'PO Item'},
                                    'Qty': 3,
                                    'UnitPrice': '7.50',
                                },
                            }
                        ],
                    }
                ]
            }
        })

        response = self.client.post(reverse('quickbooks_import_purchase_orders_to_local'), {'limit': '10'})

        self.assertEqual(response.status_code, 200)
        payload = response.json()['result']
        self.assertEqual(payload['created_count'], 1)
        compra = CompraProveedor.objects.get(quickbooks_id='QB-PO-1')
        self.assertEqual(compra.estado, CompraProveedor.STATUS_SENT)
        self.assertEqual(compra.proveedor_nombre, 'QB Purchase Vendor')
        self.assertEqual(compra.bill_number, 'PO-001')
        self.assertFalse(compra.inventory_applied)
        self.assertEqual(compra.total, Decimal('22.50'))
        self.assertEqual(compra.lineas.count(), 1)
        self.assertFalse(StockPresentacion.objects.filter(presentacion=self.presentacion).exists())

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

    @patch('config.integrations.quickbooks.client.requests.request')
    def test_pull_accounting_documents_creates_debit_and_credit_notes_when_customer_exists(self, mock_request):
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
        debit_note = NotaAjuste.objects.get(quickbooks_id='QB-INV-AUTO-1')
        credit_note = NotaAjuste.objects.get(quickbooks_id='QB-CM-AUTO-1')
        self.assertEqual(debit_note.tipo_documento, 'DEBITO')
        self.assertEqual(debit_note.tipo_ajuste, 'FINANCIERO')
        self.assertEqual(debit_note.total, Decimal('45.00'))
        self.assertEqual(debit_note.cliente, self.cliente)
        self.assertEqual(credit_note.tipo_documento, 'CREDITO')
        self.assertEqual(credit_note.tipo_ajuste, 'FINANCIERO')
        self.assertEqual(credit_note.tipo_credito, 'CREDIT_DUMP')
        self.assertEqual(credit_note.total, Decimal('10.00'))
        self.assertEqual(credit_note.cliente, self.cliente)
        self.assertFalse(QuickBooksImportConflict.objects.filter(quickbooks_id__in=['QB-INV-AUTO-1', 'QB-CM-AUTO-1']).exists())

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
        self.assertEqual(imported.usuario.first_name, 'Imported QB Contact')
        self.assertEqual(imported.usuario.email, 'qb-imported@example.com')
        self.assertEqual(imported.sync_status, 'SYNCED')

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

        response = self.client.post(reverse('quickbooks_import_items_to_local'), {'limit': '10'})

        self.assertEqual(response.status_code, 200)
        payload = response.json()['result']
        self.assertEqual(payload['created_count'], 1, payload)
        presentacion = Presentacion.objects.get(quickbooks_id='QB-ITEM-1')
        self.assertEqual(presentacion.producto.nombre, 'Imported Salsa Bottle')
        self.assertEqual(presentacion.producto.categoria.nombre, 'Salsas')
        self.assertEqual(presentacion.producto.marca.nombre, 'La Mexicana')
        self.assertEqual(presentacion.precio_1, Decimal('7.50'))
        self.assertEqual(presentacion.costo, Decimal('4.25'))
        self.assertEqual(presentacion.sync_status, 'SYNCED')
        self.assertTrue(bool(presentacion.producto.imagen.name))
        self.assertTrue(default_storage.exists(presentacion.producto.imagen.name))

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
            self._json_response({'QueryResponse': {'Invoice': []}}),
            self._json_response({'QueryResponse': {'CreditMemo': []}}),
            self._json_response({'QueryResponse': {'Bill': []}}),
        ]

        response = self.client.post(reverse('quickbooks_pull_sync_to_local'), {'limit': '10'})

        self.assertEqual(response.status_code, 200)
        queries = [call.kwargs['params']['query'] for call in mock_request.call_args_list]
        self.assertTrue(all('MetaData.LastUpdatedTime >' in query for query in queries))

    @patch('config.integrations.quickbooks.client.requests.request')
    def test_sync_customer_updates_existing_remote_customer(self, mock_request):
        self._activate_connection()
        self.cliente.quickbooks_id = '701'
        self.cliente.sync_status = 'SYNCED'
        self.cliente.save(update_fields=['quickbooks_id', 'sync_status'])
        mock_request.side_effect = [
            self._json_response({'QueryResponse': {'Customer': [{'Id': '701', 'SyncToken': '3', 'DisplayName': 'Old Name', 'CompanyName': 'Old Name', 'PrintOnCheckName': 'Old Name', 'Active': True}]}}),
            self._json_response({'Customer': {'Id': '701', 'SyncToken': '4', 'DisplayName': 'LTG Customer 1 - Cliente QuickBooks'}}),
        ]

        response = self.client.get(reverse('quickbooks_sync_customer', args=[self.cliente.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['result']['action'], 'updated')
        self.assertEqual(mock_request.call_args_list[1].kwargs['params']['operation'], 'update')

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
        self.assertEqual(conflict.resolution_note, 'Handled outside ERP')

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
            self._json_response({'Customer': {'Id': '701', 'DisplayName': 'LTG Customer 1 - Cliente QuickBooks'}}),
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
            self._json_response({'Item': {'Id': '801', 'Name': 'LTG Item 1 - Tortilla 12 - Caja'}}),
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
            self._json_response({'Customer': {'Id': '701', 'DisplayName': 'LTG Customer 1 - Cliente QuickBooks'}}),
            self._json_response({'QueryResponse': {}}),
            self._json_response({'QueryResponse': {'Account': [{'Id': '79', 'Name': 'Sales of Product Income'}]}}),
            self._json_response({'Item': {'Id': '801', 'Name': 'LTG Item 1 - Tortilla 12 - Caja'}}),
            self._json_response({'QueryResponse': {}}),
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
            self._json_response({'Customer': {'Id': '701', 'DisplayName': 'LTG Customer 1 - Cliente QuickBooks'}}),
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
    def test_supplier_purchase_endpoint_creates_bill_without_loading_inventory(self, mock_request):
        self._activate_connection()
        self.presentacion.quickbooks_id = 'QB-ITEM-EXISTING'
        self.presentacion.sync_status = 'SYNCED'
        self.presentacion.save(update_fields=['quickbooks_id', 'sync_status'])
        mock_request.side_effect = [
            self._json_response({'QueryResponse': {}}),
            self._json_response({'Vendor': {'Id': 'V-100', 'DisplayName': 'Proveedor Central'}}),
            self._json_response({'QueryResponse': {'Item': [{
                'Id': 'QB-ITEM-EXISTING',
                'SyncToken': '0',
                'Name': f'LTG Item {self.presentacion.pk} - Tortilla 12 - Caja',
                'Type': 'NonInventory',
                'Active': True,
                'Description': 'Caja | unidades',
                'UnitPrice': 15.0,
                'IncomeAccountRef': {'value': '79', 'name': 'Sales of Product Income'},
                'Sku': '7501234567890',
            }]}}),
            self._json_response({'Bill': {'Id': 'B-100', 'VendorRef': {'value': 'V-100'}}}),
        ]

        response = self.client.post(
            reverse('quickbooks_sync_supplier_purchase_create'),
            {
                'proveedor_nombre': 'Proveedor Central',
                'fecha_compra': '2026-05-15',
                'notas': 'Restock run',
                'presentacion_id': [str(self.presentacion.id), ''],
                'cantidad': ['4', ''],
                'costo_unitario': ['8.75', ''],
                'descripcion': ['Compra semanal', ''],
            },
        )

        self.assertEqual(response.status_code, 200)
        compra = CompraProveedor.objects.get(proveedor_nombre='Proveedor Central')
        stock = StockPresentacion.objects.get(presentacion=self.presentacion)
        self.assertEqual(compra.quickbooks_id, 'B-100')
        self.assertEqual(compra.sync_status, 'SYNCED')
        self.assertEqual(compra.estado, CompraProveedor.STATUS_SENT)
        self.assertEqual(compra.total, Decimal('35.00'))
        self.assertEqual(stock.stock_fisico, 0)
        self.assertEqual(stock.stock_disponible, 0)
        self.assertFalse(compra.inventory_applied)
        self.assertEqual(InventarioMovimiento.objects.filter(referencia=f'SUPPLIER-PURCHASE-{compra.id}').count(), 0)
        bill_payload = mock_request.call_args_list[-1].kwargs['json']
        self.assertEqual(bill_payload['VendorRef']['value'], 'V-100')
        self.assertEqual(bill_payload['Line'][0]['ItemBasedExpenseLineDetail']['ItemRef']['value'], 'QB-ITEM-EXISTING')
        self.assertEqual(bill_payload['Line'][0]['ItemBasedExpenseLineDetail']['Qty'], 4)

    @patch('config.integrations.quickbooks.client.requests.request')
    def test_supplier_purchase_sync_is_idempotent_without_inventory_receipt(self, mock_request):
        self._activate_connection()
        self.presentacion.quickbooks_id = 'QB-ITEM-EXISTING'
        self.presentacion.sync_status = 'SYNCED'
        self.presentacion.save(update_fields=['quickbooks_id', 'sync_status'])
        compra = CompraProveedor.objects.create(
            proveedor_nombre='Proveedor Reintento',
            fecha_compra=timezone.localdate(),
            creado_por=self.user,
        )
        CompraProveedorLinea.objects.create(
            compra=compra,
            presentacion=self.presentacion,
            cantidad=3,
            costo_unitario=Decimal('7.25'),
            descripcion='Primer intento',
        )
        compra.recalcular_totales(save=True)

        mock_request.side_effect = [
            self._json_response({'QueryResponse': {}}),
            self._json_response({'Vendor': {'Id': 'V-101', 'DisplayName': 'Proveedor Reintento'}}),
            self._json_response({'QueryResponse': {'Item': [{
                'Id': 'QB-ITEM-EXISTING',
                'SyncToken': '0',
                'Name': f'LTG Item {self.presentacion.pk} - Tortilla 12 - Caja',
                'Type': 'NonInventory',
                'Active': True,
                'Description': 'Caja | unidades',
                'UnitPrice': 15.0,
                'IncomeAccountRef': {'value': '79', 'name': 'Sales of Product Income'},
                'Sku': '7501234567890',
            }]}}),
            self._json_response({'Bill': {'Id': 'B-101'}}),
            self._json_response({'QueryResponse': {'Bill': [{'Id': 'B-101', 'SyncToken': '0'}]}}),
        ]

        first = sync_supplier_purchase(compra=compra)
        second = sync_supplier_purchase(compra=compra)

        stock = StockPresentacion.objects.get(presentacion=self.presentacion)
        self.assertEqual(first['action'], 'created')
        self.assertEqual(second['action'], 'existing')
        self.assertEqual(stock.stock_fisico, 0)
        self.assertEqual(InventarioMovimiento.objects.filter(referencia=f'SUPPLIER-PURCHASE-{compra.id}').count(), 0)

    @patch('config.integrations.quickbooks.client.requests.request')
    def test_import_bills_to_local_creates_supplier_purchase_and_stock(self, mock_request):
        self._activate_connection()
        self.presentacion.quickbooks_id = 'QB-ITEM-EXISTING'
        self.presentacion.sync_status = 'SYNCED'
        self.presentacion.save(update_fields=['quickbooks_id', 'sync_status'])
        mock_request.return_value = self._json_response({
            'QueryResponse': {
                'Bill': [
                    {
                        'Id': 'QB-BILL-1',
                        'DocNumber': 'BILL-001',
                        'TxnDate': '2026-05-15',
                        'DueDate': '2026-05-30',
                        'VendorRef': {'value': 'V-1', 'name': 'Proveedor Importado'},
                        'PrivateNote': 'Imported from QuickBooks',
                        'TotalAmt': '26.25',
                        'Line': [
                            {
                                'Id': '1',
                                'Amount': '26.25',
                                'Description': 'Reposicion de tortilla',
                                'DetailType': 'ItemBasedExpenseLineDetail',
                                'ItemBasedExpenseLineDetail': {
                                    'ItemRef': {'value': 'QB-ITEM-EXISTING', 'name': 'Tortilla Caja'},
                                    'Qty': 3,
                                    'UnitPrice': '8.75',
                                },
                            }
                        ],
                    }
                ]
            }
        })

        response = self.client.post(reverse('quickbooks_import_bills_to_local'), {'limit': '10'})

        self.assertEqual(response.status_code, 200)
        payload = response.json()['result']
        self.assertEqual(payload['created_count'], 1)
        compra = CompraProveedor.objects.get(quickbooks_id='QB-BILL-1')
        stock = StockPresentacion.objects.get(presentacion=self.presentacion)
        self.assertEqual(compra.proveedor_nombre, 'Proveedor Importado')
        self.assertEqual(compra.bill_number, 'BILL-001')
        self.assertEqual(compra.total, Decimal('26.25'))
        self.assertEqual(compra.sync_status, 'SYNCED')
        self.assertEqual(compra.estado, CompraProveedor.STATUS_RECEIVED)
        self.assertTrue(compra.inventory_applied)
        self.assertEqual(compra.lineas.count(), 1)
        self.assertEqual(stock.stock_fisico, 3)
        self.assertEqual(InventarioMovimiento.objects.filter(referencia=f'SUPPLIER-PURCHASE-{compra.id}').count(), 1)

    @patch('config.integrations.quickbooks.client.requests.request')
    def test_import_bills_to_local_creates_conflict_when_item_is_missing_locally(self, mock_request):
        self._activate_connection()
        mock_request.return_value = self._json_response({
            'QueryResponse': {
                'Bill': [
                    {
                        'Id': 'QB-BILL-CONFLICT-1',
                        'DocNumber': 'BILL-CONFLICT-1',
                        'TxnDate': '2026-05-15',
                        'VendorRef': {'value': 'V-2', 'name': 'Proveedor Conflictivo'},
                        'Line': [
                            {
                                'Id': '1',
                                'Amount': '12.00',
                                'DetailType': 'ItemBasedExpenseLineDetail',
                                'ItemBasedExpenseLineDetail': {
                                    'ItemRef': {'value': 'QB-ITEM-MISSING', 'name': 'Missing item'},
                                    'Qty': 2,
                                    'UnitPrice': '6.00',
                                },
                            }
                        ],
                    }
                ]
            }
        })

        response = self.client.post(reverse('quickbooks_import_bills_to_local'), {'limit': '10'})

        self.assertEqual(response.status_code, 200)
        payload = response.json()['result']
        self.assertEqual(payload['conflict_count'], 1)
        conflict = QuickBooksImportConflict.objects.get(entity_type='BILL', quickbooks_id='QB-BILL-CONFLICT-1')
        self.assertEqual(conflict.status, 'CONFLICT')
        self.assertIn('not linked locally yet', conflict.reason)

    @patch('config.integrations.quickbooks.client.requests.request')
    def test_sync_customers_batch_endpoint_reports_success_and_failure(self, mock_request):
        self._activate_connection()
        mock_request.side_effect = [
            self._json_response({'QueryResponse': {}}),
            self._json_response({'Customer': {'Id': '701', 'DisplayName': 'LTG Customer 1 - Cliente QuickBooks'}}),
        ]

        response = self.client.post(reverse('quickbooks_sync_customers_batch'), {'ids': f'{self.cliente.pk},999999'})

        self.assertEqual(response.status_code, 200)
        payload = response.json()['result']
        self.assertEqual(payload['success_count'], 1)
        self.assertEqual(payload['failed_count'], 1)
        self.assertTrue(any(item['ok'] for item in payload['results']))
        self.assertTrue(any(not item['ok'] for item in payload['results']))

    @patch('config.integrations.quickbooks.client.requests.request')
    def test_batch_sync_redirects_back_to_dashboard_with_message(self, mock_request):
        self._activate_connection()
        mock_request.side_effect = [
            self._json_response({'QueryResponse': {}}),
            self._json_response({'Customer': {'Id': '701', 'DisplayName': 'LTG Customer 1 - Cliente QuickBooks'}}),
        ]

        response = self.client.post(
            reverse('quickbooks_sync_customers_batch'),
            {'ids': str(self.cliente.pk), 'redirect_to': '/admin/dashboard/'},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        messages = [message.message for message in get_messages(response.wsgi_request)]
        self.assertTrue(any('Succeeded: 1. Failed: 0.' in message for message in messages))