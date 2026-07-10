"""Helpers for branded transactional emails (logo URL / inline CID)."""

from __future__ import annotations

from email.mime.image import MIMEImage
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders


EMAIL_LOGO_CID = 'ltg_brand_logo'
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


def resolve_email_logo_path():
	for relative in EMAIL_LOGO_STATIC_CANDIDATES:
		found = finders.find(relative)
		if found:
			return Path(found)
		fallback = Path(settings.BASE_DIR) / 'static' / relative
		if fallback.exists():
			return fallback
	return None


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
	logo_path = resolve_email_logo_path()
	logo_static = 'img/email_logo.jpg' if logo_path and logo_path.name.startswith('email_logo') else 'img/logo.png'
	return {
		'brand_logo_cid': EMAIL_LOGO_CID,
		'brand_logo_url': build_absolute_static_url(logo_static),
	}


def attach_inline_brand_logo(email_message):
	"""Embed the brand logo so email clients do not depend on remote image loading."""
	logo_path = resolve_email_logo_path()
	if logo_path is None or not logo_path.exists():
		return False

	payload = logo_path.read_bytes()
	subtype = 'jpeg' if logo_path.suffix.lower() in {'.jpg', '.jpeg'} else 'png'
	mime_image = MIMEImage(payload, _subtype=subtype)
	mime_image.add_header('Content-ID', f'<{EMAIL_LOGO_CID}>')
	mime_image.add_header('Content-Disposition', 'inline', filename=logo_path.name)
	email_message.attach(mime_image)
	# Keep HTML + logo in one related container for better client support.
	if getattr(email_message, 'mixed_subtype', None) != 'related':
		email_message.mixed_subtype = 'related'
	return True
