from dataclasses import dataclass
from typing import (
    ClassVar,
    Iterable,
    Optional,
    Tuple,
)

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser
from rest_framework.request import Request
from rest_framework.response import Response

from subscriptions.exceptions import InvalidOperation
from subscriptions.models import (
    Plan,
    Subscription,
    SubscriptionPayment,
)
from .api import (
    AppleAppStoreAPI,
    AppleInApp,
    AppleVerifyReceiptResponse,
)
from .app_store import (
    AppStoreNotification,
    AppStoreNotificationTypeV2,
)
from .. import Provider


@dataclass
class AppleInAppProvider(Provider):
    transaction_receipt_tag: ClassVar[str] = 'transaction_receipt'
    signed_payload_tag: ClassVar[str] = 'signedPayload'

    codename: ClassVar[str] = 'apple_in_app'

    api: AppleAppStoreAPI = None

    def __post_init__(self):
        pass

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
        if self.transaction_receipt_tag in payload:
            return self._handle_receipt(request, payload)
        elif self.signed_payload_tag in payload:
            return self._handle_app_store(request, payload)
        else:
            # Invalid, unhandled request.
            return Response(status=400)

    def check_payments(self, payments: Iterable[SubscriptionPayment]):
        pass

    @staticmethod
    def _get_validated_in_app_product(response: AppleVerifyReceiptResponse) -> AppleInApp:
        assert response.is_valid, str(response)
        assert response.receipt.bundle_id == settings.APPLE_BUNDLE_ID, str(response)
        assert len(response.receipt.in_apps) == 1
        return response.receipt.in_apps[0]

    def _handle_receipt(self, _request: Request, payload: dict) -> Response:
        receipt = payload[self.transaction_receipt_tag]

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
        receipt_data = self.api.fetch_receipt_data(receipt)
        single_in_app = self._get_validated_in_app_product(receipt_data)

        # Create a new plan mapped to the receipt.
        if payment is None:
            payment = SubscriptionPayment.objects.create(provider_transaction_id=receipt)

        payment.status = SubscriptionPayment.Status.COMPLETED
        payment.save()
        return Response()

    def _handle_app_store(self, _request: Request, payload: dict) -> Response:
        signed_payload = payload[self.signed_payload_tag]
        payload = AppStoreNotification.from_signed_payload(signed_payload)

        # We're only handling an actual renewal event. The rest means that,
        # for whatever reason, it failed, or we don't care about them for now.
        # As for expirations – these are handled on our side anyway, that would be only an additional validation.
        # In all other cases we're just returning "200 OK" to let the App Store know that we're received the message.
        if payload.notification != AppStoreNotificationTypeV2.DID_RENEW:
            return Response(status=200)

        # Find the original transaction, fetch the user, create a new subscription payment.
