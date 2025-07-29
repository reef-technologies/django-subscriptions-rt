from datetime import timedelta

from dateutil.relativedelta import relativedelta
from django.urls import reverse_lazy

DEFAULT_SUBSCRIPTIONS_PAYMENT_PROVIDERS = [
    "subscriptions._internal.providers.dummy.DummyProvider",
]
DEFAULT_SUBSCRIPTIONS_SUCCESS_URL = reverse_lazy("plan_subscription_success")

DEFAULT_SUBSCRIPTIONS_VALIDATORS = [
    "subscriptions._internal.validators.OnlyEnabledPlans",
    "subscriptions._internal.validators.AtLeastOneRecurringSubscription",
    "subscriptions._internal.validators.SingleRecurringSubscription",
]

DEFAULT_SUBSCRIPTIONS_OFFLINE_CHARGE_ATTEMPTS_SCHEDULE = (
    timedelta(days=-3),
    timedelta(days=-2),
    timedelta(days=-1),
    timedelta(hours=-12),
    timedelta(hours=-3),
    timedelta(hours=-1),
    timedelta(0),
)

DEFAULT_SUBSCRIPTIONS_CACHE_NAME = "subscriptions"
DEFAULT_SUBSCRIPTIONS_CURRENCY = "USD"
DEFAULT_SUBSCRIPTIONS_TRIAL_PERIOD = relativedelta(seconds=0)
DEFAULT_NOTIFY_PENDING_PAYMENTS_AFTER = timedelta(days=1)

DEFAULT_SUBSCRIPTIONS_ADVISORY_LOCK_TIMEOUT = 5
