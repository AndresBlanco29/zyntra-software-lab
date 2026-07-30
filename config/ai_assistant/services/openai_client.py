import json
import logging
import time

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)


class OpenAIServiceError(Exception):
    pass


class OpenAIClient:
    """Small provider adapter using the OpenAI Responses API over HTTPS."""

    def __init__(self):
        self.api_key = str(getattr(settings, 'OPENAI_API_KEY', '') or '').strip()
        self.base_url = str(getattr(settings, 'OPENAI_API_BASE_URL', 'https://api.openai.com/v1') or '').rstrip('/')
        self.timeout = int(getattr(settings, 'AI_ASSISTANT_OPENAI_TIMEOUT_SECONDS', 25))

    @property
    def configured(self):
        return bool(self.api_key)

    def _post(self, path, payload):
        if not self.configured:
            raise OpenAIServiceError('OpenAI is not configured.')
        breaker_key = 'ai-assistant:openai:circuit-open'
        if cache.get(breaker_key):
            raise OpenAIServiceError('The assistant service is temporarily unavailable.')
        last_error = None
        for attempt in range(3):
            try:
                response = requests.post(
                    f'{self.base_url}{path}',
                    headers={
                        'Authorization': f'Bearer {self.api_key}',
                        'Content-Type': 'application/json',
                    },
                    data=json.dumps(payload),
                    timeout=self.timeout,
                )
                if response.status_code not in {408, 409, 429} and response.status_code < 500:
                    response.raise_for_status()
                    cache.delete('ai-assistant:openai:failures')
                    return response.json()
                response.raise_for_status()
            except requests.RequestException as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(0.4 * (2 ** attempt))
        failures_key = 'ai-assistant:openai:failures'
        failures = int(cache.get(failures_key, 0)) + 1
        cache.set(failures_key, failures, timeout=120)
        if failures >= 3:
            cache.set(breaker_key, True, timeout=60)
        logger.warning('OpenAI request failed after retries: %s', last_error)
        raise OpenAIServiceError('The assistant service is temporarily unavailable.') from last_error

    def create_response(self, *, model, instructions, input_messages, tools, temperature=0.3, previous_response_id=''):
        payload = {
            'model': model,
            'instructions': instructions,
            'input': input_messages,
            'tools': tools,
            'temperature': float(temperature),
        }
        if previous_response_id:
            payload['previous_response_id'] = previous_response_id
            payload.pop('instructions', None)
            payload.pop('tools', None)
        response = self._post('/responses', payload)
        output_text = self._extract_output_text(response)
        tool_calls = []
        for item in response.get('output', []):
            if item.get('type') == 'function_call':
                tool_calls.append({
                    'call_id': item.get('call_id'),
                    'name': item.get('name'),
                    'arguments': item.get('arguments') or '{}',
                })
        return {
            'id': response.get('id', ''),
            'text': output_text,
            'tool_calls': tool_calls,
            'usage': response.get('usage') or {},
        }

    @staticmethod
    def _extract_output_text(response):
        """Extract assistant text from the raw Responses API JSON payload.

        The official SDK exposes ``output_text`` as a convenience property, but
        the raw REST JSON returned by requests keeps it under message content.
        """
        direct_text = response.get('output_text')
        if isinstance(direct_text, str) and direct_text.strip():
            return direct_text

        text_parts = []
        for item in response.get('output') or []:
            if item.get('type') != 'message':
                continue
            for content in item.get('content') or []:
                if content.get('type') in {'output_text', 'text'}:
                    text = content.get('text')
                    if isinstance(text, str) and text:
                        text_parts.append(text)
        return '\n'.join(text_parts)

    def create_embedding(self, *, model, content):
        response = self._post('/embeddings', {'model': model, 'input': content})
        data = response.get('data') or []
        return data[0].get('embedding', []) if data else []
