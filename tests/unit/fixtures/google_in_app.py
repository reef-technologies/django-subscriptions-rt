import json
from base64 import b64encode
from collections.abc import Callable

import pytest
from django.utils.timezone import now

from subscriptions.v0.models import Plan, SubscriptionPayment
from subscriptions.v0.providers import get_provider_by_codename
from subscriptions.v0.providers.google_in_app import GoogleInAppProvider
from subscriptions.v0.providers.google_in_app.schemas import (
    GoogleAcknowledgementState,
    GoogleAutoRenewingPlan,
    GoogleOfferDetails,
    GoogleSubscription,
    GoogleSubscriptionNotificationType,
    GoogleSubscriptionPurchaseLineItem,
    GoogleSubscriptionPurchaseV2,
    GoogleSubscriptionState,
)

from ..helpers import days


@pytest.fixture
def google_in_app(settings) -> GoogleInAppProvider:
    settings.SUBSCRIPTIONS_PAYMENT_PROVIDERS = [
        "subscriptions.v0.providers.google_in_app.GoogleInAppProvider",
    ]
    return get_provider_by_codename("google")


@pytest.fixture
def google_plan_id() -> str:
    return "some-crazy-name"


@pytest.fixture
def google_subscription_purchase(google_plan_id) -> GoogleSubscriptionPurchaseV2:
    now_ = now()
    return GoogleSubscriptionPurchaseV2(
        lineItems=[
            GoogleSubscriptionPurchaseLineItem(
                productId=google_plan_id,
                expiryTime=(now_ + days(5)).isoformat().replace("+00:00", "Z"),
                autoRenewingPlan=GoogleAutoRenewingPlan(autoRenewEnabled=True),
                offerDetails=GoogleOfferDetails(
                    basePlanId=google_plan_id,
                ),
            )
        ],
        startTime=now_.isoformat().replace("+00:00", "Z"),
        subscriptionState=GoogleSubscriptionState.ACTIVE,
        linkedPurchaseToken=None,
        acknowledgementState=GoogleAcknowledgementState.ACKNOWLEDGED,
    )


@pytest.fixture
def google_subscription(settings, google_plan_id) -> GoogleSubscription:
    return GoogleSubscription(
        packageName=settings.GOOGLE_PLAY_PACKAGE_NAME,
        productId=google_plan_id,
        basePlans=[],
    )


@pytest.fixture
def plan_with_google(google_in_app, plan, google_subscription) -> Plan:
    plan.metadata[google_in_app.codename] = google_subscription.dict()
    plan.save()
    return plan


@pytest.fixture
def google_rtdn_notification_factory(settings, google_in_app, purchase_token, google_plan_id) -> Callable:
    def build_google_rtdn_notification(type_: GoogleSubscriptionNotificationType):
        return {
            "message": {
                "messageId": "136969346945",
                "publishTime": "2022-10-25T13:15:00.858Z",
                "data": b64encode(
                    json.dumps(
                        {
                            "version": "1.0",
                            "packageName": settings.GOOGLE_PLAY_PACKAGE_NAME,
                            "eventTimeMillis": 100,
                            "subscriptionNotification": {
                                "version": "1.0",
                                "notificationType": type_,
                                "purchaseToken": purchase_token,
                                "subscriptionId": google_plan_id,
                            },
                        }
                    ).encode("utf8")
                ).decode("utf8"),
            },
            "subscription": "projects/myproject/subscriptions/mysubscription",
        }

    return build_google_rtdn_notification


@pytest.fixture
def google_rtdn_voided_purchase_notification(settings, purchase_token):
    return {
        "message": {
            "messageId": "136969346945",
            "publishTime": "2022-10-25T13:15:00.858Z",
            "data": b64encode(
                json.dumps(
                    {
                        "version": "1.0",
                        "packageName": settings.GOOGLE_PLAY_PACKAGE_NAME,
                        "eventTimeMillis": 100,
                        "voidedPurchaseNotification": {
                            "purchaseToken": purchase_token,
                            "orderId": "GS.0000-0000-0000",
                            "productType": 1,
                        },
                    }
                ).encode("utf8")
            ).decode("utf8"),
        },
        "subscription": "projects/myproject/subscriptions/mysubscription",
    }


@pytest.fixture
def google_rtdn_notification(google_rtdn_notification_factory) -> dict:
    return google_rtdn_notification_factory(GoogleSubscriptionNotificationType.PURCHASED)


@pytest.fixture
def google_test_notification() -> dict:
    return {
        "message": {
            "data": (
                "eyJ2ZXJzaW9uIjoiMS4wIiwicGFja2FnZU5hb"
                "WUiOiJjb20ucHJvbWV0aGV1c3Bva2VyLmJhdH"
                "RsZSIsImV2ZW50VGltZU1pbGxpcyI6IjE2NjY"
                "3MDM3MDA0MTMiLCJ0ZXN0Tm90aWZpY2F0aW9u"
                "Ijp7InZlcnNpb24iOiIxLjAifX0="
            ),
            "message_id": "6079043127548205",
            "messageId": "6079043127548205",
            "publish_time": "2022-10-25T13:15:00.858Z",
            "publishTime": "2022-10-25T13:15:00.858Z",
        },
        "subscription": "projects/pc-api-8164987956662966187-422/subscriptions/subscription-notifications",
    }


@pytest.fixture
def google_in_app_payment(google_in_app, purchase_token, plan_with_google, user) -> SubscriptionPayment:
    now_ = now()
    return SubscriptionPayment.objects.create(
        provider_codename=google_in_app.codename,
        provider_transaction_id=purchase_token,
        status=SubscriptionPayment.Status.COMPLETED,
        user=user,
        plan=plan_with_google,
        paid_since=now_,
        paid_until=now_ + days(7),
        created=now_,
    )


@pytest.fixture
def google_in_app__subscription_purchase_dict(google_in_app) -> dict:
    return {
        "kind": "androidpublisher#subscriptionPurchaseV2",
        "startTime": "2022-11-21T01:49:49.375Z",
        "regionCode": "CA",
        "subscriptionState": "SUBSCRIPTION_STATE_EXPIRED",
        "latestOrderId": "GPA.3315-1326-8982-37036..0",
        "canceledStateContext": {"systemInitiatedCancellation": {}},
        "testPurchase": {},
        "acknowledgementState": "ACKNOWLEDGEMENT_STATE_PENDING",
        "lineItems": [
            {
                "productId": "prometheus_pro",
                "expiryTime": "2022-11-21T01:54:51.937Z",
                "autoRenewingPlan": {},
                "offerDetails": {"basePlanId": "prometheus-pro"},
            }
        ],
    }


@pytest.fixture
def purchase_token() -> str:
    return "12345"


@pytest.fixture
def app_notification(purchase_token) -> dict:
    return {
        "purchase_token": purchase_token,
    }
