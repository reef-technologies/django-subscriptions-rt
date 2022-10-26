from contextlib import suppress
from dataclasses import dataclass
from logging import getLogger
from typing import (
    ClassVar,
    Iterable,
    Optional,
    Tuple,
)

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser
from django.core.exceptions import SuspiciousOperation
from django.db import transaction
from more_itertools import one
from pydantic import ValidationError
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.status import (
    HTTP_200_OK,
    HTTP_400_BAD_REQUEST,
    HTTP_404_NOT_FOUND,
)

from subscriptions.models import (
    Plan,
    Subscription,
    SubscriptionPayment,
)
from .api import (
    AppleAppStoreAPI,
    AppleInApp,
    AppleReceiptRequest,
    AppleVerifyReceiptResponse,
)
from .app_store import (
    AppStoreNotification,
    AppStoreNotificationTypeV2,
    AppleAppStoreNotification,
    PayloadValidationError,
    get_original_apple_certificate,
)
from .exceptions import (
    AppleInvalidOperation,
    AppleReceiptValidationError,
    AppleSubscriptionNotCompletedError,
    ProductIdChangedError,
)
from .. import Provider
from ...api.serializers import SubscriptionPaymentSerializer

logger = getLogger(__name__)


@dataclass
class AppleInAppProvider(Provider):
    # This is also name of the field in metadata of the Plan, that stores Apple App Store product id.
    codename: ClassVar[str] = 'apple_in_app'
    bundle_id: ClassVar[str] = settings.APPLE_BUNDLE_ID
    api: AppleAppStoreAPI = None

    def __post_init__(self):
        self.api = AppleAppStoreAPI(settings.APPLE_SHARED_SECRET)
        # Check whether the Apple certificate is provided and is a valid certificate.
        get_original_apple_certificate()

    def charge_online(self, user: AbstractBaseUser, plan: Plan, subscription: Optional[Subscription] = None,
                      quantity: int = 1) -> Tuple[SubscriptionPayment, str]:
        """
        In case of in-app purchase this operation is triggered from the mobile application library.
        """
        raise AppleInvalidOperation()

    def charge_offline(self, user: AbstractBaseUser, plan: Plan, subscription: Optional[Subscription] = None,
                       quantity: int = 1,
                       reference_payment: Optional[SubscriptionPayment] = None) -> SubscriptionPayment:
        raise AppleInvalidOperation()

    def webhook(self, request: Request, payload: dict) -> Response:
        handlers = {
            AppleReceiptRequest: self._handle_receipt,
            AppleAppStoreNotification: self._handle_app_store,
        }

        validation_error_messages = []
        for request_class, handler in handlers.items():
            try:
                instance = request_class.parse_obj(payload)
                return handler(request, instance)
            except ValidationError as validation_error:
                validation_error_messages.append(validation_error.json())

        # Invalid, unhandled request.
        logger.error('Failed matching the payload to any registered request: %s.', '\n'.join(validation_error_messages))
        return Response(status=HTTP_400_BAD_REQUEST)

    def check_payments(self, payments: Iterable[SubscriptionPayment]):
        for payment in payments:
            if payment.status != SubscriptionPayment.Status.COMPLETED:
                # All the operations that we care about should be completed before they reach us.
                raise AppleSubscriptionNotCompletedError(payment.provider_transaction_id)

    @classmethod
    def _get_validated_in_app_product(cls, response: AppleVerifyReceiptResponse) -> AppleInApp:
        if not response.is_valid or response.receipt.bundle_id != cls.bundle_id:
            raise AppleReceiptValidationError()
        return one(response.receipt.in_apps)

    @transaction.atomic
    def _handle_receipt(self, request: Request, payload: AppleReceiptRequest) -> Response:
        receipt = payload.transaction_receipt

        # Validate the receipt. Fetch the status and product.
        receipt_data = self.api.fetch_receipt_data(receipt)
        single_in_app = self._get_validated_in_app_product(receipt_data)

        # Check whether this receipt is anyhow interesting:
        with suppress(SubscriptionPayment.DoesNotExist):  # noqa (DoesNotExist seems not to be an exception for PyCharm)
            payment = SubscriptionPayment.objects.get(provider_codename=self.codename,
                                                      provider_transaction_id=single_in_app.transaction_id)
            # We've already handled this. And all the messages coming to us from iOS should be AFTER
            # the client money were removed.
            return Response(
                SubscriptionPaymentSerializer(payment).data,
                status=HTTP_200_OK,
            )

        # Find the right plan to create subscription.
        try:
            search_kwargs = {
                f'metadata__{self.codename}': single_in_app.product_id
            }
            plan = Plan.objects.get(**search_kwargs)
        except Plan.DoesNotExist:
            logger.warning('Plan for apple in-app purchase "%s" not found.', single_in_app.product_id)
            return Response(status=HTTP_404_NOT_FOUND)

        # Create subscription payment. Subscription is created automatically.
        subscription_payment = SubscriptionPayment.objects.create(
            provider_codename=self.codename,
            provider_transaction_id=single_in_app.transaction_id,
            status=SubscriptionPayment.Status.COMPLETED,
            # In-app purchase doesn't report the money.
            # We mark it as None to indicate we don't know how much did it cost.
            amount=None,
            user=request.user,
            plan=plan,
            subscription_start=single_in_app.purchase_date,
            subscription_end=single_in_app.expires_date,
        )
        subscription_payment.subscription.auto_prolong = False
        subscription_payment.save()

        # Return the payment.
        return Response(
            SubscriptionPaymentSerializer(subscription_payment).data,
            status=HTTP_200_OK,
        )

    @transaction.atomic
    def _handle_app_store(self, _request: Request, payload: AppleAppStoreNotification) -> Response:
        signed_payload = payload.signed_payload

        try:
            payload = AppStoreNotification.from_signed_payload(signed_payload)
        except PayloadValidationError as exception:
            raise SuspiciousOperation() from exception

        # We're only handling an actual renewal event. The rest means that,
        # for whatever reason, it failed, or we don't care about them for now.
        # As for expirations – these are handled on our side anyway, that would be only an additional validation.
        # In all other cases we're just returning "200 OK" to let the App Store know that we're received the message.
        if payload.notification != AppStoreNotificationTypeV2.DID_RENEW:
            logger.info('Received apple notification %s and ignored it. Payload: %s',
                        payload.notification,
                        str(payload))
            return Response(status=HTTP_200_OK)

        # Find the original transaction, fetch the user, create a new subscription payment.
        # Note – if we didn't find it, something is really wrong. This notification is only for subsequent payments.
        subscription_payment = SubscriptionPayment.objects.get(
            provider_codename=self.codename,
            provider_transaction_id=payload.transaction_info.original_transaction_id,
        )

        # Currently, we don't support changing of the product ID. Assert here will let us know if anyone did that.
        # In case the field is not available in metadata, the product ID error will still be raised.
        current_product_id = subscription_payment.plan.metadata.get(self.codename)
        if current_product_id != payload.transaction_info.product_id:
            raise ProductIdChangedError(current_product_id, payload.transaction_info.product_id)

        subscription_payment.pk = None
        # Updating relevant fields.
        subscription_payment.provider_transaction_id = payload.transaction_info.transaction_id
        subscription_payment.subscription_start = payload.transaction_info.purchase_date
        subscription_payment.subscription_end = payload.transaction_info.expires_date
        subscription_payment.save()

        return Response(status=HTTP_200_OK)
