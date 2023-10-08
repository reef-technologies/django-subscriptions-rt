import enum
from datetime import datetime, timezone

import pytest
from dateutil.relativedelta import relativedelta

from subscriptions.models import Subscription


class SubType(enum.Enum):
    PAST_ENDING_BEFORE_TRIAL_END = enum.auto()
    PAST_ENDING_AFTER_TRIAL_END = enum.auto()
    PAST_WITHOUT_TRIAL = enum.auto()
    CURRENT_BEFORE_TRIAL_END = enum.auto()
    CURRENT_AFTER_TRIAL_END = enum.auto()
    CURRENT_WITHOUT_TRIAL = enum.auto()


@pytest.fixture
def now_() -> datetime:
    return datetime(2024, 5, 20, tzinfo=timezone.utc)


@pytest.fixture
def subscriptions(plan, user, now_) -> dict[SubType, Subscription]:
    return {
        SubType.PAST_ENDING_BEFORE_TRIAL_END: Subscription.objects.create(
            user=user,
            plan=plan,
            auto_prolong=False,
            initial_charge_offset=relativedelta(days=5),
            # Two days long subscription, trial period.
            start=now_ - relativedelta(months=10, days=10),
            end=now_ - relativedelta(months=10, days=8),
        ),
        SubType.PAST_ENDING_AFTER_TRIAL_END: Subscription.objects.create(
            user=user,
            plan=plan,
            auto_prolong=False,
            initial_charge_offset=relativedelta(days=5),
            # Eight days long subscription, past trial period.
            start=now_ - relativedelta(months=9, days=10),
            end=now_ - relativedelta(months=9, days=2),
        ),
        SubType.PAST_WITHOUT_TRIAL: Subscription.objects.create(
            user=user,
            plan=plan,
            auto_prolong=False,
            start=now_ - relativedelta(months=8, days=12),
            end=now_ - relativedelta(months=8, days=1),
        ),
        SubType.CURRENT_BEFORE_TRIAL_END: Subscription.objects.create(
            user=user,
            plan=plan,
            auto_prolong=False,
            initial_charge_offset=relativedelta(days=5),
            # Trial period not finished yet.
            start=now_ - relativedelta(days=2),
            end=now_ + relativedelta(days=5),
        ),
        SubType.CURRENT_AFTER_TRIAL_END: Subscription.objects.create(
            user=user,
            plan=plan,
            auto_prolong=False,
            initial_charge_offset=relativedelta(days=5),
            # Trial period finished two days ago.
            start=now_ - relativedelta(days=7),
            end=now_ + relativedelta(days=5),
        ),
        SubType.CURRENT_WITHOUT_TRIAL: Subscription.objects.create(
            user=user,
            plan=plan,
            auto_prolong=False,
            start=now_ - relativedelta(days=5),
            end=now_ + relativedelta(days=5),
        )
    }


@pytest.mark.django_db(databases=['actual_db'])
def test__subscription__listing_charged_subscriptions(now_, subscriptions):
    expected_keys = {
        SubType.PAST_ENDING_AFTER_TRIAL_END,
        SubType.PAST_WITHOUT_TRIAL,
        SubType.CURRENT_AFTER_TRIAL_END,
        SubType.CURRENT_WITHOUT_TRIAL,
    }
    charged_subscriptions = [elem for elem in Subscription.objects.all() if elem.was_charged(now_)]
    received_keys = {key for key, value in subscriptions.items() if value in charged_subscriptions}
    assert expected_keys == received_keys


@pytest.mark.django_db(databases=['actual_db'])
def test__subscription__listing_charged_inactive_subscriptions(now_, subscriptions):
    expected_keys = {
        SubType.PAST_ENDING_AFTER_TRIAL_END,
        SubType.PAST_WITHOUT_TRIAL,
    }

    charged_inactive_subscriptions = [elem for elem in Subscription.objects.inactive(now_) if elem.was_charged(now_)]
    received_keys = {key for key, value in subscriptions.items() if value in charged_inactive_subscriptions}
    assert expected_keys == received_keys


@pytest.mark.django_db(databases=['actual_db'])
def test__subscription__listing_inactive_and_active_subscriptions(now_, subscriptions):
    expected_inactive_keys = {
        SubType.PAST_ENDING_AFTER_TRIAL_END,
        SubType.PAST_ENDING_BEFORE_TRIAL_END,
        SubType.PAST_WITHOUT_TRIAL,
    }
    inactive_subscriptions = Subscription.objects.inactive(now_).all()
    received_inactive_keys = {key for key, value in subscriptions.items() if value in inactive_subscriptions}
    assert expected_inactive_keys == received_inactive_keys

    expected_active_keys = {
        SubType.CURRENT_BEFORE_TRIAL_END,
        SubType.CURRENT_AFTER_TRIAL_END,
        SubType.CURRENT_WITHOUT_TRIAL,
    }
    active_subscriptions = Subscription.objects.active(now_).all()
    received_active_keys = {key for key, value in subscriptions.items() if value in active_subscriptions}
    assert expected_active_keys == received_active_keys
