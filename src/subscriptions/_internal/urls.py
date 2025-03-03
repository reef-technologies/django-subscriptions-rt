from django.urls import path

from .views import PlanListView, PlanSubscriptionSuccessView, PlanSubscriptionView, PlanView

urlpatterns = [
    path("", PlanListView.as_view(), name="plan_list"),
    path("<int:plan_id>/", PlanView.as_view(), name="plan"),  # TODO
    path("<int:plan_id>/subscribe/", PlanSubscriptionView.as_view(), name="plan_subscription"),
    path("success", PlanSubscriptionSuccessView.as_view(), name="plan_subscription_success"),
]
