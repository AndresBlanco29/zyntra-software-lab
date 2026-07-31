import json
import logging
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.http import HttpResponseBadRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from config.ai_assistant.models import (
    AssistantConfiguration,
    AssistantConversation,
    AssistantDomainEvent,
    AssistantGuidedTourProgress,
    AssistantKnowledgeDocument,
    AssistantMessage,
    AssistantPendingAction,
    AssistantUserState,
)
from config.ai_assistant.services.context import build_customer_context
from config.ai_assistant.services.identity import (
    get_customer_for_user,
    get_visitor_id,
    get_visitor_profile,
    set_visitor_cookie,
    visitor_in_rollout,
)
from config.ai_assistant.services.orchestrator import get_or_create_conversation, reply_to_message
from config.ai_assistant.services.actions import execute_confirmed_action
from config.usuarios.permissions import internal_permission_required

logger = logging.getLogger(__name__)

def _json_body(request):
    try:
        return json.loads(request.body.decode('utf-8') or '{}')
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


@require_POST
def request_account_status_code(request):
    payload = _json_body(request)
    if payload is None:
        return HttpResponseBadRequest('Invalid JSON.')
    from config.ai_assistant.services.verification import VerificationRateLimited, issue_account_status_challenge

    try:
        challenge = issue_account_status_challenge(payload.get('email', ''))
    except VerificationRateLimited:
        return JsonResponse({'error': 'Try again later.'}, status=429)
    return JsonResponse({
        'success': True,
        'challenge_id': str(challenge.public_id),
        'message': 'If the email is registered, a verification code was sent.',
    })


@require_POST
def verify_account_status_code(request):
    payload = _json_body(request)
    if payload is None:
        return HttpResponseBadRequest('Invalid JSON.')
    from config.ai_assistant.services.status_gateway import StatusGateway
    from config.ai_assistant.services.verification import verify_account_status_challenge

    cliente = verify_account_status_challenge(payload.get('challenge_id', ''), payload.get('code', ''))
    if cliente is None:
        return JsonResponse({'error': 'Invalid or expired verification code.'}, status=400)
    return JsonResponse({'success': True, 'status': StatusGateway().get_status(cliente=cliente, entity_type='account')})


@require_POST
def record_login_failure(request):
    """Record only an anonymous, short-lived failure count; never expose account existence."""
    visitor_id = get_visitor_id(request)
    key = f'ai-assistant:login-failures:{visitor_id}'
    attempts = int(cache.get(key, 0)) + 1
    cache.set(key, attempts, timeout=15 * 60)
    if attempts < 3:
        return JsonResponse({'intervene': False})
    from config.ai_assistant.services.contact import build_contact_dto

    return JsonResponse({
        'intervene': True,
        'message': 'Parece que estás teniendo problemas para iniciar sesión. Puedes recuperar tu contraseña, intentarlo nuevamente o hablar con un asesor.',
        'actions': [
            {'label': 'Recuperar contraseña', 'url': f"{reverse('home')}?show_login=1", 'tour_id': 'password-recovery'},
            {'label': 'Intentar nuevamente', 'url': '#', 'tour_id': 'login'},
            *build_contact_dto().get('actions', []),
        ],
    })


def _conversation_for_request(request, public_id):
    visitor_id = get_visitor_id(request)
    conversation = AssistantConversation.objects.filter(public_id=public_id, visitor_id=visitor_id).first()
    if conversation is None:
        return None
    if conversation.user_id and conversation.user_id != getattr(request.user, 'id', None):
        return None
    return conversation


def _rate_limited(request, config):
    since = timezone.now() - timedelta(hours=1)
    visitor_count = AssistantConversation.objects.filter(
        visitor_id=get_visitor_id(request),
        messages__role='user',
        messages__created_at__gte=since,
    ).count()
    forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR', '')
    ip_address = (forwarded_for.split(',')[0] if forwarded_for else request.META.get('REMOTE_ADDR', '')).strip()
    cache_key = f'ai-assistant:ip:{ip_address}'
    ip_count = cache.get(cache_key, 0)
    if visitor_count >= config.max_messages_per_hour or ip_count >= (config.max_messages_per_hour * 3):
        return True
    cache.set(cache_key, int(ip_count) + 1, timeout=3600)
    return False


