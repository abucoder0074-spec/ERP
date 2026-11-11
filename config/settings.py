from decimal import Decimal
from os import environ
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from config.environment import csv, integer, redis_url, required, trusted_proxies, vat_rate

BASE_DIR = Path(__file__).resolve().parent.parent
APP_ENV = environ.get("APP_ENV", "production")
if APP_ENV not in {"development", "production"}:
    raise ImproperlyConfigured("APP_ENV must be development or production")
DEBUG = APP_ENV == "development"
SECRET_KEY = required("DJANGO_SECRET_KEY")
if (
    len(SECRET_KEY) < 50
    or len(set(SECRET_KEY)) < 5
    or (not DEBUG and SECRET_KEY.startswith(("django-insecure-", "dev-only-")))
):
    raise ImproperlyConfigured("DJANGO_SECRET_KEY must be a strong deployment-specific key")
ALLOWED_HOSTS = csv("DJANGO_ALLOWED_HOSTS")
if not ALLOWED_HOSTS or any("*" in host for host in ALLOWED_HOSTS):
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS must list explicit hosts")

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "rest_framework",
    "apps.core",
    "apps.masterdata",
    "apps.inventory",
    "apps.procurement",
]
MIDDLEWARE = [
    "apps.core.middleware.RequestContextMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "apps.core.middleware.WorkspaceLocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
            ],
        },
    },
]
POSTGRES_PASSWORD = required("POSTGRES_PASSWORD")
if not DEBUG and len(POSTGRES_PASSWORD) < 24:
    raise ImproperlyConfigured("POSTGRES_PASSWORD must be strong in production")
LOGIN_REDIS_URL = redis_url("LOGIN_REDIS_URL", production=not DEBUG)
CELERY_BROKER_URL = redis_url("CELERY_BROKER_URL", production=not DEBUG)
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_TIMEZONE = "UTC"
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": required("POSTGRES_DB"),
        "USER": required("POSTGRES_USER"),
        "PASSWORD": POSTGRES_PASSWORD,
        "HOST": required("POSTGRES_HOST"),
        "PORT": integer("POSTGRES_PORT", 5432, 1, 65535),
        "OPTIONS": {"connect_timeout": 3},
    },
}
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
SESSION_COOKIE_AGE = integer("SESSION_MAX_AGE", 1800, 60, 86400)
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
CSRF_FAILURE_VIEW = "apps.core.views.csrf_failure"
SECURE_SSL_REDIRECT = not DEBUG
SECURE_REDIRECT_EXEMPT = [r"^health/$"]
TRUSTED_PROXY_IPS = trusted_proxies()
if integer("TRUST_PROXY_HTTPS", 0, 0, 1):
    if not TRUSTED_PROXY_IPS:
        raise ImproperlyConfigured("TRUST_PROXY_HTTPS requires TRUSTED_PROXY_IPS")
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 0
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["apps.core.api_permissions.ModelPermissions"],
}
LANGUAGE_CODE = "uz"
LANGUAGES = [("uz", "Oʻzbekcha"), ("ru", "Русский"), ("en", "English")]
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = "Asia/Tashkent"
USE_TZ = True
FORMAT_MODULE_PATH = "config.formats"
USE_THOUSAND_SEPARATOR = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}
DATA_UPLOAD_MAX_MEMORY_SIZE = 64 * 1024
DATA_UPLOAD_MAX_NUMBER_FILES = 1
FILE_UPLOAD_HANDLERS = [
    "apps.inventory.uploads.BoundedUploadHandler",
    "django.core.files.uploadhandler.MemoryFileUploadHandler",
    "django.core.files.uploadhandler.TemporaryFileUploadHandler",
]
VAT_RATE_PERCENT = vat_rate()
LOGIN_ATTEMPTS = integer("LOGIN_ATTEMPTS", 10, 1, 100)
LOGIN_WINDOW_SECONDS = integer("LOGIN_WINDOW_SECONDS", 900, 10, 3600)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"safe_json": {"()": "apps.core.middleware.RequestLogFormatter"}},
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "safe_json"},
    },
    "loggers": {
        "erp.request": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "django": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}

WRITE_OFF_LIMIT_UZS = Decimal(integer("WRITE_OFF_LIMIT_UZS", 100000, 0, 1000000000))
STOCK_EXPIRY_WARNING_DAYS = integer("STOCK_EXPIRY_WARNING_DAYS", 7, 1, 365)
CELERY_BEAT_SCHEDULE = {
    "refresh-stock-alerts": {
        "task": "apps.inventory.tasks.refresh_stock_alerts",
        "schedule": 300,
    },
}

PROCUREMENT_OVER_DELIVERY_PERCENT = Decimal(integer("PROCUREMENT_OVER_DELIVERY_PERCENT", 5, 0, 100))
PROCUREMENT_QUANTITY_MATCH_PERCENT = Decimal(
    integer("PROCUREMENT_QUANTITY_MATCH_PERCENT", 0, 0, 100)
)
PROCUREMENT_PRICE_MATCH_PERCENT = Decimal(integer("PROCUREMENT_PRICE_MATCH_PERCENT", 1, 0, 100))
PROCUREMENT_QUARANTINE_DRY = bool(integer("PROCUREMENT_QUARANTINE_DRY", 0, 0, 1))
PROCUREMENT_QUARANTINE_COLD = bool(integer("PROCUREMENT_QUARANTINE_COLD", 1, 0, 1))
PROCUREMENT_ATTACHMENT_LIMIT = integer("PROCUREMENT_ATTACHMENT_LIMIT", 2097152, 1024, 2097152)
PROCUREMENT_SCAN_HOOK = environ.get(
    "PROCUREMENT_SCAN_HOOK", "apps.procurement.services.attachments.no_op_scan"
)
CELERY_BEAT_SCHEDULE["refresh-overdue-deliveries"] = {
    "task": "apps.procurement.tasks.refresh_overdue_deliveries",
    "schedule": 300,
}
