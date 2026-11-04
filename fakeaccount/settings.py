import base64
import binascii
import math
import mimetypes
import os
import re
import secrets
from ipaddress import ip_network
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

mimetypes.add_type("application/javascript", ".js", strict=True)

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _env_bool(name, default):
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized not in {"true", "false"}:
        raise ImproperlyConfigured(f"{name} must be 'true' or 'false'.")
    return normalized == "true"


def _env_int(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ImproperlyConfigured(f"{name} must be an integer.") from exc


def _env_float(name, default):
    try:
        return float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ImproperlyConfigured(f"{name} must be a number.") from exc


def _get_secret_key(configured_key, debug):
    secret_key = (configured_key or "").strip()
    if not secret_key or secret_key.startswith("replace-with-"):
        if not debug:
            raise ImproperlyConfigured(
                "Set a unique DJANGO_SECRET_KEY of at least 50 characters when DEBUG=False."
            )
        return secrets.token_urlsafe(50)
    if not debug and (
        len(secret_key) < 50
        or secret_key == "django-insecure-dev-only-change-me"
        or secret_key.startswith("django-insecure-")
    ):
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY must be a unique value of at least 50 characters when DEBUG=False."
        )
    return secret_key


DEBUG = _env_bool("DJANGO_DEBUG", False)
SECRET_KEY = _get_secret_key(os.getenv("DJANGO_SECRET_KEY", ""), DEBUG)

ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv(
        "DJANGO_ALLOWED_HOSTS",
        "localhost,127.0.0.1,[::1]" if DEBUG else "",
    ).split(",")
    if host.strip()
]
if not ALLOWED_HOSTS or ("*" in ALLOWED_HOSTS and not DEBUG):
    raise ImproperlyConfigured(
        "Set DJANGO_ALLOWED_HOSTS to explicit host names; '*' is allowed only with DEBUG=True."
    )

CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "DJANGO_CSRF_TRUSTED_ORIGINS",
        "https://127.0.0.1:8000,https://localhost:8000",
    ).split(",")
    if origin.strip()
]
if not DEBUG and any(
    not origin.startswith("https://") for origin in CSRF_TRUSTED_ORIGINS
):
    raise ImproperlyConfigured(
        "Production DJANGO_CSRF_TRUSTED_ORIGINS must contain HTTPS origins only."
    )
try:
    TRUSTED_PROXY_IPS = tuple(
        ip_network(value.strip(), strict=False)
        for value in os.getenv("DJANGO_TRUSTED_PROXY_IPS", "").split(",")
        if value.strip()
    )
except ValueError as exc:
    raise ImproperlyConfigured(
        "DJANGO_TRUSTED_PROXY_IPS must contain valid IP addresses or CIDR networks."
    ) from exc

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "axes",
    "fakeaccountapp.apps.FakeaccountappConfig",
]

MIDDLEWARE = [
    "fakeaccountapp.observability.RequestCorrelationMiddleware",
    "fakeaccountapp.middleware.TrustedProxyMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "axes.middleware.AxesMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "fakeaccount.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "fakeaccount.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    },
}

database_choice = os.getenv("DJANGO_DATABASE", "sqlite").lower()
if database_choice not in {"sqlite", "postgres"}:
    raise ImproperlyConfigured("DJANGO_DATABASE must be 'sqlite' or 'postgres'.")
if database_choice == "postgres" and not os.environ.get("POSTGRES_DB"):
    raise ImproperlyConfigured(
        "POSTGRES_DB must be set when DJANGO_DATABASE=postgres."
    )

if os.environ.get("POSTGRES_DB"):
    postgres_database = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ["POSTGRES_DB"],
        "USER": os.environ.get("POSTGRES_USER", ""),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
    DATABASES["postgres"] = postgres_database
    if database_choice == "postgres":
        DATABASES["default"] = postgres_database

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        )
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_REDIRECT_URL = "/account/"
LOGIN_URL = "/login/"
LOGOUT_REDIRECT_URL = "/login/"
SECURE_BACKUP_DIR = Path(
    os.environ.get("SECURE_BACKUP_DIR", BASE_DIR / "secure_backups")
)

BACKUP_ACTIVE_KEY_ID = os.getenv("BACKUP_ACTIVE_KEY_ID", "development")
if not re.fullmatch(r"[a-zA-Z0-9._-]{1,64}", BACKUP_ACTIVE_KEY_ID):
    raise ImproperlyConfigured("BACKUP_ACTIVE_KEY_ID contains invalid characters.")
