from datetime import timedelta
from itertools import product
from operator import attrgetter

import pytest
from dateutil.parser import parse
from dateutil.relativedelta import relativedelta
from django.core.cache import caches
from djmoney.money import Money
from freezegun import freeze_time
from subscriptions.exceptions import InconsistentQuotaCache, QuotaLimitExceeded
from subscriptions.functions import get_remaining_amount, iter_subscriptions_involved, use_resource, merge_feature_sets, get_default_features
from subscriptions.models import INFINITY, Plan, Quota, QuotaCache, QuotaChunk, Subscription, Usage, Feature, Tier


def test_subscriptions_involved(five_subscriptions, user, plan, now, days):
    subscriptions_involved = iter_subscriptions_involved(user=user, at=now)
    assert sorted(subscriptions_involved, key=attrgetter('start')) == [
        five_subscriptions[1], five_subscriptions[3], five_subscriptions[0],
    ]


def test_subscriptions_involved_performance(five_subscriptions, django_assert_max_num_queries, user, now, plan):
    with django_assert_max_num_queries(2):
        list(iter_subscriptions_involved(user=user, at=now))


def test_cache_apply(resource, now, days):
    chunks = [
        QuotaChunk(resource=resource, start=now + days(2), end=now + days(3), amount=100, remains=100),
        QuotaChunk(resource=resource, start=now, end=now + days(1), amount=100, remains=100),
        QuotaChunk(resource=resource, start=now + days(1), end=now + days(2), amount=100, remains=100),
    ]

    # check that order doesn't matter
    cache = QuotaCache(
        datetime=now + days(2),
        chunks=chunks[::-1],
    )
    assert list(cache.apply(chunks)) == chunks

    cache = QuotaCache(
        datetime=now + days(1),
        chunks=[
            QuotaChunk(resource=resource, start=now, end=now + days(1), amount=100, remains=22),
            QuotaChunk(resource=resource, start=now + days(1), end=now + days(2), amount=100, remains=33),
            QuotaChunk(resource=resource, start=now + days(2), end=now + days(3), amount=100, remains=44),
        ],
    )

    assert list(cache.apply(chunks)) == [
        QuotaChunk(resource=resource, start=now + days(2), end=now + days(3), amount=100, remains=44),
        QuotaChunk(resource=resource, start=now, end=now + days(1), amount=100, remains=22),
        QuotaChunk(resource=resource, start=now + days(1), end=now + days(2), amount=100, remains=33),
    ]


def test_cache_inconsistencies(resource, now, days):
    chunks = [
        QuotaChunk(resource=resource, start=now, end=now + days(1), amount=100, remains=100),
        QuotaChunk(resource=resource, start=now + days(1), end=now + days(2), amount=100, remains=100),
        QuotaChunk(resource=resource, start=now + days(2), end=now + days(3), amount=100, remains=100),
    ]

    cache = QuotaCache(
        datetime=None,
        chunks=chunks + [
            QuotaChunk(resource=resource, start=now, end=now + days(1), amount=100, remains=100),
        ],
    )
    with pytest.raises(InconsistentQuotaCache):
        list(cache.apply(chunks))


def test_remaining_chunks_performance(db, two_subscriptions, now, remaining_chunks, django_assert_max_num_queries, get_cache, days):
    cache_day, test_day = 8, 10

    with django_assert_max_num_queries(3):
        remaining_chunks(at=now + days(test_day))

    cache = get_cache(at=now + days(cache_day))
    with django_assert_max_num_queries(3):
        remaining_chunks(at=now + days(test_day), quota_cache=cache)


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
        limit=50,  # but quantity == 2 -> real limit == 100
        recharge_period=INFINITY,
    )

    Usage.objects.bulk_create([
        Usage(user=subscription.user, resource=resource, amount=30, datetime=subscription.start + days(3)),
        Usage(user=subscription.user, resource=resource, amount=30, datetime=subscription.start + days(6)),
    ])

    assert remains(at=subscription.start) == 100
    assert remains(at=subscription.start + days(3)) == 70
    assert remains(at=subscription.start + days(6)) == 40
    assert remains(at=subscription.start + days(10)) == 0


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
        limit=50,  # but quantity == 2 -> real limit == 100
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
        limit=50,  # but quantity == 2 -> real limit == 100
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
    assert remains(at=subscription.start + days(10)) == 0


def test_multiple_subscriptions(db, two_subscriptions, user, resource, now, remains, days):

    assert remains(at=now - days(1)) == 0
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
    assert remains(at=now + days(16)) == 0