@require_GET
def assistant_context(request):
    config = AssistantConfiguration.get_solo()
    visitor_id = get_visitor_id(request)
    context = build_customer_context(request)
    page = str(request.GET.get('page') or '').strip()
    page_enabled = {
        'home': config.enable_home,
        'registration': config.enable_home,
        'login': config.enable_home,
        'catalog': config.enable_catalog,
        'cart': config.enable_customer_portal,
        'quotes': config.enable_customer_portal,
        'quote-detail': config.enable_customer_portal,
        'order-history': config.enable_customer_portal,
    }.get(page, config.enable_customer_portal)
    context.update({
        'enabled': bool(config.enabled) and bool(page_enabled) and visitor_in_rollout(visitor_id),
        'assistant_name': config.assistant_name,
        'welcome_message': config.welcome_message,
        'visitor_id': str(visitor_id),
    })
    response = JsonResponse(context)
    if context.get('proactive', {}).get('kind') == 'first_visit':
        profile, _ = get_visitor_profile(request)
        profile.first_visit_prompted_at = timezone.now()
        profile.save(update_fields=['first_visit_prompted_at'])
    return set_visitor_cookie(response, request)


@require_POST
def create_conversation(request):
    payload = _json_body(request)
    if payload is None:
        return HttpResponseBadRequest('Invalid JSON.')
    visitor_id = get_visitor_id(request)
    user = request.user
    cliente = get_customer_for_user(user)
    conversation = get_or_create_conversation(
        visitor_id=visitor_id,
        user=user,
        cliente=cliente,
        page=str(payload.get('page') or request.path),
        language=str(payload.get('language') or 'es')[:8],
    )
    AssistantUserState.objects.get_or_create(
        visitor_id=visitor_id,
        defaults={'user': user if getattr(user, 'is_authenticated', False) else None, 'cliente': cliente},
    )
    # Return the thread so navigating to another page resumes the conversation
    # instead of restarting it.
    history = list(
        conversation.messages.filter(
            role__in=[AssistantMessage.ROLE_USER, AssistantMessage.ROLE_ASSISTANT],
        ).order_by('-created_at')[:20]
    )
    payload = {
        'conversation_id': str(conversation.public_id),
        'messages': [
            {'role': item.role, 'content': item.content}
            for item in reversed(history)
        ],
    }
    # Persist the visitor id here too: if it only lived in the session, losing the
    # session would orphan every conversation this visitor owns.
    return set_visitor_cookie(JsonResponse(payload), request)


@require_POST
def conversation_message(request, public_id):
    config = AssistantConfiguration.get_solo()
    if not config.enabled or not visitor_in_rollout(get_visitor_id(request)):
        return JsonResponse({'error': 'Assistant is disabled.'}, status=503)
    if _rate_limited(request, config):
        return JsonResponse({'error': 'Message limit reached. Please try again later.'}, status=429)
    payload = _json_body(request)
    if payload is None:
        return HttpResponseBadRequest('Invalid JSON.')
    message = str(payload.get('message') or '').strip()
    if not message or len(message) > config.max_message_chars:
        return JsonResponse({'error': 'Invalid message.'}, status=400)
    conversation = _conversation_for_request(request, public_id)
    if conversation is None:
        return JsonResponse({'error': 'Tu sesión de asistencia se actualizó. Reintentaremos tu mensaje.'}, status=404)
    try:
        return JsonResponse(reply_to_message(request=request, conversation=conversation, message=message))
    except Exception:
        logger.exception('AI assistant message processing failed: conversation=%s', public_id)
        return JsonResponse(
            {'error': 'No pude completar esa consulta en este momento. Inténtalo nuevamente en unos segundos.'},
            status=503,
        )


@require_POST
def tour_progress(request, tour_key):
    payload = _json_body(request)
    if payload is None:
        return HttpResponseBadRequest('Invalid JSON.')
    visitor_id = get_visitor_id(request)
    progress, _ = AssistantGuidedTourProgress.objects.get_or_create(
        visitor_id=visitor_id,
        tour_key=tour_key,
        defaults={'user': request.user if request.user.is_authenticated else None},
    )
    progress.current_step = max(int(payload.get('current_step') or 0), 0)
    progress.completed = bool(payload.get('completed'))
    progress.dismissed = bool(payload.get('dismissed'))
    progress.context = payload.get('context') if isinstance(payload.get('context'), dict) else {}
    progress.save()
    from config.ai_assistant.services.memory import remember_assistant_context
    remember_assistant_context(request, last_tour=tour_key)
    from config.ai_assistant.services.customer_success_profile import touch_success_profile
    touch_success_profile(cliente=get_customer_for_user(request.user), tour=tour_key)
    if (
        tour_key == 'platform-history'
        and progress.completed
        and getattr(request.user, 'is_authenticated', False)
    ):
        cliente = get_customer_for_user(request.user)
        state, _ = AssistantUserState.objects.get_or_create(
            visitor_id=visitor_id,
            defaults={'user': request.user, 'cliente': cliente},
        )
        if not state.onboarding_completed:
            state.onboarding_completed = True
            state.save(update_fields=['onboarding_completed', 'updated_at'])
    return JsonResponse({'success': True})


