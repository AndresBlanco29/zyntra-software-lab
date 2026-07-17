from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class AuditLog(models.Model):
    CATEGORY_VIEW = 'VIEW'
    CATEGORY_CREATE = 'CREATE'
    CATEGORY_UPDATE = 'UPDATE'
    CATEGORY_DELETE = 'DELETE'
    CATEGORY_ACTION = 'ACTION'
    CATEGORY_EXPORT = 'EXPORT'
    CATEGORY_LOGIN = 'LOGIN'
    CATEGORY_LOGOUT = 'LOGOUT'
    CATEGORY_SYNC = 'SYNC'
    CATEGORY_PRINT = 'PRINT'

    CATEGORY_CHOICES = (
        (CATEGORY_VIEW, _('View')),
        (CATEGORY_CREATE, _('Create')),
        (CATEGORY_UPDATE, _('Update')),
        (CATEGORY_DELETE, _('Delete')),
        (CATEGORY_ACTION, _('Action')),
        (CATEGORY_EXPORT, _('Export')),
        (CATEGORY_LOGIN, _('Login')),
        (CATEGORY_LOGOUT, _('Logout')),
        (CATEGORY_SYNC, _('Sync')),
        (CATEGORY_PRINT, _('Print')),
    )

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_logs',
    )
    actor_username = models.CharField(max_length=150, blank=True, db_index=True)
    actor_full_name = models.CharField(max_length=255, blank=True)
    actor_role = models.CharField(max_length=30, blank=True, db_index=True)
    action_category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, db_index=True)
    action_label = models.CharField(max_length=255)
    http_method = models.CharField(max_length=10, db_index=True)
    path = models.CharField(max_length=500)
    route_name = models.CharField(max_length=120, blank=True, db_index=True)
    module = models.CharField(max_length=80, blank=True, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=500, blank=True)
    browser = models.CharField(max_length=80, blank=True)
    os_name = models.CharField(max_length=80, blank=True)
    device = models.CharField(max_length=40, blank=True)
    geo_city = models.CharField(max_length=120, blank=True)
    geo_country = models.CharField(max_length=120, blank=True)
    entity_type = models.CharField(max_length=80, blank=True, db_index=True)
    entity_id = models.CharField(max_length=80, blank=True, db_index=True)
    entity_label = models.CharField(max_length=255, blank=True)
    status_code = models.PositiveSmallIntegerField(default=200)
    success = models.BooleanField(default=True, db_index=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    changes = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ('-created_at', '-id')
        indexes = [
            models.Index(fields=['-created_at', 'actor']),
            models.Index(fields=['action_category', '-created_at']),
            models.Index(fields=['entity_type', 'entity_id', '-created_at']),
            models.Index(fields=['module', '-created_at']),
            models.Index(fields=['success', '-created_at']),
        ]

    def __str__(self):
        return f'{self.actor_username or "-"} | {self.action_label} | {self.created_at}'

    @property
    def actor_display(self):
        if self.actor_full_name:
            return self.actor_full_name
        if self.actor_id and self.actor:
            return self.actor.get_full_name() or self.actor.username
        return self.actor_username or _('Unknown user')

    @property
    def result_label(self):
        return _('Success') if self.success else _('Failed')

    @property
    def location_display(self):
        parts = [part for part in (self.geo_city, self.geo_country) if part]
        return ', '.join(parts) if parts else ''

    @property
    def device_summary(self):
        parts = [part for part in (self.device, self.os_name, self.browser) if part]
        return ' · '.join(parts) if parts else (self.user_agent or '')
