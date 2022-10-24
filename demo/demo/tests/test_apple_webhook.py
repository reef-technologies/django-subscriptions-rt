import datetime
from unittest import mock

import pytest
from django.conf import settings

from subscriptions.providers.apple_in_app import AppleVerifyReceiptResponse
from subscriptions.providers.apple_in_app.api import (
    AppleInApp,
    AppleReceipt,
    AppleReceiptRequest,
)
from subscriptions.providers.apple_in_app.enums import (
    AppleEnvironment,
    AppleValidationStatus,
)


@pytest.fixture
def apple_product_id() -> str:
    return 'test-product-id'


@pytest.fixture(autouse=True)
def apple_plan(apple_in_app, plan, apple_product_id):
    plan.metadata[apple_in_app.codename] = apple_product_id


def make_receipt_data(product_id: str,
                      is_valid: bool = True,
                      bundle_id: str = settings.APPLE_BUNDLE_ID,
                      transaction_id='test-transaction-id',
                      original_transaction_id='test-original-transaction-id'):
    return AppleVerifyReceiptResponse(
        environment=AppleEnvironment.PRODUCTION,
        is_retryable=False,
        status=AppleValidationStatus.OK if is_valid else AppleValidationStatus.INTERNAL_SERVICE_ERROR,

        receipt=AppleReceipt(
            application_version='test-version',
            bundle_id=bundle_id,
            in_apps=[
                AppleInApp(
                    purchase_date_ms=datetime.datetime(2022, 3, 15),
                    expires_date_ms=datetime.datetime(2022, 4, 15),
                    product_id='test-product-1',
                    quantity=1,
                    original_transaction_id=original_transaction_id,
                    transaction_id=transaction_id,
                    web_order_line_item_id=transaction_id,
                )
            ]
        ),
    )


def make_receipt_query() -> dict:
    return AppleReceiptRequest(transaction_receipt='test-receipt-string').dict()


def test__valid_receipt_sent(client, apple_in_app, apple_product_id):
    apple_in_app.api.fetch_receipt_data = \
        mock.MagicMock(return_value=make_receipt_data(apple_product_id, is_valid=True))
    response = client.post('/api/webhook/apple_in_app/', make_receipt_query(), content_type='application/json')
    assert response.status_code == 200


def test__invalid_receipt_sent(client, apple_in_app):
    pass


def test__invalid_bundle_id_in_the_receipt(client, apple_in_app):
    pass


def test__app_store_notification__renew__product_id_changed(client):
    pass


def test__app_store_notifications__renew__subscription_extended(client):
    pass


def test__app_store_notification__invalid_signature(client):
    pass


def test__app_store_notification__not_renew_operation_skipped(client):
    pass
