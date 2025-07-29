from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Iterator
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from functools import cached_property
from typing import ClassVar

import httplib2
from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser
from django.core.exceptions import SuspiciousOperation
from django.db import transaction
from django.utils.timezone import now
from googleapiclient.discovery import Resource, build
from more_itertools import one
from oauth2client import service_account
from pydantic import BaseModel, ValidationError
from rest_framework.exceptions import PermissionDenied
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.status import HTTP_200_OK, HTTP_400_BAD_REQUEST

from ...api.serializers import SubscriptionPaymentSerializer
from ...models import Plan, SubscriptionPayment
from ...utils import advisory_lock, fromisoformat
from .. import Provider
from .exceptions import InvalidOperation
from .schemas import (
    AppNotification,
    GoogleAcknowledgementState,
    GoogleAutoRenewingBasePlanType,
    GoogleBasePlan,
    GoogleBasePlanState,
    GoogleDeveloperNotification,
    GoogleMoney,
    GooglePubSubData,
    GoogleRegionalBasePlanConfig,
    GoogleResubscribeState,
    GoogleSubscription,
    GoogleSubscriptionNotificationType,
    GoogleSubscriptionProrationMode,
    GoogleSubscriptionPurchaseV2,
    GoogleSubscriptionState,
    Metadata,
    MultiNotification,
)

log = logging.getLogger(__name__)


def parse_ms_time(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000)


