from config.ai_assistant.models import AssistantDomainEvent


def record_assistant_event(*, cliente, event_type, entity_type='', entity_id='', payload=None):
    """Record a customer-visible event without coupling domain models to AI."""
    return AssistantDomainEvent.objects.create(
        cliente=cliente,
        event_type=event_type,
        entity_type=str(entity_type or '')[:80],
        entity_id=str(entity_id or '')[:80],
        payload=payload or {},
    )
