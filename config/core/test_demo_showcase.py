from django.test import TestCase, override_settings

from config.clientes.models import Cliente
from config.core.demo_showcase import DEMO_EMAIL_DOMAIN, DEMO_PASSWORD, seed_demo_showcase
from config.core.models import Testimonio
from config.facturacion.models import Delivery, Invoice
from config.integrations.models import QuickBooksConnection, QuickBooksSyncRun
from config.pedidos.models import Pedido
from config.productos.models import Marca, Presentacion, Promocion
from config.usuarios.models import Usuario


@override_settings(DEMO_MODE=True, QUICKBOOKS_ENVIRONMENT='sandbox', QUICKBOOKS_PROVIDER='mock')
class DemoShowcaseSeedTests(TestCase):
    def test_seed_creates_coherent_fictitious_dataset(self):
        summary = seed_demo_showcase(reset=True)

        self.assertEqual(summary['demo_login'], f'demo@{DEMO_EMAIL_DOMAIN}')
        self.assertEqual(summary['customers'], 5)
        self.assertGreaterEqual(summary['presentations'], 10)
        self.assertGreaterEqual(summary['promotions'], 3)
        self.assertGreaterEqual(summary['testimonials'], 3)
        self.assertGreaterEqual(summary['orders'], 10)
        self.assertGreaterEqual(summary['invoices'], 4)

        demo_user = Usuario.objects.get(email=f'demo@{DEMO_EMAIL_DOMAIN}')
        self.assertTrue(demo_user.check_password(DEMO_PASSWORD))
        self.assertEqual(demo_user.role, 'backoffice')
        self.assertFalse(demo_user.permission_overrides.get('admin.users.manage', True))

        self.assertTrue(Cliente.objects.filter(nombre_empresa='Harborline Market Group').exists())
        self.assertFalse(Cliente.objects.filter(nombre_empresa__icontains='Tortilla').exists())
        self.assertTrue(Presentacion.objects.exists())
        self.assertGreaterEqual(Marca.objects.filter(activo=True).count(), 10)
        self.assertTrue(Promocion.objects.filter(nombre__startswith='Demo ·', activa=True).exists())
        self.assertTrue(Testimonio.objects.filter(activo=True).exists())
        self.assertTrue(Pedido.objects.filter(estado='RECIBIDO').exists())
        self.assertTrue(Pedido.objects.filter(estado='DESPACHADO').exists())
        self.assertTrue(Invoice.objects.filter(numero__startswith='INV-DEMO-').exists())
        self.assertTrue(Delivery.objects.exists())
        self.assertTrue(QuickBooksConnection.objects.filter(realm_id='demo-mock-realm-0001').exists())
        self.assertGreaterEqual(QuickBooksSyncRun.objects.count(), 3)

    def test_seed_refuses_without_demo_mode(self):
        with override_settings(DEMO_MODE=False):
            with self.assertRaises(RuntimeError):
                seed_demo_showcase(reset=True)
