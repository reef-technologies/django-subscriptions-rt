from datetime import datetime
from datetime import timezone as tz
from itertools import islice

import pytest
from dateutil.relativedelta import relativedelta
from subscriptions.exceptions import ProlongationImpossible
from subscriptions.models import Quota, QuotaChunk, Subscription


def test_limited_plan_duration(db, user, plan, now, days):
    plan.max_duration = days(30)
    plan.charge_period = days(10)
    plan.save(update_fields=['max_duration', 'charge_period'])

    subscription = Subscription.objects.create(
        user=user,
        plan=plan,
        start=now,
    )

    assert subscription.end == now + plan.charge_period

    subscription.prolong()
    assert subscription.end == now + 2 * plan.charge_period

    subscription.prolong()
    assert subscription.end == now + 3 * plan.charge_period

    with pytest.raises(ProlongationImpossible):
        subscription.prolong()


def test_unlimited_plan_duration(db, user, plan, now, days):
    plan.max_duration = None
    plan.charge_period = days(300)
    plan.save(update_fields=['max_duration'])

    subscription = Subscription.objects.create(
        user=user,
        plan=plan,
        start=now,
    )

    assert subscription.end == now + days(300)

    for i in range(2, 11):
        subscription.prolong()
        assert subscription.end == now + i * days(300)


def test_subscription_charge_dates(db, plan, subscription, days):
    plan.charge_period = relativedelta(months=1)
    plan.save(update_fields=['charge_period'])

    subscription.start = datetime(2021, 11, 30, 12, 00, 00, tzinfo=tz.utc)
    subscription.end = subscription.start + days(65)
    subscription.save(update_fields=['start', 'end'])

    expected_charge_dates = [
        subscription.start,
        datetime(2021, 12, 30, 12, 00, 00, tzinfo=tz.utc),
        datetime(2022, 1, 30, 12, 00, 00, tzinfo=tz.utc),
        datetime(2022, 2, 28, 12, 00, 00, tzinfo=tz.utc),
        datetime(2022, 3, 30, 12, 00, 00, tzinfo=tz.utc),
    ]

    assert list(islice(subscription.iter_charge_dates(), 3)) == expected_charge_dates[:3]
    assert list(islice(subscription.iter_charge_dates(since=subscription.start), 3)) == expected_charge_dates[:3]
    assert list(islice(subscription.iter_charge_dates(since=subscription.start + days(1)), 3)) == expected_charge_dates[1:4]
    assert list(islice(subscription.iter_charge_dates(since=subscription.start + days(30)), 3)) == expected_charge_dates[1:4]
    assert list(islice(subscription.iter_charge_dates(since=subscription.start + days(31)), 3)) == expected_charge_dates[2:5]
    assert list(islice(subscription.iter_charge_dates(since=subscription.start + days(60)), 3)) == expected_charge_dates[2:5]


def test_subscription_iter_charge_dates_performance(db, subscription, django_assert_num_queries):
    with django_assert_num_queries(0):
        list(islice(subscription.iter_charge_dates(), 10))


def test_subscription_charge_dates_with_no_charge_period(db, plan, subscription, now):
    plan.charge_period = None
    plan.save(update_fields=['charge_period'])
    assert list(subscription.iter_charge_dates()) == [subscription.start]


def test_active_subscription_filter(db, subscription, now, days):
    subscription.start = now - days(2)
    subscription.end = now - days(1)
    subscription.save(update_fields=['start', 'end'])
    assert subscription not in Subscription.objects.active(at=now)

    subscription.end = now + days(1)
    subscription.save(update_fields=['end'])
    assert subscription in Subscription.objects.active(at=now)


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
        limit=50,  # but quantity == 2 -> real limit == 100
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
