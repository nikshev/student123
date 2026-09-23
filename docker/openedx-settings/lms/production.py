# -*- coding: utf-8 -*-
"""Resolved LMS production settings (standalone compose, no Tutor).
Modeled on Tutor 18 (Redwood) templates:
  tutor/templates/apps/openedx/settings/lms/production.py
  tutor/templates/apps/openedx/settings/partials/common_all.py
  tutor/templates/apps/openedx/settings/partials/common_lms.py
plus repo plugin patches (tutor-plugin/patches/openedx-lms-*).

Secrets NEVER live here or in lms.env.yml — they come from environment
(see docker-compose.yml + .env.example). Mounted at
/openedx/edx-platform/lms/envs/tutor/production.py with
DJANGO_SETTINGS_MODULE=lms.envs.tutor.production.
"""

import json
import os

from lms.envs.production import *
from xmodule.modulestore.modulestore_settings import update_module_store_settings

LMS_HOST = os.environ.get("LMS_HOST", "local.openedx.io")
CMS_HOST = os.environ.get("CMS_HOST", "studio.local.openedx.io")
LMS_PORT = os.environ.get("LMS_PORT", "8010")
CMS_PORT = os.environ.get("CMS_PORT", "8011")
LMS_ROOT_URL = f"http://{LMS_HOST}:{LMS_PORT}"
CMS_ROOT_URL = f"http://{CMS_HOST}:{CMS_PORT}"
# LEARNING_MICROFRONTEND_URL = LMS_ROOT_URL  # Disabled - no MFE running, use traditional courseware

# --- Secrets from environment (never in git) ---
SECRET_KEY = os.environ["OPENEDX_SECRET_KEY"]
DATABASES["default"].update({
    "HOST": os.environ.get("MYSQL_HOST", "mysql"),
    "PORT": int(os.environ.get("MYSQL_PORT", "3306")),
    "NAME": os.environ.get("MYSQL_DATABASE", "openedx"),
    "USER": os.environ.get("MYSQL_USER", "openedx"),
    "PASSWORD": os.environ["MYSQL_PASSWORD"],
})
JWT_AUTH["JWT_SECRET_KEY"] = os.environ["JWT_COMMON_SECRET_KEY"]
JWT_AUTH["JWT_PRIVATE_SIGNING_JWK"] = os.environ["JWT_PRIVATE_SIGNING_JWK_JSON"]
JWT_AUTH["JWT_PUBLIC_SIGNING_JWK_SET"] = os.environ["JWT_PUBLIC_SIGNING_JWK_SET_JSON"]
JWT_AUTH["JWT_ISSUER"] = "http://local.openedx.io/oauth2"
JWT_AUTH["JWT_AUDIENCE"] = "openedx"
JWT_AUTH["JWT_ISSUERS"] = [
    {
        "ISSUER": "http://local.openedx.io/oauth2",
        "AUDIENCE": "openedx",
        "SECRET_KEY": SECRET_KEY,
    }
]

# --- MongoDB (no auth, like Tutor defaults) ---
mongodb_parameters = {
    "db": "openedx",
    "host": "mongodb",
    "port": 27017,
    "user": None,
    "password": None,
    "connect": False,
    "ssl": False,
    "authsource": "admin",
    "replicaSet": None,
}
DOC_STORE_CONFIG = mongodb_parameters
CONTENTSTORE = {
    "ENGINE": "xmodule.contentstore.mongo.MongoContentStore",
    "ADDITIONAL_OPTIONS": {},
    "DOC_STORE_CONFIG": DOC_STORE_CONFIG,
}
update_module_store_settings(MODULESTORE, doc_store_settings=DOC_STORE_CONFIG)
DATA_DIR = "/openedx/data/modulestore"
for store in MODULESTORE["default"]["OPTIONS"]["stores"]:
    store["OPTIONS"]["fs_root"] = DATA_DIR

