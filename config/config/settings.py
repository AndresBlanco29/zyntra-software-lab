"""
Django settings for config project.
"""



# PyMySQL shim for Django DB compatibility
import pymysql
pymysql.version_info = (2, 2, 1, 'final', 0)  # Fake version for Django compatibility check
pymysql.install_as_MySQLdb()

from pathlib import Path
import os
import sys

# ========================
# BASE
# ========================

BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BASE_DIR.parent

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / '.env', override=True)


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'on'}


def env_int(name, default=0):
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

LOCALE_PATHS = [
    BASE_DIR / "locale",
]

# ========================
# SEGURIDAD
# ========================

SECRET_KEY = os.environ.get('SECRET_KEY', 'django-insecure-dev-key')


def _is_railway_deploy():
    return bool(
        os.environ.get('RAILWAY_ENVIRONMENT')
        or os.environ.get('RAILWAY_SERVICE_NAME')
        or os.environ.get('RAILWAY_PROJECT_ID')
    )


def _is_local_runserver():
    return len(sys.argv) > 1 and sys.argv[1] == 'runserver'


if os.environ.get('DEBUG') is not None:
    DEBUG = env_bool('DEBUG', False)
elif _is_local_runserver() and not _is_railway_deploy():
    DEBUG = True
else:
    DEBUG = False

SERVE_MEDIA = env_bool('SERVE_MEDIA', True)

# Dominio canónico (sin www). Se usa en ALLOWED_HOSTS, CSRF y el middleware de redirección.
CANONICAL_DOMAIN = 'latortillagroceryapp.com'

# ---- ALLOWED_HOSTS ----
allowed_hosts = os.environ.get('ALLOWED_HOSTS', '')
ALLOWED_HOSTS = [host.strip() for host in allowed_hosts.split(',') if host.strip()]

railway_domain = 'tortilla-erp-production.up.railway.app'
for _host in (railway_domain, CANONICAL_DOMAIN, f'www.{CANONICAL_DOMAIN}'):
    if _host not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(_host)

if DEBUG:
    for host in ('127.0.0.1', 'localhost' , '192.168.26.5', '.ngrok-free.dev', '.ngrok-free.app'):
        if host not in ALLOWED_HOSTS:
            ALLOWED_HOSTS.append(host)

# ---- CSRF ----
csrf_trusted_origins = os.environ.get('CSRF_TRUSTED_ORIGINS', '')
CSRF_TRUSTED_ORIGINS = [origin.strip() for origin in csrf_trusted_origins.split(',') if origin.strip()]

for _origin in (
    f'https://{railway_domain}',
    f'https://{CANONICAL_DOMAIN}',
    f'https://www.{CANONICAL_DOMAIN}',
):
    if _origin not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(_origin)

if DEBUG:
    for _origin in (
        'http://127.0.0.1:8000',
        'http://localhost:8000',
        'https://*.ngrok-free.dev',
        'https://*.ngrok-free.app',
    ):
        if _origin not in CSRF_TRUSTED_ORIGINS:
            CSRF_TRUSTED_ORIGINS.append(_origin)

# ---- HTTPS ----
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
USE_X_FORWARDED_HOST = True

# Redirige HTTP → HTTPS en producción. Railway expone todo via proxy HTTPS,
# por lo que X-Forwarded-Proto: https llega siempre y no causa bucle de redirección.
SECURE_SSL_REDIRECT = not DEBUG

# HSTS: el navegador recuerda HTTPS durante 1 año (sólo activo en producción).
SECURE_HSTS_SECONDS          = 0 if DEBUG else 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD            = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF    = True
SESSION_COOKIE_SECURE          = not DEBUG
CSRF_COOKIE_SECURE             = not DEBUG

# ========================
# APPS
# ========================

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.sitemaps',

    # terceros
    'corsheaders',
    'anymail',
    'cloudinary_storage',
    'cloudinary',

    # tus apps
    'config.core',
    'config.productos',
    'config.usuarios',
    'config.clientes',
    'config.vendedores',
    'config.carrito',
    'config.cotizaciones',
    'config.facturacion',
    'config.inventario',
    'config.integrations',
    'config.notificaciones',
    'config.pedidos',
    'config.reportes',
    'config.auditoria',
]

# ========================
# MIDDLEWARE
# ========================

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'config.config.middleware.RuntimeSchemaRepairMiddleware',
    'config.config.middleware.WwwRedirectMiddleware',
    #'config.middleware.NoCacheMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.locale.LocaleMiddleware',
    'config.config.middleware.DefaultEnglishUnlessChosenMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'config.auditoria.middleware.AuditMiddleware',
    'config.config.middleware.ProtectedAreaLoginMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

LOGIN_URL = '/login/'

