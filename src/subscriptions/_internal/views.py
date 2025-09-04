from logging import getLogger

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.utils.timezone import now
from django.views.generic import DetailView, FormView, ListView, TemplateView

from .exceptions import PaymentError
from .forms import SubscriptionSelectionForm
from .models import Plan, Subscription
from .providers import get_provider_by_codename

log = getLogger(__name__)


class PlanListView(ListView):
    template_name = "subscriptions/plans.html"
    queryset = Plan.objects.filter(is_enabled=True)
    context_object_name = "plans"


class PlanView(DetailView):
    template_name = "subscriptions/plan.html"
    queryset = Plan.objects.filter(is_enabled=True)
    pk_url_kwarg = "id"


class PlanSubscriptionView(LoginRequiredMixin, FormView):
    template_name = "subscriptions/subscribe.html"
    form_class = SubscriptionSelectionForm

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if (form := self.get_form()).is_valid():
            plan = form.cleaned_data["plan"]
            quantity = form.cleaned_data["quantity"]

            try:
                Subscription(user=request.user, plan=plan, quantity=quantity).run_validators()  # type: ignore[misc]

                provider = get_provider_by_codename(form.cleaned_data["provider"])
                now_ = now()
                _, redirect_url = provider.charge(
                    user=request.user,  # type: ignore[arg-type]
                    plan=plan,
                    amount=plan.charge_amount,
                    quantity=quantity,
                    since=now_,
                    until=now_ + plan.charge_period,
                )
                return HttpResponseRedirect(redirect_url)
            except ValidationError as exc:
                form.add_error("plan", exc)
            except PaymentError as exc:
                form.add_error(None, ValidationError(exc.user_message))

        return super().get(request, *args, **kwargs)


class PlanSubscriptionSuccessView(TemplateView):
    template_name = "subscriptions/subscribe-success.html"
