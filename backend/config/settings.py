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

# The personal account is c4rsys.accounts.UserAccount with its own server-side sessions;
# django.contrib.auth and django.contrib.sessions are intentionally not installed.
INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.postgres",
    "django.contrib.staticfiles",
    "c4rsys.platform",
    "c4rsys.accounts",
    "c4rsys.files",
    "c4rsys.companies",
    "c4rsys.finance",
    "c4rsys.fleet",
    "c4rsys.parties",
    "c4rsys.orders",
    "c4rsys.inspections",
    "c4rsys.rentals",
    "c4rsys.warehouse",
    "c4rsys.service",
    "c4rsys.delivery",
    "c4rsys.tasks",
    "c4rsys.notifications",
    "c4rsys.reports",
    "c4rsys.documents",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "c4rsys.platform.middleware.ApiMiddleware",
    "c4rsys.accounts.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES: list[dict[str, object]] = []

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgres://c4rsys:c4rsys_dev_only@localhost:5432/c4rsys"
)
DATABASES = {
    "default": dj_database_url.parse(DATABASE_URL, conn_max_age=60),
}
# Transactions are explicit per business command (A07), not per HTTP request.
DATABASES["default"]["ATOMIC_REQUESTS"] = False

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

# Content of uploaded files, outside the database (A10). On the server — a separate volume.
C4R_FILES_ROOT = os.environ.get("C4R_FILES_ROOT", str(BASE_DIR / "var" / "files"))

# Cookies and transport security (A08). The session cookie is set by c4rsys.accounts.middleware.
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = not DEBUG

# Passwords and backup codes are stored as Argon2id hashes (ТЗ § 24.4.3).
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
# Technical password policy of c4rsys, accepted by the customer on 22.10.2026:
# at least 8 characters, not a common password, not digits only.
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 8},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", default=True)
    # The container health check calls the server directly over HTTP.
    SECURE_REDIRECT_EXEMPT = [r"^api/health$"]
    SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_HSTS_SECONDS", "0"))

# One-time codes and invitations: "console" writes them to the backend log (development only);
# "email" sends them through the default mailer. The test server uses Amazon SES through
# django-anymail (decision on T20, 23.10.2026); another provider is a change of settings only.
C4R_MAIL_MODE = os.environ.get("C4R_MAIL_MODE", "console")
if C4R_MAIL_MODE not in ("console", "email"):
    raise ImproperlyConfigured("C4R_MAIL_MODE must be console or email")
if C4R_MAIL_MODE == "console" and not DEBUG and not env_bool("C4R_ALLOW_CONSOLE_MAIL", False):
    # Codes in the log are acceptable only on a developer's machine.
    raise ImproperlyConfigured("C4R_MAIL_MODE=console needs DJANGO_DEBUG or C4R_ALLOW_CONSOLE_MAIL")
MAILERS = {
    "default": {
        "BACKEND": os.environ.get(
            "DJANGO_MAIL_BACKEND", "django.core.mail.backends.locmem.EmailBackend"
        ),
    },
}
DEFAULT_FROM_EMAIL = os.environ.get("C4R_MAIL_FROM", "c4rsys <no-reply@c4rsys.com>")
ANYMAIL = {
    "AMAZON_SES_CLIENT_PARAMS": {"region_name": os.environ.get("AWS_SES_REGION", "ap-southeast-2")},
}
# Address of the interface in invitation messages.
C4R_PUBLIC_URL = os.environ.get("C4R_PUBLIC_URL", "http://localhost:5173")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": os.environ.get("DJANGO_LOG_LEVEL", "INFO")},
}
