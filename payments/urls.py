from django.urls import path

from .views import PlanListView, PlanView

urlpatterns = [
    path('', PlanListView.as_view(), name='plan_list'),
    path('<slug:plan_slug>/', PlanView.as_view(), name='plan'),
]
