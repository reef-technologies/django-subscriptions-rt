from django.urls import re_path
from django.views.decorators.csrf import csrf_exempt

from ..providers import get_providers
from .views import (
    PaymentView,
    PlanListView,
    ResourcesView,
    SubscriptionListView,
    SubscriptionSelectView,
    SubscriptionView,
    build_payment_webhook_view,
)

urlpatterns = [
    re_path(r"plans/?$", PlanListView.as_view(), name="plans"),
    re_path(r"subscriptions/?$", SubscriptionListView.as_view(), name="subscriptions"),
    re_path(r"subscriptions/(?P<uid>[0-9a-f-]{36})/?$", SubscriptionView.as_view(), name="subscription"),
    re_path(r"subscribe/?$", SubscriptionSelectView.as_view(), name="subscribe"),
    re_path(r"resources/?$", ResourcesView.as_view(), name="resources"),
    re_path(r"payments/(?P<uid>[0-9a-f-]{36})/?$", PaymentView.as_view(), name="payment"),
]

for provider in get_providers():
    if not provider.is_enabled:
        continue

    urlpatterns += [
        re_path(
            f"webhook/{provider.codename}/?$",
            csrf_exempt(build_payment_webhook_view(provider).as_view()),
            name=f"payment_webhook_{provider.codename}",
        ),
    ]
