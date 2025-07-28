from typing import ClassVar

from django.apps import AppConfig as BaseAppConfig
from django.conf import settings


class AppConfig(BaseAppConfig):
    verbose_name = "Subscriptions"

    _required_apps: ClassVar[set[str]] = {"pgactivity", "pglock"}

    def ready(self) -> None:
        from .exceptions import ConfigurationError

        if missing_apps := self._required_apps - set(settings.INSTALLED_APPS):
            raise ConfigurationError(
                f"Required apps {missing_apps} are not installed. "
                "Please add them to INSTALLED_APPS in your settings."
            )
