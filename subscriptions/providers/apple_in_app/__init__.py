from dataclasses import dataclass
from typing import (
    ClassVar,
    Iterable,
    Optional,
    Tuple,
)

from django.contrib.auth.base_user import AbstractBaseUser
from rest_framework.request import Request
from rest_framework.response import Response

from subscriptions.exceptions import InvalidOperation
from subscriptions.models import (
    Plan,
    Subscription,
    SubscriptionPayment,
)
from subscriptions.providers import Provider


@dataclass
class AppleInAppProvider(Provider):
    codename: ClassVar[str] = 'apple_in_app'

    def charge_online(self, user: AbstractBaseUser, plan: Plan, subscription: Optional[Subscription] = None,
                      quantity: int = 1) -> Tuple[SubscriptionPayment, str]:
        """
        In case of in-app purchase this operation is triggered from the mobile application library.
        """
        raise InvalidOperation()

    def charge_offline(self, user: AbstractBaseUser, plan: Plan, subscription: Optional[Subscription] = None,
                       quantity: int = 1,
                       reference_payment: Optional[SubscriptionPayment] = None) -> SubscriptionPayment:
        raise InvalidOperation()

    def webhook(self, request: Request, payload: dict) -> Response:
        receipt = payload['transaction_receipt']

        # Check whether this receipt is anyhow interesting:
        payment = None
        try:
            payment = SubscriptionPayment.objects.get(provider_transaction_id=receipt)
            # We're interested in existing payments only if these are pending, and we're waiting for an update.
            if payment.status != payment.Status.PENDING:
                return Response()
        except SubscriptionPayment.DoesNotExist:
            # If it doesn't exist, it's all right – most probably it needs to be created.
            pass

        # Validate the receipt. Fetch the status and product.

        # Create a new plan mapped to the receipt.
        if payment is None:
            payment = SubscriptionPayment.objects.create(provider_transaction_id=receipt)

        payment.status = SubscriptionPayment.Status.COMPLETED
        payment.save()
        return Response()

    def check_payments(self, payments: Iterable[SubscriptionPayment]):
        pass
