from __future__ import annotations

import datetime
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest import mock

import pytest
from django.test.client import Client
from more_itertools import one

from subscriptions.v0.models import (
    SubscriptionPayment,
    SubscriptionPaymentRefund,
)
from subscriptions.v0.providers.apple_in_app.exceptions import (
    AppleReceiptValidationError,
)
from subscriptions.v0.providers.apple_in_app.api import (
    AppleVerifyReceiptResponse,
)
from subscriptions.v0.providers.apple_in_app.exceptions import (
    InvalidAppleReceiptError,
)
from subscriptions.v0.providers.apple_in_app.api import AppleReceiptRequest
from subscriptions.v0.providers.apple_in_app.app_store import (
    AppStoreNotification,
    AppStoreNotificationTypeV2,
    AppStoreNotificationTypeV2Subtype,
    AppStoreTransactionInfo,
)
from subscriptions.v0.providers.apple_in_app.enums import (
    AppleEnvironment,
    AppleValidationStatus,
)

APPLE_API_WEBHOOK = "/api/webhook/apple_in_app/"
RECEIPT_FETCH_FUNCTION = "subscriptions.providers.apple_in_app.api.AppleAppStoreAPI._fetch_receipt_from_endpoint"
NOTIFICATION_PARSER = "subscriptions.providers.apple_in_app.AppStoreNotification.from_signed_payload"
TRANSACTION_INFO = "subscriptions.providers.apple_in_app.AppStoreNotification.transaction_info"


@pytest.fixture
def apple_product_id() -> str:
    return "test-product-id"


@pytest.fixture(autouse=True)
def apple_plan(apple_in_app, plan, apple_product_id):
    plan.metadata[apple_in_app.codename] = apple_product_id
    plan.save()


@pytest.fixture
def apple_bigger_product_id() -> str:
    return "test-bigger-product-id"


@pytest.fixture(autouse=True)
def apple_bigger_plan(apple_in_app, bigger_plan, apple_bigger_product_id):
    bigger_plan.metadata[apple_in_app.codename] = apple_bigger_product_id
    bigger_plan.save()


@pytest.fixture(autouse=True)
def apple_bundle_id_settings(settings, apple_bundle_id):
    settings.APPLE_BUNDLE_ID = apple_bundle_id


def make_receipt_data(
    product_id: str,
    bundle_id: str,
    is_valid: bool = True,
    transaction_id="test-transaction-id",
    original_transaction_id="test-original-transaction-id",
    num_latest_duplicates: int = 1,
) -> AppleVerifyReceiptResponse:
    latest_entry = {
        "purchase_date_ms": datetime.datetime(2022, 3, 15).timestamp(),
        "expires_date_ms": datetime.datetime(2022, 4, 15).timestamp(),
        "product_id": product_id,
        "quantity": 1,
        "original_transaction_id": original_transaction_id,
        "transaction_id": transaction_id,
        "web_order_line_item_id": transaction_id,
    }
    latest_entries = [latest_entry for _ in range(num_latest_duplicates)]

    return AppleVerifyReceiptResponse.parse_obj(
        {
            "environment": "Production",
            "is-retryable": False,
            "status": AppleValidationStatus.OK.value
            if is_valid
            else AppleValidationStatus.INTERNAL_SERVICE_ERROR.value,
            "latest_receipt_info": latest_entries,
            "receipt": {
                "application_version": "test-version",
                "bundle_id": bundle_id,
                "in_app": [
                    {
                        "purchase_date_ms": datetime.datetime(2022, 3, 15).timestamp(),
                        "expires_date_ms": datetime.datetime(2022, 4, 15).timestamp(),
                        "product_id": product_id,
                        "quantity": 1,
                        "original_transaction_id": original_transaction_id,
                        "transaction_id": transaction_id,
                        "web_order_line_item_id": transaction_id,
                    }
                ],
            },
        }
    )


def make_receipt_query() -> dict:
    return AppleReceiptRequest(transaction_receipt="test-receipt-string").dict()


@pytest.fixture(autouse=True)
def patched_notification():
    with mock.patch(
        "subscriptions.providers.apple_in_app.AppStoreNotification.transaction_info", new_callable=mock.PropertyMock
    ):
        yield


