import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "unsafe-development-key")
DEBUG = os.environ.get("DJANGO_DEBUG", "true").lower() == "true"
ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if host.strip()
]
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.accounts",
    "apps.businesses",
    "apps.catalog",
    "apps.attendance",
    "apps.purchasing",
    "apps.inventory",
    "apps.sales",
    "apps.cash",
    "apps.expenses",
    "apps.performance",
    "apps.public_profiles",
    "apps.documents",
    "apps.offline",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "config.middleware.SecurityHeadersMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "config.middleware.AuthenticationRateLimitMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.businesses.middleware.ActiveBusinessMiddleware",
]
if not DEBUG:
    MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.template.context_processors.i18n",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.businesses.context_processors.active_business",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

if os.environ.get("POSTGRES_HOST"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("POSTGRES_DB", "ife"),
            "USER": os.environ.get("POSTGRES_USER", "ife"),
            "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
            "HOST": os.environ["POSTGRES_HOST"],
            "PORT": os.environ.get("POSTGRES_PORT", "5432"),
            "CONN_MAX_AGE": 60,
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en"
LANGUAGES = [
    ("en", "English"),
    ("am", "Amharic"),
    ("om", "Afaan Oromoo"),
]
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = "Africa/Addis_Ababa"
USE_I18N = True
USE_TZ = True

SESSION_COOKIE_AGE = int(os.environ.get("DJANGO_SESSION_COOKIE_AGE", str(8 * 60 * 60)))
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_SAVE_EVERY_REQUEST = True

CACHES = {
    "default": {
        "BACKEND": os.environ.get(
            "DJANGO_CACHE_BACKEND",
            "django.core.cache.backends.locmem.LocMemCache",
        ),
        "LOCATION": os.environ.get("DJANGO_CACHE_LOCATION", "ife-default"),
    }
}
RATE_LIMIT_CACHE_ALIAS = "default"
RATE_LIMIT_BACKEND_APPROVED = (
    os.environ.get("RATE_LIMIT_BACKEND_APPROVED", "false").lower() == "true"
)
LOGIN_RATE_LIMIT = int(os.environ.get("LOGIN_RATE_LIMIT", "10"))
LOGIN_RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get("LOGIN_RATE_LIMIT_WINDOW_SECONDS", "300"))
UPLOAD_RATE_LIMIT = int(os.environ.get("UPLOAD_RATE_LIMIT", "20"))
UPLOAD_RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get("UPLOAD_RATE_LIMIT_WINDOW_SECONDS", "300"))
SYNC_RATE_LIMIT = int(os.environ.get("SYNC_RATE_LIMIT", "120"))
SYNC_RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get("SYNC_RATE_LIMIT_WINDOW_SECONDS", "300"))
PUBLIC_READ_RATE_LIMIT = int(os.environ.get("PUBLIC_READ_RATE_LIMIT", "240"))
PUBLIC_READ_RATE_LIMIT_WINDOW_SECONDS = int(
    os.environ.get("PUBLIC_READ_RATE_LIMIT_WINDOW_SECONDS", "60")
)
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "base-uri 'self'; "
    "connect-src 'self'; "
    "font-src 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'; "
    "img-src 'self' data:; "
    "manifest-src 'self'; "
    "object-src 'none'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "worker-src 'self'"
)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "security_json": {
            "()": "config.logging.SecurityJsonFormatter",
        }
    },
    "handlers": {
        "security_console": {
            "class": "logging.StreamHandler",
            "formatter": "security_json",
        }
    },
    "loggers": {
        "ife.security": {
            "handlers": ["security_console"],
            "level": os.environ.get("SECURITY_LOG_LEVEL", "WARNING"),
            "propagate": False,
        }
    },
}

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATICFILES_BACKEND = (
    "django.contrib.staticfiles.storage.StaticFilesStorage"
    if DEBUG
    else "whitenoise.storage.CompressedManifestStaticFilesStorage"
)
STORAGES: dict[str, dict[str, object]] = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": STATICFILES_BACKEND},
}
DOCUMENT_STORAGE_BACKEND = os.environ.get("DOCUMENT_STORAGE_BACKEND", "filesystem")
if DOCUMENT_STORAGE_BACKEND == "s3":
    STORAGES["documents"] = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": os.environ.get("DOCUMENT_S3_BUCKET", ""),
            "endpoint_url": os.environ.get("DOCUMENT_S3_ENDPOINT_URL") or None,
            "region_name": os.environ.get("DOCUMENT_S3_REGION") or None,
            "access_key": os.environ.get("DOCUMENT_S3_ACCESS_KEY") or None,
            "secret_key": os.environ.get("DOCUMENT_S3_SECRET_KEY") or None,
            "default_acl": None,
            "querystring_auth": True,
            "file_overwrite": False,
        },
    }
else:
    STORAGES["documents"] = {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {
            "location": str(BASE_DIR / "private_media"),
            "base_url": None,
            "file_permissions_mode": 0o600,
            "directory_permissions_mode": 0o700,
        },
    }