# --- Redis caches (no auth) ---
DJANGO_REDIS_IGNORE_EXCEPTIONS = True
_REDIS_BASE = "redis://redis:6379/1"
CACHES = {
    name: {
        "KEY_PREFIX": key_prefix,
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": _REDIS_BASE,
        **({"TIMEOUT": timeout} if timeout else {}),
    }
    for name, key_prefix, timeout in [
        ("default", "default", None),
        ("general", "general", None),
        ("mongo_metadata_inheritance", "mongo_metadata_inheritance", 300),
        ("configuration", "configuration", None),
        ("celery", "celery", 7200),
        ("course_structure_cache", "course_structure", 604800),
        ("ora2-storage", "ora2-storage", None),
    ]
}
CACHES["default"]["VERSION"] = "1"

# --- Elasticsearch ---
ELASTIC_SEARCH_CONFIG = [{"host": "elasticsearch", "port": 9200}]

# --- Sites / contacts ---
SITE_ID = 2
CONTACT_MAILING_ADDRESS = f"Open edX student123 - http://{LMS_HOST}:8000"
DEFAULT_FROM_EMAIL = ENV_TOKENS.get("DEFAULT_FROM_EMAIL", ENV_TOKENS["CONTACT_EMAIL"])
DEFAULT_FEEDBACK_EMAIL = ENV_TOKENS.get("DEFAULT_FEEDBACK_EMAIL", ENV_TOKENS["CONTACT_EMAIL"])
SERVER_EMAIL = ENV_TOKENS.get("SERVER_EMAIL", ENV_TOKENS["CONTACT_EMAIL"])
TECH_SUPPORT_EMAIL = ENV_TOKENS.get("TECH_SUPPORT_EMAIL", ENV_TOKENS["CONTACT_EMAIL"])
CONTACT_EMAIL = ENV_TOKENS.get("CONTACT_EMAIL", ENV_TOKENS["CONTACT_EMAIL"])
BUGS_EMAIL = ENV_TOKENS.get("BUGS_EMAIL", ENV_TOKENS["CONTACT_EMAIL"])
UNIVERSITY_EMAIL = ENV_TOKENS.get("UNIVERSITY_EMAIL", ENV_TOKENS["CONTACT_EMAIL"])
PRESS_EMAIL = ENV_TOKENS.get("PRESS_EMAIL", ENV_TOKENS["CONTACT_EMAIL"])
PAYMENT_SUPPORT_EMAIL = ENV_TOKENS.get("PAYMENT_SUPPORT_EMAIL", ENV_TOKENS["CONTACT_EMAIL"])
BULK_EMAIL_DEFAULT_FROM_EMAIL = ENV_TOKENS.get("BULK_EMAIL_DEFAULT_FROM_EMAIL", ENV_TOKENS["CONTACT_EMAIL"])
API_ACCESS_MANAGER_EMAIL = ENV_TOKENS.get("API_ACCESS_MANAGER_EMAIL", ENV_TOKENS["CONTACT_EMAIL"])
API_ACCESS_FROM_EMAIL = ENV_TOKENS.get("API_ACCESS_FROM_EMAIL", ENV_TOKENS["CONTACT_EMAIL"])

# --- Drop unused CSMH history tables ---
if "lms.djangoapps.coursewarehistoryextended" in INSTALLED_APPS:
    INSTALLED_APPS.remove("lms.djangoapps.coursewarehistoryextended")
_ROUTER = "openedx.core.lib.django_courseware_routers.StudentModuleHistoryExtendedRouter"
if _ROUTER in DATABASE_ROUTERS:
    DATABASE_ROUTERS.remove(_ROUTER)

# --- Media / files ---
MEDIA_ROOT = "/openedx/media/"
VIDEO_IMAGE_SETTINGS["STORAGE_KWARGS"]["location"] = MEDIA_ROOT
VIDEO_TRANSCRIPTS_SETTINGS["STORAGE_KWARGS"]["location"] = MEDIA_ROOT
GRADES_DOWNLOAD = {
    "STORAGE_TYPE": "",
    "STORAGE_KWARGS": {
        "base_url": "/media/grades/",
        "location": "/openedx/media/grades",
    },
}
ORA2_FILEUPLOAD_BACKEND = "filesystem"
ORA2_FILEUPLOAD_ROOT = "/openedx/data/ora2"
FILE_UPLOAD_STORAGE_BUCKET_NAME = "openedxuploads"
ORA2_FILEUPLOAD_CACHE_NAME = "ora2-storage"

