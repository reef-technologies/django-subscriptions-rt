from datetime import timedelta

from django.utils.timezone import now
from more_itertools import one

from subscriptions.models import Subscription, SubscriptionPayment

from .helpers import days


def test__models__payment__sync_with_subscription(plan, user, dummy):
    assert not Subscription.objects.exists()

    # add a payment and ensure that unconfirmed payment doesn't create a subscriptionn
    """
    Subscription: ---------------------------------------------->
    Payment:      -----?---------------------------------------->
    """
    payment = SubscriptionPayment.objects.create(
        user=user,
        plan=plan,
        provider_codename=dummy.codename,
        provider_transaction_id='12345',
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
    assert subscription.start == payment.subscription_start
    assert now() - timedelta(seconds=1) < payment.subscription_start < now()
    initial_subscription_start = subscription.start
    assert payment.subscription_end == subscription.end == initial_subscription_start + plan.charge_period

    # shrink the payment and ensure that subscription is not shrinked
    """
    Subscription: -----[===============]------------------------>
    Payment:      -----[=COMPLETED=]------------------------>
    """
    payment.subscription_end -= days(2)
    payment.save()
    subscription = one(Subscription.objects.all())
    assert subscription.start == payment.subscription_start == initial_subscription_start
    assert subscription.end > payment.subscription_end
    assert subscription.end == initial_subscription_start + plan.charge_period

    # enlarge the payment and ensure that subscription is enlarged as well
    """
    Subscription: -----[===================]------------------------>
    Payment:      -----[=====COMPLETED=====]------------------------>
    """
    payment.subscription_end = subscription.end + days(2)
    payment.save()
    subscription = one(Subscription.objects.all())
    assert subscription.start == payment.subscription_start == initial_subscription_start
    assert subscription.end == payment.subscription_end == initial_subscription_start + plan.charge_period + days(2)

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
        provider_transaction_id='12346',
        status=SubscriptionPayment.Status.COMPLETED,
    )
    assert SubscriptionPayment.objects.count() == 2
    previous_payment = subscription.payments.earliest()

    assert payment.subscription_start == previous_payment.subscription_end
    assert payment.subscription_end == initial_subscription_start + 2 * plan.charge_period

    subscription = one(Subscription.objects.all())
    assert subscription.start == initial_subscription_start
    assert subscription.end == payment.subscription_end