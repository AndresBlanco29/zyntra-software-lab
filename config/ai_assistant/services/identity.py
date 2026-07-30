import uuid

from django.conf import settings


VISITOR_SESSION_KEY = 'ai_assistant_visitor_id'


def get_visitor_id(request):
    raw_value = request.session.get(VISITOR_SESSION_KEY)
    try:
        visitor_id = uuid.UUID(str(raw_value))
    except (ValueError, TypeError, AttributeError):
        visitor_id = uuid.uuid4()
        request.session[VISITOR_SESSION_KEY] = str(visitor_id)
        request.session.modified = True
    return visitor_id


def get_customer_for_user(user):
    if not user or not getattr(user, 'is_authenticated', False):
        return None
    if getattr(user, 'role', '') != 'cliente':
        return None
    return getattr(user, 'cliente', None)


def public_assistant_settings():
    return {
        'enabled': bool(getattr(settings, 'AI_ASSISTANT_ENABLED', False)),
        'max_message_chars': int(getattr(settings, 'AI_ASSISTANT_MAX_MESSAGE_CHARS', 2000)),
    }


def visitor_in_rollout(visitor_id):
    percentage = max(0, min(int(getattr(settings, 'AI_ASSISTANT_ROLLOUT_PERCENT', 100)), 100))
    return (visitor_id.int % 100) < percentage
