from operator import attrgetter

from demo.tests.utils import days
from payments.functions import get_subscriptions_involved
from payments.models import Quota, Subscription


def test_subscriptions_involved(user, plan, now, resource):
    """
    Subscriptions:                    |now
    ----------------------------------[====sub1=====]-----> overlaps with "now"
    --------------------[======sub2=======]---------------> overlaps with "sub1"
    -------------[=sub3=]---------------------------------> overlaps with "sub2"
    -----------------------[=sub4=]-----------------------> overlaps with "sub2"
    ----[=sub5=]------------------------------------------> does not overlap with anything
    """

    sub1 = Subscription.objects.create(user=user, plan=plan, start=now - days(5), end=now + days(2))
    sub2 = Subscription.objects.create(user=user, plan=plan, start=sub1.start - days(5), end=sub1.start + days(2))
    sub3 = Subscription.objects.create(user=user, plan=plan, start=sub2.start - days(5), end=sub2.start)
    sub4 = Subscription.objects.create(user=user, plan=plan, start=sub2.start + days(1), end=sub1.start - days(1))
    _ = Subscription.objects.create(user=user, plan=plan, start=sub3.start - days(5), end=sub3.start - days(1))

    subscriptions_involved = get_subscriptions_involved(user=user, at=now, resource=resource)
    assert list(subscriptions_involved) == []

    Quota.objects.create(plan=plan, resource=resource, limit=100)
    subscriptions_involved = get_subscriptions_involved(user=user, at=now, resource=resource)
    assert sorted(subscriptions_involved, key=attrgetter('start')) == [sub2, sub4, sub1]
