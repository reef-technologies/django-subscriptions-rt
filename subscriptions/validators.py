from dataclasses import dataclass
from functools import lru_cache
from typing import List, ClassVar

from django.conf import settings
from django.db.models import QuerySet
from django.utils.module_loading import import_string

from .defaults import DEFAULT_SUBSCRIPTIONS_VALIDATORS
from .exceptions import SubscriptionError
from .models import Plan


@dataclass(frozen=True)
class SubscriptionValidator:
    def __call__(self, active_subscriptions: QuerySet, requested_plan: Plan):
        pass


@dataclass(frozen=True)
class OnlyEnabledPlans(SubscriptionValidator):
    def __call__(self, active_subscriptions: QuerySet, requested_plan: Plan):
        if not requested_plan.is_enabled:
            raise SubscriptionError('Requested plan is disabled')


@dataclass(frozen=True)
class AtLeastOneRecurringSubscription(SubscriptionValidator):
    def __call__(self, active_subscriptions: QuerySet, requested_plan: Plan):
        if not requested_plan.is_recurring() and not active_subscriptions.recurring().exists():
            raise SubscriptionError('Need any recurring subscription first')


@dataclass(frozen=True)
class SimultaneousRecurringSubscriptions(SubscriptionValidator):
    MAX_NUMBER: ClassVar[int] = 1

    def __call__(self, active_subscriptions: QuerySet, requested_plan: Plan):
        if not requested_plan.is_recurring():
            return

        if active_subscriptions.recurring().count() > self.MAX_NUMBER - 1:
            raise SubscriptionError('Too many recurring subscriptions')


@lru_cache(maxsize=1)
def get_validators() -> List[SubscriptionValidator]:
    return [import_string(module)() for module in getattr(settings, 'SUBSCRIPTIONS_VALIDATORS', DEFAULT_SUBSCRIPTIONS_VALIDATORS)]
