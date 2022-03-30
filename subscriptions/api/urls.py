from django.urls import re_path

from ..providers import get_providers
from .views import PaymentProviderListView, PlanListView, SubscriptionListView, build_payment_view, \
                   build_payment_webhook_view

urlpatterns = [
    re_path(r'^plans/?$', PlanListView.as_view(), name='plans'),
    re_path(r'^payment-providers/?$', PaymentProviderListView.as_view(), name='payment_providers'),
    re_path(r'^subscriptions/?$', SubscriptionListView.as_view(), name='subscriptions'),
]

for provider in get_providers():
    if not provider.is_enabled:
        continue

    urlpatterns += [
        re_path(
            f'^webhook/{provider.codename}/?$',
            build_payment_webhook_view(provider).as_view(),
            name=f'payment_webhook_{provider.codename}',
        ),
        re_path(
            f'^pay/{provider.codename}/?$',
            build_payment_view(provider).as_view(),
            name=f'payment_{provider.codename}',
        )
    ]