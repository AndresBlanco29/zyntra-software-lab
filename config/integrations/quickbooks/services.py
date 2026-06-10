import logging
import time

from django.conf import settings
from django.core.cache import cache
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


def _oauth_state_cache_key(state):
    return f'quickbooks_oauth_state:{state}'


def get_oauth_login_url(*, request):
    state = create_oauth_state()
    request.session['quickbooks_oauth_state'] = state
    cache.set(_oauth_state_cache_key(state), True, timeout=600)
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


def _is_transient_token_error(exc):
    message = str(exc).lower()
    transient_markers = (
        'timeout',
        'timed out',
        'connection aborted',
        'connection reset',
        'connection error',
        'service unavailable',
        'service_unavailable',
        'temporarily unavailable',
        'service unavailable',
        '503',
        '502',
        '504',
    )
    return any(marker in message for marker in transient_markers)


def _token_maintenance_hours():
    try:
        return max(1, int(getattr(settings, 'QUICKBOOKS_TOKEN_MAINTENANCE_HOURS', 12)))
    except (TypeError, ValueError):
        return 12


def _should_proactively_refresh(connection):
    if connection.access_token_is_expired():
        return True
    if not connection.last_refreshed_at:
        return True

    stale_after = timezone.now() - timezone.timedelta(hours=_token_maintenance_hours())
    if connection.last_refreshed_at <= stale_after:
        return True

    if connection.refresh_token_expires_at:
        renew_before = timezone.now() + timezone.timedelta(days=7)
        if connection.refresh_token_expires_at <= renew_before:
            return True

    return False


def maintain_quickbooks_connection(*, force=False):
    connection = get_connection()
    if not connection.is_active:
        return {
            'refreshed': False,
            'reason': 'not_connected',
            'is_active': False,
        }
    if not force and not _should_proactively_refresh(connection):
        return {
            'refreshed': False,
            'reason': 'still_fresh',
            'is_active': True,
            'last_refreshed_at': connection.last_refreshed_at.isoformat() if connection.last_refreshed_at else None,
        }

    connection = ensure_valid_access_token(connection=connection, force_refresh=True)
    return {
        'refreshed': True,
        'reason': 'refreshed',
        'is_active': connection.is_active,
        'last_refreshed_at': connection.last_refreshed_at.isoformat() if connection.last_refreshed_at else None,
    }


def maybe_maintain_quickbooks_connection(*, throttle_seconds=3600):
    if not get_connection().is_active:
        return {'refreshed': False, 'reason': 'not_connected'}

    cache_key = 'quickbooks:token_maintenance_throttle'
    if cache.get(cache_key):
        return {'refreshed': False, 'reason': 'throttled'}

    try:
        result = maintain_quickbooks_connection()
    except QuickBooksServiceError as exc:
        logger.warning('QuickBooks background token maintenance failed: %s', exc)
        return {'refreshed': False, 'reason': 'error', 'error': str(exc)}

    cache.set(cache_key, True, timeout=max(300, int(throttle_seconds or 3600)))
    return result


def handle_oauth_callback(*, request, code, state, realm_id):
    validate_quickbooks_settings()
    expected_state = request.session.pop('quickbooks_oauth_state', None)
    state_is_valid = bool(state) and (
        (expected_state and state == expected_state)
        or cache.get(_oauth_state_cache_key(state))
    )
    if state:
        cache.delete(_oauth_state_cache_key(state))
    if not state_is_valid:
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
    if not force_refresh and not _should_proactively_refresh(connection):
        return connection

    attempts = 2
    last_exc = None
    for attempt in range(attempts):
        try:
            payload = refresh_access_token(refresh_token=connection.refresh_token)
            logger.info('QuickBooks access token refreshed for environment %s', connection.environment)
            return save_token_payload(connection=connection, payload=payload, realm_id=connection.realm_id)
        except (QuickBooksOAuthError, QuickBooksConfigurationError) as exc:
            last_exc = exc
            if _is_invalid_refresh_token_error(exc):
                _clear_connection_tokens(connection=connection, last_error=INVALID_REFRESH_TOKEN_MESSAGE)
                raise QuickBooksServiceError(INVALID_REFRESH_TOKEN_MESSAGE) from exc
            if attempt < attempts - 1 and _is_transient_token_error(exc):
                logger.warning('Transient QuickBooks token refresh failure, retrying: %s', exc)
                time.sleep(1)
                continue
            connection.last_error = str(exc)
            connection.save(update_fields=['last_error', 'updated_at'])
            raise QuickBooksServiceError(str(exc)) from exc

    connection.last_error = str(last_exc)
    connection.save(update_fields=['last_error', 'updated_at'])
    raise QuickBooksServiceError(str(last_exc)) from last_exc


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