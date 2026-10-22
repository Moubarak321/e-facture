import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env", override=True)

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-change-me")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = [h for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",") if h]
CSRF_TRUSTED_ORIGINS = [o for o in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "facturation",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "facturation.context.shared",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

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

LANGUAGE_CODE = "fr-fr"
TIME_ZONE = "Africa/Porto-Novo"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

# ---------------------------------------------------------------------------
# API e-MCF (SyGM-eMCF) - DGI Bénin
# ---------------------------------------------------------------------------
# "test" = environnement SyGMEF-test, "prod" = production (factures réelles !)
EMCF_ENV = os.environ.get("EMCF_ENV", "test")
# Jeton JWT fourni par la DGI : NE JAMAIS le mettre dans le code ni dans git.
EMCF_TOKEN = os.environ.get("EMCF_TOKEN", "")
# Préfixe de l'en-tête Authorization. Si l'API répond 401, essayez EMCF_AUTH_PREFIX="" (jeton brut).
EMCF_AUTH_PREFIX = os.environ.get("EMCF_AUTH_PREFIX", "Bearer")
EMCF_URLS = {
    "test": "https://developper.impots.bj/sygmef-emcf",
    "prod": "https://sygmef.impots.bj/emcf",
}
# Taux (%) par groupe de taxe, utilisés UNIQUEMENT pour l'affichage du détail TVA sur l'impression.
# Vérifiez-les avec la page « Infos API » (groupes de taxe) et adaptez si besoin.
EMCF_TAX_RATES = {"A": 0, "B": 18, "C": 0, "D": 18, "E": 0, "F": 0}
# Champs supplémentaires à fusionner dans chaque requête de création de facture
# (ex. si le Swagger exige "taxGroups"). Laissez vide sinon.
EMCF_EXTRA_PAYLOAD = {}