# --- Logging to files (works inside containers) ---
LOGGING["handlers"]["local"] = {
    "class": "logging.handlers.WatchedFileHandler",
    "filename": os.path.join(LOG_DIR, "all.log"),
    "formatter": "standard",
}
LOGGING["handlers"]["tracking"] = {
    "level": "DEBUG",
    "class": "logging.handlers.WatchedFileHandler",
    "filename": os.path.join(LOG_DIR, "tracking.log"),
    "formatter": "standard",
}
LOGGING["loggers"]["tracking"]["handlers"] = ["console", "local", "tracking"]
LOGGING["loggers"]["blockstore.apps.bundles.storage"] = {"handlers": ["console"], "level": "WARNING"}
SILENCED_SYSTEM_CHECKS = ["2_0.W001", "fields.W903"]

# --- Email via bundled SMTP relay ---
EMAIL_USE_SSL = False
ACE_ENABLED_CHANNELS = ["django_email"]
ACE_CHANNEL_DEFAULT_EMAIL = "django_email"
ACE_CHANNEL_TRANSACTIONAL_EMAIL = "django_email"
EMAIL_FILE_PATH = "/tmp/openedx/emails"

LANGUAGE_COOKIE_NAME = "openedx-language-preference"
X_FRAME_OPTIONS = "SAMEORIGIN"

# --- Features ---
FEATURES["ENABLE_DISCUSSION_SERVICE"] = False
FEATURES["PREVENT_CONCURRENT_LOGINS"] = False
FEATURES["ENABLE_CORS_HEADERS"] = True
CORS_ALLOW_CREDENTIALS = True
CORS_ORIGIN_ALLOW_ALL = False
CORS_ALLOW_INSECURE = True
CORS_ORIGIN_WHITELIST = []

# --- CMS SSO wiring ---
LOGIN_REDIRECT_WHITELIST = [CMS_HOST]
IDA_LOGOUT_URI_LIST.append(f"http://{CMS_HOST}:{CMS_PORT}/logout/")
SEARCH_SKIP_ENROLLMENT_START_DATE_FILTERING = True
CORS_ORIGIN_WHITELIST.append(f"http://{CMS_HOST}:{CMS_PORT}")

# --- Cookies without HTTPS (local demo) ---
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SESSION_COOKIE_SAMESITE = "Lax"

# --- LMS specifics (common_lms) ---
REGISTRATION_EXTRA_FIELDS["terms_of_service"] = "hidden"
REGISTRATION_EXTRA_FIELDS["honor_code"] = "hidden"

# Disable MFE by setting PREVIEW_LMS_BASE to match our hostname (without protocol/port)
# This makes in_preview_mode() return True, which disables MFE redirects
FEATURES["PREVIEW_LMS_BASE"] = LMS_HOST

# Set LEARNING_MICROFRONTEND_URL to LMS root for breadcrumb/redirect URLs
# since we're not running a separate MFE
LEARNING_MICROFRONTEND_URL = LMS_ROOT_URL
PROFILE_IMAGE_BACKEND["options"]["location"] = os.path.join(
    MEDIA_ROOT, "profile-images/"
)
COURSE_CATALOG_VISIBILITY_PERMISSION = "see_in_catalog"
COURSE_ABOUT_VISIBILITY_PERMISSION = "see_about_page"
OAUTH_ENFORCE_SECURE = False
DEFAULT_EMAIL_LOGO_URL = LMS_ROOT_URL + "/theming/asset/images/logo.png"
BULK_EMAIL_SEND_USING_EDX_ACE = True
FEATURES["ENABLE_FOOTER_MOBILE_APP_LINKS"] = False
MOBILE_STORE_ACE_URLS = {}
SOCIAL_MEDIA_FOOTER_ACE_URLS = {}
SEARCH_SKIP_SHOW_IN_CATALOG_FILTERING = False
CACHES["staticfiles"] = {
    "KEY_PREFIX": "staticfiles_lms",
    "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    "LOCATION": "staticfiles_lms",
}
for folder in [DATA_DIR, LOG_DIR, MEDIA_ROOT, STATIC_ROOT, ORA2_FILEUPLOAD_ROOT]:
    if not os.path.exists(folder):
        os.makedirs(folder, exist_ok=True)