@dataclass
class GoogleInAppProvider(Provider):
    """
    Google Play subscriptions support.

    This provider does not allow charging on backend side, thus
    1) all Plans are managed on backend side
    2) all payments are handled by Google

    Requires enabling "Google Play Android Developer API".
    https://stackoverflow.com/a/65254700/1935381

    Testing:
    https://developer.android.com/google/play/billing/test
    """

    codename: ClassVar[str] = "google_in_app"
    is_external: ClassVar[bool] = True
    package_name: ClassVar[str] = str(settings.GOOGLE_PLAY_PACKAGE_NAME)
    metadata_class: ClassVar[type[BaseModel]] = Metadata

    service_account: dict = field(
        default_factory=lambda: json.loads(str(settings.GOOGLE_PLAY_SERVICE_ACCOUNT)),
    )

    @cached_property
    def api(self) -> Resource:
        # https://googleapis.github.io/google-api-python-client/docs/dyn/androidpublisher_v3.html
        credentials = service_account.ServiceAccountCredentials.from_json_keyfile_dict(
            self.service_account,
            scopes=["https://www.googleapis.com/auth/androidpublisher"],
        )
        http = httplib2.Http()
        http = credentials.authorize(http)
        return build("androidpublisher", "v3", http=http)

    @cached_property
    def subscriptions_api(self) -> Resource:
        return self.api.monetization().subscriptions()  # type: ignore[attr-defined]

    @classmethod
    def get_google_id(cls, plan: Plan) -> str:
        with suppress(KeyError):
            raw_data = plan.metadata[cls.codename]
            with suppress(ValidationError):
                google_subscription = GoogleSubscription.parse_obj(raw_data)
                return google_subscription.productId

        return plan.codename

    @classmethod
    def get_plan_by_google_id(cls, google_id: str) -> Plan:
        return Plan.objects.get(
            **{
                f"metadata__{cls.codename}__productId": google_id,
            }
        )

    def charge_offline(self, *args, **kwargs) -> SubscriptionPayment:
        # don't try to prolong the subscription on our side
        raise InvalidOperation(f"Offline charge not supported for {self.codename}")

    def charge_online(self, *args, **kwargs) -> tuple[SubscriptionPayment, str]:
        raise InvalidOperation(f"Online charge not supported for {self.codename}")

    def iter_subscriptions(self) -> Iterator[GoogleSubscription]:
        """Yield all Google in-app products available for users."""

        # https://developers.google.com/android-publisher/api-ref/rest/v3/monetization.subscriptions
        page_token = None
        while True:
            response = self.subscriptions_api.list(  # type: ignore[attr-defined]
                packageName=self.package_name,
                showArchived=True,
                pageToken=page_token,
            ).execute()
            yield from (GoogleSubscription.parse_obj(item) for item in response["subscriptions"])
            if not (page_token := response.get("nextPageToken")):
                return

    def sync_plans(self):
        """Sync existing Plans with Google Play."""

        plans = {}
        for plan in Plan.objects.all():
            plan_subscription = self.as_google_subscription(plan)
            plans[plan_subscription.productId] = (plan, plan_subscription)

        for google_subscription in self.iter_subscriptions():
            plan, plan_subscription = plans.pop(google_subscription["productId"], (None, None))
            self.sync_plan(plan, plan_subscription, google_subscription)

        for plan in plans:
            self.sync_plan(plan, plan_subscription, None)

    def sync_plan(
        self,
        plan: Plan | None,
        plan_subscription: GoogleSubscription | None,
        google_subscription: GoogleSubscription | None,
    ):
        """
        Sync Plans with Google.

        If `google_subscription` differs from `plan_subscription`, the update is pushed
        to Google and Plan's metadata is modified to hold latest GoogleSubscription content.
        """
        # https://googleapis.github.io/google-api-python-client/docs/dyn/androidpublisher_v3.monetization.subscriptions.html

        assert plan_subscription or google_subscription

        if plan and not google_subscription:
            log.info("Pushing plan %s", plan)
            assert plan_subscription
            plan_subscription_dict = plan_subscription.dict()
            # https://googleapis.github.io/google-api-python-client/docs/dyn/androidpublisher_v3.monetization.subscriptions.html#create
            _ = self.subscriptions_api.create(  # type: ignore[attr-defined]
                packageName=self.package_name,
                body=plan_subscription_dict,
            ).execute()

            plan.metadata[self.codename] = plan_subscription_dict
            plan.save()

        elif google_subscription and not plan:
            if one(google_subscription.basePlans).state == GoogleBasePlanState.ACTIVE:
                log.warning("Disabling google subscription %s which has no corresponding plan", google_subscription)

                self.subscriptions_api.basePlans().deactivate(  # type: ignore[attr-defined]
                    packageName=self.package_name,
                    productId=google_subscription.productId,
                    basePlanId=one(google_subscription.basePlans).basePlanId,
                ).execute()

            # google_subscription.'status': GoogleSubscriptionStatus.INACTIVE,
            # subscriptions_api.patch(
            #     packageName=self.package_name,
            #     sku=google_subscription.sku,
            #     updateMask='',
            #     body=google_subscription.dict(),
            # )

        elif google_subscription != plan_subscription:
            assert plan
            assert plan_subscription
            assert google_subscription
            log.info(
                "Updating subscription because it differs from plan: subscription=%s, plan=%s",
                google_subscription,
                plan_subscription,
            )

            # enable / disable base plan if needed
            base_plan = one(google_subscription.basePlans)
            google_id = self.get_google_id(plan)
            if base_plan.state == GoogleBasePlanState.ACTIVE and not plan.is_enabled:
                self.subscriptions_api.basePlans().deactivate(  # type: ignore[attr-defined]
                    packageName=self.package_name,
                    productId=google_id,
                    basePlanId=google_id,
                ).execute()
                base_plan.state = GoogleBasePlanState.INACTIVE
            elif base_plan.state == GoogleBasePlanState.INACTIVE and plan.is_enabled:
                self.subscriptions_api.basePlans().activate(  # type: ignore[attr-defined]
                    packageName=self.package_name,
                    productId=google_id,
                    basePlanId=google_id,
                ).execute()
                base_plan.state = GoogleBasePlanState.ACTIVE

            # update other fields if needed
            plan_subscription_dict = plan_subscription.dict()
            # https://googleapis.github.io/google-api-python-client/docs/dyn/androidpublisher_v3.monetization.subscriptions.html#patch
            # https://developers.google.com/android-publisher/api-ref/rest/v3/monetization.subscriptions/patch
            _ = self.subscriptions_api.patch(  # type: ignore[attr-defined]
                packageName=self.package_name,
                productId=plan_subscription.productId,
                body=plan_subscription_dict,
                regionsVersion={
                    "version": "2022/01",
                },
                updateMask="",  # TODO
            )

            # something changed -> update plan metadata
            plan.metadata[self.codename] = plan_subscription_dict
            plan.save()

    @classmethod
    def relativedelta_to_iso8601(cls, period: relativedelta) -> str:
        if period == relativedelta():
            return "P0D"
        # TODO
        raise NotImplementedError()

    @classmethod
    def as_google_subscription(cls, plan: Plan) -> GoogleSubscription:
        # https://support.google.com/googleplay/android-developer/answer/12154973?hl=en
        google_id = cls.get_google_id(plan)
        return GoogleSubscription(
            packageName=cls.package_name,
            productId=google_id,
            basePlans=[
                GoogleBasePlan(
                    basePlanId=google_id,
                    state=GoogleBasePlanState.ACTIVE if plan.is_enabled else GoogleBasePlanState.INACTIVE,
                    regionalConfigs=[
                        GoogleRegionalBasePlanConfig(
                            regionCode="US",
                            newSubscriberAvailability=True,
                            price=GoogleMoney(
                                currencyCode=str(plan.charge_amount.currency),
                                units=str(plan.charge_amount.amount // 1),
                                nanos=(plan.charge_amount.amount % 1) * (10**9),
                            ),
                        ),
                    ],
                    autoRenewingBasePlanType=GoogleAutoRenewingBasePlanType(
                        billingPeriodDuration=cls.relativedelta_to_iso8601(plan.charge_period),
                        gracePeriodDuration=cls.relativedelta_to_iso8601(relativedelta()),
                        resubscribeState=GoogleResubscribeState.ACTIVE,
                        prorationMode=GoogleSubscriptionProrationMode.CHARGE_ON_NEXT_BILLING_DATE,
                        legacyCompatible=False,
                        legacyCompatibleSubscriptionOfferId="",
                    ),
                )
            ],
            archived=plan.is_enabled,
        )

    def dismiss_token(self, token: str):
        """Stop a subscription associated with specific purchase token."""

        # https://chromeos.dev/en/publish/play-billing-backend#subscription-linkedpurchasetoken
        with transaction.atomic():
            try:
                latest_payment = SubscriptionPayment.objects.filter(
                    provider_codename=self.codename,
                    provider_transaction_id=token,
                ).latest()
            except SubscriptionPayment.DoesNotExist:
                log.warning("Tried to dismiss a token %s but no payment was found", token)
                return

            now_ = now()

            assert latest_payment.paid_until
            if latest_payment.paid_until > now_:
                latest_payment.paid_until = now_
                latest_payment.save()

            subscription = latest_payment.subscription
            assert subscription
            if subscription.end > now_:
                subscription.end = now_
                subscription.save()

    def get_user_by_token(self, token: str) -> AbstractBaseUser | None:
        with suppress(SubscriptionPayment.DoesNotExist):
            return self.get_last_payment(token).user

    def webhook(self, request: Request, payload: dict) -> Response:
        try:
            notification = MultiNotification.parse_obj({"notification": payload}).notification
        except ValidationError:
            log.exception("Error parsing notification %s", payload)
            return Response(status=HTTP_400_BAD_REQUEST)

        if isinstance(notification, AppNotification):
            return self.handle_app_notification(notification, request.user)  # type: ignore[arg-type]

        elif isinstance(notification, GooglePubSubData):
            raw_data = notification.message.decode()
            developer_notification = GoogleDeveloperNotification.parse_raw(raw_data)
            if developer_notification.testNotification:
                return Response(status=HTTP_200_OK)

            return self.handle_google_notification(developer_notification)

        else:
            raise ValueError(f"Unknown notification type {notification}")

    def get_purchase(self, purchase_token: str) -> GoogleSubscriptionPurchaseV2:
        # https://github.com/googleapis/google-api-python-client/blob/main/docs/start.md
        subscription_purchase_dict = (
            self.api.purchases()  # type: ignore[attr-defined]
            .subscriptionsv2()
            .get(
                packageName=self.package_name,
                token=purchase_token,
            )
            .execute()
        )
        return GoogleSubscriptionPurchaseV2.parse_obj(subscription_purchase_dict)

    # TODO: rate limit this. Maybe make this a CBV and use Throttle and IsAuthenticated classes?
    def handle_app_notification(self, notification: AppNotification, user: AbstractBaseUser | None) -> Response:
        """
        Handle notification from the app. It is expected that app sends a notification
        on initial purchase at least once, so that user <--> purchase token mapping is saved.
        All other status change notifications come from RTDN.
        """
        log.debug("Received app notification %s for user %s", notification, user)

        if not user or not user.is_authenticated:
            log.warning("Missing user for app notification: %s", notification)
            raise PermissionDenied(detail="Not authenticated")

        if not user.is_active:
            log.warning("Received app notification %s for inactive user %s", notification, user)

        payment = self.update_or_create_subscription(
            purchase_token=notification.purchase_token,
            event=GoogleSubscriptionNotificationType.PURCHASED,
            user=user,
        )

        return Response(
            SubscriptionPaymentSerializer(payment).data,
            status=HTTP_200_OK,
        )

    def handle_google_notification(self, notification: GoogleDeveloperNotification) -> Response:
        """
        Requires enabling "real-time developer notifications": https://developer.android.com/google/play/billing/getting-ready#configure-rtdn.

        https://chromeos.dev/en/publish/play-billing-backend#real-time-developer-notifications

        When a subscription is purchased, a SubscriptionNotification with type
        SUBSCRIPTION_PURCHASED notification is sent. When you receive this notification,
        you should query the Google Play Developer API to get the latest subscription state.
        """
        log.debug("Received RTDN notification %s", notification)

        if notification.voidedPurchaseNotification:
            log.debug("Received voided purchase notification -> cancelling subscription")
            self.dismiss_token(notification.voidedPurchaseNotification.purchaseToken)
            return Response({}, status=HTTP_200_OK)

        assert notification.subscriptionNotification, "subscriptionNotification field not found in notification"
        payment = self.update_or_create_subscription(
            purchase_token=notification.subscriptionNotification.purchaseToken,
            event=notification.subscriptionNotification.notificationType,
        )

        return Response(
            SubscriptionPaymentSerializer(payment).data,
            status=HTTP_200_OK,
        )

    def get_last_payment(self, purchase_token: str) -> SubscriptionPayment:
        # Google uses same purchaseToken for subsequent payments
        return SubscriptionPayment.objects.filter(
            provider_codename=self.codename,
            provider_transaction_id=purchase_token,
        ).latest()

    def update_or_create_subscription(
        self,
        purchase_token: str,
        event: GoogleSubscriptionNotificationType,
        user: AbstractBaseUser | None = None,
    ) -> SubscriptionPayment | None:
        """
        Gets purchase token from notification and use it to query for purchase
        info from Google. Subscription and SubscriptionPayments are updated if needed.
        This method also sends an acknowledgement to Google when Subscription is created.
        """

        purchase = self.get_purchase(purchase_token)
        linked_token = purchase.linkedPurchaseToken

        purchase_item = one(purchase.lineItems)
        product_id = purchase_item.productId
        purchase_end = fromisoformat(purchase_item.expiryTime)

        if not user and linked_token:
            user = self.get_user_by_token(linked_token)
        if not user:
            user = self.get_user_by_token(purchase_token)
            if not user:
                # when user just made a purchase and google notification arrives before
                # the one from app, we don't have Subscription and SubscriptionPayment yet;
                # in this case, simply ignore the notification and wait for app's notification
                if event != GoogleSubscriptionNotificationType.PURCHASED:
                    log.error("User not found for token %s", purchase_token)
                return None
        assert user

        self.check_event(event, purchase)

        with advisory_lock(lock_id=f".subscriptions.{self.__class__.__name__}.update_or_create_subscription.{user.pk}"):
            if event in {
                GoogleSubscriptionNotificationType.RECOVERED,
                GoogleSubscriptionNotificationType.DEFERRED,
                GoogleSubscriptionNotificationType.IN_GRACE_PERIOD,
                GoogleSubscriptionNotificationType.RESTARTED,
                GoogleSubscriptionNotificationType.PRICE_CHANGE_CONFIRMED,
                GoogleSubscriptionNotificationType.PAUSE_SCHEDULE_CHANGED,
            }:
                # just prolong if needed
                last_payment = self.get_last_payment(purchase_token)
                subscription = last_payment.subscription
                assert subscription, f"Subscription should exist for {event} payment"
                subscription.end = max(subscription.end, purchase_end)
                subscription.auto_prolong = True
                subscription.save()

            elif event == GoogleSubscriptionNotificationType.RENEWED:
                # TODO: handle case when subscription is resumed from a pause
                last_payment = self.get_last_payment(purchase_token)
                assert last_payment.paid_until
                if purchase_end > last_payment.paid_until:
                    last_payment.uid = ""
                    last_payment.paid_since = last_payment.paid_until
                    last_payment.paid_until = purchase_end
                    last_payment.created = last_payment.updated = ""
                    last_payment.meta = Metadata(purchase=purchase)
                    last_payment.save()

                subscription = last_payment.subscription
                assert subscription, f"Subscription should exist for {event} payment"
                subscription.auto_prolong = True
                subscription.save()

            elif event == GoogleSubscriptionNotificationType.CANCELED:
                last_payment = self.get_last_payment(purchase_token)
                subscription = last_payment.subscription
                assert subscription, f"Subscription should exist for {event} payment"

                subscription.end = purchase_end
                subscription.auto_prolong = False
                subscription.save()

                assert last_payment.paid_until
                if last_payment.paid_until > subscription.end:
                    last_payment.paid_until = subscription.end
                    last_payment.save()

            elif event == GoogleSubscriptionNotificationType.PURCHASED:
                plan = self.get_plan_by_google_id(product_id)
                last_payment, _ = SubscriptionPayment.objects.get_or_create(
                    provider_codename=self.codename,
                    provider_transaction_id=purchase_token,
                    defaults=dict(
                        user=user,
                        status=SubscriptionPayment.Status.COMPLETED,
                        plan=plan,
                        amount=None,
                        paid_since=fromisoformat(purchase.startTime),
                        paid_until=purchase_end,
                        metadata=Metadata(purchase=purchase).dict(),
                    ),
                )

            elif event in {
                GoogleSubscriptionNotificationType.ON_HOLD,
                GoogleSubscriptionNotificationType.PAUSED,
                GoogleSubscriptionNotificationType.REVOKED,
            }:
                last_payment = self.get_last_payment(purchase_token)
                subscription = last_payment.subscription
                assert subscription, f"Subscription should exist for {event} payment"

                subscription.end = now()
                subscription.auto_prolong = False
                subscription.save()

            elif event == GoogleSubscriptionNotificationType.EXPIRED:
                last_payment = self.get_last_payment(purchase_token)
                subscription = last_payment.subscription
                assert subscription, f"Subscription should exist for {event} payment"
                subscription.end = purchase_end
                subscription.auto_prolong = False
                subscription.save()

            else:
                raise ValueError("Unsupported notification type %s", event)

            # disable offline charging of the subscription
            subscription = last_payment.subscription
            if event == GoogleSubscriptionNotificationType.PURCHASED and subscription and subscription.auto_prolong:
                subscription.auto_prolong = False
                subscription.save()

            if linked_token:
                assert purchase_token != linked_token
                self.dismiss_token(linked_token)

            if purchase.acknowledgementState == GoogleAcknowledgementState.PENDING:
                self.acknowledge(
                    packageName=self.package_name,
                    subscriptionId=product_id,
                    token=purchase_token,
                    body={
                        "developerPayload": json.dumps(
                            {
                                "subscription.id": subscription and str(subscription.pk),
                                "user.id": user.pk,
                            }
                        ),
                    },
                )

        return last_payment

    def acknowledge(self, **kwargs):
        # TODO created this proxy for testing mocking, remove this later
        return self.api.purchases().subscriptions().acknowledge(**kwargs).execute()

    @classmethod
    def check_event(cls, event: GoogleSubscriptionNotificationType, purchase: GoogleSubscriptionPurchaseV2):
        """Check that event sent by RTDN matches subscription status."""
        purchase_status = purchase.subscriptionState
        expected_statuses = {
            GoogleSubscriptionNotificationType.RECOVERED: {GoogleSubscriptionState.ACTIVE},
            GoogleSubscriptionNotificationType.RENEWED: {GoogleSubscriptionState.ACTIVE},
            GoogleSubscriptionNotificationType.CANCELED: {
                GoogleSubscriptionState.CANCELED,
                GoogleSubscriptionState.EXPIRED,
            },
            GoogleSubscriptionNotificationType.PURCHASED: {GoogleSubscriptionState.ACTIVE},
            GoogleSubscriptionNotificationType.ON_HOLD: {GoogleSubscriptionState.ON_HOLD},
            GoogleSubscriptionNotificationType.IN_GRACE_PERIOD: {GoogleSubscriptionState.IN_GRACE_PERIOD},
            GoogleSubscriptionNotificationType.RESTARTED: {GoogleSubscriptionState.ACTIVE},
            GoogleSubscriptionNotificationType.PRICE_CHANGE_CONFIRMED: {GoogleSubscriptionState.ACTIVE},
            GoogleSubscriptionNotificationType.DEFERRED: {GoogleSubscriptionState.ACTIVE},
            GoogleSubscriptionNotificationType.PAUSED: {GoogleSubscriptionState.PAUSED},
            GoogleSubscriptionNotificationType.PAUSE_SCHEDULE_CHANGED: {GoogleSubscriptionState.PAUSED},
            GoogleSubscriptionNotificationType.REVOKED: {GoogleSubscriptionState.EXPIRED},
            GoogleSubscriptionNotificationType.EXPIRED: {GoogleSubscriptionState.EXPIRED},
        }[event]
        if purchase_status not in expected_statuses:
            log.warning(
                "Expected purchase status to be of %s after event %s but got %s",
                expected_statuses,
                event,
                purchase_status,
            )
            raise SuspiciousOperation(f"Status mismatch for {event=}: {purchase_status=}, {expected_statuses=}")

    def check_payments(self, payments: Iterable[SubscriptionPayment]):
        """
        The purchases.subscriptions:get method of the Google Play Developer API
        is the source of truth for managing user subscriptions. If you manage
        the state of your subscribers on a secure backend server, you should keep
        its state in sync with Google servers. However, frequent polling of
        Google Play Developer API can lead to hitting the API quota restrictions
        and delays in receiving notifications for important user actions
        (like cancelling or upgrading of a subscription).

        https://developers.google.com/android-publisher/api-ref/rest/v3/purchases.subscriptions/get?hl=en
        """
        raise NotImplementedError()  # TODO
