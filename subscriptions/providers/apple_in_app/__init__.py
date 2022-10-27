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
from django.contrib.auth.models import User
from django.core.exceptions import SuspiciousOperation
from django.db import transaction
from pydantic import ValidationError
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.status import (
    HTTP_200_OK,
    HTTP_400_BAD_REQUEST,
)

from subscriptions.models import (
    Plan,
    Subscription,
    SubscriptionPayment,
)
from .api import (
    AppleAppStoreAPI,
    AppleInApp,
    AppleLatestReceiptInfo,
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
    InvalidAppleReceiptError,
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
                validation_error_messages.append(str(validation_error))

        # Invalid, unhandled request.
        logger.error('Failed matching the payload to any registered request:\n%s.',
                     '\n\n'.join(validation_error_messages))
        return Response(status=HTTP_400_BAD_REQUEST)

    def check_payments(self, payments: Iterable[SubscriptionPayment]):
        for payment in payments:
            if payment.status != SubscriptionPayment.Status.COMPLETED:
                # All the operations that we care about should be completed before they reach us.
                raise AppleSubscriptionNotCompletedError(payment.provider_transaction_id)

    @classmethod
    def _is_receipt_valid(cls, response: AppleVerifyReceiptResponse) -> None:
        if not response.is_valid or response.receipt.bundle_id != cls.bundle_id:
            raise AppleReceiptValidationError()

    def _handle_single_receipt_info(self,
                                    user: User,
                                    receipt_info: AppleLatestReceiptInfo) -> Optional[SubscriptionPayment]:
        with suppress(SubscriptionPayment.DoesNotExist):  # noqa (DoesNotExist seems not to be an exception for PyCharm)
            payment = SubscriptionPayment.objects.get(provider_codename=self.codename,
                                                      provider_transaction_id=receipt_info.transaction_id)

            # User was refunded.
            if receipt_info.cancellation_date is not None:
                payment.subscription_end = receipt_info.cancellation_date
                payment.save()

            return payment

        # Find the right plan to create subscription.
        try:
            search_kwargs = {
                f'metadata__{self.codename}': receipt_info.product_id
            }
            plan = Plan.objects.get(**search_kwargs)
        except Plan.DoesNotExist:
            # This means that something wasn't connected as needed.
            logger.exception('Plan for apple in-app purchase "%s" not found.', receipt_info.product_id)
            return None

        # Create subscription payment. Subscription is created automatically.
        subscription_payment = SubscriptionPayment.objects.create(
            provider_codename=self.codename,
            provider_transaction_id=receipt_info.transaction_id,
            # NOTE(kkalinowski): from my understanding, we can only receive receipt after the person has paid
            # but the transaction (from the perspective of the app) might have not yet finished. Money were spent.
            status=SubscriptionPayment.Status.COMPLETED,
            # In-app purchase doesn't report the money.
            # We mark it as None to indicate we don't know how much did it cost.
            amount=None,
            user=user,
            plan=plan,
            subscription_start=receipt_info.purchase_date,
            # If the cancellation date is set, it means that the user was refunded and for whatever reason
            # we weren't notified about this purchase.
            subscription_end=receipt_info.cancellation_date or receipt_info.expires_date,
        )
        subscription_payment.subscription.auto_prolong = False
        subscription_payment.save()

        return subscription_payment

    @transaction.atomic
    def _handle_receipt(self, request: Request, payload: AppleReceiptRequest) -> Response:
        receipt = payload.transaction_receipt

        # Validate the receipt. Fetch the status and product.
        receipt_data = self.api.fetch_receipt_data(receipt)
        self._is_receipt_valid(receipt_data)

        if not receipt_data.latest_receipt_info:  # Either None or empty list.
            raise InvalidAppleReceiptError('No latest receipt info provided, no recurring subscriptions to check.')

        latest_payment = None
        for receipt_info in receipt_data.latest_receipt_info:
            # We receive all the elements and check whether we've actually activated them.
            payment = self._handle_single_receipt_info(request.user, receipt_info)
            if payment is None:
                continue

            if latest_payment is None or payment.subscription_end > latest_payment.subscription_end:
                latest_payment = payment

        # Return the latest payment or empty object if the plans are not properly assigned.
        data = {}
        if latest_payment is not None:
            data = SubscriptionPaymentSerializer(latest_payment).data
        return Response(data, status=HTTP_200_OK)

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
