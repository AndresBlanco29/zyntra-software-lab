from celery import shared_task
from django.utils import timezone


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={'max_retries': 3})
def rebuild_and_embed_knowledge_document(self, document_id):
    from config.ai_assistant.models import AssistantKnowledgeDocument
    from config.ai_assistant.services.knowledge import embed_document_chunks, rebuild_document_chunks

    document = AssistantKnowledgeDocument.objects.filter(pk=document_id).first()
    if document is None:
        return {'status': 'missing'}
    rebuild_document_chunks(document)
    return {'status': 'complete', 'embedded': embed_document_chunks(document)}


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={'max_retries': 2})
def summarize_assistant_conversation(self, conversation_id):
    from config.ai_assistant.models import AssistantConfiguration, AssistantConversation
    from config.ai_assistant.services.openai_client import OpenAIClient
    from config.ai_assistant.services.privacy import redact_content

    conversation = AssistantConversation.objects.filter(pk=conversation_id).first()
    if conversation is None:
        return {'status': 'missing'}
    messages = list(conversation.messages.order_by('-created_at')[:24])
    if len(messages) < 12:
        return {'status': 'not-needed'}
    transcript = '\n'.join(
        f'{message.role}: {message.redacted_content}'
        for message in reversed(messages)
        if message.role in {'user', 'assistant'}
    )
    client = OpenAIClient()
    if not client.configured:
        return {'status': 'unavailable'}
    response = client.create_response(
        model=AssistantConfiguration.get_solo().chat_model,
        instructions='Summarize this customer conversation in Spanish. Preserve only helpful goals, preferences and unresolved requests. Do not retain sensitive data.',
        input_messages=[{'role': 'user', 'content': transcript}],
        tools=[],
    )
    conversation.summary = redact_content(response['text'])[:4000]
    conversation.save(update_fields=['summary'])
    return {'status': 'complete'}


@shared_task
def cleanup_expired_assistant_data():
    from datetime import timedelta
    from config.ai_assistant.models import AssistantConfiguration, AssistantConversation, AssistantPendingAction

    config = AssistantConfiguration.get_solo()
    cutoff = timezone.now() - timedelta(days=config.retention_days)
    AssistantConversation.objects.filter(last_activity_at__lt=cutoff).delete()
    return AssistantPendingAction.objects.filter(
        status=AssistantPendingAction.STATUS_PENDING,
        expires_at__lte=timezone.now(),
    ).update(status=AssistantPendingAction.STATUS_EXPIRED)
