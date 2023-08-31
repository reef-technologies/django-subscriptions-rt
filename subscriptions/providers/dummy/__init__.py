from __future__ import annotations

from datetime import datetime
from typing import ClassVar, Iterable

from django.contrib.auth.models import AbstractBaseUser
from django.forms import Form
from django.utils.crypto import get_random_string
from djmoney.money import Money
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.status import HTTP_200_OK, HTTP_404_NOT_FOUND, HTTP_400_BAD_REQUEST

from ...exceptions import PaymentError
from ...models import Plan, Subscription, SubscriptionPayment
from .. import Provider
from .forms import DummyForm


class DummyProvider(Provider):
    codename: ClassVar[str] = 'dummy'
    is_external: ClassVar[bool] = False
    form: ClassVar[Form] = DummyForm

    _payment_url: ClassVar[str] = '/payment/{}/'

    def charge_online(
        self,
        user: AbstractBaseUser,
        plan: Plan,
        subscription: Subscription | None = None,
        amount: Money | None = None,
        quantity: int = 1,
        subscription_start: datetime | None = None,
        subscription_end: datetime | None = None,
    ) -> tuple[SubscriptionPayment, str]:

        transaction_id = get_random_string(8)
        if amount is None:
            amount = self.get_amount(user=user, plan=plan)

        payment = SubscriptionPayment.objects.create(  # TODO: limit number of creations per day
            provider_codename=self.codename,
            provider_transaction_id=transaction_id,
            amount=amount,
            quantity=quantity,
            user=user,
            plan=plan,
            subscription=subscription,
            subscription_start=subscription_start,
            subscription_end=subscription_end,
        )
        return payment, self._payment_url.format(transaction_id)

    def charge_offline(
        self,
        user: AbstractBaseUser,
        plan: Plan,
        subscription: Subscription | None = None,
        amount: Money | None = None,
        quantity: int = 1,
        reference_payment: SubscriptionPayment | None = None,
    ) -> SubscriptionPayment:

        if not user.payments.filter(
            provider_codename=self.codename,
            status=SubscriptionPayment.Status.COMPLETED,
        ).exists():
            raise PaymentError('Cannot offline-charge without previous successful charge')

        if amount is None:
            amount = self.get_amount(user=user, plan=plan)

        return SubscriptionPayment.objects.create(  # TODO: limit number of creations per day
            provider_codename=self.codename,
            provider_transaction_id=get_random_string(8),
            amount=self.get_amount(user=user, plan=plan),
            quantity=quantity,
            user=user,
            plan=plan,
            subscription=subscription,
            status=SubscriptionPayment.Status.COMPLETED,
        )

    def webhook(self, request: Request, payload: dict) -> Response:
        if not (transaction_id := payload.get('transaction_id')):
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
