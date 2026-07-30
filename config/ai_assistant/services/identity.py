import uuid

from django.conf import settings
from django.utils import timezone


VISITOR_SESSION_KEY = 'ai_assistant_visitor_id'
VISITOR_COOKIE_NAME = 'ai_assistant_visitor'


def get_visitor_id(request):
    raw_value = request.session.get(VISITOR_SESSION_KEY) or request.COOKIES.get(VISITOR_COOKIE_NAME)
    try:
        visitor_id = uuid.UUID(str(raw_value))
    except (ValueError, TypeError, AttributeError):
        visitor_id = uuid.uuid4()
        request.session[VISITOR_SESSION_KEY] = str(visitor_id)
        request.session.modified = True
    return visitor_id


def get_visitor_profile(request):
    """Create/touch a first-party anonymous profile and merge it after login."""
    from config.ai_assistant.models import AssistantVisitorProfile

    visitor_id = get_visitor_id(request)
    user = request.user if getattr(request.user, 'is_authenticated', False) else None
    cliente = get_customer_for_user(user)
    profile, created = AssistantVisitorProfile.objects.get_or_create(
        visitor_id=visitor_id,
        defaults={'user': user, 'cliente': cliente},
    )
    updates = []
    if profile.user_id != getattr(user, 'id', None):
        profile.user = user
        updates.append('user')
    if profile.cliente_id != getattr(cliente, 'id', None):
        profile.cliente = cliente
        updates.append('cliente')
    profile.last_seen_at = timezone.now()
    updates.append('last_seen_at')
    profile.save(update_fields=updates)
    return profile, created


def set_visitor_cookie(response, request):
    """Persist only the anonymous UUID; it carries no customer data or authorization."""
    response.set_cookie(
        VISITOR_COOKIE_NAME,
        str(get_visitor_id(request)),
        max_age=90 * 24 * 60 * 60,
        secure=not settings.DEBUG,
        httponly=True,
        samesite='Lax',
    )
    return response


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
