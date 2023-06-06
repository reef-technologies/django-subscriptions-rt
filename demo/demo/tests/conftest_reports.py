import pytest

from subscriptions.models import Subscription, SubscriptionPayment, SubscriptionPaymentRefund
from typing import List

from .helpers import days, usd


@pytest.fixture
def reports_subscriptions(db, user, other_user, plan, bigger_plan, recharge_plan, now) -> List[Subscription]:
    """
    plan (no prolongation)
    -------[============================]x------------------------------->

    bigger plan (we expect prolongation)
    ----------------[========================](========================)->

    x2 plan (other user)
    -------[============================](============================)-->

    x10 recharge plan (other user)
    ------------[==============]x---------------------------------------->

    days:--0----3---7----------17-------30---37-------------------------->
    """
    return [
        Subscription.objects.create(user=user, plan=plan, start=now, auto_prolong=False),
        Subscription.objects.create(user=user, plan=bigger_plan, start=now+days(7)),
        Subscription.objects.create(user=other_user, plan=plan, start=now, quantity=2),
        Subscription.objects.create(user=other_user, plan=recharge_plan, start=now+days(3), auto_prolong=False, quantity=10),
    ]


@pytest.fixture
def reports_payments(db, user, other_user, plan, bigger_plan, now, paddle) -> List[SubscriptionPayment]:
    """
    x2 plan $100, $90, $80, $70, user, COMPLETED
    -------x----------x----------x----------x---------->

    x2 bigger_plan $200, other_user, PENDING, CANCELLED
    ------------------x----------x--------------------->

    x3 plan $?, other_user, COMPLETED
    -----------------------------x--------------------->

    refund $250 for #0, COMPLETED
    ------------------x-------------------------------->

    refund $20 for #4, CANCELLED
    -----------------------------x--------------------->

    days:--0----------10---------20---------30--------->
    """
    Status = SubscriptionPayment.Status

    pmts = [
        SubscriptionPayment.objects.create(created=now, user=user, plan=plan, status=Status.COMPLETED, quantity=2, amount=usd(100), provider_codename=paddle.codename),
        SubscriptionPayment.objects.create(created=now+days(10), user=user, plan=plan, status=Status.COMPLETED, quantity=2, amount=usd(90), provider_codename=paddle.codename),
        SubscriptionPayment.objects.create(created=now+days(20), user=user, plan=plan, status=Status.COMPLETED, quantity=2, amount=usd(80), provider_codename=paddle.codename),
        SubscriptionPayment.objects.create(created=now+days(30), user=user, plan=plan, status=Status.COMPLETED, quantity=2, amount=usd(70), provider_codename=paddle.codename),

        SubscriptionPayment.objects.create(created=now+days(10), user=other_user, plan=bigger_plan, status=Status.PENDING, quantity=2, amount=usd(200), provider_codename=paddle.codename),
        SubscriptionPayment.objects.create(created=now+days(20), user=other_user, plan=bigger_plan, status=Status.CANCELLED, quantity=2, amount=usd(200), provider_codename=paddle.codename),

        SubscriptionPayment.objects.create(created=now+days(20), user=other_user, plan=plan, status=Status.COMPLETED, quantity=3, amount=None, provider_codename=paddle.codename),
    ]

    refunds = [
        SubscriptionPaymentRefund.objects.create(created=now+days(10), original_payment=pmts[0], status=Status.COMPLETED, amount=usd(250), provider_codename=paddle.codename),
        SubscriptionPaymentRefund.objects.create(created=now+days(20), original_payment=pmts[4], status=Status.CANCELLED, amount=usd(20), provider_codename=paddle.codename),
    ]

    return pmts + refunds
