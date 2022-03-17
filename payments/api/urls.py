from django.urls import path
from payments.api.views import PlanListView, SubscriptionListView

urlpatterns = [
    path('plans/', PlanListView.as_view(), name='plans'),
    path('subscriptions/', SubscriptionListView.as_view(), name='subscriptions'),
]
