from collections.abc import Callable
from functools import lru_cache

from django.conf import settings
from django.utils.module_loading import import_string

from .defaults import DEFAULT_SUBSCRIPTIONS_VALIDATORS
from .exceptions import RecurringSubscriptionsAlreadyExist, SubscriptionError
from .models import Subscription, SubscriptionQuerySet


def plan_is_enabled(self: Subscription, user_subscriptions: SubscriptionQuerySet) -> None:
    if not self.plan.is_enabled:
        raise SubscriptionError("Requested plan is disabled")


def not_recurring_requires_recurring(self: Subscription, user_subscriptions: SubscriptionQuerySet) -> None:
    if not self.plan.is_recurring() and not user_subscriptions.recurring().exists():
        raise SubscriptionError("No recurring subscription exists")


def exclusive_recurring_subscription(self: Subscription, user_subscriptions: SubscriptionQuerySet) -> None:
    if self.plan.is_recurring() and user_subscriptions.recurring().exists():
        raise RecurringSubscriptionsAlreadyExist("Only one recurring subscription is allowed")


@lru_cache
def get_validators() -> list[Callable]:
    return [
        import_string(module)()
        for module in getattr(settings, "SUBSCRIPTIONS_VALIDATORS", DEFAULT_SUBSCRIPTIONS_VALIDATORS)
    ]
