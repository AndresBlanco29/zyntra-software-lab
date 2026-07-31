from django.contrib import admin

from .models import (
    AssistantConfiguration,
    AssistantConversation,
    AssistantCustomerSuccessProfile,
    AssistantDomainEvent,
    AssistantGuidedTourProgress,
    AssistantKnowledgeChunk,
    AssistantKnowledgeDocument,
    AssistantMessage,
    AssistantProductAlias,
    AssistantUserState,
)
from .services.knowledge import rebuild_document_chunks
from .tasks import rebuild_and_embed_knowledge_document


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
        for document in queryset:
            rebuild_and_embed_knowledge_document.delay(document.id)
        self.message_user(request, f'Queued embeddings for {queryset.count()} knowledge documents.')


@admin.register(AssistantConversation)
class AssistantConversationAdmin(admin.ModelAdmin):
    list_display = ('public_id', 'user', 'cliente', 'language', 'status', 'last_activity_at')
    list_filter = ('status', 'language')
    search_fields = ('public_id', 'summary')
    readonly_fields = ('public_id', 'visitor_id', 'created_at', 'last_activity_at')


admin.site.register(AssistantMessage)
admin.site.register(AssistantKnowledgeChunk)
admin.site.register(AssistantUserState)
admin.site.register(AssistantCustomerSuccessProfile)
admin.site.register(AssistantGuidedTourProgress)
admin.site.register(AssistantDomainEvent)


@admin.register(AssistantProductAlias)
class AssistantProductAliasAdmin(admin.ModelAdmin):
    list_display = ('alias', 'product', 'brand', 'active')
    list_filter = ('active',)
    search_fields = ('alias', 'product__nombre', 'brand__nombre')
