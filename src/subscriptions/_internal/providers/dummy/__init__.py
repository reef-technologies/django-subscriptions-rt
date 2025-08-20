from collections.abc import Iterable
from datetime import datetime
from typing import ClassVar
from uuid import uuid4

from django.contrib.auth.base_user import AbstractBaseUser
from django.forms import Form
from django.utils.crypto import get_random_string
from djmoney.money import Money
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.status import HTTP_200_OK, HTTP_400_BAD_REQUEST, HTTP_404_NOT_FOUND

from ...models import Plan, Subscription, SubscriptionPayment
from .. import Provider
from .forms import DummyForm


class DummyProvider(Provider):
    form: ClassVar[type[Form] | None] = DummyForm

    _payment_url: ClassVar[str] = "/payment/{}/"

    def charge_interactively(
        self,
        user: AbstractBaseUser,
        plan: Plan,
        amount: Money,
        quantity: int,
        since: datetime,
        until: datetime,
        subscription: Subscription | None = None,
    ) -> tuple[SubscriptionPayment, str]:
        transaction_id = get_random_string(8)
        payment = SubscriptionPayment.objects.create(
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
        reference_payment: SubscriptionPayment,
        subscription: Subscription | None = None,
    ) -> SubscriptionPayment:
        return SubscriptionPayment.objects.create(
            provider_codename=self.codename,
            provider_transaction_id=get_random_string(8),
            amount=amount,  # type: ignore[misc]
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