# --- Code jail off (demo) ---
import codejail.jail_code
codejail.jail_code.configure("python", "nonexistingpythonbinary", user=None)
CODE_JAIL = {
    "python_bin": "nonexistingpythonbinary",
    "user": None,
}

# --- Repo plugins (mirror tutor-plugin/patches) ---
# FR-001-07: CSP для iframe Bunny player + Referrer-Policy.
# (Plain assignment: django-csp читає CSP_FRAME_SRC через getattr, а в Redwood
# edx-platform цю змінну за замовчуванням не визначає, тож += впав би з NameError.)
CSP_FRAME_SRC = ["https://player.mediadelivery.net"]
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
# FR-002-10: AI Tutor Service (внутрішній URL + секрети з env)
AI_TUTOR_SERVICE_URL = os.environ.get("AI_TUTOR_SERVICE_URL", "http://ai-tutor-service:8001")
AI_TUTOR_LLM_API_KEY = os.environ.get("AI_TUTOR_LLM_API_KEY", "")
AI_TUTOR_SHARED_SECRET = os.environ.get("AI_TUTOR_SHARED_SECRET", "")
# FR-002-13: live вимкнено, доки людина не записала go (quickstart S10)
AI_TUTOR_LIVE_MODE = False
GATE_DECISIONS_REQUIRED = True

# --- Third Party Auth (Social Login) ---
# Secrets from environment (see .env.example)
THIRD_PARTY_AUTH_BACKENDS = [
    "social_core.backends.google.GoogleOAuth2",
    "social_core.backends.facebook.FacebookOAuth2",
    "social_core.backends.linkedin.LinkedinOAuth2",
    "social_core.backends.apple.AppleIdAuth",
    "social_core.backends.azuread.AzureADOAuth2",
    "social_core.backends.github.GithubOAuth2",
]

# Base AUTHENTICATION_BACKENDS already has google/facebook/linkedin/azuread/apple;
# append only what's missing (github) instead of replacing the whole list.
_GITHUB_BACKEND = "social_core.backends.github.GithubOAuth2"
if _GITHUB_BACKEND not in AUTHENTICATION_BACKENDS:
    AUTHENTICATION_BACKENDS = list(AUTHENTICATION_BACKENDS) + [_GITHUB_BACKEND]

SOCIAL_AUTH_GOOGLE_OAUTH2_KEY = os.environ.get("SOCIAL_AUTH_GOOGLE_OAUTH2_KEY", "")
SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET = os.environ.get("SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET", "")
SOCIAL_AUTH_GOOGLE_OAUTH2_SCOPE = [
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]

SOCIAL_AUTH_FACEBOOK_KEY = os.environ.get("SOCIAL_AUTH_FACEBOOK_KEY", "")
SOCIAL_AUTH_FACEBOOK_SECRET = os.environ.get("SOCIAL_AUTH_FACEBOOK_SECRET", "")
SOCIAL_AUTH_FACEBOOK_SCOPE = ["email", "public_profile"]
SOCIAL_AUTH_FACEBOOK_PROFILE_EXTRA_PARAMS = {"fields": "id,name,email,first_name,last_name"}

SOCIAL_AUTH_LINKEDIN_OAUTH2_KEY = os.environ.get("SOCIAL_AUTH_LINKEDIN_OAUTH2_KEY", "")
SOCIAL_AUTH_LINKEDIN_OAUTH2_SECRET = os.environ.get("SOCIAL_AUTH_LINKEDIN_OAUTH2_SECRET", "")
SOCIAL_AUTH_LINKEDIN_OAUTH2_SCOPE = ["r_liteprofile", "r_emailaddress"]
SOCIAL_AUTH_LINKEDIN_OAUTH2_FIELD_SELECTORS = ["id", "first-name", "last-name", "email-address", "headline"]

