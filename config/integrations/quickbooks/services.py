import logging

from django.utils import timezone

from config.integrations.models import QuickBooksConnection

from .auth import (
    QuickBooksConfigurationError,
    QuickBooksOAuthError,
    build_authorization_url,
    build_expiry_timestamp,
    create_oauth_state,
    exchange_code_for_tokens,
    quickbooks_credentials_configured,
    refresh_access_token,
    validate_quickbooks_settings,
)


logger = logging.getLogger(__name__)


class QuickBooksServiceError(Exception):
    pass


INVALID_REFRESH_TOKEN_MESSAGE = 'QuickBooks connection expired. Reconnect QuickBooks to continue.'


def get_connection():
    return QuickBooksConnection.get_solo()


def get_oauth_login_url(*, request):
    state = create_oauth_state()
    request.session['quickbooks_oauth_state'] = state
    return build_authorization_url(state=state)


def save_token_payload(*, connection, payload, realm_id=None):
    connection.access_token = payload.get('access_token', '')
    connection.refresh_token = payload.get('refresh_token', '')
    connection.token_type = payload.get('token_type', 'Bearer')
    connection.scope = payload.get('scope', '')
    if realm_id is not None:
        connection.realm_id = str(realm_id)
    connection.access_token_expires_at = build_expiry_timestamp(seconds=payload.get('expires_in'))
    connection.refresh_token_expires_at = build_expiry_timestamp(seconds=payload.get('x_refresh_token_expires_in'))
    connection.connected_at = connection.connected_at or timezone.now()
    connection.last_refreshed_at = timezone.now()
    connection.last_error = ''
    connection.save()
    return connection


def _clear_connection_tokens(*, connection, last_error):
    connection.access_token = ''
    connection.refresh_token = ''
    connection.access_token_expires_at = None
    connection.refresh_token_expires_at = None
    connection.last_error = last_error
    connection.save(update_fields=['access_token', 'refresh_token', 'access_token_expires_at', 'refresh_token_expires_at', 'last_error', 'updated_at'])
    return connection


def _is_invalid_refresh_token_error(exc):
    message = str(exc).lower()
    return 'invalid_grant' in message or 'invalid refresh token' in message


def handle_oauth_callback(*, request, code, state, realm_id):
    validate_quickbooks_settings()
    expected_state = request.session.pop('quickbooks_oauth_state', None)
    if not expected_state or state != expected_state:
        raise QuickBooksServiceError('Invalid QuickBooks OAuth state.')
    if not code:
        raise QuickBooksServiceError('QuickBooks OAuth callback did not include an authorization code.')
    if not realm_id:
        raise QuickBooksServiceError('QuickBooks OAuth callback did not include a realm ID.')
    payload = exchange_code_for_tokens(code=code)
    return save_token_payload(connection=get_connection(), payload=payload, realm_id=realm_id)


def ensure_valid_access_token(*, connection=None, force_refresh=False):
    validate_quickbooks_settings()
    connection = connection or get_connection()
    if not connection.refresh_token:
        raise QuickBooksServiceError('QuickBooks is not connected yet.')
    if not force_refresh and not connection.access_token_is_expired():
        return connection
    try:
        payload = refresh_access_token(refresh_token=connection.refresh_token)
    except (QuickBooksOAuthError, QuickBooksConfigurationError) as exc:
        if _is_invalid_refresh_token_error(exc):
            _clear_connection_tokens(connection=connection, last_error=INVALID_REFRESH_TOKEN_MESSAGE)
            raise QuickBooksServiceError(INVALID_REFRESH_TOKEN_MESSAGE) from exc
        connection.last_error = str(exc)
        connection.save(update_fields=['last_error', 'updated_at'])
        raise QuickBooksServiceError(str(exc)) from exc
    logger.info('QuickBooks access token refreshed for environment %s', connection.environment)
    return save_token_payload(connection=connection, payload=payload, realm_id=connection.realm_id)


def get_connection_status():
    connection = get_connection()
    return {
        'environment': connection.environment,
        'configured': bool(connection.environment),
        'credentials_configured': quickbooks_credentials_configured(),
        'realm_id': connection.realm_id,
        'is_active': connection.is_active,
        'connected_at': connection.connected_at.isoformat() if connection.connected_at else None,
        'last_refreshed_at': connection.last_refreshed_at.isoformat() if connection.last_refreshed_at else None,
        'last_error': connection.last_error,
    }