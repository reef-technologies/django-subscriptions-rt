from django.urls import path

from .views import PlanListView, PlanPaymentSuccessView, PlanPaymentView, PlanView

urlpatterns = [
    path('', PlanListView.as_view(), name='plan_list'),
    path('<slug:plan_slug>/', PlanView.as_view(), name='plan'),
    path('<slug:plan_slug>/pay/', PlanPaymentView.as_view(), name='plan_payment'),
    path('success', PlanPaymentSuccessView.as_view(), name='plan_payment_success'),
]
