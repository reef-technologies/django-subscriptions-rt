# cookiecutter-rt-pkg macro: requires cookiecutter.is_django_package
import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory


@pytest.fixture
def request_factory() -> RequestFactory:
    return RequestFactory()


@pytest.fixture
def user_model():
    return get_user_model()
