import datetime
from typing import Type
from unittest import mock

import pytest
from more_itertools import one

from subscriptions.models import SubscriptionPayment
from subscriptions.providers.apple_in_app import (
    AppStoreNotification,
    AppleAppStoreNotification,
    AppleReceiptValidationError,
    AppleVerifyReceiptResponse,
)
from subscriptions.providers.apple_in_app.api import AppleReceiptRequest
from subscriptions.providers.apple_in_app.app_store import (
    AppStoreNotificationTypeV2,
    AppStoreNotificationTypeV2Subtype,
    AppStoreTransactionInfo,
)
from subscriptions.providers.apple_in_app.enums import (
    AppleEnvironment,
    AppleValidationStatus,
)

APPLE_API_WEBHOOK = '/api/webhook/apple_in_app/'
RECEIPT_FETCH_FUNCTION = 'subscriptions.providers.apple_in_app.api.AppleAppStoreAPI._fetch_receipt_from_endpoint'
NOTIFICATION_PARSER = 'subscriptions.providers.apple_in_app.AppStoreNotification.from_signed_payload'
TRANSACTION_INFO = 'subscriptions.providers.apple_in_app.AppStoreNotification.transaction_info'


@pytest.fixture
def apple_product_id() -> str:
    return 'test-product-id'


@pytest.fixture
def apple_bundle_id() -> str:
    return 'test-bundle-id'


@pytest.fixture(autouse=True)
def apple_plan(apple_in_app, plan, apple_product_id):
    plan.metadata[apple_in_app.codename] = apple_product_id
    plan.save()


@pytest.fixture(autouse=True)
def apple_bundle_id_settings(settings, apple_bundle_id):
    settings.APPLE_BUNDLE_ID = apple_bundle_id


def make_receipt_data(product_id: str,
                      bundle_id: str,
                      is_valid: bool = True,
                      transaction_id='test-transaction-id',
                      original_transaction_id='test-original-transaction-id') -> AppleVerifyReceiptResponse:
    return AppleVerifyReceiptResponse.parse_obj(
        {
            'environment': 'Production',
            'is-retryable': False,
            'status': AppleValidationStatus.OK.value if is_valid else AppleValidationStatus.INTERNAL_SERVICE_ERROR.value,
            'receipt': {
                'application_version': 'test-version',
                'bundle_id': bundle_id,
                'in_app': [
                    {
                        'purchase_date_ms': datetime.datetime(2022, 3, 15).timestamp(),
                        'expires_date_ms': datetime.datetime(2022, 4, 15).timestamp(),
                        'product_id': product_id,
                        'quantity': 1,
                        'original_transaction_id': original_transaction_id,
                        'transaction_id': transaction_id,
                        'web_order_line_item_id': transaction_id,
                    }
                ]
            }
        }
    )


def make_receipt_query() -> dict:
    return AppleReceiptRequest(transaction_receipt='test-receipt-string').dict()


@pytest.fixture
def patched_notification_class():
    notification_class = AppStoreNotification
    with mock.patch.object(notification_class, 'transaction_info', new=mock.MagicMock()):
        yield notification_class


def make_notification_data(patched_notification_class: Type[AppStoreNotification],
                           bundle_id: str,
                           product_id: str,
                           is_renew: bool = True) -> AppStoreNotification:
    result = patched_notification_class.parse_obj(
        {
            'notification': AppStoreNotificationTypeV2.DID_RENEW.value if is_renew else AppStoreNotificationTypeV2.TEST.value,
            'subtype': AppStoreNotificationTypeV2Subtype.ACCEPTED.value,
            'notificationUUID': '00000000-0000-0000-0000-000000000000',
            'data': {
                'appAppleId': 12345,
                'bundleId': bundle_id,
                'bundleVersion': 'test-bundle-version',
                'environment': AppleEnvironment.PRODUCTION.value,
                'signedTransactionInfo': 'fake-transaction-info',
            },
        }
    )
    transaction_info = AppStoreTransactionInfo.parse_obj(
        {
            'appAccountToken': 'test-app-account-token',
            'bundleId': bundle_id,
            'purchaseDate': datetime.datetime(2022, 4, 15).timestamp(),
            'expiresDate': datetime.datetime(2022, 5, 15).timestamp(),
            'productId': product_id,
            'transactionId': 'test-transaction-id',
            'originalTransactionId': 'test-original-transaction-id',
        }
    )
    result.transaction_info.return_value = transaction_info
    return result


def make_notification_query() -> dict:
    return AppleAppStoreNotification(signedPayload='test-signed-payload').dict()


