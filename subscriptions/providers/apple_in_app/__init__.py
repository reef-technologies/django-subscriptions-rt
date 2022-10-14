from dataclasses import dataclass
from decimal import Decimal
from typing import (
    ClassVar,
    Iterable,
    Optional,
    Tuple,
)

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser
from django.core.exceptions import SuspiciousOperation
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
    PayloadValidationError,
    setup_original_apple_certificate,
)
from .. import Provider


@dataclass
class AppleInAppProvider(Provider):
    transaction_receipt_tag: ClassVar[str] = 'transaction_receipt'
    signed_payload_tag: ClassVar[str] = 'signedPayload'

    codename: ClassVar[str] = 'apple_in_app'

    api: AppleAppStoreAPI = None

    def __post_init__(self):
        self.api = AppleAppStoreAPI(settings.APPLE_SHARED_SECRET)
        setup_original_apple_certificate(settings.APPLE_ROOT_CERTIFICATE_PATH)

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

    def _handle_receipt(self, request: Request, payload: dict) -> Response:
        receipt = payload[self.transaction_receipt_tag]

        # Validate the receipt. Fetch the status and product.
        receipt_data = self.api.fetch_receipt_data(receipt)
        single_in_app = self._get_validated_in_app_product(receipt_data)

        # Check whether this receipt is anyhow interesting:
        try:
            payment = SubscriptionPayment.objects.get(provider_codename=self.codename,
                                                      provider_transaction_id=single_in_app.transaction_id)
            # We've already handled this. And all the messages coming to us from iOS should be AFTER
            # the client money were removed.
            # TODO(kkalinowski): return the subscription payment object.
            return Response()
        except SubscriptionPayment.DoesNotExist:
            # If it doesn't exist, it's all right – most probably it needs to be created.
            pass

        # Find the right plan to create subscription.
        plan = Plan.objects.get(apple_in_app=single_in_app.product_id)

        # Create subscription.
        subscription = Subscription(
            user=request.user,
            plan=plan,
            # For in-app purchases this option doesn't make sense.
            auto_prolong=False,
        )
        subscription.save()

        # Create subscription payment.
        subscription_payment = SubscriptionPayment(
            provider_codename=self.codename,
            provider_transaction_id=single_in_app.transaction_id,
            status=SubscriptionPayment.Status.COMPLETED,
            # In-app purchase doesn't report the money.
            amount=Decimal('0.00'),
            user=subscription.user,
            plan=subscription.plan,
            subscription_start=single_in_app.purchase_date,
            subscription_end=single_in_app.expires_date,
        )
        subscription_payment.save()

        # Return the payment.
        return Response()

    def _handle_app_store(self, _request: Request, payload: dict) -> Response:
        signed_payload = payload[self.signed_payload_tag]

        try:
            payload = AppStoreNotification.from_signed_payload(signed_payload)
        except PayloadValidationError:
            raise SuspiciousOperation()

        # We're only handling an actual renewal event. The rest means that,
        # for whatever reason, it failed, or we don't care about them for now.
        # As for expirations – these are handled on our side anyway, that would be only an additional validation.
        # In all other cases we're just returning "200 OK" to let the App Store know that we're received the message.
        if payload.notification != AppStoreNotificationTypeV2.DID_RENEW:
            return Response(status=200)

        # Find the original transaction, fetch the user, create a new subscription payment.
        # Note – if we didn't find it, something is really wrong. This notification is only for subsequent payments.
        subscription_payment = SubscriptionPayment.objects.get(
            provider_codename=self.codename,
            provider_transaction_id=payload.transaction_info.original_transaction_id,
        )

        # Making a silly copy.
        subscription_payment.pk = None
        # Updating relevant fields.
        # TODO(kkalinowski): check whether the product ID didn't change in the meantime –
        #  someone might have upgraded their subscription.
        subscription_payment.provider_transaction_id = payload.transaction_info.transaction_id
        subscription_payment.subscription_start = payload.transaction_info.purchase_date
        subscription_payment.subscription_end = payload.transaction_info.expires_date
        subscription_payment.save()

        return Response(status=200)
