from .._internal.apps import AppConfig as BaseAppConfig


class AppConfig(BaseAppConfig):
    name = "subscriptions.v0"
    label = "subscriptions"
