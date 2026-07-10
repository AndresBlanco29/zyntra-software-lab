"""Helpers for branded transactional emails (absolute logo URL).

Resend/Anymail does not support inline content-id attachments, so logos must
be referenced by a public HTTPS URL rather than cid: embeds.
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders


EMAIL_LOGO_STATIC_CANDIDATES = (
	'img/email_logo.jpg',
	'img/logo.png',
)


def get_app_base_url():
	configured = (getattr(settings, 'APP_BASE_URL', '') or '').rstrip('/')
	if configured:
		return configured
	if getattr(settings, 'DEBUG', False):
		return 'http://127.0.0.1:8000'
	domain = (getattr(settings, 'CANONICAL_DOMAIN', '') or '').strip().lstrip('.')
	if domain:
		return f'https://{domain}'
	return ''


def resolve_email_logo_static_path():
	for relative in EMAIL_LOGO_STATIC_CANDIDATES:
		found = finders.find(relative)
		if found:
			return relative
		fallback = Path(settings.BASE_DIR) / 'static' / relative
		if fallback.exists():
			return relative
	return 'img/email_logo.jpg'


def build_absolute_static_url(path):
	from django.contrib.staticfiles.storage import staticfiles_storage

	try:
		static_path = staticfiles_storage.url(path)
	except ValueError:
		static_path = f"{settings.STATIC_URL.rstrip('/')}/{path.lstrip('/')}"

	if static_path.startswith('http://') or static_path.startswith('https://'):
		return static_path

	base = get_app_base_url()
	if not base:
		return static_path
	if not static_path.startswith('/'):
		static_path = f'/{static_path}'
	return f'{base}{static_path}'


def brand_email_context():
	return {
		'brand_logo_url': build_absolute_static_url(resolve_email_logo_static_path()),
	}
