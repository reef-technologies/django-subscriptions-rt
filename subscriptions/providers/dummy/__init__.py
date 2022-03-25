from uuid import uuid4

from django.db import transaction
from django.http import HttpRequest
from django.utils.timezone import now
from rest_framework.request import Request
from rest_framework.response import Response

from ...api.serializers import PaymentSerializer
from ...models import Subscription, SubscriptionPayment
from .. import Provider
from .forms import DummyForm


class DummyProvider(Provider):
    form = DummyForm

    @transaction.atomic
    def process_payment(self, request: HttpRequest, serializer: PaymentSerializer) -> Response:
        plan = serializer.validated_data['plan']

        subscription = Subscription.objects.create(
            user=request.user,
            plan=plan,
            start=now(),
        )

        SubscriptionPayment.objects.create(
            provider_name=self.name,
            provider_transaction_id=uuid4(),
            status=SubscriptionPayment.Status.COMPLETED,
            amount=plan.charge_amount or 0,
            user=request.user,
            subscription=subscription,
            subscription_charge_date=subscription.start,
        )

        result = PaymentSerializer({
            'redirect_url': self.redirect_url,
            'plan': plan,
        })

        return Response(result.data)

    def handle_webhook(self, request: Request) -> Response:
        return Response(request.data)
