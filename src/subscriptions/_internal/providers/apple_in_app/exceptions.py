from subscriptions.v0.exceptions import (
    InvalidOperation,
    PaymentError,
    SubscriptionError,
)
from subscriptions.v0.providers.apple_in_app.enums import AppleValidationStatus


class AppleInvalidOperation(SubscriptionError):
    def __init__(self):
        super().__init__("Apple subscription provider doesn't support this operation.")


class AppleSubscriptionNotCompletedError(SubscriptionError):
    def __init__(self, transaction_id: str):
        super().__init__(
            f"Apple subscription for transaction ID {transaction_id} found to be not in a COMPLETED state."
        )


class AppleReceiptValidationError(PaymentError):
    def __init__(
        self,
        server_response_code: AppleValidationStatus,
        received_bundle_id: str | None,
        expected_bundle_id: str,
    ):
        super().__init__(
            "Apple receipt validation failed",
            user_message="Provided transaction receipt was either malformed or invalid",
            debug_info={
                "server_response_code": server_response_code,
                "received_bundle_id": received_bundle_id,
                "expected_bundle_id": expected_bundle_id,
            },
        )


class InvalidAppleReceiptError(InvalidOperation):
    pass


class AppleAppStoreError(Exception):
    pass


class ConfigurationError(AppleAppStoreError):
    pass


class PayloadValidationError(AppleAppStoreError):
    pass