# ========================
# CORS
# ========================

CORS_ALLOW_ALL_ORIGINS = env_bool('CORS_ALLOW_ALL_ORIGINS', True)

# ========================
# URLS / TEMPLATES
# ========================

ROOT_URLCONF = 'config.config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.template.context_processors.i18n',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'config.notificaciones.context_processors.workspace_urgent_alerts',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.config.wsgi.application'

# ========================
# BASE DE DATOS
# ========================

def _env_first(*names, default=''):
    for name in names:
        value = os.environ.get(name)
        if value is not None and str(value).strip() != '':
            return value.strip()
    return default


mysql_name = _env_first('MYSQLDATABASE', 'DB_NAME')
mysql_user = _env_first('MYSQLUSER', 'DB_USER')
mysql_host = _env_first('MYSQLHOST', 'DB_HOST', default='127.0.0.1')
mysql_port = _env_first('MYSQLPORT', 'DB_PORT', default='3306')
mysql_password = os.environ.get('MYSQLPASSWORD')
if mysql_password is None:
    mysql_password = os.environ.get('DB_PASSWORD')
db_conn_max_age = env_int('DB_CONN_MAX_AGE', 600)
view_cache_timeout = env_int('VIEW_CACHE_TIMEOUT', 60)
QUICKBOOKS_CLIENT_ID = os.environ.get('QUICKBOOKS_CLIENT_ID', '').strip()
QUICKBOOKS_CLIENT_SECRET = os.environ.get('QUICKBOOKS_CLIENT_SECRET', '').strip()
QUICKBOOKS_REDIRECT_URI = os.environ.get(
    'QUICKBOOKS_REDIRECT_URI',
    'http://127.0.0.1:8000/quickbooks/callback/',
).strip()
QUICKBOOKS_ENVIRONMENT = os.environ.get('QUICKBOOKS_ENVIRONMENT', 'sandbox').strip().lower() or 'sandbox'
QUICKBOOKS_SCOPES = tuple(
    scope.strip()
    for scope in os.environ.get('QUICKBOOKS_SCOPES', 'com.intuit.quickbooks.accounting').split()
    if scope.strip()
)
QUICKBOOKS_API_MINOR_VERSION = os.environ.get('QUICKBOOKS_API_MINOR_VERSION', '75').strip() or '75'
# When True, only catalog preview/import is allowed in QuickBooks Center (blocks vendor/bill sync).
QUICKBOOKS_CATALOG_ONLY_MODE = env_bool('QUICKBOOKS_CATALOG_ONLY_MODE', default=True)
# When False (default), QuickBooks invoices and credit memos are not imported into the app.
# Local invoices are exported to QuickBooks only.
QUICKBOOKS_IMPORT_ACCOUNTING_DOCUMENTS = env_bool('QUICKBOOKS_IMPORT_ACCOUNTING_DOCUMENTS', default=False)
QUICKBOOKS_TOKEN_MAINTENANCE_HOURS = int(os.environ.get('QUICKBOOKS_TOKEN_MAINTENANCE_HOURS', '12') or '12')
# QuickBooks catalog sync tuning: larger pages and deferred image downloads speed up import/refresh.
QUICKBOOKS_CATALOG_SYNC_PAGE_SIZE = min(max(int(os.environ.get('QUICKBOOKS_CATALOG_SYNC_PAGE_SIZE', '1000') or 1000), 1), 1000)
# Skip inline image downloads during catalog import by default; run image sync separately.
QUICKBOOKS_CATALOG_SYNC_SKIP_IMAGES = env_bool('QUICKBOOKS_CATALOG_SYNC_SKIP_IMAGES', default=True)
QUICKBOOKS_IMAGE_DOWNLOAD_TIMEOUT = max(int(os.environ.get('QUICKBOOKS_IMAGE_DOWNLOAD_TIMEOUT', '8') or 8), 3)
# When True, new QuickBooks items are created as Inventory so invoicing reduces QtyOnHand in QuickBooks.
QUICKBOOKS_USE_INVENTORY_ITEMS = env_bool('QUICKBOOKS_USE_INVENTORY_ITEMS', default=True)
QUICKBOOKS_UNDEPOSITED_FUNDS_ACCOUNT_ID = os.environ.get('QUICKBOOKS_UNDEPOSITED_FUNDS_ACCOUNT_ID', '').strip()
QUICKBOOKS_CASH_ACCOUNT_ID = os.environ.get('QUICKBOOKS_CASH_ACCOUNT_ID', '').strip()

mysql_configured = bool(
    mysql_name
    and mysql_user
    and mysql_host
    and (mysql_password is not None)
)

