"""Django settings for c4rsys.

Values come from the environment; secrets never live in the repository (A11, DEV_CONVENTIONS).
"""

import os
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


DEBUG = env_bool("DJANGO_DEBUG", default=False)

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "dev-only-insecure-key-do-not-use-outside-local"  # noqa: S105
    else:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is off")

ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,backend").split(",")
    if h.strip()
]

# T00: no business models yet. The custom user model (accounts.UserAccount) must be added in T02
# BEFORE django.contrib.auth or any app with a user foreign key is migrated for the first time.
INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "c4rsys.platform",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES: list[dict[str, object]] = []

DATABASE_URL = os.environ.get("DATABASE_URL", "postgres://c4rsys:c4rsys_dev_only@localhost:5432/c4rsys")
DATABASES = {
    "default": dj_database_url.parse(DATABASE_URL, conn_max_age=60),
}
DATABASES["default"]["ATOMIC_REQUESTS"] = False  # transactions are explicit per business command (A07)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Time: moments are stored in UTC; display uses the location time zone (ТЗ § 4.2.2).
USE_TZ = True
TIME_ZONE = "UTC"

# Interface languages of the MVP (ТЗ § 2.7.1). Texts come from prepared translations.
LANGUAGE_CODE = "en"
LANGUAGES = [("en", "English"), ("ru", "Русский"), ("th", "ไทย")]
USE_I18N = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Cookies and transport security (A08). Server-side sessions are implemented in T02.
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", default=True)
    SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_HSTS_SECONDS", "0"))

# Development: one-time codes are printed to the console instead of being emailed.
EMAIL_BACKEND = os.environ.get(
    "DJANGO_EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend"
)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": os.environ.get("DJANGO_LOG_LEVEL", "INFO")},
}