BACKUP_ENCRYPTION_KEYS = {}
for entry in os.getenv("BACKUP_ENCRYPTION_KEYS", "").split(","):
    if not entry.strip():
        continue
    try:
        key_id, encoded_key = entry.strip().split(":", 1)
        decoded_key = base64.b64decode(
            encoded_key.encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, UnicodeEncodeError, binascii.Error) as exc:
        raise ImproperlyConfigured(
            "BACKUP_ENCRYPTION_KEYS entries must use key-id:base64-key format."
        ) from exc
    if not re.fullmatch(r"[a-zA-Z0-9._-]{1,64}", key_id) or len(decoded_key) != 32:
        raise ImproperlyConfigured(
            "Each backup key needs a non-empty ID and exactly 32 decoded bytes."
        )
    if key_id in BACKUP_ENCRYPTION_KEYS:
        raise ImproperlyConfigured("BACKUP_ENCRYPTION_KEYS contains a duplicate key ID.")
    BACKUP_ENCRYPTION_KEYS[key_id] = decoded_key

if BACKUP_ENCRYPTION_KEYS:
    if BACKUP_ACTIVE_KEY_ID not in BACKUP_ENCRYPTION_KEYS:
        raise ImproperlyConfigured(
            "BACKUP_ACTIVE_KEY_ID must identify a key in BACKUP_ENCRYPTION_KEYS."
        )
elif not DEBUG:
    raise ImproperlyConfigured(
        "Configure BACKUP_ENCRYPTION_KEYS and BACKUP_ACTIVE_KEY_ID when DEBUG=False."
    )

BACKUP_LEGACY_SECRET_KEY = (
    os.getenv("BACKUP_LEGACY_SECRET_KEY", "").strip() or SECRET_KEY
)
BACKUP_RETENTION_COUNT = _env_int("BACKUP_RETENTION_COUNT", 10)
if BACKUP_RETENTION_COUNT < 1:
    raise ImproperlyConfigured("BACKUP_RETENTION_COUNT must be at least 1.")
BACKUP_LOCK_TIMEOUT = _env_float("BACKUP_LOCK_TIMEOUT", 30)
if not math.isfinite(BACKUP_LOCK_TIMEOUT) or BACKUP_LOCK_TIMEOUT <= 0:
    raise ImproperlyConfigured("BACKUP_LOCK_TIMEOUT must be greater than zero.")
BACKUP_PROCESSING_LEASE_SECONDS = _env_int("BACKUP_PROCESSING_LEASE_SECONDS", 900)
if BACKUP_PROCESSING_LEASE_SECONDS < 1:
    raise ImproperlyConfigured("BACKUP_PROCESSING_LEASE_SECONDS must be at least 1.")
BACKUP_MAX_ATTEMPTS = _env_int("BACKUP_MAX_ATTEMPTS", 5)
if BACKUP_MAX_ATTEMPTS < 1:
    raise ImproperlyConfigured("BACKUP_MAX_ATTEMPTS must be at least 1.")

AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = 1
AXES_RESET_ON_SUCCESS = True

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_HTTPONLY = False

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = _env_bool("DJANGO_SECURE_SSL_REDIRECT", not DEBUG)
SECURE_HSTS_SECONDS = _env_int("DJANGO_HSTS_SECONDS", 0)
if SECURE_HSTS_SECONDS < 0:
    raise ImproperlyConfigured("DJANGO_HSTS_SECONDS cannot be negative.")
SECURE_HSTS_INCLUDE_SUBDOMAINS = _env_bool("DJANGO_HSTS_INCLUDE_SUBDOMAINS", False)
SECURE_HSTS_PRELOAD = _env_bool("DJANGO_HSTS_PRELOAD", False)
if SECURE_HSTS_PRELOAD and (
    SECURE_HSTS_SECONDS < 31_536_000 or not SECURE_HSTS_INCLUDE_SUBDOMAINS
):
    raise ImproperlyConfigured(
        "HSTS preload requires at least one year and subdomain coverage."
    )
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "()": "fakeaccountapp.observability.StructuredJsonFormatter",
        },
    },
    "handlers": {
        "application_console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
        },
    },
    "loggers": {
        "fakeaccount.security": {
            "handlers": ["application_console"],
            "level": "INFO",
            "propagate": False,
        },
        "fakeaccount.access": {
            "handlers": ["application_console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