def make_notification_data(
    product_id: str,
    bundle_id: str,
    notification_type: AppStoreNotificationTypeV2 = AppStoreNotificationTypeV2.DID_RENEW,
    subtype: AppStoreNotificationTypeV2Subtype | None = None,
    transaction_id: str = "test-transaction-id",
    original_transaction_id: str = "test-original-transaction-id",
) -> AppStoreNotification:
    result = AppStoreNotification.parse_obj(
        {
            "notificationType": notification_type.value,
            "subtype": subtype and subtype.value,
            "notificationUUID": "00000000-0000-0000-0000-000000000000",
            "data": {
                "appAppleId": 12345,
                "bundleId": bundle_id,
                "bundleVersion": "test-bundle-version",
                "environment": AppleEnvironment.PRODUCTION.value,
                "signedTransactionInfo": "fake-transaction-info",
            },
        }
    )

    result.transaction_info.app_account_token = "test-app-account-token"
    result.transaction_info.bundle_id = bundle_id
    result.transaction_info.purchase_date = datetime.datetime(2022, 4, 15, tzinfo=datetime.UTC)
    result.transaction_info.expires_date = datetime.datetime(2022, 5, 15, tzinfo=datetime.UTC)
    result.transaction_info.product_id = product_id
    result.transaction_info.transaction_id = transaction_id
    result.transaction_info.original_transaction_id = original_transaction_id
    result.transaction_info.revocation_date = datetime.datetime(2022, 3, 30, tzinfo=datetime.UTC)
    result.transaction_info.web_order_line_item_id = transaction_id

    return result


def make_notification_query() -> dict:
    return {"signedPayload": "test-signed-payload"}


@pytest.mark.django_db(databases=["actual_db"])
def test__apple__invalid_query_sent(user_client):
    response = user_client.post(APPLE_API_WEBHOOK, {"test": "data"}, content_type="application/json")
    assert response.status_code == 400


@pytest.mark.django_db(databases=["actual_db"])
def assert__valid_receipt(user_client, apple_in_app, product_id, bundle_id, **receipt_data_kwargs):
    receipt_data = make_receipt_data(product_id, bundle_id, **receipt_data_kwargs)
    with mock.patch(RECEIPT_FETCH_FUNCTION, return_value=receipt_data):
        response = user_client.post(APPLE_API_WEBHOOK, make_receipt_query(), content_type="application/json")

    assert response.status_code == 200

    payment = one(SubscriptionPayment.objects.all())
    single_in_app = one(receipt_data.receipt.in_apps)
    assert payment.plan.metadata[apple_in_app.codename] == single_in_app.product_id
    assert payment.status == SubscriptionPayment.Status.COMPLETED
    assert payment.provider_codename == apple_in_app.codename
    assert payment.provider_transaction_id == single_in_app.transaction_id
    assert payment.subscription_start == single_in_app.purchase_date
    assert payment.subscription_end == single_in_app.expires_date


@pytest.mark.django_db(databases=["actual_db"])
def test__apple__valid_receipt_sent(user_client, apple_in_app, apple_product_id, apple_bundle_id, user):
    assert__valid_receipt(user_client, apple_in_app, apple_product_id, apple_bundle_id)


@pytest.mark.django_db(transaction=True, databases=["actual_db"])
def test__apple__multiple_receipts(apple_in_app, apple_product_id, apple_bundle_id, user):
    receipt_data = make_receipt_data(apple_product_id, apple_bundle_id)

    num_threads = 16
    starting_barrier = threading.Barrier(num_threads, timeout=5)

    def runner():
        user_client = Client()
        user_client.force_login(user)
        starting_barrier.wait()
        with mock.patch(RECEIPT_FETCH_FUNCTION, return_value=receipt_data):
            response = user_client.post(APPLE_API_WEBHOOK, make_receipt_query(), content_type="application/json")
        assert response.status_code == 200

    assert SubscriptionPayment.objects.count() == 0

    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(runner) for _ in range(num_threads)]
        for future in as_completed(futures, timeout=3):
            future.result(timeout=1)

    assert SubscriptionPayment.objects.count() == 1, f"{SubscriptionPayment.objects.all()} payments created"
    payment = one(SubscriptionPayment.objects.all())
    single_in_app = one(receipt_data.receipt.in_apps)
    assert payment.status == SubscriptionPayment.Status.COMPLETED
    assert payment.provider_codename == apple_in_app.codename
    assert payment.provider_transaction_id == single_in_app.transaction_id
    assert payment.subscription_start == single_in_app.purchase_date
    assert payment.subscription_end == single_in_app.expires_date


