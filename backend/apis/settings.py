from pathlib import Path
import os
import ssl
from datetime import timedelta
from urllib.parse import urlparse
from dotenv import load_dotenv
import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent
# Load .env from backend directory so Django and Celery see the same vars no matter where the process is started
load_dotenv(BASE_DIR / ".env")
os.makedirs(BASE_DIR / 'logs', exist_ok=True)


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


DEBUG = _as_bool(os.getenv('DEBUG', 'False'))
# Expose GET /api/schema/ when True (needed for CI registry sync against production). Swagger UI stays DEBUG-only.
ENABLE_OPENAPI_SCHEMA = _as_bool(os.getenv('ENABLE_OPENAPI_SCHEMA', 'False'))
SECRET_KEY = os.getenv('SECRET_KEY', '')
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = 'django-insecure-local-dev-key'
    else:
        raise RuntimeError("SECRET_KEY must be set when DEBUG=False")

# Backend's own URL in production (e.g. https://saramsa-backend-xxx.centralus-01.azurewebsites.net).
# When set, this host is added to ALLOWED_HOSTS so Azure can route to the app.
BACKEND_BASE_URL = os.getenv('BACKEND_BASE_URL', '').rstrip('/')
FRONTEND_BASE_URL = os.getenv('FRONTEND_BASE_URL', '').rstrip('/')

_allowed_hosts_env = os.getenv("ALLOWED_HOSTS", "")
if _allowed_hosts_env.strip():
    _allowed = [h.strip() for h in _allowed_hosts_env.split(",") if h.strip()]
elif DEBUG:
    _allowed = ["127.0.0.1", "localhost"]
else:
    raise RuntimeError("ALLOWED_HOSTS must be set when DEBUG=False")

if BACKEND_BASE_URL:
    _backend_host = urlparse(BACKEND_BASE_URL).netloc or BACKEND_BASE_URL
    if _backend_host and _backend_host not in _allowed:
        _allowed.append(_backend_host)
ALLOWED_HOSTS = _allowed

APPLICATIONINSIGHTS_CONNECTION_STRING = os.getenv(
    'APPLICATIONINSIGHTS_CONNECTION_STRING',
    'InstrumentationKey=00000000-0000-0000-0000-000000000000'
)

# OpenTelemetry is initialized in apis/otel.py based on APPLICATIONINSIGHTS_CONNECTION_STRING

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {process:d} {thread:d} {message}',
            'style': '{',
        },
        'simple': {
            'format': '{levelname} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'level': 'INFO',
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
        'api_file': {
            'level': 'DEBUG' if DEBUG else 'INFO',
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': BASE_DIR / 'logs' / 'api.log',
            'maxBytes': 10 * 1024 * 1024,  # 10 MB
            'backupCount': 3,
            'formatter': 'verbose',
        },
        'celery_file': {
            'level': 'DEBUG' if DEBUG else 'INFO',
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': BASE_DIR / 'logs' / 'celery.log',
            'maxBytes': 10 * 1024 * 1024,  # 10 MB
            'backupCount': 3,
            'formatter': 'verbose',
        },
    },
    'loggers': {
        # --- API / Django loggers → api.log ---
        'django': {
            'handlers': ['console', 'api_file'],
            'level': 'INFO',
            'propagate': False,
        },
        'django.request': {
            'handlers': ['console', 'api_file'],
            'level': 'DEBUG' if DEBUG else 'WARNING',
            'propagate': False,
        },
        'apis.infrastructure.middleware': {
            'handlers': ['console', 'api_file'],
            'level': 'INFO',
            'propagate': False,
        },
        'apis.app': {
            'handlers': ['console', 'api_file'],
            'level': 'INFO',
            'propagate': False,
        },
        'apis.prompts': {
            'handlers': ['console', 'api_file'],
            'level': 'INFO',
            'propagate': False,
        },
        # --- Celery / background task loggers → celery.log ---
        'celery': {
            'handlers': ['console', 'celery_file'],
            'level': 'INFO',
            'propagate': False,
        },
        'celery.task': {
            'handlers': ['console', 'celery_file'],
            'level': 'INFO',
            'propagate': False,
        },
        'feedback_analysis': {
            'handlers': ['console', 'celery_file'],
            'level': 'INFO',
            'propagate': False,
        },
        'feedback_analysis.services': {
            'handlers': ['console', 'celery_file'],
            'level': 'DEBUG' if DEBUG else 'INFO',
            'propagate': False,
        },
        'feedback_analysis.services.ml': {
            'handlers': ['console', 'celery_file'],
            'level': 'DEBUG' if DEBUG else 'INFO',
            'propagate': False,
        },
        'feedback_analysis.services.ml.embedding_service': {
            'handlers': ['console', 'celery_file'],
            'level': 'INFO',
            'propagate': False,
        },
        'feedback_analysis.services.ml.local_sentiment_service': {
            'handlers': ['console', 'celery_file'],
            'level': 'INFO',
            'propagate': False,
        },
        'feedback_analysis.services.ml.local_processing_service': {
            'handlers': ['console', 'celery_file'],
            'level': 'INFO',
            'propagate': False,
        },
        # --- Suppress verbose Azure SDK logging ---
        'azure': {
            'handlers': ['api_file'],
            'level': 'ERROR',
            'propagate': False,
        },
        'azure.core.pipeline.policies.http_logging_policy': {
            'handlers': ['api_file'],
            'level': 'ERROR',
            'propagate': False,
        },
    },
    'root': {
        'handlers': ['console', 'api_file'],
        'level': 'INFO',
    },
}

