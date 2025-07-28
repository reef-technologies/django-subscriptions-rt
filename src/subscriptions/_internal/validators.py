from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

from django.conf import settings
from django.utils.module_loading import import_string

from .defaults import DEFAULT_SUBSCRIPTIONS_VALIDATORS
from .exceptions import RecurringSubscriptionsAlreadyExist, SubscriptionError

if TYPE_CHECKING:
    from .models import Plan, SubscriptionQuerySet


@dataclass(frozen=True)
class SubscriptionValidator:
    def __call__(self, active_subscriptions: SubscriptionQuerySet, requested_plan: Plan):
        pass


@dataclass(frozen=True)
class OnlyEnabledPlans(SubscriptionValidator):
    def __call__(self, active_subscriptions: SubscriptionQuerySet, requested_plan: Plan):
        if not requested_plan.is_enabled:
            raise SubscriptionError("Requested plan is disabled")


@dataclass(frozen=True)
class AtLeastOneRecurringSubscription(SubscriptionValidator):
    def __call__(self, active_subscriptions: SubscriptionQuerySet, requested_plan: Plan):
        if not requested_plan.is_recurring() and not active_subscriptions.recurring().exists():
            raise SubscriptionError("Need any recurring subscription first")


@dataclass(frozen=True)
class SingleRecurringSubscription(SubscriptionValidator):
    def __call__(self, active_subscriptions: SubscriptionQuerySet, requested_plan: Plan):
        if not requested_plan.is_recurring():
            return

        if active_recurring_subscriptions := list(active_subscriptions.recurring()):
            raise RecurringSubscriptionsAlreadyExist(
                f"{len(active_recurring_subscriptions)} recurring subscription(s) already exist",
                subscriptions=active_recurring_subscriptions,
            )


@lru_cache(maxsize=1)
def get_validators() -> list[SubscriptionValidator]:
    return [
        import_string(module)()
        for module in getattr(settings, "SUBSCRIPTIONS_VALIDATORS", DEFAULT_SUBSCRIPTIONS_VALIDATORS)
    ]
