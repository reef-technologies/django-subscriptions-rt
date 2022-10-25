import datetime
from unittest import mock

import pytest
from more_itertools import one

from subscriptions.models import (
    Subscription,
    SubscriptionPayment,
)
from subscriptions.providers.apple_in_app import (
    AppleReceiptValidationError,
    AppleVerifyReceiptResponse,
)
from subscriptions.providers.apple_in_app.api import AppleReceiptRequest
from subscriptions.providers.apple_in_app.enums import AppleValidationStatus


RECEIPT_FETCH_FUNCTION = 'subscriptions.providers.apple_in_app.api.AppleAppStoreAPI._fetch_receipt_from_endpoint'


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


@pytest.fixture(scope='function', autouse=True)
def cleanup_subscriptions():
    yield
    SubscriptionPayment.objects.all().delete()
    Subscription.objects.all().delete()


def make_receipt_data(product_id: str,
                      bundle_id: str,
                      is_valid: bool = True,
                      transaction_id='test-transaction-id',
                      original_transaction_id='test-original-transaction-id'):
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


def test__valid_receipt_sent(user_client, apple_in_app, apple_product_id, apple_bundle_id, user):
    receipt_data = make_receipt_data(apple_product_id, apple_bundle_id)
    with mock.patch(RECEIPT_FETCH_FUNCTION) as mock_receipt:
        mock_receipt.return_value = receipt_data
        response = user_client.post('/api/webhook/apple_in_app/', make_receipt_query(), content_type='application/json')

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
            user_client.post('/api/webhook/apple_in_app/', make_receipt_query(), content_type='application/json')


def test__invalid_bundle_id_in_the_receipt(user_client, apple_in_app, apple_product_id, apple_bundle_id):
    receipt_data = make_receipt_data(apple_product_id, apple_bundle_id + 'x')
    with mock.patch(RECEIPT_FETCH_FUNCTION) as mock_receipt:
        mock_receipt.return_value = receipt_data
        with pytest.raises(AppleReceiptValidationError):
            user_client.post('/api/webhook/apple_in_app/', make_receipt_query(), content_type='application/json')


def test__app_store_notification__renew__product_id_changed(client):
    pass


def test__app_store_notifications__renew__subscription_extended(client):
    pass


def test__app_store_notification__invalid_signature(client):
    pass


def test__app_store_notification__not_renew_operation_skipped(client):
    pass
