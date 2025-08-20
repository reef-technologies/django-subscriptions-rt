from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.views.generic import DetailView, FormView, ListView, TemplateView

from .exceptions import PaymentError
from .forms import SubscriptionSelectionForm
from .models import Plan, subscribe
from .providers import get_provider_by_codename


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
            try:
                _, redirect_url = subscribe(
                    user=request.user,  # type: ignore[arg-type]
                    plan=form.cleaned_data["plan"],
                    quantity=form.cleaned_data["quantity"],
                    provider=get_provider_by_codename(form.cleaned_data["provider"]),
                )
                return HttpResponseRedirect(redirect_url)
            except PaymentError as exc:
                form.add_error(None, ValidationError(exc.user_message))

        return super().get(request, *args, **kwargs)


class PlanSubscriptionSuccessView(TemplateView):
    template_name = "subscriptions/subscribe-success.html"