CATALOG_MEDIA_STORAGE_BACKEND = os.environ.get("CATALOG_MEDIA_STORAGE_BACKEND", "filesystem")
if CATALOG_MEDIA_STORAGE_BACKEND == "s3":
    STORAGES["catalog_media"] = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": os.environ.get("CATALOG_MEDIA_S3_BUCKET", ""),
            "endpoint_url": os.environ.get("CATALOG_MEDIA_S3_ENDPOINT_URL") or None,
            "region_name": os.environ.get("CATALOG_MEDIA_S3_REGION") or None,
            "access_key": os.environ.get("CATALOG_MEDIA_S3_ACCESS_KEY") or None,
            "secret_key": os.environ.get("CATALOG_MEDIA_S3_SECRET_KEY") or None,
            "default_acl": None,
            "querystring_auth": True,
            "file_overwrite": False,
        },
    }
else:
    STORAGES["catalog_media"] = {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {
            "location": str(BASE_DIR / "catalog_media"),
            "base_url": None,
            "file_permissions_mode": 0o600,
            "directory_permissions_mode": 0o700,
        },
    }

PRODUCT_IMAGE_MAX_FILE_BYTES = 8 * 1024 * 1024
PRODUCT_IMAGE_MAX_PIXELS = 24_000_000
PRODUCT_IMAGE_MAX_EDGE = 2048
TELEBIRR_QR_MAX_FILE_BYTES = 4 * 1024 * 1024
TELEBIRR_QR_MAX_PIXELS = 12_000_000
TELEBIRR_QR_MAX_EDGE = 1600

STAGE12_RECOVERY_ATTESTATION_PATH = os.environ.get(
    "STAGE12_RECOVERY_ATTESTATION_PATH",
    "",
)
STAGE12_MAX_RPO_HOURS = int(os.environ.get("STAGE12_MAX_RPO_HOURS", "1"))
STAGE12_MAX_RTO_HOURS = int(os.environ.get("STAGE12_MAX_RTO_HOURS", "4"))
STAGE12_SECURITY_REVIEW_APPROVED = (
    os.environ.get("STAGE12_SECURITY_REVIEW_APPROVED", "false").lower() == "true"
)
STAGE12_PRIVACY_LEGAL_APPROVED = (
    os.environ.get("STAGE12_PRIVACY_LEGAL_APPROVED", "false").lower() == "true"
)
STAGE12_INCIDENT_RESPONSE_APPROVED = (
    os.environ.get("STAGE12_INCIDENT_RESPONSE_APPROVED", "false").lower() == "true"
)
STAGE12_OPERATOR_TRAINING_APPROVED = (
    os.environ.get("STAGE12_OPERATOR_TRAINING_APPROVED", "false").lower() == "true"
)
STAGE12_TRANSLATION_REVIEW_APPROVED = (
    os.environ.get("STAGE12_TRANSLATION_REVIEW_APPROVED", "false").lower() == "true"
)

DOCUMENT_MAX_FILES = 5
DOCUMENT_MAX_FILE_BYTES = 10 * 1024 * 1024
DOCUMENT_MAX_TOTAL_BYTES = 25 * 1024 * 1024
DOCUMENT_SCANNER_BACKEND = os.environ.get(
    "DOCUMENT_SCANNER_BACKEND",
    "apps.documents.scanning.DevelopmentDocumentScanner",
)
DOCUMENT_SCANNER_HOST = os.environ.get("DOCUMENT_SCANNER_HOST", "127.0.0.1")
DOCUMENT_SCANNER_PORT = int(os.environ.get("DOCUMENT_SCANNER_PORT", "3310"))
DOCUMENT_SCANNER_TIMEOUT_SECONDS = float(os.environ.get("DOCUMENT_SCANNER_TIMEOUT_SECONDS", "10"))
DOCUMENT_CONFIRMED_RETENTION_POLICY_APPROVED = (
    os.environ.get("DOCUMENT_CONFIRMED_RETENTION_POLICY_APPROVED", "false").lower() == "true"
)
DOCUMENT_CANCELLED_RETENTION_DAYS = int(os.environ.get("DOCUMENT_CANCELLED_RETENTION_DAYS", "30"))
DOCUMENT_QUARANTINE_STALE_HOURS = int(os.environ.get("DOCUMENT_QUARANTINE_STALE_HOURS", "24"))
DOCUMENT_ORPHAN_RETENTION_HOURS = int(os.environ.get("DOCUMENT_ORPHAN_RETENTION_HOURS", "24"))

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

PUBLIC_SITE_ORIGIN = os.environ.get("PUBLIC_SITE_ORIGIN", "http://localhost:8000").rstrip("/")
PUBLIC_SUPPORT_URL = os.environ.get("PUBLIC_SUPPORT_URL", "")
PUBLIC_METRIC_RETENTION_MONTHS = 24

SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 3600
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
