from datetime import datetime

import pytest
from demo.tests.utils import days
from payments.models import Quota, QuotaChunk, Subscription


def test_limited_plan_duration(db, user, plan, now):
    plan.subscription_duration = days(30)
    plan.save(update_fields=['subscription_duration'])

    subscription = Subscription.objects.create(
        user=user,
        plan=plan,
        start=now,
    )
    assert subscription.end == now + days(30)


def test_unlimited_plan_duration(db, user, plan, now):
    plan.subscription_duration = None
    plan.save(update_fields=['subscription_duration'])

    subscription = Subscription.objects.create(
        user=user,
        plan=plan,
        start=now,
    )
    assert subscription.end >= now + days(365 * 10)


def test_plan_charge_dates(db, plan):
    plan.charge_period = days(30)
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
    subscription.start = now - days(2)
    subscription.end = now - days(1)
    subscription.save(update_fields=['start', 'end'])
    assert subscription not in Subscription.objects.active(as_of=now)

    subscription.end = now + days(1)
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


def test_iter_quota_chunks(db, subscription, resource):
    """
                       Subscription
    ----------[=========================]-------------> time

    quota 1:  [----------------]
              recharge (+100)  burn

    quota 2:               [-----------------]
                           recharge (+100)   burn
    """
    subscription.end = subscription.start + days(10)
    subscription.save(update_fields=['end'])

    Quota.objects.create(
        plan=subscription.plan,
        resource=resource,
        limit=100,
        recharge_period=days(5),
        burns_in=days(7),
    )

    start = subscription.start
    chunks = [
        QuotaChunk(resource=resource, start=start, end=start + days(7), remains=100),
        QuotaChunk(resource=resource, start=start + days(5), end=start + days(10), remains=100),
        QuotaChunk(resource=resource, start=start + days(10), end=start + days(10), remains=100),
    ]

    assert list(subscription.iter_quota_chunks(since=start - days(1), until=start - days(1))) == []
    assert list(subscription.iter_quota_chunks(until=start)) == chunks[0:1]
    assert list(subscription.iter_quota_chunks(until=start + days(2))) == chunks[0:1]
    assert list(subscription.iter_quota_chunks(until=start + days(5))) == chunks[0:2]
    assert list(subscription.iter_quota_chunks(until=start + days(11))) == chunks
    assert list(subscription.iter_quota_chunks(since=subscription.end - days(1))) == chunks[1:]
