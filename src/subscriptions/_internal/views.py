from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.views.generic import DetailView, FormView, ListView, TemplateView

from .exceptions import PaymentError
from .forms import SubscriptionSelectionForm
from .models import Plan
from .providers import get_provider_by_codename
from .models import subscribe


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
        if self.form.is_valid():
            try:
                _, redirect_url, _ = subscribe(
                    user=request.user,
                    plan=self.form.cleaned_data["plan"],
                    quantity=self.form.cleaned_data["quantity"],
                    provider=get_provider_by_codename(self.form.cleaned_data["provider"]),
                )
                return HttpResponseRedirect(redirect_url)
            except PaymentError as exc:
                self.form.add_error(None, ValidationError(exc.user_message))

        return super().get(request, *args, **kwargs)


class PlanSubscriptionSuccessView(TemplateView):
    template_name = "subscriptions/subscribe-success.html"
