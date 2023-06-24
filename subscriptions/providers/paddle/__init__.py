from dataclasses import dataclass
from datetime import datetime, timedelta
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

from ...exceptions import BadReferencePayment, PaymentError
from ...models import Plan, Subscription, SubscriptionPayment
from .. import Provider
from .api import Paddle
from .models import Passthrough, Alert

log = getLogger(__name__)


@dataclass
class PaddleProvider(Provider):
    codename: ClassVar[str] = 'paddle'
    is_external: ClassVar[bool] = False

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

    def get_amount(self, user: AbstractBaseUser, plan: Plan) -> Optional[Money]:
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
        amount: Optional[Money] = None,
        quantity: int = 1,
        subscription_start: Optional[datetime] = None,
        subscription_end: Optional[datetime] = None,
    ) -> Tuple[SubscriptionPayment, str]:

        if amount is None:
            amount = self.get_amount(user=user, plan=plan)

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
                subscription_start=subscription_start,
                subscription_end=subscription_end,
            ),
        )

        if is_new:
            payment_link = self._api.generate_payment_link(
                product_id=self._plan['id'],
                prices=[amount * quantity] if amount else [],
                email=user.email,
                metadata=Passthrough(
                    subscription_payment_id=payment.id,
                ).dict(),
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
        amount: Optional[Money] = None,
        quantity: int = 1,
        reference_payment: Optional[SubscriptionPayment] = None,
    ) -> SubscriptionPayment:

        assert quantity > 0

        if amount is None:
            amount = self.get_amount(user=user, plan=plan)

        if amount is None or amount.amount == 0:
            return SubscriptionPayment.objects.create(
                provider_codename=self.codename,
                provider_transaction_id=None,  # paddle doesn't return anything
                amount=amount,
                quantity=quantity,
                status=SubscriptionPayment.Status.COMPLETED,
                user=user,
                plan=plan,
                subscription=subscription,
                metadata={},
            )

        if not reference_payment:
            reference_payment = SubscriptionPayment.get_last_successful(user)

        if not reference_payment:
            raise PaymentError('No reference payment to take credentials from')

        assert reference_payment.status == SubscriptionPayment.Status.COMPLETED
        try:
            subscription_id = reference_payment.metadata['subscription_id']
        except KeyError as exc:
            log.warning('Reference payment (%s) metadata has no "subscription_id" field', reference_payment)
            raise BadReferencePayment('Reference payment metadata has no "subscription_id" field') from exc

        # paddle doesn't allow one-off charges with different currencies
        if reference_payment.subscription.plan.charge_amount.currency != plan.charge_amount.currency:
            raise BadReferencePayment('Reference payment has different currency than current plan')

        metadata = self._api.one_off_charge(
            subscription_id=subscription_id,
            amount=amount.amount * quantity,
            name=plan.name,
        )

        status_mapping = {
            'success': SubscriptionPayment.Status.COMPLETED,
            'pending': SubscriptionPayment.Status.PENDING,
        }
        paddle_status = metadata.get('status')

        try:
            status = status_mapping[paddle_status]
        except KeyError:
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
        alert = Alert.parse_obj(payload)

        try:
            status = self.WEBHOOK_ACTION_TO_PAYMENT_STATUS[alert.alert_name]
        except KeyError:
            log.warning(f'No handler for {alert.alert_name=}')
            return Response(status=HTTP_200_OK)

        with transaction.atomic():
            try:
                payment = SubscriptionPayment.objects.get(
                    provider_codename=self.codename,
                    uid=alert.passthrough.subscription_payment_id,
                )
                payment.provider_transaction_id = alert.subscription_payment_id
                payment.metadata.update(payload)
                payment.status = status
                payment.save()
            except SubscriptionPayment.DoesNotExist:
                log.debug('Payment not found for payload %s', payload)

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
        for alert_dict in unique_everseen(alerts, key=itemgetter('id')):
            alert_dict.update(**alert_dict.pop('fields'))  # flatten alert structure

            try:
                alert = Alert.parse_obj(alert_dict)
                if alert.passthrough.subscription_payment_id not in payment_ids:
                    continue

                self.webhook(None, alert_dict)
            except Exception:
                log.exception(f'Could not process alert {alert_dict}')
