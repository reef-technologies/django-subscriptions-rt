from datetime import datetime
from datetime import timezone as tz
from decimal import Decimal
from functools import wraps
from typing import List
from dateutil.relativedelta import relativedelta

import pytest
from django.contrib.auth import get_user_model
from payments.functions import get_remaining_amount, get_remaining_chunks
from payments.models import Plan, Quota, QuotaCache, Resource, Subscription, Usage


@pytest.fixture
def days():
    def fn(n: int) -> relativedelta:
        return relativedelta(days=n)
    return fn


@pytest.fixture
def now():
    return datetime(2022, 1, 1, 12, 00, 00, tzinfo=tz.utc)


@pytest.fixture
def user(db):
    return get_user_model().objects.create(
        username='test',
    )


@pytest.fixture
def resource(db) -> Resource:
    return Resource.objects.create(
        codename='resource',
    )


@pytest.fixture
def plan(db, days) -> Plan:
    return Plan.objects.create(
        codename='plan',
        name='Plan',
        charge_amount=Decimal(100),
        charge_period=days(30),
    )


@pytest.fixture
def subscription(db, now, user, plan) -> Subscription:
    return Subscription.objects.create(
        user=user,
        plan=plan,
        start=now,
    )


@pytest.fixture
def quota(db, resource, subscription) -> Quota:
    return Quota.calculate_remaining(user)


@pytest.fixture
def remaining_chunks(user) -> callable:
    @wraps(get_remaining_chunks)
    def wrapped(**kwargs):
        return get_remaining_chunks(user=user, **kwargs)

    return wrapped


@pytest.fixture
def remains(user, resource) -> callable:
    @wraps(get_remaining_amount)
    def wrapped(**kwargs):
        return get_remaining_amount(user=user, **kwargs).get(resource, 0)

    return wrapped


@pytest.fixture
def get_cache(remaining_chunks) -> callable:

    def fn(at: datetime) -> QuotaCache:
        return QuotaCache(
            datetime=at,
            chunks=remaining_chunks(at=at),
        )

    return fn


@pytest.fixture
def two_subscriptions(user, now, days, resource):
    """
                         Subscription 1
    --------------[========================]------------> time

    quota 1.1:    [-----------------]
             0    100             100  0

    quota 1.2:                 [-----------x (subscription ended)
                          0    100       100  0

    days__________0__1______4__5____7______10_______________

                                 Subscription 2
    ------------------------[===========================]-----> time

    quota 2.1:              [-----------------]
                       0    100             100  0

    quota 2.2:                           [--------------x (subscription ended)
                                    0    100          100  0

    -----------------|------------|-----------------|----------------
    usage:           50          200               50

    """

    plan1 = Plan.objects.create(codename='plan1', name='Plan 1')
    Subscription.objects.create(
        user=user,
        plan=plan1,
        start=now,
        end=now + days(10),
    )
    Quota.objects.create(
        plan=plan1,
        resource=resource,
        limit=100,
        recharge_period=days(5),
        burns_in=days(7),
    )

    plan2 = Plan.objects.create(codename='plan2', name='Plan 2')
    Subscription.objects.create(
        user=user,
        plan=plan2,
        start=now + days(4),
        end=now + days(14),
    )
    Quota.objects.create(
        plan=plan2,
        resource=resource,
        limit=100,
        recharge_period=days(5),
        burns_in=days(7),
    )

    Usage.objects.bulk_create([
        Usage(user=user, resource=resource, amount=50, datetime=now + days(1)),
        Usage(user=user, resource=resource, amount=200, datetime=now + days(6)),
        Usage(user=user, resource=resource, amount=50, datetime=now + days(12)),
    ])


@pytest.fixture
def five_subscriptions(db, plan, user, now, days) -> List[Subscription]:
    """
    Subscriptions:                    |now
    ----------------------------------[====sub0=====]-----> overlaps with "now"
    --------------------[======sub1=======]---------------> overlaps with "sub0"
    -------------[=sub2=]---------------------------------> does not overlap with "sub1"
    -----------------------[=sub3=]-----------------------> overlaps with "sub1"
    ----[=sub4=]------------------------------------------> does not overlap with anything
    """

    sub0 = Subscription.objects.create(user=user, plan=plan, start=now - days(5), end=now + days(2))
    sub1 = Subscription.objects.create(user=user, plan=plan, start=sub0.start - days(5), end=sub0.start + days(2))
    sub2 = Subscription.objects.create(user=user, plan=plan, start=sub1.start - days(5), end=sub1.start)
    sub3 = Subscription.objects.create(user=user, plan=plan, start=sub1.start + days(1), end=sub0.start - days(1))
    sub4 = Subscription.objects.create(user=user, plan=plan, start=sub2.start - days(5), end=sub2.start - days(1))
    return [sub0, sub1, sub2, sub3, sub4]
