from datetime import timedelta

from payments.models import INFINITY, Quota, Usage


def test_usage_with_simple_quota(db, subscription, resource, remains):
    """
                     Subscription
    --------------[================]------------> time
    quota:    0   100            100   0

    -----------------|------|-------------------
    usage:           30     30
    """
    subscription.end = subscription.start + timedelta(days=10)
    subscription.save(update_fields=['end'])

    Quota.objects.create(
        plan=subscription.plan,
        resource=resource,
        limit=100,
        recharge_period=INFINITY,
    )

    Usage.objects.bulk_create([
        Usage(user=subscription.user, resource=resource, amount=30, datetime=subscription.start + timedelta(days=3)),
        Usage(user=subscription.user, resource=resource, amount=30, datetime=subscription.start + timedelta(days=6)),
    ])

    assert remains(at=subscription.start) == 100
    assert remains(at=subscription.start + timedelta(days=3)) == 70
    assert remains(at=subscription.start + timedelta(days=6)) == 40
    assert remains(at=subscription.start + timedelta(days=10)) == 0


def test_usage_with_recharging_quota(db, subscription, resource, remains):
    """
                         Subscription
    --------------[========================]------------> time

    quota 1:      [----------------]
             0    100           100  0

    quota 2:                   [---------------]
                          0    100           100  0

    -----------------|------|----|-------|-----------
    usage:           30     30   30      30
    """
    subscription.end = subscription.start + timedelta(days=10)
    subscription.save(update_fields=['end'])

    Quota.objects.create(
        plan=subscription.plan,
        resource=resource,
        limit=100,
        recharge_period=timedelta(days=5),
        burns_in=timedelta(days=7),
    )

    Usage.objects.bulk_create([
        Usage(user=subscription.user, resource=resource, amount=amount, datetime=when)
        for amount, when in [
            (30, subscription.start + timedelta(days=2)),
            (30, subscription.start + timedelta(days=4)),
            (30, subscription.start + timedelta(days=6)),
            (30, subscription.start + timedelta(days=9)),
        ]
    ])

    assert remains(at=subscription.start) == 100
    assert remains(at=subscription.start + timedelta(days=3)) == 70
    assert remains(at=subscription.start + timedelta(days=4, hours=12)) == 40
    assert remains(at=subscription.start + timedelta(days=5)) == 140
    assert remains(at=subscription.start + timedelta(days=6)) == 110
    assert remains(at=subscription.start + timedelta(days=7)) == 10
    assert remains(at=subscription.start + timedelta(days=9)) == -20


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
    subscription.end = subscription.start + timedelta(days=10)
    subscription.save(update_fields=['end'])

    Quota.objects.create(
        plan=subscription.plan,
        resource=resource,
        limit=100,
        recharge_period=timedelta(days=5),
        burns_in=timedelta(days=7),
    )

    Usage.objects.create(
        user=subscription.user,
        resource=resource,
        amount=150,
        datetime=subscription.start + timedelta(days=6),
    )

    assert remains(at=subscription.start + timedelta(days=5)) == 200
    assert remains(at=subscription.start + timedelta(days=6)) == 50
    assert remains(at=subscription.start + timedelta(days=7)) == 50
    assert remains(at=subscription.start + timedelta(days=10)) == 0
