from django.apps import AppConfig as BaseAppConfig


class AppConfig(BaseAppConfig):
    name = "subscriptions.v0"
    verbose_name = "Subscriptions"
    label = "subscriptions_v0"