# Celery Configuration - Azure Redis
CELERY_BROKER_URL = os.getenv('CELERY_BROKER_URL')  # Required for Azure Redis Cache
CELERY_RESULT_BACKEND = os.getenv('CELERY_RESULT_BACKEND')  # Required for Azure Redis Cache

_redis_ssl_cert_reqs_env = os.getenv("REDIS_SSL_CERT_REQS", "required").strip().lower()
_redis_ssl_cert_map = {
    "required": ssl.CERT_REQUIRED,
    "optional": ssl.CERT_OPTIONAL,
    "none": ssl.CERT_NONE,
}
if _redis_ssl_cert_reqs_env not in _redis_ssl_cert_map:
    raise RuntimeError("REDIS_SSL_CERT_REQS must be one of: required, optional, none")
_redis_ssl_cert_reqs = _redis_ssl_cert_map[_redis_ssl_cert_reqs_env]
_redis_ssl_cert_reqs_url = _redis_ssl_cert_reqs_env

# Add SSL certificate requirements to Redis URLs for Azure Redis
if CELERY_BROKER_URL and CELERY_BROKER_URL.startswith('rediss://'):
    # Append ssl_cert_reqs parameter to URL if not already present
    if 'ssl_cert_reqs' not in CELERY_BROKER_URL:
        separator = '&' if '?' in CELERY_BROKER_URL else '?'
        CELERY_BROKER_URL = f"{CELERY_BROKER_URL}{separator}ssl_cert_reqs={_redis_ssl_cert_reqs_url}"
        # Update environment variable for celery.py
        os.environ['CELERY_BROKER_URL'] = CELERY_BROKER_URL
    CELERY_BROKER_TRANSPORT_OPTIONS = {
        'ssl_cert_reqs': _redis_ssl_cert_reqs,
    }
else:
    CELERY_BROKER_TRANSPORT_OPTIONS = {}

if CELERY_RESULT_BACKEND and CELERY_RESULT_BACKEND.startswith('rediss://'):
    # Append ssl_cert_reqs parameter to URL if not already present
    if 'ssl_cert_reqs' not in CELERY_RESULT_BACKEND:
        separator = '&' if '?' in CELERY_RESULT_BACKEND else '?'
        CELERY_RESULT_BACKEND = f"{CELERY_RESULT_BACKEND}{separator}ssl_cert_reqs={_redis_ssl_cert_reqs_url}"
        # Update environment variable for celery.py
        os.environ['CELERY_RESULT_BACKEND'] = CELERY_RESULT_BACKEND
    CELERY_RESULT_BACKEND_TRANSPORT_OPTIONS = {
        'ssl_cert_reqs': _redis_ssl_cert_reqs,
    }
