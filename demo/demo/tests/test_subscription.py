from datetime import datetime
from datetime import timezone as tz

import pytest
from payments.models import Quota, QuotaChunk, Subscription


def test_limited_plan_duration(db, user, plan, now, days):
    plan.subscription_duration = days(30)
    plan.save(update_fields=['subscription_duration'])

    subscription = Subscription.objects.create(
        user=user,
        plan=plan,
        start=now,
    )
    assert subscription.end == now + days(30)


def test_unlimited_plan_duration(db, user, plan, now, days):
    plan.subscription_duration = None
    plan.save(update_fields=['subscription_duration'])

    subscription = Subscription.objects.create(
        user=user,
        plan=plan,
        start=now,
    )
    assert subscription.end >= now + days(365 * 10)


def test_subscription_charge_dates(db, plan, subscription, days):
    plan.charge_period = days(30)
    plan.save(update_fields=['charge_period'])

    subscription.start = datetime(2021, 11, 30, 12, 00, 00, tzinfo=tz.utc)
    subscription.end = subscription.start + days(65)
    subscription.save(update_fields=['start', 'end'])

    expected_charge_dates = [
        subscription.start,
        datetime(2021, 12, 30, 12, 00, 00, tzinfo=tz.utc),
        datetime(2022, 1, 29, 12, 00, 00, tzinfo=tz.utc),
    ]

    assert list(subscription.iter_charge_dates()) == expected_charge_dates
    assert list(subscription.iter_charge_dates(since=subscription.start)) == expected_charge_dates
    assert list(subscription.iter_charge_dates(since=subscription.start + days(1))) == expected_charge_dates[1:]
    assert list(subscription.iter_charge_dates(since=subscription.start + days(30))) == expected_charge_dates[1:]
    assert list(subscription.iter_charge_dates(since=subscription.start + days(31))) == expected_charge_dates[2:]
    assert list(subscription.iter_charge_dates(since=subscription.start + days(60))) == expected_charge_dates[2:]
    assert list(subscription.iter_charge_dates(since=subscription.start + days(64))) == []
    assert list(subscription.iter_charge_dates(since=subscription.start + days(65))) == []


def test_subscription_charge_dates_with_no_charge_period(db, plan, subscription, now):
    plan.charge_period = None
    plan.save(update_fields=['charge_period'])

    assert list(subscription.iter_charge_dates()) == [subscription.start]


def test_active_subscription_filter(db, subscription, now, days):
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


def test_iter_quota_chunks(db, subscription, resource, days):
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


def test_subscription_get_expiring_performance(django_assert_num_queries, two_subscriptions, days):
    with django_assert_num_queries(1):
        list(Subscription.get_expiring(within=days(5)))


@pytest.mark.skip
def test_subscription_get_remaining_amount_performance():
    Subscription().get_remaining_amount()


@pytest.mark.skip
def test_subscription_iter_quota_chunks_performance():
    Subscription().iter_quota_chunks()


@pytest.mark.skip
def test_subscription_iter_subscriptions_quota_chunks_performance():
    Subscription().iter_subscriptions_quota_chunks()


@pytest.mark.skip
def test_subscription_iter_charge_dates_performance():
    Subscription().iter_charge_dates()

