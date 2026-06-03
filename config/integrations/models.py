from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class QuickBooksConnection(models.Model):
    environment = models.CharField(max_length=20, unique=True)
    realm_id = models.CharField(max_length=100, blank=True)
    access_token = models.TextField(blank=True)
    refresh_token = models.TextField(blank=True)
    token_type = models.CharField(max_length=40, blank=True, default='Bearer')
    scope = models.TextField(blank=True)
    access_token_expires_at = models.DateTimeField(blank=True, null=True)
    refresh_token_expires_at = models.DateTimeField(blank=True, null=True)
    connected_at = models.DateTimeField(blank=True, null=True)
    last_refreshed_at = models.DateTimeField(blank=True, null=True)
    last_error = models.TextField(blank=True)
    sync_state = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('environment',)

    def __str__(self):
        return f'QuickBooks {self.environment} connection'

    @property
    def is_active(self):
        return bool(self.realm_id and self.refresh_token)

    def access_token_is_expired(self, *, leeway_seconds=120):
        if not self.access_token or self.access_token_expires_at is None:
            return True
        return self.access_token_expires_at <= timezone.now() + timezone.timedelta(seconds=leeway_seconds)

    @classmethod
    def get_solo(cls):
        connection, _ = cls.objects.get_or_create(
            environment=getattr(settings, 'QUICKBOOKS_ENVIRONMENT', 'sandbox'),
        )
        return connection

    def get_sync_cursor(self, entity_key, *, default=None):
        state = self.sync_state if isinstance(self.sync_state, dict) else {}
        return state.get('cursors', {}).get(entity_key, default)

    def set_sync_cursor(self, entity_key, value):
        state = dict(self.sync_state or {})
        cursors = dict(state.get('cursors') or {})
        cursors[str(entity_key)] = value
        state['cursors'] = cursors
        self.sync_state = state

    def clear_sync_cursor(self, entity_key):
        state = dict(self.sync_state or {})
        cursors = dict(state.get('cursors') or {})
        cursors.pop(str(entity_key), None)
        state['cursors'] = cursors
        self.sync_state = state


class QuickBooksImportConflict(models.Model):
    ENTITY_CUSTOMER = 'CUSTOMER'
    ENTITY_VENDOR = 'VENDOR'
    ENTITY_ITEM = 'ITEM'
    ENTITY_INVOICE = 'INVOICE'
    ENTITY_CREDIT_MEMO = 'CREDIT_MEMO'
    ENTITY_BILL = 'BILL'
    ENTITY_PURCHASE_ORDER = 'PURCHASE_ORDER'

    STATUS_CONFLICT = 'CONFLICT'
    STATUS_MATCHED = 'MATCHED'
    STATUS_DISMISSED = 'DISMISSED'

    ENTITY_CHOICES = (
        (ENTITY_CUSTOMER, _('Customer')),
        (ENTITY_VENDOR, _('Vendor')),
        (ENTITY_ITEM, _('Item')),
        (ENTITY_INVOICE, _('Invoice')),
        (ENTITY_CREDIT_MEMO, _('Credit memo')),
        (ENTITY_BILL, _('Bill')),
        (ENTITY_PURCHASE_ORDER, _('Purchase order')),
    )

    STATUS_CHOICES = (
        (STATUS_CONFLICT, _('Conflict')),
        (STATUS_MATCHED, _('Matched')),
        (STATUS_DISMISSED, _('Dismissed')),
    )

    entity_type = models.CharField(max_length=30, choices=ENTITY_CHOICES)
    quickbooks_id = models.CharField(max_length=100)
    doc_number = models.CharField(max_length=100, blank=True)
    display_name = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_CONFLICT)
    reason = models.TextField(blank=True)
    payload = models.JSONField(default=dict, blank=True)
    local_model = models.CharField(max_length=50, blank=True)
    local_record_id = models.PositiveIntegerField(blank=True, null=True)
    resolution_note = models.TextField(blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='quickbooks_conflicts_resolved',
    )
    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ('-last_seen_at',)
        constraints = [
            models.UniqueConstraint(fields=('entity_type', 'quickbooks_id'), name='quickbooks_import_conflict_unique_record'),
        ]

    def __str__(self):
        label = self.doc_number or self.display_name or self.quickbooks_id
        return f'{self.get_entity_type_display()} {label}'