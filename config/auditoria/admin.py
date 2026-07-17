from django.contrib import admin

from config.auditoria.models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = (
        'created_at',
        'actor_username',
        'action_category',
        'action_label',
        'module',
        'http_method',
        'success',
        'status_code',
    )
    list_filter = ('action_category', 'http_method', 'actor_role', 'module', 'success', 'created_at')
    search_fields = ('actor_username', 'actor_full_name', 'action_label', 'path', 'entity_label', 'route_name', 'ip_address')
    readonly_fields = (
        'actor',
        'actor_username',
        'actor_full_name',
        'actor_role',
        'action_category',
        'action_label',
        'http_method',
        'path',
        'route_name',
        'module',
        'ip_address',
        'user_agent',
        'browser',
        'os_name',
        'device',
        'geo_city',
        'geo_country',
        'entity_type',
        'entity_id',
        'entity_label',
        'status_code',
        'success',
        'duration_ms',
        'changes',
        'metadata',
        'created_at',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser
