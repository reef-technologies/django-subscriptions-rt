from datetime import timedelta

import pytest
from demo.tests.utils import days
from payments.exceptions import NoActiveSubscription
from payments.models import INFINITY, Plan, Quota, Subscription, Usage


def test_usage_with_simple_quota(db, subscription, resource, remains):
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


def test_usage_with_recharging_quota(db, subscription, resource, remains):
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


def test_subtraction_priority(db, subscription, resource, remains):
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


def test_multiple_subscriptions(db, user, resource, now, remains):
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
