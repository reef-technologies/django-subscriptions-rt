from datetime import timedelta
from django.utils.timezone import now
from more_itertools import one

from subscriptions.models import Subscription, SubscriptionPayment


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
    assert payment.subscription_end == subscription.end == subscription.start + plan.charge_period

    # shrink the payment and ensure that subscription is not shrinked
    """
    Subscription: -----[===============]------------------------>
    Payment:      -----[=COMPLETED=]------------------------>
    """
    payment.subscription_end -= plan.charge_period / 3
    payment.save()
    subscription = one(Subscription.objects.all())
    assert subscription.start == payment.subscription_start
    assert subscription.end != payment.subscription_end
    assert subscription.end == subscription.start + plan.charge_period
