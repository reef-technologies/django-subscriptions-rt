from django.urls import reverse_lazy

DEFAULT_SUBSCRIPTIONS_PAYMENT_PROVIDERS = [
    'subscriptions.providers.dummy.DummyProvider',
]
DEFAULT_SUBSCRIPTIONS_SUCCESS_URL = reverse_lazy('plan_subscription_success')

DEFAULT_SUBSCRIPTIONS_VALIDATORS = [
    'subscriptions.validators.OnlyEnabledPlans',
    'subscriptions.validators.AtLeastOneRecurringSubscription',
    'subscriptions.validators.SimultaneousRecurringSubscriptions',
]
