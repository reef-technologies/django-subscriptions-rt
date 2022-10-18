import datetime
from typing import ClassVar

import requests
import tenacity
from pydantic import (
    BaseModel,
    Field,
    ValidationError,
)

from .enums import (
    AppleEnvironment,
    AppleValidationStatus,
)
from .exceptions import InvalidAppleReceiptError


class AppleInApp(BaseModel):
    # Several fields were omitted. For a full list go to
    # https://developer.apple.com/documentation/appstorereceipts/responsebody/receipt/in_app

    # From documentation: For auto-renewable subscriptions, the time the App Store charged the user’s account
    # for a subscription purchase or renewal after a lapse (...otherwise) the time the App Store charged
    # the user's account for a purchased or restored product.
    purchase_date: datetime.datetime = Field(alias='purchase_date_ms')
    # From documentation: The time a subscription expires or when it will renew.
    expires_date: datetime.datetime = Field(alias='expires_date_ms')

    product_id: str
    quantity: int

    original_transaction_id: str
    transaction_id: str

    # From documentation:
    # A unique identifier for purchase events across devices, including subscription-renewal events.
    # This value is the primary key for identifying subscription purchases.
    web_order_line_item_id: str


class AppleReceipt(BaseModel):
    # Several fields were omitted. For a full list go to
    # https://developer.apple.com/documentation/appstorereceipts/responsebody/receipt
    application_version: str
    bundle_id: str

    in_apps: list[AppleInApp] = Field(alias='in_app')


class AppleVerifyReceiptResponse(BaseModel):
    # Several fields were omitted. For a full list go to
    # https://developer.apple.com/documentation/appstorereceipts/responsebody

    FINISHED_STATES: ClassVar[set[AppleValidationStatus]] = {
        AppleValidationStatus.OK,
        # Receiving sandbox receipt is handled by changing the URL that we target.
        AppleValidationStatus.SANDBOX_RECEIPT_ON_PRODUCTION_ENV,
    }

    # The environment for which the receipt was generated.
    environment: AppleEnvironment

    receipt: AppleReceipt

    is_retryable: bool = Field(alias='is-retryable')
    status: AppleValidationStatus

    @property
    def is_valid(self) -> bool:
        return self.status == AppleValidationStatus.OK

    @property
    def should_be_retried(self) -> bool:
        is_finished = self.status in self.FINISHED_STATES
        return not is_finished and self.is_retryable


RETRY_RULES_FOR_VERIFICATION_RESPONSE = tenacity.retry(
    # Retry if the response tells us it's ok to retry or
    # retry if we received any kind of error, documentation says that 200 is the only correct response.
    retry=tenacity.retry_if_result(lambda verification_response: verification_response.should_be_retried)
          | tenacity.retry_if_exception_type(requests.HTTPError),
    stop=tenacity.stop_after_attempt(10),
    wait=tenacity.wait_exponential(),
)


class AppleAppStoreAPI:
    PRODUCTION_ENDPOINT: ClassVar[str] = 'https://buy.itunes.apple.com/verifyReceipt'
    SANDBOX_ENDPOINT: ClassVar[str] = 'https://sandbox.itunes.apple.com/verifyReceipt'
    TIMEOUT_S: ClassVar[float] = 30.0

    def __init__(self, apple_shared_secret: str):
        self._session = requests.Session()
        self._shared_secret = apple_shared_secret

    def fetch_receipt_data(self, receipt_data: str) -> AppleVerifyReceiptResponse:
        # https://developer.apple.com/documentation/appstorereceipts/verifyreceipt
        # "As a best practice, always call the production URL for verifyReceipt first, and proceed
        # to verify with the sandbox URL if you receive a 21007 status code."
        response = self._fetch_receipt_from_endpoint(self.PRODUCTION_ENDPOINT, receipt_data)

        if response.status == AppleValidationStatus.SANDBOX_RECEIPT_ON_PRODUCTION_ENV:
            response = self._fetch_receipt_from_endpoint(self.SANDBOX_ENDPOINT, receipt_data)

        return response

    @RETRY_RULES_FOR_VERIFICATION_RESPONSE
    def _fetch_receipt_from_endpoint(self, endpoint: str, receipt_data: str) -> AppleVerifyReceiptResponse:
        # Omitting parameter 'exclude-old-transactions' as it's only for recurring subscriptions.
        # https://developer.apple.com/documentation/appstorereceipts/requestbody
        payload = {
            'receipt-data': receipt_data,
            'password': self._shared_secret,
        }
        response = self._session.post(endpoint, json=payload, timeout=self.TIMEOUT_S)
        response.raise_for_status()

        json_data = response.json()
        try:
            return AppleVerifyReceiptResponse.parse_obj(json_data)
        except ValidationError as validation_error:
            raise InvalidAppleReceiptError() from validation_error


class AppleReceiptRequest(BaseModel):
    transaction_receipt: str
