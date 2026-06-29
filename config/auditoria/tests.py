from django.test import Client, TestCase
from django.urls import reverse

from config.auditoria.models import AuditLog
from config.auditoria.business_events import log_business_event
from config.auditoria.services import record_audit_event
from config.usuarios.models import Usuario


class AuditTrailTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = Usuario.objects.create_user(
            username='admin_audit',
            password='test-pass-123',
            role='admin',
        )
        self.backoffice = Usuario.objects.create_user(
            username='backoffice_audit',
            password='test-pass-123',
            role='backoffice',
        )

    def test_admin_can_access_audit_panel(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('audit_log_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Audit trail')

    def test_backoffice_user_cannot_access_audit_panel(self):
        self.client.force_login(self.backoffice)
        response = self.client.get(reverse('audit_log_list'))
        self.assertEqual(response.status_code, 302)

    def test_record_audit_event_persists_entry(self):
        self.client.force_login(self.admin)
        request = self.client.get('/auditoria/').wsgi_request
        request.user = self.admin
        log = record_audit_event(
            request,
            action_label='Test action',
            action_category=AuditLog.CATEGORY_VIEW,
            entity_type='TestEntity',
            entity_id='1',
            entity_label='Sample',
        )
        self.assertIsNotNone(log)
        self.assertEqual(AuditLog.objects.count(), 1)
        self.assertEqual(log.actor_username, self.admin.username)
        self.assertEqual(log.action_label, 'Test action')

    def test_middleware_logs_authenticated_post(self):
        self.client.force_login(self.admin)
        before = AuditLog.objects.count()
        self.client.post(reverse('audit_log_list'), {'q': 'demo', 'business_only': '0'})
        self.assertGreater(AuditLog.objects.count(), before)

    def test_business_only_filter_hides_page_views(self):
        record_audit_event(
            self.client.get('/auditoria/').wsgi_request,
            action_label='Page view',
            action_category=AuditLog.CATEGORY_VIEW,
        )
        log_business_event(
            self.admin,
            action_label='Import from QuickBooks: import items',
            action_category=AuditLog.CATEGORY_SYNC,
            entity_type='QuickBooks',
            entity_label='Items sync',
        )
        self.client.force_login(self.admin)
        response = self.client.get(reverse('audit_log_list'))
        self.assertContains(response, 'Page view')
        self.assertContains(response, 'Import from QuickBooks')

        response = self.client.get(reverse('audit_log_list'), {'business_only': '1'})
        self.assertNotContains(response, 'Page view')
        self.assertContains(response, 'Import from QuickBooks')

    def test_user_filter_lists_all_internal_users_including_backoffice(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('audit_log_list'))
        self.assertContains(response, 'backoffice_audit')
