from django.urls import re_path
from subscriptions.api.views import PaymentProviderListView, PaymentView, PaymentWebhookView, PlanListView, \
                               SubscriptionListView

urlpatterns = [
    re_path(r'^plans/?$', PlanListView.as_view(), name='plans'),
    re_path(r'^payment-providers/?$', PaymentProviderListView.as_view(), name='payment_providers'),
    re_path(r'^subscriptions/?$', SubscriptionListView.as_view(), name='subscriptions'),
    re_path(r'^pay/?$', PaymentView.as_view(), name='payment'),
    re_path(r'^webhook/(?P<payment_provider_name>\w+)/?$', PaymentWebhookView.as_view(), name='payment_webhook'),
]
