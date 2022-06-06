from django.urls import re_path
from django.views.decorators.csrf import csrf_exempt

from ..providers import get_providers
from .views import PlanListView, ResourcesView, SubscriptionListView, SubscriptionSelectView, build_payment_webhook_view

urlpatterns = [
    re_path(r'plans/?$', PlanListView.as_view(), name='plans'),
    # re_path(r'payment-providers/?$', PaymentProviderListView.as_view(), name='payment_providers'),
    re_path(r'subscriptions/?$', SubscriptionListView.as_view(), name='subscriptions'),
    re_path(r'subscribe/?$', SubscriptionSelectView.as_view(), name='subscribe'),
    re_path(r'resources/?$', ResourcesView.as_view(), name='resources'),
]

for provider in get_providers():
    if not provider.is_enabled:
        continue

    urlpatterns += [
        re_path(
            f'webhook/{provider.codename}/?$',
            csrf_exempt(
                build_payment_webhook_view(provider).as_view()
            ),
            name=f'payment_webhook_{provider.codename}',
        ),
    ]