@require_POST
def consume_event(request, event_id):
    cliente = get_customer_for_user(request.user)
    if cliente is None:
        profile, _ = get_visitor_profile(request)
        cliente = profile.cliente
        if cliente is None:
            return JsonResponse({'error': 'Customer login required.'}, status=403)
    event = AssistantDomainEvent.objects.filter(pk=event_id, cliente=cliente).first()
    if event is None:
        return JsonResponse({'error': 'Event not found.'}, status=404)
    event.consumed_at = timezone.now()
    event.save(update_fields=['consumed_at'])
    return JsonResponse({'success': True})


@require_POST
def confirm_action(request, public_id):
    action = AssistantPendingAction.objects.filter(public_id=public_id).first()
    if action is None:
        return JsonResponse({'error': 'Action not found.'}, status=404)
    try:
        result = execute_confirmed_action(request=request, action=action)
    except ValidationError as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    from config.auditoria.business_events import log_business_event
    log_business_event(
        request.user,
        action_label='Confirmed AI Assistant action',
        entity_type='AssistantPendingAction',
        entity_id=action.id,
        metadata={'action_type': action.action_type},
        request=request,
        module='ai_assistant',
    )
    return JsonResponse({'success': True, **result})


@require_POST
def delete_history(request):
    visitor_id = get_visitor_id(request)
    AssistantConversation.objects.filter(visitor_id=visitor_id).delete()
    AssistantUserState.objects.filter(visitor_id=visitor_id).delete()
    request.session.pop('ai_assistant_visitor_id', None)
    request.session.modified = True
    return JsonResponse({'success': True})


@login_required
@internal_permission_required('backoffice.ai_assistant.manage')
def backoffice_assistant_settings(request):
    config = AssistantConfiguration.get_solo()
    if request.method == 'POST':
        config.assistant_name = str(request.POST.get('assistant_name') or config.assistant_name).strip()[:80]
        config.welcome_message = str(request.POST.get('welcome_message') or '').strip()
        config.personality = str(request.POST.get('personality') or '').strip()
        config.sales_goal = str(request.POST.get('sales_goal') or '').strip()
        config.system_prompt = str(request.POST.get('system_prompt') or '').strip()
        config.default_language = str(request.POST.get('default_language') or 'es').strip()[:8]
        config.chat_model = str(request.POST.get('chat_model') or config.chat_model).strip()[:100]
        config.embedding_model = str(request.POST.get('embedding_model') or config.embedding_model).strip()[:100]
        config.support_phone = str(request.POST.get('support_phone') or '').strip()[:40]
        config.support_whatsapp = ''.join(
            character for character in str(request.POST.get('support_whatsapp') or '') if character.isdigit()
        )[:40]
        config.support_email = str(request.POST.get('support_email') or '').strip()[:254]
        config.location_address = str(request.POST.get('location_address') or '').strip()
        config.location_map_url = str(request.POST.get('location_map_url') or '').strip()[:200]
        config.delivery_coverage = str(request.POST.get('delivery_coverage') or '').strip()[:250]
        try:
            config.temperature = max(0, min(float(request.POST.get('temperature') or config.temperature), 1))
        except (TypeError, ValueError):
            pass
        config.enabled = request.POST.get('enabled') == 'on'
        config.enable_home = request.POST.get('enable_home') == 'on'
        config.enable_catalog = request.POST.get('enable_catalog') == 'on'
        config.enable_customer_portal = request.POST.get('enable_customer_portal') == 'on'
        config.save()
        messages.success(request, 'AI Assistant configuration updated.')
        return redirect('ai_assistant_backoffice')
    return render(request, 'backoffice/ai_assistant_settings.html', {
        'assistant_config': config,
        'knowledge_documents': AssistantKnowledgeDocument.objects.all().order_by('title'),
        'conversations_count': AssistantConversation.objects.count(),
        'assistant_metrics': {
            'messages': AssistantMessage.objects.count(),
            'pending_actions': AssistantPendingAction.objects.filter(status=AssistantPendingAction.STATUS_PENDING).count(),
            'tool_calls': AssistantMessage.objects.filter(role=AssistantMessage.ROLE_TOOL).count(),
        },
    })