def test_cache(db, two_subscriptions, now, remaining_chunks, get_cache, days):

    for cache_day, test_day in product(range(13), range(13)):
        assert remaining_chunks(
            at=now + days(test_day / 2),
            quota_cache=get_cache(at=now + days(cache_day / 2)),
        ) == remaining_chunks(
            at=now + days(test_day / 2),
        )  # "middle" cases

        assert remaining_chunks(
            at=now + days(test_day),
            quota_cache=get_cache(at=now + days(cache_day)),
        ) == remaining_chunks(
            at=now + days(test_day),
        )  # corner cases


def test_use_resource(db, user, subscription, quota, resource, remains, now, days):
    with freeze_time(now):
        assert remains() == 100
        with use_resource(user, resource, 10) as left:
            assert left == 90
            assert remains() == 90

        assert remains() == 90

    with freeze_time(now + days(1)):
        try:
            with use_resource(user, resource, 10) as left:
                assert remains() == left == 80
                raise ValueError()
        except ValueError:
            pass
        assert remains() == 90

    with freeze_time(now + days(2)):
        with pytest.raises(QuotaLimitExceeded):
            with use_resource(user, resource, 100):
                pass

    with freeze_time(now + days(2)):
        with use_resource(user, resource, 100, raises=False):
            pass


def test_cache_backend_correctness(cache_backend, db, user, two_subscriptions, remains, days, now, resource):
    cache = caches['subscriptions']

    assert cache.get(user.pk) is None

    assert remains(at=now - days(1)) == 0
    assert cache.get(user.pk) == QuotaCache(
        datetime=now - days(1),
        chunks=[],
    )

    assert remains(at=now) == 100
    assert cache.get(user.pk) == QuotaCache(
        datetime=now,
        chunks=[
            QuotaChunk(
                resource=resource,
                start=now,
                end=now + days(7),
                amount=100,
                remains=100,
            ),
        ],
    )

    # corrupt cache
    cache.set(user.pk, QuotaCache(
        datetime=now,
        chunks=[
            QuotaChunk(
                resource=resource,
                start=now,
                end=now + days(4),
                amount=900,
                remains=900,
            ),
        ],
    ))

    assert remains(at=now + days(1)) == 50
    assert cache.get(user.pk) == QuotaCache(
        datetime=now + days(1),
        chunks=[
            QuotaChunk(
                resource=resource,
                start=now,
                end=now + days(7),
                amount=100,
                remains=50,
            ),
        ],
    )

    assert remains(at=now + days(6)) == 50
    assert cache.get(user.pk) == QuotaCache(
        datetime=now + days(6),
        chunks=[
            QuotaChunk(
                resource=resource,
                start=now,
                end=now + days(7),
                amount=100,
                remains=0,
            ),
            QuotaChunk(
                resource=resource,
                start=now + days(4),
                end=now + days(4) + days(7),
                amount=100,
                remains=50,
            ),
            QuotaChunk(
                resource=resource,
                start=now + days(5),
                end=now + days(10),
                amount=100,
                remains=0,
            ),
        ],
    )


