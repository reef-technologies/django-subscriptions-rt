from operator import attrgetter

from payments.helpers import get_subscriptions_involved
from payments.models import Subscription

from .utils import days


def test_subscriptions_involved(user, plan, now):
    """
    Subscriptions:                    |now
    -----------------------[====sub1=====]-----> overlaps with "now"
    ---------[======sub2=======]---------------> overlaps with "sub1"
    --[=sub3=]---------------------------------> does not overlap with "sub2"
    ------------[=sub4=]-----------------------> overlaps with "sub2"
    """

    sub1 = Subscription.objects.create(user=user, plan=plan, start=now - days(5), end=now + days(2))
    sub2 = Subscription.objects.create(user=user, plan=plan, start=sub1.start - days(5), end=sub1.start + days(2))
    _ = Subscription.objects.create(user=user, plan=plan, start=sub2.start - days(5), end=sub2.start)
    sub4 = Subscription.objects.create(user=user, plan=plan, start=sub2.start + days(1), end=sub1.start - days(1))

    subscriptions_involved = get_subscriptions_involved(at=now)
    assert sorted(subscriptions_involved, key=attrgetter('start')) == [sub2, sub4, sub1]
