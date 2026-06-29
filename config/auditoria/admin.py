from django.contrib import admin

from config.auditoria.models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = (
        'created_at',
        'actor_username',
        'action_category',
        'action_label',
        'http_method',
        'status_code',
    )
    list_filter = ('action_category', 'http_method', 'actor_role', 'created_at')
    search_fields = ('actor_username', 'action_label', 'path', 'entity_label', 'route_name')
    readonly_fields = (
        'actor',
        'actor_username',
        'actor_role',
        'action_category',
        'action_label',
        'http_method',
        'path',
        'route_name',
        'ip_address',
        'user_agent',
        'entity_type',
        'entity_id',
        'entity_label',
        'status_code',
        'metadata',
        'created_at',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser
