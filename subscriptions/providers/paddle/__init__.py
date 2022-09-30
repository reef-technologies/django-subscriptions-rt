import json
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from functools import cached_property
from logging import getLogger
from operator import itemgetter
from typing import ClassVar, Iterable, Optional, Tuple

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction
from django.utils.timezone import now
from djmoney.money import Money
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

    # we assume that first webhook will arrive within this period after payment
    WEBHOOK_LOOKUP_PERIOD = timedelta(hours=6)

    # if user already created a SubscriptionPayment within this period, reuse it
    ONLINE_CHARGE_DUPLICATE_LOOKUP_TIME = timedelta(hours=1)

    # make staff members pay ~1<currency> instead of real charge amount
    STAFF_DISCOUNT = True

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

    def get_amount(self, user: AbstractBaseUser, plan: Plan, quantity: int) -> Money:
        if self.STAFF_DISCOUNT and user.is_staff:
            return Money(
                amount=Decimal('1.0') + Decimal('0.01') * plan.id,
                currency=plan.charge_amount.currency,
            )

        return plan.charge_amount

    def charge_online(
        self,
        user: AbstractBaseUser,
        plan: Plan,
        subscription: Optional[Subscription] = None,
        quantity: int = 1,
    ) -> Tuple[SubscriptionPayment, str]:

        amount = self.get_amount(user=user, plan=plan, quantity=quantity)

        payment, is_new = SubscriptionPayment.objects.get_or_create(
            created__gte=now() - self.ONLINE_CHARGE_DUPLICATE_LOOKUP_TIME,
            status=SubscriptionPayment.Status.PENDING,
            metadata__payment_url__isnull=False,

            provider_codename=self.codename,
            amount=amount,
            user=user,
            plan=plan,
            subscription=subscription,
            quantity=quantity,
            defaults=dict(
                provider_transaction_id=None,
            ),
        )

        if is_new:
            payment_link = self._api.generate_payment_link(
                product_id=self._plan['id'],
                prices=[amount * quantity] if amount else [],
                email=user.email,
                metadata={
                    'SubscriptionPayment.id': payment.id,
                },
            )['url']

            payment.metadata = {
                'payment_url': payment_link,
            }
            payment.save()
        else:
            payment_link = payment.metadata['payment_url']

        return payment, payment_link

    def charge_offline(
        self,
        user: AbstractBaseUser,
        plan: Plan,
        subscription: Optional[Subscription] = None,
        quantity: int = 1,
        reference_payment: Optional[SubscriptionPayment] = None,
    ) -> SubscriptionPayment:

        if not reference_payment:
            reference_payment = SubscriptionPayment.get_last_successful(user)

        if not reference_payment:
            raise PaymentError('No reference payment to take credentials from')

        assert reference_payment.status == SubscriptionPayment.Status.COMPLETED

        try:
            subscription_id = reference_payment.metadata['subscription_id']
        except KeyError as exc:
            log.warning('Reference payment (%s) metadata has no "subscription_id" field', reference_payment)
            raise PaymentError('Reference payment metadata has no "subscription_id" field') from exc

        # TODO: check that currency of last payment matches currency of this plan (paddle doesn't allow one-off charges with different currencies
        amount = self.get_amount(user=user, plan=plan, quantity=quantity)
        metadata = self._api.one_off_charge(
            subscription_id=subscription_id,
            amount=amount.amount * quantity,
            name=plan.name,
        ) if plan.charge_amount else {}

        status_mapping = {
            'success': SubscriptionPayment.Status.COMPLETED,
            'pending': SubscriptionPayment.Status.PENDING,
        }
        paddle_status = metadata.get('status')
        status = status_mapping.get(paddle_status)
        if status is None:
            log.error(f'Paddle one-off charge status "{paddle_status}" is unknown, should be from {set(status_mapping.keys())}')
            status = SubscriptionPayment.Status.ERROR

        # when status is PENDING, no webhook will come, so we rely on
        # background task to search for payments not in webhook history

        return SubscriptionPayment.objects.create(
            provider_codename=self.codename,
            provider_transaction_id=None,  # paddle doesn't return anything
            amount=amount,
            status=status,
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

        with transaction.atomic():
            payment = SubscriptionPayment.objects.get(
                provider_codename=self.codename,
                uid=self.extract_payment_id(payload),
            )
            payment.provider_transaction_id = payload['subscription_payment_id']
            payment.metadata.update(payload)
            payment.status = self.WEBHOOK_ACTION_TO_PAYMENT_STATUS[action]
            payment.save()

        return Response(status=HTTP_200_OK)

    def check_payments(self, payments: Iterable[SubscriptionPayment]):
        self.check_payments_using_webhook_history(payments)

    # def check_payments_using_payments_endpoint(self, payments: Iterable[SubscriptionPayment]):
    #     records = self._api.get_payments(
    #         from_=min(payment.created for payment in payments),
    #         to=max(payment.created for payment in payments) + self.WEBHOOK_LOOKUP_PERIOD,
    #     )

    def check_payments_using_webhook_history(self, payments: Iterable[SubscriptionPayment]):
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
