from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponseRedirect
from django.urls import reverse_lazy
from django.views.generic import DetailView, ListView, TemplateView, View

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
    success_url = reverse_lazy('plan_payment_success')

    def dispatch(self, request, *args, **kwargs):
        self.provider_name = request.GET.get('provider', next(iter(settings.PAYMENT_PROVIDERS.keys())))
        try:
            self.payment_provider = get_provider(self.provider_name)
        except ProviderNotFound:
            raise Http404()

        self.plan = Plan.objects.get(slug=kwargs['plan_slug'])

        if (redirect_url := self.payment_provider.redirect_url):
            return HttpResponseRedirect(redirect_url)

        self.form = form(request.POST or None) if (form := self.payment_provider.form) else None

        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):

        if self.form.is_valid():
            try:
                self.payment = self.payment_provider.process_payment(form_data=self.form.data, request=request, plan=self.plan)
                return HttpResponseRedirect(self.success_url)
            except PaymentError as exc:
                self.form.add_error(None, ValidationError(exc.user_message, code=exc.code))

        return super().get(request, *args, **kwargs)


class PlanPaymentSuccessView(TemplateView):
    template_name = 'subscriptions/payment-success.html'
