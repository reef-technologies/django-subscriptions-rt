from collections.abc import Iterable
from datetime import datetime
from typing import ClassVar

from django.contrib.auth.models import AbstractBaseUser
from django.forms import Form
from django.utils.crypto import get_random_string
from djmoney.money import Money
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.status import HTTP_200_OK, HTTP_400_BAD_REQUEST, HTTP_404_NOT_FOUND

from ...exceptions import PaymentError
from ...models import Plan, Subscription, SubscriptionPayment
from .. import Provider
from .forms import DummyForm


class DummyProvider(Provider):
    is_external: ClassVar[bool] = False
    form: ClassVar[type[Form] | None] = DummyForm

    _payment_url: ClassVar[str] = "/payment/{}/"

    def charge_interactively(
        self,
        user: AbstractBaseUser,
        plan: Plan,
        since: datetime,
        until: datetime,
        subscription: Subscription | None = None,
        amount: Money | None = None,
        quantity: int = 1,
    ) -> tuple[SubscriptionPayment, str]:
        transaction_id = get_random_string(8)

        payment = SubscriptionPayment.objects.create(  # TODO: limit number of creations per day
            provider_codename=self.codename,
            provider_transaction_id=transaction_id,
            amount=amount,  # type: ignore[misc]
            quantity=quantity,
            user_id=user.pk,
            plan=plan,
            subscription=subscription,
            paid_since=since,
            paid_until=until,
        )
        return payment, self._payment_url.format(transaction_id)

    def charge_automatically(
        self,
        plan: Plan,
        amount: Money,
        quantity: int,
        since: datetime,
        until: datetime,
        subscription: Subscription | None = None,  # TODO: probably better to change signature (remove unrelated to payment fields)
        reference_payment: SubscriptionPayment | None = None,
    ) -> SubscriptionPayment:
        return SubscriptionPayment.objects.create(  # TODO: limit number of creations per day
            provider_codename=self.codename,
            provider_transaction_id=get_random_string(8),
            amount=plan.charge_amount,  # type: ignore[misc]
            quantity=quantity,
            user_id=reference_payment.user_id,
            plan=plan,
            subscription=subscription,
            status=SubscriptionPayment.Status.COMPLETED,
            paid_since=since,
            paid_until=until,
        )

    def webhook(self, request: Request, payload: dict) -> Response:
        if not (transaction_id := payload.get("transaction_id")):
            return Response(status=HTTP_400_BAD_REQUEST)

        try:
            payment = SubscriptionPayment.objects.get(provider_transaction_id=transaction_id)
        except SubscriptionPayment.DoesNotExist:
            return Response(status=HTTP_404_NOT_FOUND)

        if payment.status != payment.Status.PENDING:
            return Response(status=HTTP_400_BAD_REQUEST)

        payment.status = SubscriptionPayment.Status.COMPLETED
        payment.save()
        return Response(status=HTTP_200_OK)

    def check_payments(self, payments: Iterable[SubscriptionPayment]):
        pass

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
