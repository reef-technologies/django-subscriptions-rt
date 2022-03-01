from datetime import timedelta

import pytest
from demo.tests.utils import days
from payments.exceptions import NoActiveSubscription
from payments.models import INFINITY, Quota


def test_quota_without_subscription(db, plan, resource, remains, now):
    Quota.objects.create(
        plan=plan,
        resource=resource,
        limit=100,
    )

    with pytest.raises(NoActiveSubscription):
        remains(at=now)


def test_quota_without_usage(db, subscription, resource, remains):
    """
                     Subscription
    --------------[================]------------> time
    quota:    0   100            100   0
    """
    subscription.end = subscription.start + days(30)
    subscription.save(update_fields=['end'])

    Quota.objects.create(
        plan=subscription.plan,
        resource=resource,
        limit=100,
    )

    with pytest.raises(NoActiveSubscription):
        remains(at=subscription.start - timedelta(seconds=1))

    assert remains(at=subscription.start) == 100
    assert remains(at=subscription.start + days(1)) == 100

    with pytest.raises(NoActiveSubscription):
        remains(at=subscription.end)

    with pytest.raises(NoActiveSubscription):
        remains(at=subscription.end + timedelta(seconds=1))


def test_quota_recharge(db, subscription, resource, remains):
    """
                   Subscription
    ----------[=========================]-------------> time
              ^           ^           ^
              recharge    recharge    recharge
    quota: 0  100         200         300     0
    """
    subscription.end = subscription.start + days(30)
    subscription.save(update_fields=['end'])

    Quota.objects.create(
        plan=subscription.plan,
        resource=resource,
        limit=100,
        recharge_period=days(9),
        burns_in=INFINITY,
    )

    with pytest.raises(NoActiveSubscription):
        remains(at=subscription.start - timedelta(seconds=1))

    assert remains(at=subscription.start) == 100
    assert remains(at=subscription.start + days(9)) == 200
    assert remains(at=subscription.start + days(18)) == 300

    with pytest.raises(NoActiveSubscription):
        remains(at=subscription.end)


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
    subscription.end = subscription.start + days(10)
    subscription.save(update_fields=['end'])

    Quota.objects.create(
        plan=subscription.plan,
        resource=resource,
        limit=100,
        recharge_period=days(5),
        burns_in=days(7),
    )

    with pytest.raises(NoActiveSubscription):
        remains(at=subscription.start - timedelta(seconds=1))

    assert remains(at=subscription.start) == 100
    assert remains(at=subscription.start + days(5)) == 200
    assert remains(at=subscription.start + days(7)) == 100

    with pytest.raises(NoActiveSubscription):
        remains(at=subscription.start + days(10))

    with pytest.raises(NoActiveSubscription):
        remains(at=subscription.start + days(15))
