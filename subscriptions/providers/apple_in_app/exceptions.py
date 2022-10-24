from subscriptions.exceptions import (
    InvalidOperation,
    PaymentError,
    SubscriptionError,
)


class AppleInvalidOperation(SubscriptionError):
    def __init__(self):
        super().__init__(f'Apple subscription provider doesn\'t support this operation.')


class AppleSubscriptionNotCompletedError(SubscriptionError):
    def __init__(self, transaction_id: str):
        super().__init__(f'Apple subscription for transaction ID {transaction_id} '
                         f'found to be not in a COMPLETED state.')


class ProductIdChangedError(SubscriptionError):
    def __init__(self, old_product_id: str, new_product_id: str):
        super().__init__(f'Unexpected change of the product id occurred during renewal. '
                         f'{old_product_id=}, {new_product_id=}.')


class AppleReceiptValidationError(PaymentError):
    def __init__(self):
        self.code = 'invalid_receipt'
        self.user_message = 'Provided transaction receipt was either malformed or invalid.'


class InvalidAppleReceiptError(InvalidOperation):
    pass


class AppleAppStoreError(Exception):
    pass


class ConfigurationError(AppleAppStoreError):
    pass


class PayloadValidationError(AppleAppStoreError):
    pass
