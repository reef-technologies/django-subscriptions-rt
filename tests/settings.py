"""
Django settings used in tests.
"""
# cookiecutter-rt-pkg macro: requires cookiecutter.is_django_package
from os import environ

DEBUG = True
SECRET_KEY = "DUMMY"

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

CONSTANCE_BACKEND = "constance.backends.database.DatabaseBackend"
CONSTANCE_CONFIG = {
    "SUBSCRIPTIONS_DEFAULT_PLAN_ID": (0, "Default plan ID", int),
}
SUBSCRIPTIONS_PAYMENT_PROVIDERS = [
    "subscriptions.v0.providers.dummy.DummyProvider",
    "subscriptions.v0.providers.paddle.PaddleProvider",
    "subscriptions.v0.providers.google_in_app.GoogleInAppProvider",
    "subscriptions.v0.providers.apple_in_app.AppleInAppProvider",
]

PADDLE_VENDOR_ID = environ.get("PADDLE_VENDOR_ID")
PADDLE_VENDOR_AUTH_CODE = environ.get("PADDLE_VENDOR_AUTH_CODE")
PADDLE_ENDPOINT = environ.get("PADDLE_ENDPOINT")
PADDLE_TEST_EMAIL = environ.get("PADDLE_TEST_EMAIL")

GOOGLE_PLAY_PACKAGE_NAME = environ.get("GOOGLE_PLAY_PACKAGE_NAME")
GOOGLE_PLAY_SERVICE_ACCOUNT = environ.get("GOOGLE_PLAY_SERVICE_ACCOUNT")

# ID of the application that we're trying to verify.
APPLE_BUNDLE_ID = environ.get("APPLE_BUNDLE_ID")
# Shared secret that can be used to ask Apple about receipts. Obtainable from
# https://help.apple.com/app-store-connect/#/devf341c0f01
APPLE_SHARED_SECRET = environ.get("APPLE_SHARED_SECRET")
# Optional. One can obtain it from https://www.apple.com/certificateauthority/
# Current certificate is also embedded into the application.
# APPLE_ROOT_CERTIFICATE_PATH = environ.get('APPLE_ROOT_CERTIFICATE_PATH')

ROOT_URLCONF = __name__
urlpatterns = []  # type: ignore
