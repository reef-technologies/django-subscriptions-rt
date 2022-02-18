from datetime import datetime, timedelta

import pytest
from payments.models import Plan, Subscription


def test_limited_plan_duration(db, user, plan, now):
    plan.subscription_duration = timedelta(days=30)
    plan.save(update_fields=['subscription_duration'])

    subscription = Subscription.objects.create(
        user=user,
        plan=plan,
        start=now,
    )
    assert subscription.end == now + timedelta(days=30)


def test_unlimited_plan_duration(db, user, plan, now):
    plan.subscription_duration = None
    plan.save(update_fields=['subscription_duration'])

    subscription = Subscription.objects.create(
        user=user,
        plan=plan,
        start=now,
    )
    assert subscription.end >= now + timedelta(days=365 * 10)


def test_plan_charge_dates(db, plan):
    plan.charge_period = timedelta(days=30)
    plan.save(update_fields=['charge_period'])

    start = datetime(2021, 11, 30, 12, 00, 00)
    expected_charge_dates = [
        datetime(2021, 12, 30, 12, 00, 00),
        datetime(2022, 1, 29, 12, 00, 00),
    ]

    for expected_date, yielded_date in zip(expected_charge_dates, plan.iter_charge_dates(from_=start)):
        assert expected_date == yielded_date


def test_plan_charge_dates_with_no_charge_period(db, plan, now):
    plan.charge_period = None
    plan.save(update_fields=['charge_period'])

    with pytest.raises(StopIteration):
        next(plan.iter_charge_dates(now))


def test_active_subscription_filter(db, subscription, now):
    subscription.start = now - timedelta(days=2)
    subscription.end = now - timedelta(days=1)
    subscription.save(update_fields=['start', 'end'])
    assert subscription not in Subscription.objects.active(as_of=now)

    subscription.end = now + timedelta(days=1)
    subscription.save(update_fields=['end'])
    assert subscription in Subscription.objects.active(as_of=now)


# def test_plan_charge_amount(plan: Plan):

#     raise NotImplementedError()


# def test_plan_periodic_charge(plan: Plan):
#     raise NotImplementedError()


# def test_plan_subscription_duration(plan: Plan):
#     raise NotImplementedError()


# def test_plan_is_enabled(plan: Plan):
#     raise NotImplementedError()
