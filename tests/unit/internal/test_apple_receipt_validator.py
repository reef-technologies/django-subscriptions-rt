import json
import unittest.mock
from datetime import datetime

import pytest
import requests

from subscriptions.v0.providers.apple_in_app.api import (
    AppleAppStoreAPI,
    AppleVerifyReceiptResponse,
)
from subscriptions.v0.providers.apple_in_app.enums import (
    AppleEnvironment,
    AppleValidationStatus,
)


def make_ms_date_str(entry: datetime) -> str:
    return str(datetime.timestamp(entry) * 1000)


def make_json_response_with_status(status: AppleValidationStatus, retryable: bool = False) -> str:
    response = {
        "environment": AppleEnvironment.SANDBOX.value,
        "is-retryable": retryable,
        "status": status.value,
        "receipt": {
            "application_version": "test1",
            "bundle_id": "com.test.test1",
            "in_app": [
                {
                    "purchase_date_ms": make_ms_date_str(datetime(2022, 1, 1, 4, 30)),
                    "expires_date_ms": make_ms_date_str(datetime(2022, 2, 1, 4, 30)),
                    "product_id": "test-product",
                    "quantity": "1",
                    "transaction_id": "test-transaction-id",
                    "original_transaction_id": "test-original-transaction-id",
                    "web_order_line_item_id": "test-item-id",
                }
            ],
        },
    }
    return json.dumps(response)


def make_mock_response(code: int, data_json: str) -> unittest.mock.MagicMock:
    result = unittest.mock.MagicMock()
    result.raise_for_status = unittest.mock.MagicMock()
    if code != 200:
        result.raise_for_status = unittest.mock.MagicMock(side_effect=requests.HTTPError(code))
    result.json = unittest.mock.MagicMock(return_value=json.loads(data_json))
    return result


def make_api_call(
    service_responses: list[tuple[int, str]],
) -> tuple[AppleVerifyReceiptResponse, list[unittest.mock.call]]:
    api = AppleAppStoreAPI("shared-secret")

    responses = [make_mock_response(code, data) for code, data in service_responses]

    fake_session = unittest.mock.MagicMock()
    fake_session.post = unittest.mock.MagicMock(side_effect=responses)
    api._session = fake_session

    result = api.fetch_receipt_data("receipt-data")

    return result, fake_session.post.call_args_list


@pytest.mark.django_db(databases=["actual_db"])
def test__apple__proper_receipt():
    responses = [
        (200, make_json_response_with_status(AppleValidationStatus.OK)),
    ]

    responses, call_list = make_api_call(responses)
    assert responses.status == AppleValidationStatus.OK
    assert len(call_list) == 1


@pytest.mark.django_db(databases=["actual_db"])
def test__apple__retry_on_sandbox_when_status_code_tells_you_so():
    responses = [
        (200, make_json_response_with_status(AppleValidationStatus.SANDBOX_RECEIPT_ON_PRODUCTION_ENV)),
        (200, make_json_response_with_status(AppleValidationStatus.OK)),
    ]

    responses, call_list = make_api_call(responses)
    assert responses.status == AppleValidationStatus.OK
    assert len(call_list) == 2
    # Index, args, first argument.
    assert call_list[0][0][0] == "https://buy.itunes.apple.com/verifyReceipt"
    assert call_list[1][0][0] == "https://sandbox.itunes.apple.com/verifyReceipt"


@pytest.mark.django_db(databases=["actual_db"])
def test__apple__retry_when_failed_request_is_retryable():
    responses = [
        (200, make_json_response_with_status(AppleValidationStatus.INTERNAL_SERVICE_ERROR, retryable=True)),
        (200, make_json_response_with_status(AppleValidationStatus.OK)),
    ]
    responses, call_list = make_api_call(responses)
    assert responses.status == AppleValidationStatus.OK
    assert len(call_list) == 2


@pytest.mark.django_db(databases=["actual_db"])
def test__apple__dont_retry_when_failed_request_is_not_retryable():
    responses = [
        (200, make_json_response_with_status(AppleValidationStatus.INTERNAL_SERVICE_ERROR, retryable=False)),
    ]
    responses, call_list = make_api_call(responses)
    assert responses.status == AppleValidationStatus.INTERNAL_SERVICE_ERROR
    assert len(call_list) == 1


@pytest.mark.django_db(databases=["actual_db"])
def test__apple__retry_in_case_of_service_error():
    responses = [
        (400, f'{{"status": {AppleValidationStatus.SANDBOX_RECEIPT_ON_PRODUCTION_ENV.value}}}'),
        (200, make_json_response_with_status(AppleValidationStatus.OK)),
    ]
    responses, call_list = make_api_call(responses)
    assert responses.status == AppleValidationStatus.OK
    assert len(call_list) == 2
