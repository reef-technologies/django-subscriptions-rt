from django.conf import settings
from django.urls import re_path

from .views import PaymentProviderListView, PaymentWebhookView, PlanListView, SubscriptionListView, \
                   build_payment_view, build_payment_webhook_view

urlpatterns = [
    re_path(r'^plans/?$', PlanListView.as_view(), name='plans'),
    re_path(r'^payment-providers/?$', PaymentProviderListView.as_view(), name='payment_providers'),
    re_path(r'^subscriptions/?$', SubscriptionListView.as_view(), name='subscriptions'),
]

for payment_provider_name in settings.PAYMENT_PROVIDERS.keys():
    urlpatterns += [
        re_path(
            f'^webhook/{payment_provider_name}/?$',
            build_payment_webhook_view(payment_provider_name).as_view(),
            name=f'payment_webhook_{payment_provider_name}',
        ),
        re_path(
            f'^pay/{payment_provider_name}/?$',
            build_payment_view(payment_provider_name).as_view(),
            name=f'payment_{payment_provider_name}',
        )
    ]
