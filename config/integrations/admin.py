from django.contrib import admin

from .models import QuickBooksConnection, QuickBooksImportConflict


@admin.register(QuickBooksConnection)
class QuickBooksConnectionAdmin(admin.ModelAdmin):
    list_display = ('environment', 'realm_id', 'is_active', 'connected_at', 'last_refreshed_at', 'updated_at')
    readonly_fields = ('connected_at', 'last_refreshed_at', 'updated_at', 'created_at', 'sync_state')
    search_fields = ('realm_id', 'environment')


@admin.register(QuickBooksImportConflict)
class QuickBooksImportConflictAdmin(admin.ModelAdmin):
    list_display = ('entity_type', 'display_name', 'doc_number', 'quickbooks_id', 'status', 'local_model', 'local_record_id', 'resolved_by', 'last_seen_at')
    list_filter = ('entity_type', 'status')
    search_fields = ('quickbooks_id', 'doc_number', 'display_name', 'reason')
    readonly_fields = ('first_seen_at', 'last_seen_at', 'resolved_at')