def test__valid_receipt_sent(user_client, apple_in_app, apple_product_id, apple_bundle_id, user):
    receipt_data = make_receipt_data(apple_product_id, apple_bundle_id)
    with mock.patch(RECEIPT_FETCH_FUNCTION) as mock_receipt:
        mock_receipt.return_value = receipt_data
        response = user_client.post(APPLE_API_WEBHOOK, make_receipt_query(), content_type='application/json')

    assert response.status_code == 200

    payment = one(SubscriptionPayment.objects.all())
    single_in_app = one(receipt_data.receipt.in_apps)
    assert payment.user == user
    assert payment.plan.metadata[apple_in_app.codename] == single_in_app.product_id
    assert payment.status == SubscriptionPayment.Status.COMPLETED
    assert payment.provider_codename == apple_in_app.codename
    assert payment.provider_transaction_id == single_in_app.transaction_id
    assert payment.subscription_start == single_in_app.purchase_date
    assert payment.subscription_end == single_in_app.expires_date


def test__invalid_receipt_sent(user_client, apple_in_app, apple_product_id, apple_bundle_id):
    receipt_data = make_receipt_data(apple_product_id, apple_bundle_id, is_valid=False)
    with mock.patch(RECEIPT_FETCH_FUNCTION) as mock_receipt:
        mock_receipt.return_value = receipt_data
        with pytest.raises(AppleReceiptValidationError):
            user_client.post(APPLE_API_WEBHOOK, make_receipt_query(), content_type='application/json')

    assert len(SubscriptionPayment.objects.all()) == 0


def test__invalid_bundle_id_in_the_receipt(user_client, apple_in_app, apple_product_id, apple_bundle_id):
    receipt_data = make_receipt_data(apple_product_id, apple_bundle_id + 'x')
    with mock.patch(RECEIPT_FETCH_FUNCTION) as mock_receipt:
        mock_receipt.return_value = receipt_data
        with pytest.raises(AppleReceiptValidationError):
            user_client.post(APPLE_API_WEBHOOK, make_receipt_query(), content_type='application/json')

    assert len(SubscriptionPayment.objects.all()) == 0


def test__app_store_notification__renew__product_id_changed(user_client,
                                                            patched_notification_class,
                                                            apple_bundle_id,
                                                            apple_product_id):
    # Create the subscription via API.
    receipt_data = make_receipt_data(apple_product_id, apple_bundle_id)
    with mock.patch(RECEIPT_FETCH_FUNCTION) as mock_receipt:
        mock_receipt.return_value = receipt_data
        response = user_client.post(APPLE_API_WEBHOOK, make_receipt_query(), content_type='application/json')
        assert response.status_code == 200

    # Provide a notification with a different product id.
    notification_data = make_notification_data(patched_notification_class, apple_bundle_id, apple_product_id + 'x')
    with mock.patch(NOTIFICATION_PARSER) as mock_parser:
        mock_parser.return_value = notification_data
        user_client.post(APPLE_API_WEBHOOK, make_notification_query(), content_type='application/json')


def test__app_store_notifications__renew__subscription_extended(user_client,
                                                                patched_notification_class,
                                                                apple_bundle_id,
                                                                apple_product_id,
                                                                user,
                                                                apple_in_app):
    # Create the subscription via API.
    receipt_data = make_receipt_data(apple_product_id, apple_bundle_id)
    with mock.patch(RECEIPT_FETCH_FUNCTION) as mock_receipt:
        mock_receipt.return_value = receipt_data
        response = user_client.post(APPLE_API_WEBHOOK, make_receipt_query(), content_type='application/json')
        assert response.status_code == 200

    # Receive a notification about subscription extension.
    notification_data = make_notification_data(patched_notification_class, apple_bundle_id, apple_product_id)
    with mock.patch(NOTIFICATION_PARSER) as mock_parser:
        mock_parser.return_value = notification_data
        response = user_client.post(APPLE_API_WEBHOOK, make_notification_query(), content_type='application/json')
        assert response.status_code == 200

    all_subscriptions = list(SubscriptionPayment.objects.all())
    assert len(all_subscriptions) == 2
    payment = list(SubscriptionPayment.objects.all())[1]
    transaction_info = notification_data.transaction_info
    assert payment.user == user
    assert payment.plan.metadata[apple_in_app.codename] == transaction_info.product_id
    assert payment.status == SubscriptionPayment.Status.COMPLETED
    assert payment.provider_codename == apple_in_app.codename
    assert payment.provider_transaction_id == transaction_info.transaction_id
    assert payment.subscription_start == transaction_info.purchase_date
    assert payment.subscription_end == transaction_info.expires_date


def test__app_store_notification__invalid_signature(client):
    pass


def test__app_store_notification__not_renew_operation_skipped(user_client, patched_notification_class):
    # Provide a notification with a different product id.
    notification_data = \
        make_notification_data(patched_notification_class, 'test-bundle', 'test-product', is_renew=False)
    with mock.patch(NOTIFICATION_PARSER) as mock_parser:
        mock_parser.return_value = notification_data
        response = user_client.post(APPLE_API_WEBHOOK, make_notification_query(), content_type='application/json')

    assert response == 200
    assert len(SubscriptionPayment.objects.all()) == 0