def test_cache_recalculation_real_case(cache_backend, db, user, resource, remains):
    plan_pro = Plan.objects.create(
        codename='11-pro-quarterly',
        name='Pro',
        charge_amount=Money(132, 'USD'),
        charge_period=relativedelta(months=3),
        max_duration=relativedelta(days=365000),
    )
    Quota.objects.create(
        plan=plan_pro, resource=resource,
        limit=6,
        recharge_period=relativedelta(months=3),
        burns_in=relativedelta(months=3),
    )

    plan_endboss = Plan.objects.create(
        codename='12-endboss-quarterly',
        name='Endboss',
        charge_amount=Money(267, 'USD'),
        charge_period=relativedelta(months=3),
        max_duration=relativedelta(days=365000),
    )
    Quota.objects.create(
        plan=plan_endboss, resource=resource,
        limit=45,
        recharge_period=relativedelta(months=3),
        burns_in=relativedelta(months=3),
    )

    Subscription.objects.create(
        user=user, plan=plan_endboss,
        start=parse('2022-11-17 07:47:14 UTC'),
    )

    Usage.objects.create(
        user=user, resource=resource,
        amount=35, datetime=parse('2022-11-17 07:50:44 UTC'),
    )
    assert get_remaining_amount(user=user, at=parse('2022-11-17 07:50:50 UTC')) == {resource: 10}

    Subscription.objects.create(
        user=user, plan=plan_pro,
        start=parse('2022-11-17 07:51:29 UTC'),
    )
    assert get_remaining_amount(user=user, at=parse('2022-11-17 07:51:30 UTC')) == {resource: 16}

    Usage.objects.create(
        user=user, resource=resource,
        amount=2, datetime=parse('2022-11-17 07:52:07 UTC'),
    )
    assert get_remaining_amount(user=user, at=parse('2022-11-17 07:52:08 UTC')) == {resource: 14}

    Usage.objects.create(
        user=user, resource=resource,
        amount=3, datetime=parse('2022-11-17 07:52:30 UTC'),
    )
    assert get_remaining_amount(user=user, at=parse('2022-11-17 07:52:31 UTC')) == {resource: 11}

    Usage.objects.create(
        user=user, resource=resource,
        amount=2, datetime=parse('2022-11-17 07:52:45 UTC'),
    )
    assert get_remaining_amount(user=user, at=parse('2022-11-17 07:52:46 UTC')) == {resource: 9}

    Usage.objects.create(
        user=user, resource=resource,
        amount=1, datetime=parse('2022-11-17 07:52:57 UTC'),
    )
    assert get_remaining_amount(user=user, at=parse('2022-11-17 07:52:58 UTC')) == {resource: 8}

    Usage.objects.create(
        user=user, resource=resource,
        amount=4, datetime=parse('2022-11-17 07:53:11 UTC'),
    )
    assert get_remaining_amount(user=user, at=parse('2022-11-17 07:53:12 UTC')) == {resource: 4}

    Usage.objects.create(
        user=user, resource=resource,
        amount=2, datetime=parse('2022-11-17 07:53:24 UTC'),
    )
    assert get_remaining_amount(user=user, at=parse('2022-11-17 07:53:25 UTC')) == {resource: 2}

    Usage.objects.create(
        user=user, resource=resource,
        amount=2, datetime=parse('2022-11-17 07:53:44 UTC'),
    )
    assert get_remaining_amount(user=user, at=parse('2022-11-17 07:53:45 UTC')) == {resource: 0}


def test__merge_feature_sets(db):
    show_ads = Feature.objects.create(codename='SHOW_ADS', is_negative=True)
    add_premium_badge = Feature.objects.create(codename='ADD_PREMIUM_BADGE')
    extra_reward = Feature.objects.create(codename='EXTRA_REWARD')

    assert merge_feature_sets(
        {show_ads, add_premium_badge},
        {show_ads},
        {},
    ) == {add_premium_badge}

    assert merge_feature_sets(
        {show_ads, extra_reward},
        {show_ads, add_premium_badge},
        {show_ads},
    ) == {show_ads, extra_reward, add_premium_badge}


def test__get_default_features(db, django_assert_num_queries):
    tiers = Tier.objects.bulk_create([
        Tier(codename='zero', is_default=True),
        Tier(codename='one'),
        Tier(codename='two', is_default=True),
    ])

    default_feature_many_tiers = Feature.objects.create(codename='DEFAULT_FEATURE_MANY_TIERS')
    tiers[0].features.add(default_feature_many_tiers)
    tiers[2].features.add(default_feature_many_tiers)

    default_feature_one_tier = Feature.objects.create(codename='DEFAULT_FEATURE_ONE_TIER')
    tiers[0].features.add(default_feature_one_tier)

    non_default_feature = Feature.objects.create(codename='NON_DEFAULT_FEATURE')
    tiers[1].features.add(non_default_feature)

    with django_assert_num_queries(2):
        assert get_default_features() == {default_feature_many_tiers, default_feature_one_tier}

    with django_assert_num_queries(0):
        assert get_default_features() == {default_feature_many_tiers, default_feature_one_tier}


    new_default_feature = Feature.objects.create(codename='NEW_DEFAULT_FEATURE')
    tiers[0].features.set([new_default_feature])
    tiers[0].save()

    with django_assert_num_queries(2):
        assert get_default_features() == {default_feature_many_tiers, new_default_feature}

    with django_assert_num_queries(0):
        assert get_default_features() == {default_feature_many_tiers, new_default_feature}


    tiers[0].is_default = False
    tiers[0].save()

    with django_assert_num_queries(2):
        assert get_default_features() == {default_feature_many_tiers}

    with django_assert_num_queries(0):
        assert get_default_features() == {default_feature_many_tiers}