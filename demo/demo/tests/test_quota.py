from datetime import timedelta

from payments.helpers import get_remaining
from payments.models import Quota


def test_quota_without_subscription(db, plan, resource, remains, now):
    Quota.objects.create(
        plan=plan,
        resource=resource,
        limit=100,
    )

    assert remains(at=now) == 0


def test_quota_without_usage(db, subscription, resource, remains):
    """
                     Subscription
    --------------[================]------------> time
    quota:    0   100            100   0
    """
    subscription.end = subscription.start + timedelta(days=30)
    subscription.save(update_fields=['end'])

    Quota.objects.create(
        plan=subscription.plan,
        resource=resource,
        limit=100,
    )

    assert remains(at=subscription.start - timedelta(seconds=1)) == 0
    assert remains(at=subscription.start) == 100
    assert remains(at=subscription.end) == 100
    assert remains(at=subscription.end + timedelta(seconds=1)) == 0


def test_quota_recharge(db, subscription, resource, remains):
    """
                   Subscription
    ----------[=========================]-------------> time
              ^           ^           ^
              recharge    recharge    recharge
    quota: 0  100         200         300     0
    """
    subscription.end = subscription.start + timedelta(days=30)
    subscription.save(update_fields=['end'])

    Quota.objects.create(
        plan=subscription.plan,
        resource=resource,
        limit=100,
        recharge_period=timedelta(days=9),
    )

    assert remains(at=subscription.start - timedelta(seconds=1)) == 0
    assert remains(at=subscription.start) == 100
    assert remains(at=subscription.start + timedelta(days=9)) == 200
    assert remains(at=subscription.start + timedelta(days=18)) == 300
    assert remains(at=subscription.end) == 0


def test_quota_burn(db, subscription, resource, remains):
    """
                   Subscription
    ----------[=========================]-------------> time

    quota 1:  [----------------]
              recharge (+100)  burn

    quota 2:               [-----------------]
                           recharge (+100)   burn

    total: 0  100          200  100      0
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

    assert remains(at=subscription.start - timedelta(seconds=1)) == 0
    assert remains(at=subscription.start) == 100
    assert remains(at=subscription.start + timedelta(days=5)) == 200
    assert remains(at=subscription.start + timedelta(days=7)) == 100
    assert remains(at=subscription.start + timedelta(days=10)) == 0
    assert remains(at=subscription.start + timedelta(days=15)) == 0