SOCIAL_AUTH_APPLE_ID_CLIENT = os.environ.get("SOCIAL_AUTH_APPLE_ID_CLIENT", "")
SOCIAL_AUTH_APPLE_ID_TEAM = os.environ.get("SOCIAL_AUTH_APPLE_ID_TEAM", "")
SOCIAL_AUTH_APPLE_ID_KEY = os.environ.get("SOCIAL_AUTH_APPLE_ID_KEY", "")
SOCIAL_AUTH_APPLE_ID_SECRET = os.environ.get("SOCIAL_AUTH_APPLE_ID_SECRET", "")
SOCIAL_AUTH_APPLE_ID_SCOPE = ["name", "email"]

SOCIAL_AUTH_AZUREAD_OAUTH2_KEY = os.environ.get("SOCIAL_AUTH_AZUREAD_OAUTH2_KEY", "")
SOCIAL_AUTH_AZUREAD_OAUTH2_SECRET = os.environ.get("SOCIAL_AUTH_AZUREAD_OAUTH2_SECRET", "")
SOCIAL_AUTH_AZUREAD_OAUTH2_TENANT_ID = os.environ.get("SOCIAL_AUTH_AZUREAD_OAUTH2_TENANT_ID", "common")

SOCIAL_AUTH_GITHUB_KEY = os.environ.get("SOCIAL_AUTH_GITHUB_KEY", "")
SOCIAL_AUTH_GITHUB_SECRET = os.environ.get("SOCIAL_AUTH_GITHUB_SECRET", "")
SOCIAL_AUTH_GITHUB_SCOPE = ["user:email", "read:user"]

# NOTE: no custom SOCIAL_AUTH_PIPELINE — the base edx-platform pipeline
# (common.djangoapps.third_party_auth.pipeline.*) must stay intact.
# Third Party Auth settings
SOCIAL_AUTH_LOGIN_REDIRECT_URL = "/dashboard"
SOCIAL_AUTH_LOGIN_ERROR_URL = "/login"
SOCIAL_AUTH_RAISE_EXCEPTIONS = False
SOCIAL_AUTH_USERNAME_IS_FULL_EMAIL = True
SOCIAL_AUTH_CLEAN_USERNAMES = True
SOCIAL_AUTH_SANITIZE_REDIRECTS = True

# Username generation for social auth
SOCIAL_AUTH_UUID_LENGTH = 16

# Email verification for social accounts
SOCIAL_AUTH_SEND_EMAIL_VALIDATION = False
SOCIAL_AUTH_EMAIL_VALIDATION_FUNCTION = None

# --- Translations ---
# Language settings
LANGUAGES = [
    ("uk", "Ukrainian"),
    ("en", "English"),
]
LANGUAGE_CODE = "uk"
# Project uk catalogs live in the repo (docker/openedx-locale) and are baked
# into the image at /openedx/locale-overrides (see docker/edx-platform/Dockerfile).
# Prepend so they win over the upstream catalog; keep the base paths intact.
LOCALE_OVERRIDES_DIR = "/openedx/locale-overrides"
if os.path.isdir(LOCALE_OVERRIDES_DIR) and LOCALE_OVERRIDES_DIR not in LOCALE_PATHS:
    LOCALE_PATHS = [LOCALE_OVERRIDES_DIR] + list(LOCALE_PATHS)

# NOTE: share buttons on the course-about page are unconditional in the
# upstream template (course_about_sidebar_header.html) — no flag needed.
# Disable SafeSessionMiddleware (requires proper cookie format from login)
# Use standard Django SessionMiddleware instead
ENFORCE_SAFE_SESSIONS = False

# Replace SafeSessionMiddleware with Django's SessionMiddleware
MIDDLEWARE = [
    m for m in MIDDLEWARE
    if m != 'openedx.core.djangoapps.safe_sessions.middleware.SafeSessionMiddleware'
]
MIDDLEWARE.insert(17, 'django.contrib.sessions.middleware.SessionMiddleware')

# Disable CacheBackedAuthenticationMiddleware (conflicts with standard SessionMiddleware)
# Use standard Django AuthenticationMiddleware instead
MIDDLEWARE = [
    m for m in MIDDLEWARE
    if m != 'openedx.core.djangoapps.cache_toolbox.middleware.CacheBackedAuthenticationMiddleware'
]
MIDDLEWARE.insert(18, 'django.contrib.auth.middleware.AuthenticationMiddleware')
