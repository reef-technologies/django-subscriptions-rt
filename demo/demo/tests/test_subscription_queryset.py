from datetime import datetime, timezone

import pytest
from dateutil.relativedelta import relativedelta
from djmoney.money import Money

from subscriptions.models import Subscription, SubscriptionPayment


@pytest.fixture
def now_() -> datetime:
    return datetime(2024, 5, 20, tzinfo=timezone.utc)


@pytest.fixture
def past_subscription_ending_before_trial_end(plan, user, now_) -> Subscription:
    return Subscription.objects.create(
        user=user,
        plan=plan,
        auto_prolong=False,
        initial_charge_offset=relativedelta(days=5),
        # Two days long subscription, trial period.
        start=now_ - relativedelta(months=10, days=10),
        end=now_ - relativedelta(months=10, days=8),
    )


@pytest.fixture
def past_subscription_ending_after_trial_end(plan, user, now_, dummy) -> Subscription:
    subscription = Subscription.objects.create(
        user=user,
        plan=plan,
        auto_prolong=False,
        initial_charge_offset=relativedelta(days=5),
        # Eight days long subscription, past trial period.
        start=now_ - relativedelta(months=9, days=10),
        end=now_ - relativedelta(months=9, days=2),
    )
    SubscriptionPayment.objects.create(
        provider_codename=dummy,
        amount=Money(1, 'USD'),
        status=SubscriptionPayment.Status.COMPLETED,
        user=user,
        plan=plan,
        subscription=subscription,
        subscription_start=now_ - relativedelta(months=9, days=5),
        subscription_end=subscription.end,
    )
    return subscription


@pytest.fixture
def past_subscription_without_trial(plan, user, now_, dummy) -> Subscription:
    subscription = Subscription.objects.create(
        user=user,
        plan=plan,
        auto_prolong=False,
        start=now_ - relativedelta(months=8, days=12),
        end=now_ - relativedelta(months=8, days=1),
    )
    SubscriptionPayment.objects.create(
        provider_codename=dummy,
        amount=Money(1, 'USD'),
        status=SubscriptionPayment.Status.COMPLETED,
        user=user,
        plan=plan,
        subscription=subscription,
        subscription_start=subscription.start,
        subscription_end=subscription.end,
    )
    return subscription


@pytest.fixture
def current_subscription_before_trial_end(plan, user, now_) -> Subscription:
    return Subscription.objects.create(
        user=user,
        plan=plan,
        auto_prolong=False,
        initial_charge_offset=relativedelta(days=5),
        # Trial period not finished yet.
        start=now_ - relativedelta(days=2),
        end=now_ + relativedelta(days=5),
    )


@pytest.fixture
def current_subscription_after_trial_end(plan, user, now_, dummy) -> Subscription:
    subscription = Subscription.objects.create(
        user=user,
        plan=plan,
        auto_prolong=False,
        initial_charge_offset=relativedelta(days=5),
        # The trial period finished two days ago.
        start=now_ - relativedelta(days=7),
        end=now_ + relativedelta(days=5),
    )
    SubscriptionPayment.objects.create(
        provider_codename=dummy,
        amount=Money(1, 'USD'),
        status=SubscriptionPayment.Status.COMPLETED,
        user=user,
        plan=plan,
        subscription=subscription,
        subscription_start=now_ - relativedelta(days=2),
        subscription_end=subscription.end,
    )
    return subscription


@pytest.fixture
def current_subscription_without_trial(plan, user, now_, dummy) -> Subscription:
    subscription = Subscription.objects.create(
        user=user,
        plan=plan,
        auto_prolong=False,
        start=now_ - relativedelta(days=5),
        end=now_ + relativedelta(days=5),
    )
    SubscriptionPayment.objects.create(
        provider_codename=dummy,
        amount=Money(1, 'USD'),
        status=SubscriptionPayment.Status.COMPLETED,
        user=user,
        plan=plan,
        subscription=subscription,
        subscription_start=subscription.start,
        subscription_end=subscription.end,
    )
    return subscription


@pytest.mark.django_db(databases=['actual_db'])
def test__subscription__listing_charged_subscriptions(
        now_,
        past_subscription_ending_before_trial_end,
        past_subscription_ending_after_trial_end,
        past_subscription_without_trial,
        current_subscription_before_trial_end,
        current_subscription_after_trial_end,
        current_subscription_without_trial,
):
    charged_subscriptions = set(elem for elem in Subscription.objects.charged())
    assert charged_subscriptions == {
        past_subscription_ending_after_trial_end,
        past_subscription_without_trial,
        current_subscription_after_trial_end,
        current_subscription_without_trial,
    }


@pytest.mark.django_db(databases=['actual_db'])
def test__subscription__listing_charged_inactive_subscriptions(
        now_,
        past_subscription_ending_before_trial_end,
        past_subscription_ending_after_trial_end,
        past_subscription_without_trial,
        current_subscription_before_trial_end,
        current_subscription_after_trial_end,
        current_subscription_without_trial,
):
    charged_inactive_subscriptions = set(elem for elem in Subscription.objects.charged().inactive(now_))
    assert charged_inactive_subscriptions == {
        past_subscription_ending_after_trial_end,
        past_subscription_without_trial,
    }

    # Reverse order of operations.
    charged_inactive_subscriptions = set(elem for elem in Subscription.objects.inactive(now_).charged())
    assert charged_inactive_subscriptions == {
        past_subscription_ending_after_trial_end,
        past_subscription_without_trial,
    }


@pytest.mark.django_db(databases=['actual_db'])
def test__subscription__listing_charged_active_subscriptions(
        now_,
        past_subscription_ending_before_trial_end,
        past_subscription_ending_after_trial_end,
        past_subscription_without_trial,
        current_subscription_before_trial_end,
        current_subscription_after_trial_end,
        current_subscription_without_trial,
):
    charged_inactive_subscriptions = set(elem for elem in Subscription.objects.charged().active(now_))
    assert charged_inactive_subscriptions == {
        current_subscription_after_trial_end,
        current_subscription_without_trial,
    }

    # Reverse order of operations.
    charged_inactive_subscriptions = set(elem for elem in Subscription.objects.active(now_).charged())
    assert charged_inactive_subscriptions == {
        current_subscription_after_trial_end,
        current_subscription_without_trial,
    }


@pytest.mark.django_db(databases=['actual_db'])
def test__subscription__listing_inactive_and_active_subscriptions(
        now_,
        past_subscription_ending_before_trial_end,
        past_subscription_ending_after_trial_end,
        past_subscription_without_trial,
        current_subscription_before_trial_end,
        current_subscription_after_trial_end,
        current_subscription_without_trial,
):
    inactive_subscriptions = set(Subscription.objects.inactive(now_).all())
    assert inactive_subscriptions == {
        past_subscription_ending_after_trial_end,
        past_subscription_ending_before_trial_end,
        past_subscription_without_trial,
    }

    active_subscriptions = set(Subscription.objects.active(now_).all())
    assert active_subscriptions == {
        current_subscription_before_trial_end,
        current_subscription_after_trial_end,
        current_subscription_without_trial,
    }
