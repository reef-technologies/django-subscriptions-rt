# cookiecutter-rt-pkg macro: requires cookiecutter.is_django_package
import pytest

from subscriptions import _internal
from subscriptions._internal.apps import AppConfig
from subscriptions._internal.exceptions import ConfigurationError


@pytest.mark.django_db(databases=["actual_db"])
def test__setup():
    pass


@pytest.mark.django_db(databases=["actual_db"])
def test__installed_apps(settings):
    """Ensure that ConfigurationError is raised if some of _required_apps is not in INSTALLED_APPS"""

    assert {"pgactivity", "pglock"} <= set(settings.INSTALLED_APPS)
    AppConfig("subscriptions.v0", _internal).ready()

    settings.INSTALLED_APPS.remove("pglock")
    with pytest.raises(ConfigurationError):
        AppConfig("subscriptions.v0", _internal).ready()
