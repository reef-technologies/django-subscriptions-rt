from subscriptions.exceptions import (
    InvalidOperation,
    PaymentError,
    SubscriptionError,
)


class AppleInvalidOperation(SubscriptionError):
    def __init__(self):
        super().__init__('Apple subscription provider doesn\'t support this operation.')


class AppleSubscriptionNotCompletedError(SubscriptionError):
    def __init__(self, transaction_id: str):
        super().__init__(f'Apple subscription for transaction ID {transaction_id} '
                         f'found to be not in a COMPLETED state.')


class AppleReceiptValidationError(PaymentError):
    def __init__(self):
        super().__init__(
            'Apple receipt validation failed',
            user_message='Provided transaction receipt was either malformed or invalid',
        )


class InvalidAppleReceiptError(InvalidOperation):
    pass


class AppleAppStoreError(Exception):
    pass


class ConfigurationError(AppleAppStoreError):
    pass


class PayloadValidationError(AppleAppStoreError):
    pass
