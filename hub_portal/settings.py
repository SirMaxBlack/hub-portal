import base64
import hashlib
from pathlib import Path
import os
import sys


BASE_DIR = Path(__file__).resolve().parent.parent
TESTING = "test" in sys.argv
APP_ROOT_DIR = BASE_DIR.parent
RUNTIME_ROOT_DIR = APP_ROOT_DIR / "runtime"


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: list[str]) -> list[str]:
    value = os.environ.get(name)
    if not value:
        return default
    return [item.strip() for item in value.split(",") if item.strip()]


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return int(value.strip())


def build_database_config() -> dict:
    db_name = env("HUB_PORTAL_DB_NAME")
    if not db_name:
        return {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }

    return {
        "ENGINE": "django.db.backends.mysql",
        "NAME": db_name,
        "USER": env("HUB_PORTAL_DB_USER"),
        "PASSWORD": env("HUB_PORTAL_DB_PASSWORD"),
        "HOST": env("HUB_PORTAL_DB_HOST", "127.0.0.1"),
        "PORT": env("HUB_PORTAL_DB_PORT", "3306"),
        "CONN_MAX_AGE": env_int("HUB_PORTAL_DB_CONN_MAX_AGE", 120),
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {
            "charset": "utf8mb4",
            "init_command": "SET sql_mode='STRICT_TRANS_TABLES'",
        },
    }


SECRET_KEY = env("HUB_PORTAL_SECRET_KEY", "CHANGE_ME_SECRET_KEY")
DEBUG = env_bool("HUB_PORTAL_DEBUG", default=False)
HUB_PORTAL_PUBLIC_HOST = env("HUB_PORTAL_PUBLIC_HOST", "hub.example.com").lower()
HUB_PORTAL_PUBLIC_SCHEME = env("HUB_PORTAL_PUBLIC_SCHEME", "https").lower()
HUB_PORTAL_PUBLIC_URL = env(
    "HUB_PORTAL_PUBLIC_URL",
    f"{HUB_PORTAL_PUBLIC_SCHEME}://{HUB_PORTAL_PUBLIC_HOST}",
).rstrip("/")
HUB_PORTAL_SERVICE_DOMAIN_SUFFIX = env(
    "HUB_PORTAL_SERVICE_DOMAIN_SUFFIX",
    f".{HUB_PORTAL_PUBLIC_HOST}",
).lower()
HUB_PORTAL_TOKEN_ENCRYPTION_KEY = env("HUB_PORTAL_TOKEN_ENCRYPTION_KEY")
if not HUB_PORTAL_TOKEN_ENCRYPTION_KEY:
    HUB_PORTAL_TOKEN_ENCRYPTION_KEY = base64.urlsafe_b64encode(
        hashlib.sha256(SECRET_KEY.encode("utf-8")).digest()
    ).decode("ascii")
HUB_PORTAL_INTERNAL_AUTH_KEY = env("HUB_PORTAL_INTERNAL_AUTH_KEY", SECRET_KEY)
HUB_PORTAL_AUDIT_RETENTION_DAYS = env_int("HUB_PORTAL_AUDIT_RETENTION_DAYS", 180)
HUB_PORTAL_AUDIT_MAINTENANCE_LOCK = env(
    "HUB_PORTAL_AUDIT_MAINTENANCE_LOCK",
    "/run/hub-portal/audit-maintenance.lock",
)
HUB_PORTAL_CLEAR_AUDIT_HELPER = env(
    "HUB_PORTAL_CLEAR_AUDIT_HELPER",
    "/usr/local/bin/hub-portal-clear-audit",
)
HUB_PORTAL_SERVICE_ADMIN_HELPER = env(
    "HUB_PORTAL_SERVICE_ADMIN_HELPER",
    "/usr/local/bin/hub-portal-service-admin",
)
HUB_PORTAL_SERVICE_ADMIN_RUNTIME_DIR = env(
    "HUB_PORTAL_SERVICE_ADMIN_RUNTIME_DIR",
    str(RUNTIME_ROOT_DIR / "service-admin"),
)
HUB_PORTAL_LOGIN_PROTECTION_RUNTIME_DIR = env(
    "HUB_PORTAL_LOGIN_PROTECTION_RUNTIME_DIR",
    str(RUNTIME_ROOT_DIR / "login-protection"),
)
HUB_PORTAL_SERVICE_SITE_AVAILABLE_DIR = env(
    "HUB_PORTAL_SERVICE_SITE_AVAILABLE_DIR",
    "/etc/nginx/sites-available",
)
HUB_PORTAL_SERVICE_SITE_ENABLED_DIR = env(
    "HUB_PORTAL_SERVICE_SITE_ENABLED_DIR",
    "/etc/nginx/sites-enabled",
)

ALLOWED_HOSTS = env_list(
    "HUB_PORTAL_ALLOWED_HOSTS",
    [HUB_PORTAL_PUBLIC_HOST, HUB_PORTAL_SERVICE_DOMAIN_SUFFIX, "127.0.0.1", "localhost"],
)

CSRF_TRUSTED_ORIGINS = env_list(
    "HUB_PORTAL_CSRF_TRUSTED_ORIGINS",
    [HUB_PORTAL_PUBLIC_URL, f"{HUB_PORTAL_PUBLIC_SCHEME}://*{HUB_PORTAL_SERVICE_DOMAIN_SUFFIX}"],
)


INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "portal",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "portal.middleware.BrowserIdentityMiddleware",
    "portal.middleware.AccessControlMiddleware",
    "portal.middleware.SessionTrackingMiddleware",
    "portal.middleware.MustChangePasswordMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "hub_portal.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "portal.context_processors.release_state",
            ],
        },
    },
]

WSGI_APPLICATION = "hub_portal.wsgi.application"


DATABASES = {
    "default": build_database_config(),
}


AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 8},
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

AUTH_USER_MODEL = "portal.User"

LANGUAGE_CODE = "ru-ru"
TIME_ZONE = env("HUB_PORTAL_TIME_ZONE", "Asia/Yekaterinburg")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if TESTING
            else "hub_portal.storage.HubStaticFilesStorage"
        ),
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "/"
LOGIN_REDIRECT_URL = "/services/"
LOGOUT_REDIRECT_URL = "/"

SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_AGE = 86400
SESSION_SAVE_EVERY_REQUEST = False
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
SESSION_COOKIE_NAME = env("HUB_PORTAL_SESSION_COOKIE_NAME", "hub_portal_session")
SESSION_COOKIE_SECURE = env_bool("HUB_PORTAL_SESSION_COOKIE_SECURE", default=not DEBUG)
SESSION_COOKIE_SAMESITE = "Lax"

session_domain = env("HUB_PORTAL_SESSION_COOKIE_DOMAIN")
if session_domain:
    SESSION_COOKIE_DOMAIN = session_domain

CSRF_COOKIE_SECURE = env_bool("HUB_PORTAL_CSRF_COOKIE_SECURE", default=not DEBUG)
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True
SECURE_SSL_REDIRECT = env_bool(
    "HUB_PORTAL_SECURE_SSL_REDIRECT",
    default=HUB_PORTAL_PUBLIC_SCHEME == "https" and not TESTING,
)
SECURE_HSTS_SECONDS = env_int(
    "HUB_PORTAL_SECURE_HSTS_SECONDS",
    31536000 if HUB_PORTAL_PUBLIC_SCHEME == "https" else 0,
)
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

X_FRAME_OPTIONS = "DENY"