@pytest.mark.django_db(databases=["actual_db"])
def test__apple__receipt_with_multiple_same_entries(user_client, apple_in_app, apple_product_id, apple_bundle_id):
    assert__valid_receipt(user_client, apple_in_app, apple_product_id, apple_bundle_id, num_latest_duplicates=10)


@pytest.mark.django_db(databases=["actual_db"])
def test__apple__invalid_receipt_sent(user_client, apple_in_app, apple_product_id, apple_bundle_id):
    receipt_data = make_receipt_data(apple_product_id, apple_bundle_id, is_valid=False)
    with mock.patch(RECEIPT_FETCH_FUNCTION, return_value=receipt_data):
        with pytest.raises(AppleReceiptValidationError):
            user_client.post(APPLE_API_WEBHOOK, make_receipt_query(), content_type="application/json")

    assert not SubscriptionPayment.objects.exists()


@pytest.mark.django_db(databases=["actual_db"])
def test__apple__unauthorised_user(client, apple_in_app, apple_product_id, apple_bundle_id):
    receipt_data = make_receipt_data(apple_product_id, apple_bundle_id, is_valid=False)
    with mock.patch(RECEIPT_FETCH_FUNCTION, return_value=receipt_data):
        response = client.post(APPLE_API_WEBHOOK, make_receipt_query(), content_type="application/json")
        assert response.status_code == 401

    assert not SubscriptionPayment.objects.exists()


@pytest.mark.django_db(databases=["actual_db"])
def test__apple__no_latest_receipt_info_passed(user_client, apple_in_app, apple_product_id, apple_bundle_id):
    receipt_data = make_receipt_data(apple_product_id, apple_bundle_id, is_valid=True)
    receipt_data.latest_receipt_info = None
    with mock.patch(RECEIPT_FETCH_FUNCTION, return_value=receipt_data):
        with pytest.raises(InvalidAppleReceiptError):
            user_client.post(APPLE_API_WEBHOOK, make_receipt_query(), content_type="application/json")

    assert not SubscriptionPayment.objects.exists()


@pytest.mark.django_db(databases=["actual_db"])
def test__apple__invalid_bundle_id_in_the_receipt(user_client, apple_in_app, apple_product_id, apple_bundle_id):
    receipt_data = make_receipt_data(apple_product_id, apple_bundle_id + "x")
    with mock.patch(RECEIPT_FETCH_FUNCTION, return_value=receipt_data):
        with pytest.raises(AppleReceiptValidationError):
            user_client.post(APPLE_API_WEBHOOK, make_receipt_query(), content_type="application/json")

    assert not SubscriptionPayment.objects.exists()


@pytest.mark.django_db(databases=["actual_db"])
def test__apple__basic_receipt_with_status_returned(user_client):
    receipt_data = AppleVerifyReceiptResponse.parse_obj(
        {"status": AppleValidationStatus.MALFORMED_DATA_OR_SERVICE_ISSUE.value}
    )
    with mock.patch(RECEIPT_FETCH_FUNCTION, return_value=receipt_data):
        with pytest.raises(AppleReceiptValidationError):
            user_client.post(APPLE_API_WEBHOOK, make_receipt_query(), content_type="application/json")

    assert not SubscriptionPayment.objects.exists()


def assert__notification(user_client, product_id, bundle_id, **notification_kwargs) -> AppStoreTransactionInfo:
    notification_data = make_notification_data(product_id, bundle_id, **notification_kwargs)
    with mock.patch(NOTIFICATION_PARSER, return_value=notification_data):
        response = user_client.post(APPLE_API_WEBHOOK, make_notification_query(), content_type="application/json")
        assert response.status_code == 200
    return notification_data.transaction_info