if mysql_configured:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.mysql',
            'NAME': mysql_name,
            'USER': mysql_user,
            'PASSWORD': mysql_password,
            'HOST': mysql_host,
            'PORT': mysql_port,
            'CONN_MAX_AGE': db_conn_max_age,
            'CONN_HEALTH_CHECKS': True,
            'OPTIONS': {
                'charset': 'utf8mb4',
                'connect_timeout': 10,
            },
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
            'CONN_MAX_AGE': 0,
        }
    }

# ========================
# MODELO USUARIO
# ========================

AUTH_USER_MODEL = 'usuarios.Usuario'

# ========================
# VALIDACIÓN PASSWORDS
# ========================

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# ========================
# INTERNACIONALIZACIÓN
# ========================

LANGUAGE_CODE = 'en'
LANGUAGE_COOKIE_AGE = None

TIME_ZONE = 'America/New_York'

USE_I18N = True
USE_TZ = True
USE_L10N = True

FORMAT_MODULE_PATH = 'config.core.formats'

DATE_FORMAT = 'm/d/Y'
TIME_FORMAT = 'H:i'
DATETIME_FORMAT = 'm/d/Y H:i'
SHORT_DATE_FORMAT = 'm/d/Y'

LANGUAGES = [
    ("en", "English"),
    ("es", "Spanish"),
]

# ========================
# STATIC FILES
# ========================

STATIC_URL = '/static/'

STATIC_ROOT = BASE_DIR / 'staticfiles'

STATICFILES_DIRS = [
    BASE_DIR / 'static',
    PROJECT_ROOT / 'static',
]

if DEBUG:
    staticfiles_backend = 'django.contrib.staticfiles.storage.StaticFilesStorage'
else:
    staticfiles_backend = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

USE_CLOUDINARY_MEDIA = env_bool('USE_CLOUDINARY_MEDIA', False)

STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': staticfiles_backend,
    },
}

# Prevent 500 errors if a static file is missing from the hashed manifest.
# WhiteNoise will fall back to the original static path instead of raising ValueError.
WHITENOISE_MANIFEST_STRICT = False

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'tortilla-railway-cache',
        'TIMEOUT': view_cache_timeout,
        'OPTIONS': {
            'MAX_ENTRIES': 1000,
            'CULL_FREQUENCY': 3,
        },
    }
}

# ========================
# MEDIA
# ========================

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

if USE_CLOUDINARY_MEDIA:
    cloudinary_cloud_name = os.environ.get('CLOUDINARY_CLOUD_NAME', '').strip()
    cloudinary_api_key = os.environ.get('CLOUDINARY_API_KEY', '').strip()
    cloudinary_api_secret = os.environ.get('CLOUDINARY_API_SECRET', '').strip()

    if not all([cloudinary_cloud_name, cloudinary_api_key, cloudinary_api_secret]):
        raise RuntimeError('Cloudinary media storage is enabled but CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY or CLOUDINARY_API_SECRET is missing.')

    CLOUDINARY_STORAGE = {
        'CLOUD_NAME': cloudinary_cloud_name,
        'API_KEY': cloudinary_api_key,
        'API_SECRET': cloudinary_api_secret,
        'SECURE': True,
    }

    STORAGES['default'] = {
        'BACKEND': 'cloudinary_storage.storage.MediaCloudinaryStorage',
    }

# ========================
# EMAIL  (Resend HTTP API via django-anymail)
# ========================

EMAIL_BACKEND = 'anymail.backends.resend.EmailBackend'

ANYMAIL = {
    'RESEND_API_KEY': os.environ.get('RESEND_API_KEY', ''),
}

# Destinatario para notificaciones de cotizaciones y pedidos.
ORDERS_NOTIFICATION_EMAIL = os.environ.get('ORDERS_NOTIFICATION_EMAIL', 'ltgordersapp@gmail.com')

APP_BASE_URL = os.environ.get('APP_BASE_URL', '').rstrip('/')
if not APP_BASE_URL:
    # Emails need an absolute site URL for logo/images when APP_BASE_URL is unset.
    APP_BASE_URL = 'http://127.0.0.1:8000' if DEBUG else f'https://{CANONICAL_DOMAIN}'

TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID', '')
TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN', '')
TWILIO_SMS_FROM = os.environ.get('TWILIO_SMS_FROM', '')
TWILIO_WHATSAPP_FROM = os.environ.get('TWILIO_WHATSAPP_FROM', '')

# Remitente: debe ser un correo de tu dominio verificado en Resend.
# Ej: 'Pedidos LTG <pedidos@tudominio.com>'
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'Pedidos LTG <pedidos@latortillagroceryapp.com>')
SERVER_EMAIL = DEFAULT_FROM_EMAIL
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'DEBUG' if DEBUG else 'INFO',
    },
}
