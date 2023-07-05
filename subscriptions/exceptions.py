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
    user_message: str = 'unknown error'  # TODO: won't work with __init__()
    code = 'unknown'


class BadReferencePayment(PaymentError):
    pass


class InvalidOperation(Exception):
    pass