else:
    CELERY_RESULT_BACKEND_TRANSPORT_OPTIONS = {}

CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = 'UTC'

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL must be set. Neon PostgreSQL is required and no local DB fallback is supported."
    )

_database_host = (urlparse(DATABASE_URL).hostname or "").lower()
_test_db_bypass = os.getenv("DJANGO_TEST_MODE") == "1" and DEBUG
if not _database_host.endswith(".neon.tech") and not _test_db_bypass:
    raise RuntimeError(
        f"DATABASE_URL must point to Neon PostgreSQL (.neon.tech). Current host: '{_database_host or 'missing'}'."
    )

DATABASES = {
    "default": dj_database_url.parse(
        DATABASE_URL,
        conn_max_age=int(os.getenv("DB_CONN_MAX_AGE", "600")),
        ssl_require=_as_bool(os.getenv("DB_SSL_REQUIRE", "true")),
    )
}

# Neon best-practice: use the *-pooler.* hostname in DATABASE_URL so PgBouncer
# handles connection reuse.  CONN_HEALTH_CHECKS avoids handing a stale pooled
# connection to a request (Django 4.1+).
CONN_HEALTH_CHECKS = True
if "-pooler." not in _database_host:
    import warnings
    warnings.warn(
        "DATABASE_URL does not use the Neon connection-pooler hostname "
        "(expected '-pooler.' in host). Cold-start latency will be higher. "
        "See https://neon.tech/docs/connect/connection-pooling",
        stacklevel=1,
    )

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = _as_bool(os.getenv("SECURE_SSL_REDIRECT", "true"))
    SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
    SESSION_COOKIE_SAMESITE = "Lax"
    CSRF_COOKIE_SAMESITE = "Lax"

INSTALLED_APPS = [
    'django.contrib.contenttypes',
    'django.contrib.auth',
    'django.contrib.sessions',
    'django.contrib.staticfiles',
    'rest_framework',
    'rest_framework_simplejwt.token_blacklist',
    'drf_spectacular',
    'aiCore',
    'feedback_analysis',
    'work_items',
    'corsheaders',
    'authentication',
    'integrations',
    'billing',
]

# Custom authentication backend for PostgreSQL
AUTHENTICATION_BACKENDS = [
    'authentication.authentication.AppAuthenticationBackend',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    # Custom logging middleware
    'apis.infrastructure.middleware.RequestResponseLoggingMiddleware',
    'apis.infrastructure.performance_middleware.PerformanceTrackingMiddleware',
    'apis.infrastructure.performance_middleware.DatabaseQueryCountMiddleware',
    'apis.infrastructure.middleware.SecurityLoggingMiddleware',
]

ROOT_URLCONF = 'apis.urls'
APPEND_SLASH = False

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
            ],
        },
    },
]

WSGI_APPLICATION = 'apis.wsgi.application'

# Password validation
# https://docs.djangoproject.com/en/5.2/ref/settings/#auth-password-validators

# Password validation disabled - using custom PostgreSQL authentication
# AUTH_PASSWORD_VALIDATORS = []


# Internationalization
# https://docs.djangoproject.com/en/5.2/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.2/howto/static-files/

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

DATA_UPLOAD_MAX_MEMORY_SIZE = int(os.getenv("DATA_UPLOAD_MAX_MEMORY_SIZE", str(10 * 1024 * 1024)))  # 10 MB
FILE_UPLOAD_MAX_MEMORY_SIZE = int(os.getenv("FILE_UPLOAD_MAX_MEMORY_SIZE", str(10 * 1024 * 1024)))  # 10 MB

# Default primary key field type
# PostgreSQL is used for all data storage - no Django models needed

CORS_ALLOW_ALL_ORIGINS = False

# Optional: Allow cookies (if using session/auth)
CORS_ALLOW_CREDENTIALS = True

CORS_ALLOWED_ORIGINS = [
    'https://agent.saramsa.ai',
]
if DEBUG:
    CORS_ALLOWED_ORIGINS += [
        'http://localhost:3000',
        'http://localhost:3001',
        'http://localhost:8000',
        'http://localhost',
    ]
