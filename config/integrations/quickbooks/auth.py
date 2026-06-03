import logging
import secrets
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.utils import timezone

from .constants import QUICKBOOKS_AUTHORIZATION_URL, QUICKBOOKS_TOKEN_URL


logger = logging.getLogger(__name__)


class QuickBooksConfigurationError(Exception):
    pass


class QuickBooksOAuthError(Exception):
    pass


def validate_quickbooks_settings():
    if not settings.QUICKBOOKS_CLIENT_ID or not settings.QUICKBOOKS_CLIENT_SECRET:
        raise QuickBooksConfigurationError('QuickBooks client credentials are not configured.')
    if not settings.QUICKBOOKS_REDIRECT_URI:
        raise QuickBooksConfigurationError('QuickBooks redirect URI is not configured.')


def create_oauth_state():
    return secrets.token_urlsafe(24)


def build_authorization_url(*, state):
    validate_quickbooks_settings()
    query = urlencode({
        'client_id': settings.QUICKBOOKS_CLIENT_ID,
        'redirect_uri': settings.QUICKBOOKS_REDIRECT_URI,
        'response_type': 'code',
        'scope': ' '.join(settings.QUICKBOOKS_SCOPES),
        'state': state,
    })
    return f'{QUICKBOOKS_AUTHORIZATION_URL}?{query}'


def _post_token_request(payload):
    validate_quickbooks_settings()
    response = requests.post(
        QUICKBOOKS_TOKEN_URL,
        data=payload,
        auth=(settings.QUICKBOOKS_CLIENT_ID, settings.QUICKBOOKS_CLIENT_SECRET),
        headers={'Accept': 'application/json'},
        timeout=30,
    )
    if not response.ok:
        logger.warning('QuickBooks token request failed with status %s', response.status_code)
        try:
            detail = response.json()
        except ValueError:
            detail = response.text
        raise QuickBooksOAuthError(f'QuickBooks token request failed: {detail}')
    return response.json()


def exchange_code_for_tokens(*, code):
    return _post_token_request({
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': settings.QUICKBOOKS_REDIRECT_URI,
    })


def refresh_access_token(*, refresh_token):
    return _post_token_request({
        'grant_type': 'refresh_token',
        'refresh_token': refresh_token,
    })


def build_expiry_timestamp(*, seconds):
    if not seconds:
        return None
    return timezone.now() + timezone.timedelta(seconds=int(seconds))