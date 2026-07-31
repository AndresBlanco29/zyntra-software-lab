"""Instrumented entry point for every assistant tool call.

All business data must come from a tool, so each call is timed, logged and
persisted as a tool message. Failures are retried once and then returned as a
structured error the caller can turn into a human sentence: a technical
exception must never reach the customer.
"""

import logging
import time

from config.ai_assistant.models import AssistantMessage
from config.ai_assistant.services.privacy import redact_content
from config.ai_assistant.tools import execute_tool

logger = logging.getLogger(__name__)


def run_tool(*, request, conversation, name, arguments='{}', model='', attempts=2):
    """Execute a tool, log it, and never raise to the caller."""
    result = None
    last_error = None
    duration_ms = 0
    for attempt in range(1, attempts + 1):
        started_at = time.monotonic()
        try:
            result = execute_tool(request, name, arguments)
            duration_ms = int((time.monotonic() - started_at) * 1000)
            logger.info(
                'AI assistant tool ok: name=%s attempt=%s duration_ms=%s',
                name, attempt, duration_ms,
            )
            last_error = None
            break
        except Exception as exc:
            duration_ms = int((time.monotonic() - started_at) * 1000)
            last_error = exc
            logger.exception(
                'AI assistant tool failed: name=%s attempt=%s duration_ms=%s',
                name, attempt, duration_ms,
            )

    if last_error is not None:
        result = {'error': 'tool_unavailable', 'tool': name}

    if conversation is not None:
        AssistantMessage.objects.create(
            conversation=conversation,
            role=AssistantMessage.ROLE_TOOL,
            content=redact_content(str(result)),
            redacted_content=redact_content(str(result)),
            tool_name=name,
            tool_payload=result if isinstance(result, dict) else {'value': str(result)},
            model=model,
        )
    return result


def tool_failed(result):
    return isinstance(result, dict) and result.get('error') == 'tool_unavailable'


UNAVAILABLE_MESSAGE = (
    'No pude obtener esa información en este momento. ¿Deseas intentarlo nuevamente?'
)


def unavailable_result(actions=None):
    """Uniform, non-technical answer when the system could not be consulted."""
    return {
        'message': UNAVAILABLE_MESSAGE,
        'suggested_actions': actions or [],
        'tour_id': None,
        'tool_results': [],
        'confirmation_actions': [],
    }
