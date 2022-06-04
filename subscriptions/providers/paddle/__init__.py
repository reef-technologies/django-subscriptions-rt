import json
from dataclasses import dataclass
from logging import getLogger
from typing import ClassVar, Optional, Type

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser
from django.http import HttpResponseRedirect
from django.utils.crypto import get_random_string
from rest_framework.request import Request
from rest_framework.response import Response

from ...api.serializers import WebhookSerializer
from ...models import Plan, Subscription, SubscriptionPayment
from .. import Provider
from .api import Paddle
from .serializers import PaddleWebhookSerializer

log = getLogger(__name__)


@dataclass
class PaddleProvider(Provider):
    codename: ClassVar[str] = 'paddle'
    webhook_serializer_class: ClassVar[Type[WebhookSerializer]] = PaddleWebhookSerializer

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

    def charge_online(self, user: AbstractBaseUser, plan: Plan, subscription: Optional[Subscription] = None) -> HttpResponseRedirect:
        for _ in range(10):
            transaction_id = get_random_string(16)
            if not SubscriptionPayment.objects.filter(
                provider_codename=self.codename,
                provider_transaction_id=transaction_id,
            ).exists():
                break
        else:
            raise ValueError('Cannot generate unique transaction ID')

        payment_link = self._api.generate_payment_link(
            product_id=self._plan['id'],
            prices=[
                plan.charge_amount,
            ],
            email=user.email,
            metadata={
                'transaction_id': transaction_id,
            },
        )
        SubscriptionPayment.objects.create(  # TODO: limit number of creations per day
            provider_codename=self.codename,
            provider_transaction_id=transaction_id,
            amount=plan.charge_amount,
            user=user,
            plan=plan,
            subscription=subscription,
        )
        return HttpResponseRedirect(payment_link)

    # def charge_offline(self, user: AbstractBaseUser, plan: Plan, subscription: Optional[Subscription] = None):
    #     SubscriptionPayment.objects.create(  # TODO: limit number of creations per day
    #         provider_codename=self.codename,
    #         provider_transaction_id=get_random_string(8),
    #         amount=plan.charge_amount,
    #         user=user,
    #         plan=plan,
    #         subscription=subscription,
    #     )

    WEBHOOK_ACTION_TO_PAYMENT_STATUS: ClassVar[dict] = {
        'subscription_payment_succeeded': SubscriptionPayment.Status.COMPLETED,
        'subscription_payment_failed': SubscriptionPayment.Status.ERROR,
    }

    def webhook(self, request: Request, serializer: PaddleWebhookSerializer) -> Response:
        data = serializer.validated_data
        if (action := data['alert_name']) not in self.WEBHOOK_ACTION_TO_PAYMENT_STATUS:
            log.warning(f'No handler for {action=}')
            return

        passthrough = json.loads(data['passthrough'])
        transaction_id = passthrough['transaction_id']

        payment = SubscriptionPayment.objects.get(
            provider_codename=self.codename,
            provider_transaction_id=transaction_id,
        )

        payment.metadata = data
        payment.status = self.WEBHOOK_ACTION_TO_PAYMENT_STATUS[action]
        payment.save()

        return Response()
