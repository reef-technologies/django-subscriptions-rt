from uuid import uuid4

from django.db import transaction
from django.http import HttpRequest
from django.utils.timezone import now

from ...models import Plan, Subscription, SubscriptionPayment
from .. import Provider
from .forms import DummyForm


class DummyProvider(Provider):
    form = DummyForm

    @transaction.atomic
    def process_payment(self, form_data: dict, request: HttpRequest, plan: Plan) -> SubscriptionPayment:

        subscription = Subscription.objects.create(
            user=request.user,
            plan=plan,
            start=now(),
        )

        return SubscriptionPayment.objects.create(
            provider_name=self.name,
            provider_transaction_id=uuid4(),
            status=SubscriptionPayment.Status.COMPLETED,
            amount=plan.charge_amount or 0,
            user=request.user,
            subscription=subscription,
            subscription_charge_date=subscription.start,
        )