# Extra CORS origins from env (comma-separated), e.g. production frontend URL
_cors_extra = os.getenv('CORS_EXTRA_ORIGINS', '')
if _cors_extra:
    CORS_ALLOWED_ORIGINS = list(CORS_ALLOWED_ORIGINS) + [o.strip() for o in _cors_extra.split(',') if o.strip()]

# Regex CORS allowlist (comma-separated regex patterns). Lets us match Vercel
# preview deployments like https://saramsa-ai-<branch-hash>-<team>.vercel.app
# without enumerating every preview URL. Empty default keeps prod strict.
_cors_regexes = os.getenv('CORS_ALLOWED_ORIGIN_REGEXES', '')
if _cors_regexes:
    CORS_ALLOWED_ORIGIN_REGEXES = [r.strip() for r in _cors_regexes.split(',') if r.strip()]

CORS_ALLOW_METHODS = [
    'GET',
    'POST',
    'PUT',
    'PATCH',
    'DELETE',
    'OPTIONS',
]

CORS_ALLOW_HEADERS = [
    'Accept',
    'Content-Type',
    'Authorization',
    'X-Requested-With',
    'X-CSRFToken',
    'Origin',
    'Referer',
]

# Trust requests coming from the static frontend domain (needed for some POSTs)
CSRF_TRUSTED_ORIGINS = [
    'https://agent.saramsa.ai',
]
if DEBUG:
    CSRF_TRUSTED_ORIGINS += [
        'http://localhost:3000',
        'http://localhost:3001',
        'http://localhost:8000',
        'http://localhost',
    ]
# Extra CSRF origins from env (comma-separated), e.g. production frontend URL
_csrf_extra = os.getenv('CSRF_EXTRA_ORIGINS', '')
if _csrf_extra:
    CSRF_TRUSTED_ORIGINS = list(CSRF_TRUSTED_ORIGINS) + [o.strip() for o in _csrf_extra.split(',') if o.strip()]

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'authentication.authentication.AppJWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.LimitOffsetPagination',
    'PAGE_SIZE': int(os.getenv('DEFAULT_PAGE_SIZE', '50')),
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.UserRateThrottle',
        'rest_framework.throttling.AnonRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'user': os.getenv('THROTTLE_RATE_USER', '120/minute'),
        'anon': os.getenv('THROTTLE_RATE_ANON', '30/minute'),
        'analysis': os.getenv('THROTTLE_RATE_ANALYSIS', '30/hour'),
        'upload': os.getenv('THROTTLE_RATE_UPLOAD', '60/hour'),
        'work_items': os.getenv('THROTTLE_RATE_WORK_ITEMS', '60/hour'),
    },
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'EXCEPTION_HANDLER': 'apis.core.exceptions.custom_exception_handler',
}

SIMPLE_JWT = {
    'AUTH_TOKEN_CLASSES': ('rest_framework_simplejwt.tokens.AccessToken',),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'ACCESS_TOKEN_LIFETIME': timedelta(hours=1),  # 1 hour access token
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),    # Longer-lived refresh token
    'AUTH_HEADER_TYPES': ('Bearer',),
    'UPDATE_LAST_LOGIN': True,
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'user_id',
    'TOKEN_TYPE_CLAIM': 'token_type',
    'JTI_CLAIM': 'jti',
    'TOKEN_USER_CLASS': 'rest_framework_simplejwt.models.TokenUser',
    'SLIDING_TOKEN_REFRESH_EXP_CLAIM': 'refresh_exp',
    'SLIDING_TOKEN_LIFETIME': timedelta(hours=1),
    'SLIDING_TOKEN_REFRESH_LIFETIME': timedelta(days=7),
}


# Performance Configuration
SLOW_REQUEST_THRESHOLD = float(os.getenv('SLOW_REQUEST_THRESHOLD', '2.0'))  # seconds
VERY_SLOW_REQUEST_THRESHOLD = float(os.getenv('VERY_SLOW_REQUEST_THRESHOLD', '5.0'))  # seconds

