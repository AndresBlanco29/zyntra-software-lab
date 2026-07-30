from django.contrib import admin

from .models import (
    AssistantConfiguration,
    AssistantConversation,
    AssistantDomainEvent,
    AssistantGuidedTourProgress,
    AssistantKnowledgeChunk,
    AssistantKnowledgeDocument,
    AssistantMessage,
    AssistantUserState,
)
from .services.knowledge import embed_document_chunks, rebuild_document_chunks


@admin.register(AssistantConfiguration)
class AssistantConfigurationAdmin(admin.ModelAdmin):
    list_display = ('assistant_name', 'enabled', 'chat_model', 'updated_at')

    def has_add_permission(self, request):
        return not AssistantConfiguration.objects.exists()


@admin.register(AssistantKnowledgeDocument)
class AssistantKnowledgeDocumentAdmin(admin.ModelAdmin):
    list_display = ('title', 'language', 'status', 'version', 'updated_at')
    list_filter = ('status', 'language', 'category')
    search_fields = ('title', 'content')
    prepopulated_fields = {'slug': ('title',)}
    actions = ('rebuild_chunks', 'embed_chunks')

    @admin.action(description='Rebuild AI knowledge chunks')
    def rebuild_chunks(self, request, queryset):
        total = sum(rebuild_document_chunks(document) for document in queryset)
        self.message_user(request, f'Rebuilt {total} knowledge chunks.')

    @admin.action(description='Create OpenAI embeddings for selected chunks')
    def embed_chunks(self, request, queryset):
        total = sum(embed_document_chunks(document) for document in queryset)
        self.message_user(request, f'Created {total} embeddings.')


@admin.register(AssistantConversation)
class AssistantConversationAdmin(admin.ModelAdmin):
    list_display = ('public_id', 'user', 'cliente', 'language', 'status', 'last_activity_at')
    list_filter = ('status', 'language')
    search_fields = ('public_id', 'summary')
    readonly_fields = ('public_id', 'visitor_id', 'created_at', 'last_activity_at')


admin.site.register(AssistantMessage)
admin.site.register(AssistantKnowledgeChunk)
admin.site.register(AssistantUserState)
admin.site.register(AssistantGuidedTourProgress)
admin.site.register(AssistantDomainEvent)
