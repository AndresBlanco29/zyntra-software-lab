import json
import logging

import requests
from django.conf import settings

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
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            logger.warning('OpenAI request failed: %s', exc)
            raise OpenAIServiceError('The assistant service is temporarily unavailable.') from exc

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
        output_text = response.get('output_text', '')
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

    def create_embedding(self, *, model, content):
        response = self._post('/embeddings', {'model': model, 'input': content})
        data = response.get('data') or []
        return data[0].get('embedding', []) if data else []
