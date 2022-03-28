from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponseRedirect
from django.urls import reverse_lazy
from django.views.generic import DetailView, ListView, TemplateView

from .exceptions import PaymentError, ProviderNotFound
from .models import Plan
from .providers import get_provider


class PlanListView(ListView):
    template_name = 'subscriptions/plans.html'
    queryset = Plan.objects.all()
    context_object_name = 'plans'


class PlanView(DetailView):
    template_name = 'subscriptions/plan.html'
    model = Plan

    def get_object(self):
        return self.model.objects.get(slug=self.kwargs['plan_slug'])


class PlanPaymentView(LoginRequiredMixin, PlanView):
    template_name = 'subscriptions/pay.html'

    def dispatch(self, request, *args, **kwargs):
        self.provider_codename = request.GET.get('provider')
        try:
            self.payment_provider = get_provider(self.provider_codename)
        except ProviderNotFound:
            raise Http404()

        self.plan = Plan.objects.get(slug=kwargs['plan_slug'])
        self.form = form(request.POST or None) if (form := self.payment_provider.form) else None

        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):

        if self.form.is_valid():
            try:
                return self.payment_provider.process_payment(request=request)
            except PaymentError as exc:
                self.form.add_error(None, ValidationError(exc.user_message, code=exc.code))

        return super().get(request, *args, **kwargs)


class PlanPaymentSuccessView(TemplateView):
    template_name = 'subscriptions/payment-success.html'
