import os
from pathlib import Path

SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]
if len(SECRET_KEY) < 50:
    raise ValueError("DJANGO_SECRET_KEY must be at least 50 characters.")
ALLOWED_HOSTS = [host.strip() for host in os.environ.get(
    "APP_HOSTS", "*"
).split(",") if host.strip()]

ROOT_URLCONF = "daylist.views"
INSTALLED_APPS = ["django.contrib.auth", "django.contrib.contenttypes",
                  "django.contrib.sessions", "daylist"]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "daylist.middleware.secure_cookies",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": ["django.template.context_processors.request"]},
}]
DATABASES = {"default": {
    "ENGINE": "django.db.backends.sqlite3",
    "NAME": Path(os.environ.get("DATA_DIR", "data")) / "daylist.sqlite3",
    "OPTIONS": {"timeout": 15},
}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.Argon2PasswordHasher"]
USE_TZ = True
TIME_ZONE = os.environ.get("APP_TIME_ZONE", "Asia/Singapore")
LOGIN_URL = "/sign-in/"
SESSION_COOKIE_AGE = 60 * 60 * 24 * 7
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 14}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
