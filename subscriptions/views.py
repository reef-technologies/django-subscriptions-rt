import json

from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import User
from django.core.exceptions import (
    BadRequest,
    PermissionDenied,
    ValidationError,
)
from django.http import Http404
from django.views.generic import (
    DetailView,
    ListView,
    TemplateView,
)
from rest_framework.decorators import api_view

from .exceptions import (
    PaymentError,
    ProviderNotFound,
)
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
        return self.model.objects.get(id=self.kwargs['id'])


class PlanSubscriptionView(LoginRequiredMixin, PlanView):
    template_name = 'subscriptions/subscribe.html'

    def dispatch(self, request, *args, **kwargs):
        self.provider_codename = request.GET.get('provider')
        try:
            self.payment_provider = get_provider(self.provider_codename)
        except ProviderNotFound:
            raise Http404()

        self.plan = Plan.objects.get(id=kwargs['id'])
        self.form = form(request.POST or None) if (form := self.payment_provider.form) else None

        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):

        if self.form.is_valid():
            try:
                return self.payment_provider.process_subscription_request(request=request)
            except PaymentError as exc:
                self.form.add_error(None, ValidationError(exc.user_message, code=exc.code))

        return super().get(request, *args, **kwargs)


class PlanSubscriptionSuccessView(TemplateView):
    template_name = 'subscriptions/subscribe-success.html'


@api_view(['POST'])
def in_app_purchase_handler(request):
    user: User = request.user

    if not user.is_authenticated:
        raise PermissionDenied

    try:
        json_body = json.loads(request.body)
    except json.JSONDecodeError:
        raise BadRequest('Invalid format, expected JSON.')

    try:
        provider = json_body['provider']
        transaction_data = json_body['transaction_data']
    except KeyError:
        raise BadRequest('Invalid payload, missing fields.')

    # Check for duplication of the transaction_data.

    # Fetch a provider and validate the transaction_data against the store. Also, fetch the plan purchased.

    # Activate the plan.
