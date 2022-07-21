import json
from dataclasses import dataclass
from datetime import timedelta
from functools import cached_property
from logging import getLogger
from operator import itemgetter
from typing import ClassVar, Iterable, Optional, Tuple

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser
from more_itertools import unique_everseen
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.status import HTTP_200_OK

from ...exceptions import PaymentError
from ...models import Plan, Subscription, SubscriptionPayment
from .. import Provider
from .api import Paddle

log = getLogger(__name__)


@dataclass
class PaddleProvider(Provider):
    codename: ClassVar[str] = 'paddle'

    vendor_id: ClassVar[str] = settings.PADDLE_VENDOR_ID
    vendor_auth_code: ClassVar[str] = settings.PADDLE_VENDOR_AUTH_CODE
    endpoint: ClassVar[str] = settings.PADDLE_ENDPOINT

    _api: Paddle = None

    WEBHOOK_LOOKUP_PERIOD = timedelta(hours=6)  # we assume that first webhook will arrive within this period after payment

    def __post_init__(self):
        self._api = Paddle(
            vendor_id=self.vendor_id,
            vendor_auth_code=self.vendor_auth_code,
            endpoint=self.endpoint,
        )

    @cached_property
    def _plan(self) -> dict:
        plans = self._api.list_subscription_plans()
        assert (num_plans := len(plans)) == 1, \
            f'There should be exactly one subscription plan, but there are {num_plans}: {plans}'
        return plans[0]

    def charge_online(
        self,
        user: AbstractBaseUser,
        plan: Plan,
        subscription: Optional[Subscription] = None,
        quantity: int = 1,
    ) -> Tuple[SubscriptionPayment, str]:
        payment = SubscriptionPayment.objects.create(  # TODO: limit number of creations per day
            provider_codename=self.codename,
            provider_transaction_id=None,
            amount=plan.charge_amount,
            user=user,
            plan=plan,
            subscription=subscription,
            quantity=quantity,
        )

        payment_link = self._api.generate_payment_link(
            product_id=self._plan['id'],
            prices=[plan.charge_amount * quantity] if plan.charge_amount else [],
            email=user.email,
            metadata={
                'SubscriptionPayment.id': payment.id,
            },
        )['url']

        payment.metadata = {
            'payment_url': payment_link,
        }
        payment.save()

        return payment, payment_link

    def charge_offline(
        self,
        user: AbstractBaseUser,
        plan: Plan,
        subscription: Optional[Subscription] = None,
        quantity: int = 1,
    ) -> SubscriptionPayment:
        last_successful_payment = SubscriptionPayment.get_last_successful(user)
        if not last_successful_payment:
            raise PaymentError('No last successful payment to take credentials from')

        try:
            subscription_id = last_successful_payment.metadata['subscription_id']
        except KeyError as exc:
            log.warning(f'Last successful payment ({last_successful_payment}) metadata has no "subscription_id" field')
            raise PaymentError('Last successful payment metadata has no "subscription_id" field') from exc

        # TODO: check that currency of last payment matches currency of this plan (paddle doesn't allow one-off charges with different currencies
        metadata = self._api.one_off_charge(
            subscription_id=subscription_id,
            amount=plan.charge_amount.amount * quantity,
            name=plan.name,
        ) if plan.charge_amount else {}

        return SubscriptionPayment.objects.create(
            provider_codename=self.codename,
            provider_transaction_id=None,  # paddle doesn't return anything
            amount=plan.charge_amount,
            status=SubscriptionPayment.Status.COMPLETED,  # TODO: will this auto-prolong subscription?
            user=user,
            plan=plan,
            subscription=subscription,
            quantity=quantity,
            metadata=metadata,
        )

    WEBHOOK_ACTION_TO_PAYMENT_STATUS: ClassVar[dict] = {
        'subscription_payment_succeeded': SubscriptionPayment.Status.COMPLETED,
        'subscription_payment_failed': SubscriptionPayment.Status.ERROR,
    }

    def webhook(self, request: Optional[Request], payload: dict) -> Response:
        if (action := payload['alert_name']) not in self.WEBHOOK_ACTION_TO_PAYMENT_STATUS:
            log.warning(f'No handler for {action=}')
            return Response()

        payment = SubscriptionPayment.objects.get(
            provider_codename=self.codename,
            id=self.extract_payment_id(payload),
        )
        payment.provider_transaction_id = payload['subscription_payment_id']
        payment.metadata.update(payload)
        payment.status = self.WEBHOOK_ACTION_TO_PAYMENT_STATUS[action]
        payment.save()

        return Response(status=HTTP_200_OK)

    def check_payments(self, payments: Iterable[SubscriptionPayment]):
        payment_ids = {payment.id for payment in payments}

        alerts = self._api.iter_webhook_history(
            start_date=min(payment.created for payment in payments),
            end_date=max(payment.created for payment in payments) + self.WEBHOOK_LOOKUP_PERIOD,
        )

        # don't process alerts with same `id`
        for alert in unique_everseen(alerts, key=itemgetter('id')):
            alert.update(**alert.pop('fields'))  # flatten alert structure

            try:
                if self.extract_payment_id(alert) not in payment_ids:
                    continue

                self.webhook(None, alert)
            except Exception:
                log.exception(f'Could not process alert {alert}')

    @classmethod
    def extract_payment_id(cls, alert: dict) -> int:
        try:
            passthrough = json.loads(alert['passthrough'])
        except (json.JSONDecodeError, KeyError) as exc:
            raise ValueError('Could not decode `passthrough`') from exc

        try:
            id_ = passthrough['SubscriptionPayment.id']
        except KeyError as exc:
            raise ValueError('Passthrough does not contain "SubscriptionPayment.id" field') from exc

        return id_
