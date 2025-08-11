import pytest
from django.utils.timezone import now
from more_itertools import one

from subscriptions.v0.models import Subscription, SubscriptionPayment

from ..helpers import days


@pytest.mark.django_db(databases=["actual_db"])
def test__models__payment__sync_with_subscription(plan, user, dummy):
    assert not Subscription.objects.exists()

    # add a payment and ensure that unconfirmed payment doesn't create a subscriptionn
    """
    Subscription: ---------------------------------------------->
    Payment:      -----?---------------------------------------->
    """
    start = now()

    payment = SubscriptionPayment.objects.create(
        user=user,
        plan=plan,
        provider_codename=dummy.codename,
        provider_transaction_id="12345",
        paid_since=start,
        paid_until=start + plan.charge_period,
    )
    assert not Subscription.objects.exists()

    # confirm the payment and ensure that subscriptionn appears
    """
    Subscription: -----[===============]------------------------>
    Payment:      -----[===COMPLETED===]------------------------>
    """
    payment.status = SubscriptionPayment.Status.COMPLETED
    payment.save()
    subscription = one(Subscription.objects.all())
    assert subscription.start == payment.paid_since
    assert payment.paid_until == subscription.end == start + plan.charge_period

    # shrink the payment and ensure that subscription is not shrunk
    """
    Subscription: -----[===============]------------------------>
    Payment:      -----[=COMPLETED=]------------------------>
    """
    payment.paid_until -= days(2)
    payment.save()
    subscription = one(Subscription.objects.all())
    assert subscription.start == payment.paid_since == start
    assert subscription.end > payment.paid_until
    assert subscription.end == start + plan.charge_period

    # enlarge the payment and ensure that subscription is left as-is (since no status change)
    """
    Subscription: -----[===============]---------------------------->
    Payment:      -----[=====COMPLETED=====]------------------------>
    """
    payment.paid_until = subscription.end + days(2)
    payment.save()
    subscription = one(Subscription.objects.all())
    assert subscription.start == payment.paid_since == start
    assert subscription.end == start + plan.charge_period

    # re-set payment status and ensure that subscription is enlarged as well
    """
    Subscription: -----[===================]------------------------>
    Payment:      -----[=====COMPLETED=====]------------------------>
    """
    payment.status = SubscriptionPayment.Status.PENDING
    payment.save()
    payment.status = SubscriptionPayment.Status.COMPLETED
    payment.save()

    subscription = one(Subscription.objects.all())
    assert subscription.start == payment.paid_since == start
    assert subscription.end == payment.paid_until == start + plan.charge_period + days(2)

    # create a second payment and ensure that subscription is extended
    """
    Subscription: -----[==================================]--------->
    Payment:      -----[=====COMPLETED=====][==COMPLETED==]--------->
    """
    payment = SubscriptionPayment.objects.create(
        user=user,
        plan=plan,
        subscription=subscription,
        provider_codename=dummy.codename,
        provider_transaction_id="12346",
        status=SubscriptionPayment.Status.COMPLETED,
        paid_since=subscription.end,
        paid_until=subscription.prolong(),
    )

    assert SubscriptionPayment.objects.count() == 2

    subscription = one(Subscription.objects.all())
    assert subscription.start == start
    assert subscription.end == payment.paid_until


@pytest.mark.django_db(databases=["actual_db"])
def test__models__payment__no_sync_with_subscription(plan, user, dummy, subscription):
    """
    Subscription: -----[===============]------------------------>
    Payment:      ---------------------------------------------->
    """
    subsciption_initial_start = subscription.start
    subscription_initial_end = subscription.end

    """
    Subscription: -----[===============]------------------------>
    Payment:      ----------------------[==PENDING==]----------->
    """
    SubscriptionPayment.objects.create(
        user=user,
        plan=plan,
        subscription=subscription,
        provider_codename=dummy.codename,
        provider_transaction_id="12345",
        status=SubscriptionPayment.Status.PENDING,
        paid_since=subscription.end,
        paid_until=subscription.prolong(),
    )
    subscription = one(Subscription.objects.all())
    assert subscription.start == subsciption_initial_start
    assert subscription.end == subscription_initial_end
