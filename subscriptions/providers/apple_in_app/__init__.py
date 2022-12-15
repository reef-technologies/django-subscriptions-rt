import datetime
import logging
from dataclasses import dataclass
from logging import getLogger
from typing import (
    Callable,
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
from django.utils import timezone
from pydantic import (
    BaseModel,
    ValidationError,
)
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.status import (
    HTTP_200_OK,
    HTTP_400_BAD_REQUEST,
    HTTP_401_UNAUTHORIZED,
)

from subscriptions.models import (
    Plan,
    Subscription,
    SubscriptionPayment,
    SubscriptionPaymentRefund,
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
    AppStoreNotificationTypeV2Subtype,
    AppleAppStoreNotification,
    PayloadValidationError,
    get_original_apple_certificate,
)
from .exceptions import (
    AppleInvalidOperation,
    AppleReceiptValidationError,
    AppleSubscriptionNotCompletedError,
    InvalidAppleReceiptError,
)
from .. import Provider
from ...api.serializers import SubscriptionPaymentSerializer

NotificationHandler = Callable[[AppStoreNotification, logging.LoggerAdapter], None]

global_logger = getLogger(__name__)


class AppleInAppMetadata(BaseModel):
    original_transaction_id: str


@dataclass
class AppleInAppProvider(Provider):
    # This is also name of the field in metadata of the Plan, that stores Apple App Store product id.
    codename: ClassVar[str] = 'apple_in_app'
    bundle_id: ClassVar[str] = settings.APPLE_BUNDLE_ID
    api: AppleAppStoreAPI = None
    metadata_class = AppleInAppMetadata

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
                run_handler = handler
                # If we find a matching object, stop performing operations in context of this try-except.
                break
            except ValidationError as validation_error:
                validation_error_messages.append(str(validation_error))
        else:
            # Came to an end without breaking.
            global_logger.error('Failed matching the payload to any registered request:\n%s.',
                                '\n\n'.join(validation_error_messages))
            return Response(status=HTTP_400_BAD_REQUEST)

        return run_handler(request, instance)

    def check_payments(self, payments: Iterable[SubscriptionPayment]):
        for payment in payments:
            if payment.status != SubscriptionPayment.Status.COMPLETED:
                # All the operations that we care about should be completed before they reach us.
                raise AppleSubscriptionNotCompletedError(payment.provider_transaction_id)

    @classmethod
    def _raise_if_invalid(cls, response: AppleVerifyReceiptResponse) -> None:
        if not response.is_valid or response.receipt.bundle_id != cls.bundle_id:
            raise AppleReceiptValidationError()

    def _get_plan_for_product_id(self, product_id: str) -> Plan:
        search_kwargs = {
            f'metadata__{self.codename}': product_id,
        }
        return Plan.objects.get(**search_kwargs)

    def _get_latest_transaction(self, original_transaction_id: str) -> Optional[SubscriptionPayment]:
        # We assume that the user has a single subscription active for this app on the Apple platform.
        return SubscriptionPayment.objects.filter(
            provider_codename=self.codename,
            metadata__original_transaction_id=original_transaction_id,
        ).order_by('subscription_end').last()

    def _get_active_transaction(self,
                                transaction_id: str,
                                original_transaction_id: str,
                                logger: Optional[logging.LoggerAdapter] = None) -> SubscriptionPayment:
        logger = logger or global_logger
        kwargs = dict(
            provider_codename=self.codename,
            provider_transaction_id=transaction_id,
            metadata__original_transaction_id=original_transaction_id,
            status=SubscriptionPayment.Status.COMPLETED,
        )
        try:
            return SubscriptionPayment.objects.get(**kwargs)
        except SubscriptionPayment.MultipleObjectsReturned:
            # This is left as a countermeasure in case the deduplication fails or the code is still "not good enough"
            # and generates duplicates. It allows us to read a warning from sentry instead of rushing another fix.
            logger.warning('Multiple payments found when fetching active transaction id "%s". '
                           'Consider cleaning it up. Returning first of them.', transaction_id)
            return SubscriptionPayment.objects.filter(**kwargs).first()

    def _get_or_create_payment(self,
                               transaction_id: str,
                               original_transaction_id: str,
                               user: User,
                               plan: Plan,
                               start: datetime.datetime,
                               end: datetime.datetime,
                               subscription: Optional[Subscription] = None,
                               logger: Optional[logging.LoggerAdapter] = None) -> SubscriptionPayment:
        logger = logger or global_logger
        kwargs = dict(
            provider_codename=self.codename,
            provider_transaction_id=transaction_id,
        )
        try:
            payment, was_created = SubscriptionPayment.objects.get_or_create(
                defaults={
                    'status': SubscriptionPayment.Status.COMPLETED,
                    # In-app purchase doesn't report the money.
                    # We mark it as None to indicate we don't know how much did it cost.
                    'amount': None,
                    'user': user,
                    'plan': plan,
                    'subscription': subscription,
                    'subscription_start': start,
                    'subscription_end': end,
                    'metadata': AppleInAppMetadata(original_transaction_id=original_transaction_id).dict(),
                },
                **kwargs
            )
            if was_created:
                payment.subscription.auto_prolong = False
                payment.subscription.save()
        except SubscriptionPayment.MultipleObjectsReturned:
            # This is left as a countermeasure in case the deduplication fails or the code is still "not good enough"
            # and generates duplicates. It allows us to read a warning from sentry instead of rushing another fix.
            logger.warning('Multiple payments found when get_or_create for transaction id "%s". '
                           'Consider cleaning it up. Returning first of them.', transaction_id)
            payment = SubscriptionPayment.objects.filter(**kwargs).first()

        return payment

    def _handle_single_receipt_info(self,
                                    user: User,
                                    receipt_info: AppleLatestReceiptInfo,
                                    logger: logging.LoggerAdapter) -> Optional[SubscriptionPayment]:
        logger.debug('Receipt handling started.')
        if receipt_info.cancellation_date is not None:
            # Cancellation/refunds are handled via notifications, we skip them during receipt handling to simplify.
            logger.warning('Found a cancellation date in receipt: %s, ignoring this receipt.', receipt_info)
            return None

        # Find the right plan to create subscription. This raises an error if the plan is not found.
        plan = self._get_plan_for_product_id(receipt_info.product_id)
        logger.debug('Handling receipt with transaction id: %s; original transaction id: %s and product id: %s.',
                     receipt_info.transaction_id,
                     receipt_info.original_transaction_id,
                     receipt_info.product_id)

        subscription_payment = self._get_or_create_payment(
            receipt_info.transaction_id,
            receipt_info.original_transaction_id,
            user,
            plan,
            receipt_info.purchase_date,
            receipt_info.expires_date,
            logger=logger,
        )

        logger.debug('Receipt handling finished.')
        return subscription_payment

    @transaction.atomic
    def _handle_receipt(self, request: Request, payload: AppleReceiptRequest) -> Response:
        # Check whether the user is authenticated.
        if not request.user.is_authenticated:
            return Response(status=HTTP_401_UNAUTHORIZED)

        receipt = payload.transaction_receipt

        # Validate the receipt. Fetch the status and product.
        receipt_data = self.api.fetch_receipt_data(receipt)
        self._raise_if_invalid(receipt_data)

        if not receipt_data.latest_receipt_info:  # Either None or empty list.
            raise InvalidAppleReceiptError('No latest receipt info provided, no recurring subscriptions to check.')

        latest_payment = None
        for receipt_info in receipt_data.latest_receipt_info:
            logger = self._make_receipt_logger(receipt_data, receipt_info)
            # We receive all the elements and check whether we've actually activated them.
            payment = self._handle_single_receipt_info(request.user, receipt_info, logger)
            if payment is None:
                continue

            if latest_payment is None or payment.subscription_end > latest_payment.subscription_end:
                latest_payment = payment

        if latest_payment is None:
            global_logger.warning('No subscription information provided in the payload receipt.')
            return Response(status=HTTP_400_BAD_REQUEST)

        return Response(SubscriptionPaymentSerializer(latest_payment).data, status=HTTP_200_OK)

    def _handle_new_subscription(self, notification: AppStoreNotification, logger: logging.LoggerAdapter) -> None:
        logger.debug('New subscription add operation started.')
        # This can be a renewal, this could be subscription change with immediate effect.
        transaction_info = notification.transaction_info

        # Check if we handled this before.
        if SubscriptionPayment.objects.filter(
            provider_codename=self.codename,
            provider_transaction_id=transaction_info.transaction_id,
        ).exists():
            # we've already handled this transaction, just the App Store didn't
            # receive the information that we did. Skip it. If it wasn't renewal,
            # we could start searching.
            logger.info('Transaction with id %s was already handled', transaction_info.transaction_id)
            return

        latest_payment = self._get_latest_transaction(transaction_info.original_transaction_id)
        assert latest_payment, f'New subscription received for {transaction_info=} where no payments exist.'

        current_plan = latest_payment.plan
        subscription = latest_payment.subscription
        if current_plan.metadata.get(self.codename) != transaction_info.product_id:
            current_plan = self._get_plan_for_product_id(transaction_info.product_id)
            # Stopping old subscription, so that the user won't benefit from both of them.
            subscription.end = timezone.now()
            subscription.save()
            logger.debug('Current plan %s is different than the new plan %s. '
                         'Ending old subscription %s with datetime %s.',
                         current_plan.metadata.get(self.codename),
                         transaction_info.product_id,
                         subscription,
                         subscription.end)
            # New subscription will be created with a new payment object.
            subscription = None

        self._get_or_create_payment(
            transaction_info.transaction_id,
            transaction_info.original_transaction_id,
            latest_payment.user,
            current_plan,
            transaction_info.purchase_date,
            transaction_info.expires_date,
            subscription=subscription,
            logger=logger,
        )
        logger.debug('New subscription add operation finished.')

    def _handle_subscription_change(self, notification: AppStoreNotification, logger: logging.LoggerAdapter) -> None:
        logger.debug('Subscription change operation started.')
        assert notification.subtype in {
            AppStoreNotificationTypeV2Subtype.UPGRADE,
            AppStoreNotificationTypeV2Subtype.DOWNGRADE,
        }, f'Unsupported notification subtype received for subscription change: {notification.subtype}.'

        transaction_info = notification.transaction_info

        if notification.subtype == AppStoreNotificationTypeV2Subtype.DOWNGRADE:
            logger.info('Downgrade requested for original transaction id "%s" to product "%s". '
                        'This will happen during the next renewal.',
                        transaction_info.original_transaction_id, transaction_info.product_id)
            return

        # We handle this in two steps:
        # 1. We shorten the current subscription and subscription payment to current start of the period.
        # 2. We add a new subscription with a new plan.

        latest_payment = self._get_latest_transaction(transaction_info.original_transaction_id)
        assert latest_payment, f'Change received for {transaction_info=} where no payments exist.'

        # Ensuring that subscription ends earlier before making the payment end earlier.
        now = timezone.now()
        latest_payment.subscription.end = now
        latest_payment.subscription.save()
        latest_payment.subscription_end = now

        latest_payment.save()
        logger.debug('Subscription %s and latest payment %s end dates set to %s',
                     latest_payment.subscription,
                     latest_payment,
                     now)

        # Creating a new subscription with a new plan.
        self._handle_new_subscription(notification, logger)
        logger.debug('Subscription change operation finished.')

    def _handle_refund(self, notification: AppStoreNotification, logger: logging.LoggerAdapter) -> None:
        logger.debug('Refund operation started.')
        transaction_info = notification.transaction_info
        assert transaction_info.revocation_date is not None, f'Received refund without revocation date: {notification}'

        # I didn't find clear information whether the refund is a separate transaction or not. Checking both ways then.
        try:
            refunded_payment = self._get_active_transaction(
                transaction_info.transaction_id,
                transaction_info.original_transaction_id,
                logger=logger,
            )
        except SubscriptionPayment.DoesNotExist:
            logger.warning('Refund called on unknown transaction id "%s". Searching for latest payment for given '
                           'original transaction id "%s".',
                           transaction_info.transaction_id,
                           transaction_info.original_transaction_id)
            refunded_payment = self._get_latest_transaction(transaction_info.original_transaction_id)

        logger.debug('Payment to be refunded: %s (original transaction id: %s, transaction id: %s)',
                     refunded_payment,
                     transaction_info.original_transaction_id,
                     transaction_info.transaction_id)
        assert refunded_payment, f'Refund received for {transaction_info=} where no payments exist.'

        refunded_payment.subscription.end = transaction_info.revocation_date
        refunded_payment.subscription.save()
        refunded_payment.subscription_end = transaction_info.revocation_date
        refunded_payment.status = SubscriptionPayment.Status.CANCELLED

        refunded_payment.save()
        logger.debug('Subscription %s and subscription payment %s got end date set to %s.',
                     refunded_payment.subscription,
                     refunded_payment,
                     transaction_info.revocation_date)

        refund_object = SubscriptionPaymentRefund.objects.create(
            original_payment=refunded_payment,
            provider_codename=refunded_payment.provider_codename,
            provider_transaction_id=refunded_payment.provider_transaction_id,
            status=SubscriptionPayment.Status.COMPLETED,
            amount=refunded_payment.amount,
            metadata=refunded_payment.metadata,
        )
        logger.debug('Refund object created: %s', refund_object)
        logger.debug('Refund operation finished.')

    @transaction.atomic
    def _handle_app_store(self, _request: Request, payload: AppleAppStoreNotification) -> Response:
        signed_payload = payload.signed_payload

        try:
            notification_object = AppStoreNotification.from_signed_payload(signed_payload)
        except PayloadValidationError as exception:
            global_logger.exception('Invalid payload received from the notification endpoint: "%s"', signed_payload)
            raise SuspiciousOperation() from exception
        logger = self._make_notification_logger(notification_object)

        notification_handling: dict[AppStoreNotificationTypeV2, NotificationHandler] = {
            AppStoreNotificationTypeV2.DID_RENEW: self._handle_new_subscription,
            AppStoreNotificationTypeV2.DID_CHANGE_RENEWAL_PREF: self._handle_subscription_change,
            AppStoreNotificationTypeV2.REFUND: self._handle_refund,
        }

        # We're only handling a handful of events. The rest means that,
        # for whatever reason, it failed, or we don't care about them for now.
        # As for expirations – these are handled on our side anyway, that would be only an additional validation.
        # In all other cases we're just returning "200 OK" to let the App Store know that we're received the message.
        handler = notification_handling.get(notification_object.notification, None)
        if handler is None:
            logger.info('Received apple notification %s (%s) and ignored it. Payload: %s',
                        notification_object.notification,
                        notification_object.subtype,
                        str(payload))
            return Response(status=HTTP_200_OK)

        logger.debug('Received apple notification %s (%s). Raw payload: %s',
                     notification_object.notification,
                     notification_object.subtype,
                     str(payload))
        # Handlers can at most raise an exception.
        handler(notification_object, logger)
        logger.debug('Finished handling notification')

        return Response(status=HTTP_200_OK)

    def _make_child_logger(self, format_addition: str) -> logging.Logger:
        log_format = '[%(asctime)s](%(levelName)s)' + format_addition + ': %(message)s'
        formatter = logging.Formatter(log_format)

        # This won't be a nice name, but we're not using it anywhere.
        logger = global_logger.getChild(format_addition)
        handlers = logger.handlers
        # By default, the StreamHandler is "implied" and not actually in handlers.
        if len(handlers) == 0:
            handler = logging.StreamHandler()
            handler.setFormatter(formatter)
            logger.addHandler(handler)
        else:
            for handler in handlers:
                handler.setFormatter(formatter)

        return logger

    def _make_receipt_logger(self,
                             receipt_data: AppleVerifyReceiptResponse,
                             receipt_info: AppleLatestReceiptInfo) -> logging.LoggerAdapter:
        logger = self._make_child_logger('<Version: %(app_version)s'
                                         ' Transaction ID: %(transaction_id)s'
                                         ' %(purchase_date)s -> %(expires_date)s>')

        logger_adapter = logging.LoggerAdapter(
            logger,
            dict(
                app_version=receipt_data.receipt.application_version,
                transaction_id=receipt_info.transaction_id,
                purchase_date=receipt_info.purchase_date,
                expires_date=receipt_info.expires_date,
            ),
        )

        return logger_adapter

    def _make_notification_logger(self, notification: AppStoreNotification) -> logging.LoggerAdapter:
        logger = self._make_child_logger('<Notification UUID: %(notification_uuid)s'
                                         ' Transaction ID: %(transaction_id)s'
                                         ' %(purchase_date)s -> %(expires_date)s>')

        logger_adapter = logging.LoggerAdapter(
            logger,
            dict(
                notification_uuid=notification.notification_uuid,
                transaction_id=notification.transaction_info.transaction_id,
                purchase_date=notification.transaction_info.purchase_date,
                expires_date=notification.transaction_info.expires_date,
            ),
        )

        return logger_adapter
