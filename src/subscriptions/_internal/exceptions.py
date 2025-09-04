from django.db.models import QuerySet
from django.forms import ValidationError


class SubscriptionsError(Exception):
    pass


class QuotaLimitExceeded(SubscriptionsError):
    def __init__(self, resource, amount_requested: int, amount_available: int):
        self.resource = resource
        self.amount_requested = amount_requested
        self.amount_available = amount_available


class InconsistentQuotaCache(SubscriptionsError):
    pass


class ProviderNotFound(SubscriptionsError):
    pass


class ProlongationImpossible(SubscriptionsError):
    pass


class PaymentError(SubscriptionsError):
    def __init__(self, message, user_message: str = "", debug_info: dict | None = None):
        super().__init__(message)
        self.user_message = user_message
        self.debug_info = debug_info or {}

    def __str__(self) -> str:
        return super().__str__() + f" {self.debug_info}"


class BadReferencePayment(PaymentError):
    pass


class InvalidOperation(SubscriptionsError):
    pass


class ConfigurationError(SubscriptionsError):
    pass


class PlanDisabled(SubscriptionsError, ValidationError):
    pass


class RecurringSubscriptionRequired(SubscriptionsError, ValidationError):
    pass


class MultipleRecurringSubscriptions(SubscriptionsError, ValidationError):
    def __init__(self, message: str, conflicting_subscriptions: QuerySet):
        self.conflicting_subscriptions = conflicting_subscriptions
        super().__init__(message)
