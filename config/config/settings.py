"""
Django settings for config project.
"""

# PyMySQL shim for Django DB compatibility
import pymysql
pymysql.version_info = (2, 2, 1, 'final', 0)  # Fake version for Django compatibility check
pymysql.install_as_MySQLdb()

from pathlib import Path
import os

# ========================
# BASE
# ========================

BASE_DIR = Path(__file__).resolve().parent.parent


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

DEBUG = env_bool('DEBUG', False)
SERVE_MEDIA = env_bool('SERVE_MEDIA', True)

allowed_hosts = os.environ.get('ALLOWED_HOSTS', '')
ALLOWED_HOSTS = [host.strip() for host in allowed_hosts.split(',') if host.strip()]

railway_domain = 'tortilla-erp-production.up.railway.app'
if railway_domain not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(railway_domain)

if DEBUG:
    for host in ('127.0.0.1', 'localhost'):
        if host not in ALLOWED_HOSTS:
            ALLOWED_HOSTS.append(host)

if not ALLOWED_HOSTS:
    ALLOWED_HOSTS = [railway_domain]

csrf_trusted_origins = os.environ.get('CSRF_TRUSTED_ORIGINS', '')
CSRF_TRUSTED_ORIGINS = [origin.strip() for origin in csrf_trusted_origins.split(',') if origin.strip()]

railway_origin = f'https://{railway_domain}'
if railway_origin not in CSRF_TRUSTED_ORIGINS:
    CSRF_TRUSTED_ORIGINS.append(railway_origin)

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
USE_X_FORWARDED_HOST = True

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

    # terceros
    'corsheaders',

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
    'config.notificaciones',
    'config.pedidos',
    'config.reportes',
]

# ========================
# MIDDLEWARE
# ========================

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    #'config.middleware.NoCacheMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.locale.LocaleMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

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
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.config.wsgi.application'

# ========================
# BASE DE DATOS
# ========================

mysql_name = os.environ.get('MYSQLDATABASE', '')
mysql_user = os.environ.get('MYSQLUSER', '')
mysql_password = os.environ.get('MYSQLPASSWORD', '')
mysql_host = os.environ.get('MYSQLHOST', '')
mysql_port = os.environ.get('MYSQLPORT', '3306')
db_conn_max_age = env_int('DB_CONN_MAX_AGE', 600)
view_cache_timeout = env_int('VIEW_CACHE_TIMEOUT', 60)

if mysql_name and mysql_user and mysql_host and mysql_password:
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

LANGUAGE_CODE = 'es'

TIME_ZONE = 'America/New_York'

USE_I18N = True
USE_TZ = True

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
    BASE_DIR / 'static'
]

if DEBUG:
    staticfiles_backend = 'django.contrib.staticfiles.storage.StaticFilesStorage'
else:
    staticfiles_backend = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

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

# ========================
# EMAIL
# ========================

EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 587
EMAIL_USE_TLS = True

EMAIL_HOST_USER = os.environ.get('EMAIL_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_PASSWORD', '')

# Destinatario para notificaciones de cotizaciones y pedidos.
ORDERS_NOTIFICATION_EMAIL = os.environ.get('ORDERS_NOTIFICATION_EMAIL', 'ltgordersapp@gmail.com')

DEFAULT_FROM_EMAIL = EMAIL_HOST_USER
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
