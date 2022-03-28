from uuid import uuid4

from django.db import transaction
from django.http import HttpRequest, HttpResponseRedirect
from django.utils.timezone import now
from rest_framework.exceptions import PermissionDenied
from rest_framework.request import Request
from rest_framework.response import Response

from ...api.serializers import PaymentSerializer
from ...exceptions import NoNextChargeDate
from ...models import Subscription, SubscriptionPayment
from .. import Provider
from .forms import DummyForm


class DummyPayloadProvider(Provider):
    codename = 'dummy-payload'
    form = DummyForm

    @transaction.atomic
    def process_payment(self, request: HttpRequest, serializer: PaymentSerializer) -> Response:
        plan = serializer.validated_data['plan']

        now_ = now()
        subscription = request.user.subscriptions.active().filter(plan=plan).order_by('-end').last()
        if created := not bool(subscription):
            if not plan.is_enabled:
                raise PermissionDenied(detail='Cannot register new subscription because the plan is disabled')

            subscription = Subscription.objects.create(
                user=request.user,
                plan=plan,
                start=now_,
            )

        try:
            charge_date = next(subscription.iter_charge_dates(since=now_, within_lifetime=True))
        except NoNextChargeDate as exc:
            raise PermissionDenied(detail='No ongoing charge dates found') from exc

        SubscriptionPayment.objects.create(
            provider_codename=self.codename,
            provider_transaction_id=uuid4(),
            status=SubscriptionPayment.Status.COMPLETED,
            amount=plan.charge_amount or 0,
            user=request.user,
            subscription=subscription,
            subscription_charge_date=charge_date,
        )

        if not created:
            subscription.prolong()

        result = PaymentSerializer({
            'plan': plan,
        })

        return Response(result.data)

    def handle_webhook(self, request: Request) -> Response:
        return Response(request.data)


class DummyRedirectProvider(Provider):
    codename = 'dummy-redirect'
    form = DummyForm

    @transaction.atomic
    def process_payment(self, request: HttpRequest, serializer: PaymentSerializer) -> Response:
        return HttpResponseRedirect('/')

    def handle_webhook(self, request: Request) -> Response:
        return Response(request.data)
