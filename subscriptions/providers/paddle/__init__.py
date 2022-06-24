import json
from dataclasses import dataclass
from logging import getLogger
from typing import ClassVar, Optional

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser
from rest_framework.status import HTTP_200_OK
from rest_framework.request import Request
from rest_framework.response import Response

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
    _plan: dict = None

    def __post_init__(self):
        self._api = Paddle(
            vendor_id=self.vendor_id,
            vendor_auth_code=self.vendor_auth_code,
            endpoint=self.endpoint,
        )

        plans = self._api.list_subscription_plans()
        assert (num_plans := len(plans)) == 1, \
            f'There should be exactly one subscription plan, but there are {num_plans}: {plans}'
        self._plan = plans[0]

    def charge_online(self, user: AbstractBaseUser, plan: Plan, subscription: Optional[Subscription] = None) -> str:

        payment = SubscriptionPayment.objects.create(  # TODO: limit number of creations per day
            provider_codename=self.codename,
            provider_transaction_id=None,
            amount=plan.charge_amount,
            user=user,
            plan=plan,
            subscription=subscription,
        )

        payment_link = self._api.generate_payment_link(
            product_id=self._plan['id'],
            prices=[plan.charge_amount] if plan.charge_amount else [],
            email=user.email,
            metadata={
                'SubscriptionPayment.id': payment.id,
            },
        )['url']

        return payment_link

    def charge_offline(self, user: AbstractBaseUser, plan: Plan, subscription: Optional[Subscription] = None):
        last_successful_payment = SubscriptionPayment.get_last_successful(user)
        if not last_successful_payment:
            raise PaymentError('No last successful payment to take credentials from')

        try:
            subscription_id = last_successful_payment.metadata['subscription_id']
        except KeyError as exc:
            log.warning(f'Last successful payment ({last_successful_payment}) metadata has no "subscription_id" field')
            raise PaymentError('Last successful payment metadata has no "subscription_id" field') from exc
        amount = plan.charge_amount.amount  # TODO: check that currency of last payment matches currency of this plan (paddle doesn't allow one-off charges with different currencies)

        metadata = self._api.one_off_charge(
            subscription_id=subscription_id,
            amount=amount,
            name=plan.name,
        )

        SubscriptionPayment.objects.create(
            provider_codename=self.codename,
            provider_transaction_id=metadata['subscription_payment_id'],
            amount=amount,
            status=SubscriptionPayment.Status.COMPLETED,  # TODO: will this auto-prolong subscription?
            user=user,
            plan=plan,
            subscription=subscription,
            metadata=metadata,
        )

    WEBHOOK_ACTION_TO_PAYMENT_STATUS: ClassVar[dict] = {
        'subscription_payment_succeeded': SubscriptionPayment.Status.COMPLETED,
        'subscription_payment_failed': SubscriptionPayment.Status.ERROR,
    }

    def webhook(self, request: Request, payload: dict) -> Response:
        if (action := payload['alert_name']) not in self.WEBHOOK_ACTION_TO_PAYMENT_STATUS:
            log.warning(f'No handler for {action=}')
            return Response()

        try:
            passthrough = json.loads(payload['passthrough'])
        except (json.JSONDecodeError, KeyError) as exc:
            raise ValueError('Could not decode `passthrough`') from exc

        try:
            id_ = passthrough['SubscriptionPayment.id']
        except KeyError as exc:
            raise ValueError('Passthrough does not contain "SubscriptionPayment.id" field') from exc

        payment = SubscriptionPayment.objects.get(provider_codename=self.codename, id=id_)
        payment.provider_transaction_id = payload['subscription_payment_id']
        payment.metadata.update(payload)
        payment.status = self.WEBHOOK_ACTION_TO_PAYMENT_STATUS[action]
        payment.save()

        return Response(status=HTTP_200_OK)
