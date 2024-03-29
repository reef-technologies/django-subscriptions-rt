from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import Subscription


class QuotaLimitExceeded(Exception):
    pass


class InconsistentQuotaCache(Exception):
    pass


class ProviderNotFound(Exception):
    pass


class ProlongationImpossible(Exception):
    pass


class SubscriptionError(Exception):
    pass


class RecurringSubscriptionsAlreadyExist(SubscriptionError):
    def __init__(self, message, subscriptions: list[Subscription]):
        super().__init__(message)
        self.subscriptions = subscriptions


class PaymentError(Exception):
    def __init__(self, message, user_message: str = '', debug_info: dict | None = None):
        super().__init__(message)
        self.user_message = user_message
        self.debug_info = debug_info or {}

    def __str__(self) -> str:
        return super().__str__() + f' {self.debug_info}'


class BadReferencePayment(PaymentError):
    pass


class InvalidOperation(Exception):
    pass
