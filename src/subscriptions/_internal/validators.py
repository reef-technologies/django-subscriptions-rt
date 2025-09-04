from collections.abc import Callable
from functools import lru_cache

from django.conf import settings
from django.utils.module_loading import import_string

from .defaults import DEFAULT_SUBSCRIPTIONS_VALIDATORS
from .exceptions import MultipleRecurringSubscriptions, PlanDisabled, RecurringSubscriptionRequired
from .models import Subscription


def plan_is_enabled(self: Subscription) -> None:
    # when creating new subscription, chosen plan should be enabled
    if self._state.adding and not self.plan.is_enabled:
        raise PlanDisabled("")


def not_recurring_requires_recurring(self: Subscription) -> None:
    if not self._state.adding:
        return

    if not self.plan.is_recurring() and not self.user.subscriptions.active().recurring().exists():
        raise RecurringSubscriptionRequired("")


def exclusive_recurring_subscription(self: Subscription) -> None:
    if not self._state.adding:
        return

    other_recurring = self.user.subscriptions.active().recurring()
    if self.plan.is_recurring() and other_recurring.exists():
        raise MultipleRecurringSubscriptions(
            message="",
            conflicting_subscriptions=other_recurring,
        )


@lru_cache
def get_validators() -> list[Callable]:
    return [
        import_string(module)
        for module in getattr(settings, "SUBSCRIPTIONS_VALIDATORS", DEFAULT_SUBSCRIPTIONS_VALIDATORS)
    ]
