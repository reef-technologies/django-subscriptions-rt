from datetime import timedelta
from itertools import product
from operator import attrgetter

import pytest
from payments.exceptions import InconsistentQuotaCache, NoActiveSubscription
from payments.functions import iter_subscriptions_involved
from payments.models import INFINITY, Quota, QuotaCache, QuotaChunk, Usage


def test_subscriptions_involved(five_subscriptions, user, plan, now, days):

    subscriptions_involved = iter_subscriptions_involved(user=user, at=now)
    assert sorted(subscriptions_involved, key=attrgetter('start')) == [
        five_subscriptions[1], five_subscriptions[3], five_subscriptions[0],
    ]


def test_subscriptions_involved_performance(five_subscriptions, django_assert_max_num_queries, user, now, plan):
    with django_assert_max_num_queries(1):
        list(iter_subscriptions_involved(user=user, at=now))


def test_cache_apply(resource, now, days):
    chunks = [
        QuotaChunk(resource=resource, start=now, end=now + days(1), remains=100),
        QuotaChunk(resource=resource, start=now + days(1), end=now + days(2), remains=100),
        QuotaChunk(resource=resource, start=now + days(2), end=now + days(3), remains=100),
    ]

    with pytest.raises(InconsistentQuotaCache):
        list(QuotaCache(
            datetime=now + days(2),
            chunks=chunks[::-1],
        ).apply(chunks))

    cache = QuotaCache(
        datetime=now + days(1),
        chunks=[
            QuotaChunk(resource=resource, start=now, end=now + days(1), remains=22),
            QuotaChunk(resource=resource, start=now + days(1), end=now + days(2), remains=33),
            QuotaChunk(resource=resource, start=now + days(2), end=now + days(3), remains=44),
        ],
    )

    assert list(cache.apply(chunks)) == cache.chunks


@pytest.mark.skip
def test_remaining_chunks():
    ...


@pytest.mark.skip
def test_remaining_chunks_performance(db, two_subscriptions, now, remains, django_assert_max_num_queries, get_cache, days):
    cache_day, test_day = 8, 10

    with django_assert_max_num_queries(2):
        remains(at=now + days(test_day))

    cache = get_cache(at=now + days(cache_day))
    with django_assert_max_num_queries(4):
        remains(at=now + days(test_day), quota_cache=cache)


def test_usage_with_simple_quota(db, subscription, resource, remains, days):
    """
                     Subscription
    --------------[================]------------> time
    quota:    0   100            100   0

    -----------------|------|-------------------
    usage:           30     30
    """
    subscription.end = subscription.start + days(10)
    subscription.save(update_fields=['end'])

    Quota.objects.create(
        plan=subscription.plan,
        resource=resource,
        limit=100,
        recharge_period=INFINITY,
    )

    Usage.objects.bulk_create([
        Usage(user=subscription.user, resource=resource, amount=30, datetime=subscription.start + days(3)),
        Usage(user=subscription.user, resource=resource, amount=30, datetime=subscription.start + days(6)),
    ])

    assert remains(at=subscription.start) == 100
    assert remains(at=subscription.start + days(3)) == 70
    assert remains(at=subscription.start + days(6)) == 40
    with pytest.raises(NoActiveSubscription):
        remains(at=subscription.start + days(10))


def test_usage_with_recharging_quota(db, subscription, resource, remains, days):
    """
                         Subscription
    --------------[========================]------------> time

    quota 1:      [----------------]
             0    100           100  0

    quota 2:                   [-----------]
                          0    100       100  0

    -----------------|------|----|-------|-----------
    usage:           30     30   30      30
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

    Usage.objects.bulk_create([
        Usage(user=subscription.user, resource=resource, amount=amount, datetime=when)
        for amount, when in [
            (30, subscription.start + days(2)),
            (30, subscription.start + days(4)),
            (30, subscription.start + days(6)),
            (30, subscription.start + days(9)),
        ]
    ])

    assert remains(at=subscription.start) == 100
    assert remains(at=subscription.start + days(3)) == 70
    assert remains(at=subscription.start + days(4) + timedelta(hours=12)) == 40
    assert remains(at=subscription.start + days(5)) == 140
    assert remains(at=subscription.start + days(6)) == 110
    assert remains(at=subscription.start + days(7)) == 100
    assert remains(at=subscription.start + days(9)) == 70


def test_subtraction_priority(db, subscription, resource, remains, days):
    """
                         Subscription
    --------------[========================]------------> time

    quota 1:      [----------------]
             0    100           100  0

    quota 2:                   [---------------]
                          0    100           100  0

    -----------------------------|-------------------
    usage:                      150
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

    Usage.objects.create(
        user=subscription.user,
        resource=resource,
        amount=150,
        datetime=subscription.start + days(6),
    )

    assert remains(at=subscription.start + days(5)) == 200
    assert remains(at=subscription.start + days(6)) == 50
    assert remains(at=subscription.start + days(7)) == 50
    with pytest.raises(NoActiveSubscription):
        remains(at=subscription.start + days(10))


def test_multiple_subscriptions(db, two_subscriptions, user, resource, now, remains, days):

    with pytest.raises(NoActiveSubscription):
        remains(at=now - days(1))

    assert remains(at=now + days(0)) == 100
    assert remains(at=now + days(1)) == 50
    assert remains(at=now + days(2)) == 50
    assert remains(at=now + days(4)) == 150
    assert remains(at=now + days(5)) == 250
    assert remains(at=now + days(6)) == 50
    assert remains(at=now + days(7)) == 50
    assert remains(at=now + days(9)) == 150
    assert remains(at=now + days(10)) == 150
    assert remains(at=now + days(11)) == 100
    assert remains(at=now + days(12)) == 50

    with pytest.raises(NoActiveSubscription):
        remains(at=now + days(16))


def test_cache(db, two_subscriptions, now, remains, remaining_chunks, get_cache, days):

    for cache_day, test_day in product(range(13), range(13)):
        if cache_day > test_day:
            continue

        assert remains(at=now + days(test_day / 2), quota_cache=get_cache(at=now + days(cache_day / 2))) == remains(at=now + days(test_day / 2))  # "middle" cases
        assert remains(at=now + days(test_day), quota_cache=get_cache(at=now + days(cache_day))) == remains(at=now + days(test_day))  # corner cases
