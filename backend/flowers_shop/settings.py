"""
Django settings for flowers_shop project.
"""

from pathlib import Path
import os
import dj_database_url
from dotenv import load_dotenv

load_dotenv()
load_dotenv(BASE_DIR_ROOT := Path(__file__).resolve().parent.parent.parent / '.env')


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv('SECRET_KEY', 'django-insecure-change-this-in-production')
DEBUG = env_bool('DEBUG', True)
ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')
    if host.strip()
]

RENDER_EXTERNAL_HOSTNAME = os.getenv('RENDER_EXTERNAL_HOSTNAME')
if RENDER_EXTERNAL_HOSTNAME:
    ALLOWED_HOSTS.append(RENDER_EXTERNAL_HOSTNAME)

CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.getenv('CSRF_TRUSTED_ORIGINS', '').split(',')
    if origin.strip()
]
if RENDER_EXTERNAL_HOSTNAME:
    CSRF_TRUSTED_ORIGINS.append(f'https://{RENDER_EXTERNAL_HOSTNAME}')

# Respect reverse-proxy headers (ngrok / Render) for correct absolute HTTPS URLs.
USE_X_FORWARDED_HOST = env_bool('USE_X_FORWARDED_HOST', True)
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'corsheaders',
    'catalog.apps.CatalogConfig',
    'telegram_bot',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.middleware.gzip.GZipMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'flowers_shop.middleware.AdminNoIndexMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'flowers_shop.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'flowers_shop.wsgi.application'

DATABASES = {
    'default': dj_database_url.config(
        default=f'sqlite:///{BASE_DIR / "db.sqlite3"}',
        conn_max_age=env_int('DB_CONN_MAX_AGE', 600),
        ssl_require=env_bool('DB_SSL_REQUIRE', False),
    )
}

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

LANGUAGE_CODE = 'ru-ru'
TIME_ZONE = 'Europe/Moscow'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [
    ('css', BASE_DIR.parent / 'css'),
    ('js', BASE_DIR.parent / 'js'),
    ('assets', BASE_DIR.parent / 'assets'),
]

STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage',
    },
}

# Frontend HTML pages directory
FRONTEND_DIR = BASE_DIR.parent

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# REST Framework
REST_FRAMEWORK = {
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.AllowAny',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 20
}

# CORS
CORS_ALLOWED_ORIGINS = [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://127.0.0.1:5500",
    "http://localhost:5500",
    "http://127.0.0.1:3000",
    "http://localhost:3000",
]
if RENDER_EXTERNAL_HOSTNAME:
    CORS_ALLOWED_ORIGINS.append(f'https://{RENDER_EXTERNAL_HOSTNAME}')

CORS_ALLOW_ALL_ORIGINS = DEBUG

# Telegram Bot
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_GROUP_ID = os.getenv('TELEGRAM_GROUP_ID', '')
TELEGRAM_CHANNEL_ID = os.getenv('TELEGRAM_CHANNEL_ID', '')
WEBHOOK_HOST = os.getenv('WEBHOOK_HOST', '').rstrip('/')
if not WEBHOOK_HOST and RENDER_EXTERNAL_HOSTNAME:
    WEBHOOK_HOST = f'https://{RENDER_EXTERNAL_HOSTNAME}'

TELEGRAM_WEBHOOK_PATH = os.getenv('TELEGRAM_WEBHOOK_PATH', '/bot/webhook/').strip() or '/bot/webhook/'
if not TELEGRAM_WEBHOOK_PATH.startswith('/'):
    TELEGRAM_WEBHOOK_PATH = f'/{TELEGRAM_WEBHOOK_PATH}'
if not TELEGRAM_WEBHOOK_PATH.endswith('/'):
    TELEGRAM_WEBHOOK_PATH = f'{TELEGRAM_WEBHOOK_PATH}/'

TELEGRAM_WEBHOOK_SECRET = os.getenv('TELEGRAM_WEBHOOK_SECRET', '').strip()
TELEGRAM_WEBHOOK_AUTOCONFIGURE = env_bool('TELEGRAM_WEBHOOK_AUTOCONFIGURE', False)
TELEGRAM_WEBHOOK_DROP_PENDING_UPDATES = env_bool('TELEGRAM_WEBHOOK_DROP_PENDING_UPDATES', False)

# Base site URL (for SEO and payment return links)
SITE_URL = os.getenv('SITE_URL', '').rstrip('/')

# YooKassa
YOOKASSA_SHOP_ID = os.getenv('YOOKASSA_SHOP_ID', '')
YOOKASSA_SECRET_KEY = os.getenv('YOOKASSA_SECRET_KEY', '')
YOOKASSA_RETURN_URL = os.getenv('YOOKASSA_RETURN_URL', SITE_URL)

# Temporary / manual payment fallback (used when YooKassa is not configured).
# Supports placeholders: {order_id}, {amount}, {telegram_user_id}.
MANUAL_PAYMENT_URL_TEMPLATE = os.getenv('MANUAL_PAYMENT_URL_TEMPLATE', '')

# Promo settings
PROMO_DISCOUNT_PERCENT = env_int('PROMO_DISCOUNT_PERCENT', 10)
PROMO_ENABLED = env_bool('PROMO_ENABLED', True)

# Maps integration (Google Maps / Yandex Maps)
GOOGLE_MAPS_API_KEY = os.getenv('GOOGLE_MAPS_API_KEY', '')
YANDEX_MAPS_API_KEY = os.getenv('YANDEX_MAPS_API_KEY', '')
MAPS_PLACE_ID = os.getenv('MAPS_PLACE_ID', '')

# Taxi delivery integration
YANDEX_TAXI_API_KEY = os.getenv('YANDEX_TAXI_API_KEY', '')
YANDEX_TAXI_CLID = os.getenv('YANDEX_TAXI_CLID', '')
YANDEX_GEOCODER_API_KEY = os.getenv('YANDEX_GEOCODER_API_KEY', '')
UBER_API_KEY = os.getenv('UBER_API_KEY', '')
TAXI_DELIVERY_SERVICE = os.getenv('TAXI_DELIVERY_SERVICE', 'yandex')  # yandex, uber, custom