@pytest.mark.django_db(databases=["actual_db"])
def test__apple__app_store_notification__product_upgrade(
    user_client,
    apple_in_app,
    user,
    apple_bundle_id,
    apple_product_id,
    apple_bigger_product_id,
):
    transaction_id = "special-transaction-id"
    new_transaction_id = "upgrade-transaction-id"

    assert__valid_receipt(
        user_client,
        apple_in_app,
        apple_product_id,
        apple_bundle_id,
        transaction_id=transaction_id,
        original_transaction_id=transaction_id,
    )

    transaction_info = assert__notification(
        user_client,
        apple_bigger_product_id,
        apple_bundle_id,
        notification_type=AppStoreNotificationTypeV2.DID_CHANGE_RENEWAL_PREF,
        subtype=AppStoreNotificationTypeV2Subtype.UPGRADE,
        transaction_id=new_transaction_id,
        original_transaction_id=transaction_id,
    )

    payment = SubscriptionPayment.objects.get(provider_transaction_id=transaction_id)
    assert payment.user == user
    assert payment.plan.metadata[apple_in_app.codename] == apple_product_id
    assert payment.subscription.plan.metadata[apple_in_app.codename] == apple_product_id
    # Original transaction is marked as cancelled, so we know not to care about it any more.
    assert payment.status == SubscriptionPayment.Status.COMPLETED
    # End date is set to "now", so it's not important for checking purposes.

    payment = SubscriptionPayment.objects.get(provider_transaction_id=new_transaction_id)
    assert payment.user == user
    assert payment.plan.metadata[apple_in_app.codename] == apple_bigger_product_id
    assert payment.subscription.plan.metadata[apple_in_app.codename] == apple_bigger_product_id
    assert payment.status == SubscriptionPayment.Status.COMPLETED
    assert payment.subscription_start == transaction_info.purchase_date
    assert payment.subscription_end == transaction_info.expires_date


@pytest.mark.django_db(databases=["actual_db"])
def test__apple__app_store_notification__product_downgrade(
    user_client,
    apple_in_app,
    user,
    apple_bundle_id,
    apple_product_id,
    apple_bigger_product_id,
):
    transaction_id = "special-transaction-id"
    new_transaction_id = "upgrade-transaction-id"

    assert__valid_receipt(
        user_client,
        apple_in_app,
        apple_bigger_product_id,
        apple_bundle_id,
        transaction_id=transaction_id,
        original_transaction_id=transaction_id,
    )

    assert__notification(
        user_client,
        apple_product_id,
        apple_bundle_id,
        notification_type=AppStoreNotificationTypeV2.DID_CHANGE_RENEWAL_PREF,
        subtype=AppStoreNotificationTypeV2Subtype.DOWNGRADE,
        transaction_id=new_transaction_id,
        original_transaction_id=transaction_id,
    )

    # Other object will appear on renew for downgrades.
    assert SubscriptionPayment.objects.count() == 1

    payment = SubscriptionPayment.objects.get(provider_transaction_id=transaction_id)
    assert payment.user == user
    assert payment.plan.metadata[apple_in_app.codename] == apple_bigger_product_id
    assert payment.subscription.plan.metadata[apple_in_app.codename] == apple_bigger_product_id
    assert payment.status == SubscriptionPayment.Status.COMPLETED


def assert__app_store_notifications__renew__subscription_extended(
    user_client,
    apple_bundle_id,
    apple_product_id,
    user,
    apple_in_app,
    in_the_middle_call=None,
):
    transaction_id = "special-transaction-id"
    renewal_transaction_id = "renewal-transaction-id"
    assert__valid_receipt(
        user_client,
        apple_in_app,
        apple_product_id,
        apple_bundle_id,
        transaction_id=transaction_id,
        original_transaction_id=transaction_id,
    )

    if in_the_middle_call is not None:
        in_the_middle_call(renewal_transaction_id, transaction_id)

    transaction_info = assert__notification(
        user_client,
        apple_product_id,
        apple_bundle_id,
        transaction_id=renewal_transaction_id,
        original_transaction_id=transaction_id,
    )

    assert SubscriptionPayment.objects.count() == 2

    payment = SubscriptionPayment.objects.get(provider_transaction_id=renewal_transaction_id)
    assert payment.user == user
    assert payment.plan.metadata[apple_in_app.codename] == transaction_info.product_id
    assert payment.status == SubscriptionPayment.Status.COMPLETED
    assert payment.provider_codename == apple_in_app.codename
    assert payment.subscription_start == transaction_info.purchase_date
    assert payment.subscription_end == transaction_info.expires_date


