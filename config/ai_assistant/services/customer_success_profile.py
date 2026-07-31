from django.utils import timezone

from config.ai_assistant.models import AssistantCustomerSuccessProfile


def get_success_profile(cliente):
    if cliente is None:
        return None
    profile, _ = AssistantCustomerSuccessProfile.objects.get_or_create(cliente=cliente)
    return profile


def touch_success_profile(*, cliente, module='', conversation=False, tour='', product=None, help_topic=''):
    profile = get_success_profile(cliente)
    if profile is None:
        return None
    now = timezone.now()
    updates = []
    if profile.first_login_at is None:
        profile.first_login_at = now
        updates.append('first_login_at')
    profile.last_login_at = now
    updates.append('last_login_at')
    if module:
        profile.last_module = module[:80]
        updates.append('last_module')
    if conversation:
        profile.last_conversation_at = now
        updates.append('last_conversation_at')
    if tour:
        profile.last_tour = tour[:80]
        updates.append('last_tour')
    if product:
        products = [item for item in profile.recently_viewed_products if item.get('id') != product['id']]
        products.insert(0, {'id': product['id'], 'name': product['name'][:120]})
        profile.recently_viewed_products = products[:8]
        updates.append('recently_viewed_products')
    if help_topic:
        topics = [topic for topic in profile.help_topics if topic != help_topic]
        profile.help_topics = [help_topic] + topics[:7]
        updates.append('help_topics')
    if updates:
        profile.save(update_fields=[*set(updates), 'updated_at'])
    return profile


def mark_event(profile, event_key):
    if profile is None:
        return
    marks = dict(profile.event_marks or {})
    marks[event_key] = timezone.now().isoformat()
    profile.event_marks = marks
    profile.save(update_fields=['event_marks', 'updated_at'])