REDIS_URL = os.getenv('REDIS_URL')  

# Database performance tuning knobs
DB_CONNECTION_POOL_SIZE = int(os.getenv('DB_CONNECTION_POOL_SIZE', '10'))
DB_REQUEST_TIMEOUT = int(os.getenv('DB_REQUEST_TIMEOUT', '30'))
DB_RETRY_TOTAL = int(os.getenv('DB_RETRY_TOTAL', '3'))

# Local ML Pipeline Configuration
USE_LOCAL_PIPELINE = os.getenv('USE_LOCAL_PIPELINE', 'false').lower() == 'true'
AZURE_DEVOPS_ORGANIZATION = os.getenv('AZURE_DEVOPS_ORGANIZATION')
AZURE_DEVOPS_PROJECT = os.getenv('AZURE_DEVOPS_PROJECT')
AZURE_DEVOPS_PAT = os.getenv('AZURE_DEVOPS_PAT')

# Jira Configuration - Optional, but required if using Jira
JIRA_EMAIL = os.getenv('JIRA_EMAIL')
JIRA_API_TOKEN = os.getenv('JIRA_API_TOKEN')
JIRA_PROJECT_KEY = os.getenv('JIRA_PROJECT_KEY')
JIRA_DOMAIN = os.getenv('JIRA_DOMAIN')

# Slack OAuth Configuration
SLACK_CLIENT_ID = os.getenv('SLACK_CLIENT_ID', '').strip()
SLACK_CLIENT_SECRET = os.getenv('SLACK_CLIENT_SECRET', '').strip()
SLACK_REDIRECT_URI = os.getenv('SLACK_REDIRECT_URI', '').strip()
SLACK_TEAM_ID = os.getenv('SLACK_TEAM_ID', '').strip()

# Stripe Billing Configuration
STRIPE_SECRET_KEY = os.getenv('STRIPE_SECRET_KEY', '').strip()
STRIPE_PUBLISHABLE_KEY = os.getenv('STRIPE_PUBLISHABLE_KEY', '').strip()
STRIPE_WEBHOOK_SECRET = os.getenv('STRIPE_WEBHOOK_SECRET', '').strip()
STRIPE_DEFAULT_PRICE_ID = os.getenv('STRIPE_DEFAULT_PRICE_ID', '').strip()

# Swagger/OpenAPI Configuration
SPECTACULAR_SETTINGS = {
    'TITLE': 'Saramsa API',
    'DESCRIPTION': 'API documentation for Saramsa application',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
    'COMPONENT_SPLIT_REQUEST': True,
    'SCHEMA_PATH_PREFIX': '/api/',
    'SWAGGER_UI_SETTINGS': {
        'deepLinking': True,
        'persistAuthorization': True,
        'displayOperationId': True,
    },
    'SECURITY': [
        {
            'Bearer': []
        }
    ],
    'TAGS': [
        {'name': 'auth', 'description': 'Authentication endpoints'},
        {'name': 'insights', 'description': 'Insights generation endpoints'},
        {'name': 'workitems', 'description': 'DevOps work items endpoints'},
        {'name': 'upload', 'description': 'File upload endpoints'},
    ],
}

# Email configuration (password reset + registration OTP)
EMAIL_BACKEND = os.getenv('EMAIL_BACKEND') or (
    'django.core.mail.backends.console.EmailBackend' if DEBUG else 'django.core.mail.backends.smtp.EmailBackend'
)
EMAIL_HOST = os.getenv('EMAIL_HOST', '')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', '587'))
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')
EMAIL_USE_TLS = os.getenv('EMAIL_USE_TLS', 'True').lower() == 'true'
EMAIL_USE_SSL = os.getenv('EMAIL_USE_SSL', 'False').lower() == 'true'
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'no-reply@saramsa.ai')
PASSWORD_RESET_FROM_EMAIL = os.getenv('PASSWORD_RESET_FROM_EMAIL', DEFAULT_FROM_EMAIL)
PASSWORD_RESET_EMAIL_SUBJECT = os.getenv('PASSWORD_RESET_EMAIL_SUBJECT', 'Reset your Saramsa password')