@pytest.mark.django_db(databases=["actual_db"])
def test__apple__app_store_notifications__renew__subscription_extended(
    user_client,
    apple_bundle_id,
    apple_product_id,
    user,
    apple_in_app,
):
    assert__app_store_notifications__renew__subscription_extended(
        user_client,
        apple_bundle_id,
        apple_product_id,
        user,
        apple_in_app,
    )


@pytest.mark.django_db(databases=["actual_db"])
def test__apple__app_store_notifications__renew__subscription_extended__received_twice(
    user_client,
    apple_bundle_id,
    apple_product_id,
    user,
    apple_in_app,
):
    assert__app_store_notifications__renew__subscription_extended(
        user_client,
        apple_bundle_id,
        apple_product_id,
        user,
        apple_in_app,
        lambda renewal_transaction_id, transaction_id: assert__notification(
            user_client,
            apple_product_id,
            apple_bundle_id,
            transaction_id=renewal_transaction_id,
            original_transaction_id=transaction_id,
        ),
    )


@pytest.mark.skip("Not implemented")
def test__apple__app_store_notification__invalid_signature(client):
    pass


@pytest.mark.django_db(databases=["actual_db"])
def test__apple__app_store_notification__not_renew_operation_skipped(user_client):
    # Provide a notification with a different product id.
    notification_data = make_notification_data(
        "test-product", "test-bundle", notification_type=AppStoreNotificationTypeV2.TEST
    )
    with mock.patch(NOTIFICATION_PARSER, return_value=notification_data):
        response = user_client.post(APPLE_API_WEBHOOK, make_notification_query(), content_type="application/json")

    assert response.status_code == 200, response.content
    assert not SubscriptionPayment.objects.exists()


@pytest.mark.django_db(databases=["actual_db"])
def test__apple__app_store_notifications__refund(
    user_client,
    apple_bundle_id,
    apple_product_id,
    user,
    apple_in_app,
):
    transaction_id = "special-transaction-id"
    assert__valid_receipt(
        user_client,
        apple_in_app,
        apple_product_id,
        apple_bundle_id,
        transaction_id=transaction_id,
        original_transaction_id=transaction_id,
    )

    transaction_info = assert__notification(
        user_client,
        apple_product_id,
        apple_bundle_id,
        notification_type=AppStoreNotificationTypeV2.REFUND,
        transaction_id=transaction_id,
        original_transaction_id=transaction_id,
    )

    assert SubscriptionPayment.objects.count() == 1
    payment = SubscriptionPayment.objects.get(provider_transaction_id=transaction_id)
    assert payment.user == user
    assert payment.plan.metadata[apple_in_app.codename] == transaction_info.product_id
    assert payment.provider_codename == apple_in_app.codename
    assert payment.subscription_end == transaction_info.revocation_date
    assert payment.status == SubscriptionPayment.Status.CANCELLED

    assert SubscriptionPaymentRefund.objects.count() == 1
    refund = SubscriptionPaymentRefund.objects.get(provider_transaction_id=transaction_id)
    assert refund.provider_codename == apple_in_app.codename
    assert refund.original_payment == payment
    assert refund.status == SubscriptionPayment.Status.COMPLETED


@pytest.mark.django_db(databases=["actual_db"])
def test__apple__notification_without_receipt(user_client, apple_bundle_id, apple_product_id):
    # In this case nothing should break, it means that we were not informed about an operation.
    notification_data = make_notification_data(
        apple_product_id,
        apple_bundle_id,
        notification_type=AppStoreNotificationTypeV2.DID_CHANGE_RENEWAL_PREF,
        subtype=AppStoreNotificationTypeV2Subtype.UPGRADE,
    )
    with mock.patch(NOTIFICATION_PARSER, return_value=notification_data):
        with pytest.raises(AssertionError):
            user_client.post(APPLE_API_WEBHOOK, make_notification_query(), content_type="application/json")
