"""
Django settings used in tests.
"""
# cookiecutter-rt-pkg macro: requires cookiecutter.is_django_package

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

ROOT_URLCONF = __name__
urlpatterns = []  # type: ignore
