import dataclasses
import datetime
import enum
from typing import ClassVar

import requests
import tenacity


def datetime_from_ms_timestamp(ms_timestamp: str) -> datetime.datetime:
    seconds_timestamp = float(ms_timestamp) / 1000.0
    return datetime.datetime.fromtimestamp(seconds_timestamp, tz=datetime.timezone.utc)


@enum.unique
class AppleEnvironment(str, enum.Enum):
    SANDBOX = 'Sandbox'
    PRODUCTION = 'Production'


@enum.unique
class AppleValidationStatus(int, enum.Enum):
    OK = 0
    NOT_A_POST = 21000
    __NO_LONGER_SENT = 21001
    MALFORMED_DATA_OR_SERVICE_ISSUE = 21002
    RECEIPT_AUTHENTICATION_FAILED = 21003
    INVALID_SHARED_SECRET = 21004
    SERVICE_UNAVAILABLE = 21005
    # Only returned for iOS 6-style transaction receipts for auto-renewable subscriptions.
    SUBSCRIPTION_EXPIRED = 21006
    SANDBOX_RECEIPT_ON_PRODUCTION_ENV = 21007
    PRODUCTION_RECEIPT_ON_SANDBOX_ENV = 21008
    INTERNAL_SERVICE_ERROR = 21009
    USER_ACCOUNT_DOESNT_EXIST = 21010


@dataclasses.dataclass
class AppleInApp:
    # Several fields were omitted. For a full list go to
    # https://developer.apple.com/documentation/appstorereceipts/responsebody/receipt/in_app

    # From documentation: For auto-renewable subscriptions, the time the App Store charged the user’s account
    # for a subscription purchase or renewal after a lapse (...otherwise) the time the App Store charged
    # the user's account for a purchased or restored product.
    purchase_date: datetime.datetime
    # From documentation: The time a subscription expires or when it will renew.
    expires_date: datetime.datetime

    product_id: str
    quantity: int

    transaction_id: str

    # From documentation:
    # A unique identifier for purchase events across devices, including subscription-renewal events.
    # This value is the primary key for identifying subscription purchases.
    web_order_line_item_id: str

    @classmethod
    def from_json(cls, json_dict: dict) -> 'AppleInApp':
        return cls(
            purchase_date=datetime_from_ms_timestamp(json_dict['purchase_date_ms']),
            expires_date=datetime_from_ms_timestamp(json_dict['expires_date_ms']),

            product_id=json_dict['product_id'],
            quantity=int(json_dict['quantity']),

            transaction_id=json_dict['transaction_id'],
            web_order_line_item_id=json_dict['web_order_line_item_id'],
        )

    @classmethod
    def from_json_list(cls, json_list: list[dict]) -> list['AppleInApp']:
        return [
            cls.from_json(entry)
            for entry in json_list
        ]


@dataclasses.dataclass
class AppleReceipt:
    # Several fields were omitted. For a full list go to
    # https://developer.apple.com/documentation/appstorereceipts/responsebody/receipt
    application_version: str
    bundle_id: str

    in_apps: list[AppleInApp]

    @classmethod
    def from_json(cls, json_dict: dict) -> 'AppleReceipt':
        return cls(
            application_version=json_dict['application_version'],
            bundle_id=json_dict['bundle_id'],
            in_apps=AppleInApp.from_json_list(json_dict['in_app']),
        )


@dataclasses.dataclass
class AppleVerifyReceiptResponse:
    # Several fields were omitted. For a full list go to
    # https://developer.apple.com/documentation/appstorereceipts/responsebody

    FINISHED_STATES: ClassVar[set[AppleValidationStatus]] = {
        AppleValidationStatus.OK,
        # Receiving sandbox receipt is handled by changing the URL that we target.
        AppleValidationStatus.SANDBOX_RECEIPT_ON_PRODUCTION_ENV,
    }

    # The environment for which the receipt was generated.
    environment: AppleEnvironment

    is_retryable: bool
    status: AppleValidationStatus

    receipt: AppleReceipt

    @property
    def is_valid(self) -> bool:
        return self.status == AppleValidationStatus.OK

    @property
    def should_be_retried(self) -> bool:
        is_finished = self.status in self.FINISHED_STATES
        return not is_finished and self.is_retryable

    @classmethod
    def from_json(cls, json_dict: dict) -> 'AppleVerifyReceiptResponse':
        receipt = AppleReceipt.from_json(json_dict['receipt'])

        return cls(
            environment=AppleEnvironment(json_dict['environment']),

            is_retryable=json_dict['is-retryable'],
            status=AppleValidationStatus(json_dict['status']),

            receipt=receipt,
        )


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
        return AppleVerifyReceiptResponse.from_json(json_data)
