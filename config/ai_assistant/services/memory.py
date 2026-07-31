from config.ai_assistant.models import AssistantUserState
from config.ai_assistant.services.identity import get_customer_for_user, get_visitor_id


def remember_assistant_context(request, **values):
    """Persist compact commercial context without storing message content or PII."""
    visitor_id = get_visitor_id(request)
    user = request.user if getattr(request.user, 'is_authenticated', False) else None
    state, _ = AssistantUserState.objects.get_or_create(
        visitor_id=visitor_id,
        defaults={'user': user, 'cliente': get_customer_for_user(user)},
    )
    preferences = dict(state.preferences or {})
    preferences.update({key: value for key, value in values.items() if value is not None})
    state.preferences = preferences
    state.save(update_fields=['preferences', 'updated_at'])
