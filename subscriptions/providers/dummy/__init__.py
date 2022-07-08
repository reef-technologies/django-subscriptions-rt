from dataclasses import dataclass
from typing import ClassVar, Optional

from django.contrib.auth.models import AbstractBaseUser
from django.forms import Form
from django.utils.crypto import get_random_string
from rest_framework.request import Request
from rest_framework.response import Response

from ...models import Plan, Subscription, SubscriptionPayment
from .. import Provider
from .forms import DummyForm


@dataclass  # TODO: not needed?
class DummyProvider(Provider):
    codename: ClassVar[str] = 'dummy'
    form: ClassVar[Form] = DummyForm

    _payment_url: ClassVar[str] = '/payment/{}/'

    def charge_online(
        self,
        user: AbstractBaseUser,
        plan: Plan,
        subscription: Optional[Subscription] = None,
        quantity: int = 1,
    ) -> str:
        transaction_id = get_random_string(8)
        SubscriptionPayment.objects.create(  # TODO: limit number of creations per day
            provider_codename=self.codename,
            provider_transaction_id=transaction_id,
            amount=plan.charge_amount * quantity,
            user=user,
            plan=plan,
            subscription=subscription,
            quantity=quantity,
        )
        return self._payment_url.format(transaction_id)

    def charge_offline(
        self,
        user: AbstractBaseUser,
        plan: Plan, subscription: Optional[Subscription] = None,
        quantity: int = 1,
    ):
        SubscriptionPayment.objects.create(  # TODO: limit number of creations per day
            provider_codename=self.codename,
            provider_transaction_id=get_random_string(8),
            amount=plan.charge_amount * quantity,
            user=user,
            plan=plan,
            subscription=subscription,
            quantity=quantity,
        )

    def webhook(self, request: Request, payload: dict) -> Response:
        payment = SubscriptionPayment.objects.get(provider_transaction_id=payload['transaction_id'])
        if payment.status != payment.Status.PENDING:
            return

        payment.status = SubscriptionPayment.Status.COMPLETED
        payment.save()
        return Response()

    # TODO: what to do with this?
    # @transaction.atomic
    # def process_subscription_request(self, request: HttpRequest, serializer: PaymentSerializer) -> Response:
    #     plan = serializer.validated_data['plan']

    #     now_ = now()
    #     subscription = request.user.subscriptions.active().filter(plan=plan).order_by('-end').last()
    #     if created := not bool(subscription):
    #         if not plan.is_enabled:
    #             raise PermissionDenied(detail='Cannot register new subscription because the plan is disabled')

    #         subscription = Subscription.objects.create(
    #             user=request.user,
    #             plan=plan,
    #             start=now_,
    #         )

    #     try:
    #         charge_date = next(subscription.iter_charge_dates(since=now_))
    #     except StopIteration as exc:
    #         raise PermissionDenied(detail='No ongoing charge dates found') from exc

    #     SubscriptionPayment.objects.create(
    #         provider_codename=self.codename,
    #         provider_transaction_id=uuid4(),
    #         status=SubscriptionPayment.Status.COMPLETED,
    #         amount=plan.charge_amount or 0,
    #         user=request.user,
    #         subscription=subscription,
    #         subscription_charge_date=charge_date,
    #     )

    #     if not created:
    #         try:
    #             subscription.prolong()
    #         except ProlongationImpossible as exc:
    #             raise PermissionDenied(detail='Cannot prolong subscription') from exc

    #     result = PaymentSerializer({
    #         'plan': plan,
    #     })

    #     return Response(result.data)
