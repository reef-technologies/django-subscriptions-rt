from datetime import timedelta
from typing import List

from freezegun import freeze_time
from more_itertools import spy
from subscriptions.models import Subscription, SubscriptionPayment


def middle(period: List[timedelta]) -> timedelta:
    return (period[0] + period[1]) / 2


def test_not_charged_beyond_schedule(subscription, payment, now, charge_expiring, charge_schedule):
    initial_end = subscription.end

    max_advance = charge_schedule[0]
    with freeze_time(subscription.end + max_advance - timedelta(minutes=1)):
        charge_expiring()
        assert SubscriptionPayment.objects.count() == 1

    max_post_deadline = charge_schedule[-1]
    with freeze_time(subscription.end + max_post_deadline):
        charge_expiring()
        assert SubscriptionPayment.objects.count() == 1

    assert subscription.end == initial_end

    with freeze_time(subscription.end + max_advance):
        charge_expiring()
        assert SubscriptionPayment.objects.count() == 2


def test_not_charging_twice_in_same_period(subscription, payment, now, charge_expiring, charge_schedule):
    assert SubscriptionPayment.objects.count() == 1
    charge_period = charge_schedule[1:3]

    with freeze_time(subscription.end + charge_period[0]):
        charge_expiring(payment_status=SubscriptionPayment.Status.PENDING)
        assert SubscriptionPayment.objects.count() == 2

    with freeze_time(subscription.end + middle(charge_period)):  # middle of charge period
        charge_expiring(payment_status=SubscriptionPayment.Status.PENDING)
        assert SubscriptionPayment.objects.count() == 2


def test_not_charging_if_previous_attempt_pending(subscription, payment, now, charge_expiring, charge_schedule):
    # make previous charge period have PENDING attempt
    charge_period = charge_schedule[-4:-2]
    with freeze_time(subscription.end + middle(charge_period)):
        charge_expiring(payment_status=SubscriptionPayment.Status.PENDING)
        assert SubscriptionPayment.objects.count() == 2
        payment = SubscriptionPayment.objects.last()
        assert payment.status == SubscriptionPayment.Status.PENDING
        assert payment.subscription.end == subscription.end

    charge_period = charge_schedule[-3:-1]
    with freeze_time(subscription.end + middle(charge_period)):
        # check that new charge period doesn't try to charge
        charge_expiring()
        assert SubscriptionPayment.objects.count() == 2
        assert SubscriptionPayment.objects.last() == payment


def test_not_charging_if_previous_attempt_succeeded(subscription, payment, now, charge_expiring, charge_schedule):
    # make previous charge period have COMPLETED attempt
    charge_period = charge_schedule[-4:-2]
    with freeze_time(subscription.end + middle(charge_period)):
        charge_expiring(payment_status=SubscriptionPayment.Status.COMPLETED)
        assert SubscriptionPayment.objects.count() == 2
        payment = SubscriptionPayment.objects.last()
        assert payment.status == SubscriptionPayment.Status.COMPLETED
        assert payment.subscription.end != subscription.end

    charge_period = charge_schedule[-3:-1]
    with freeze_time(subscription.end + middle(charge_period)):
        # check that new charge period doesn't try to charge
        charge_expiring()
        assert SubscriptionPayment.objects.count() == 2
        assert SubscriptionPayment.objects.last() == payment


def test_charging_if_previous_attempt_failed(subscription, payment, now, charge_expiring, charge_schedule):
    # make previous charge period have FAILED attempt
    charge_period = charge_schedule[-4:-2]
    with freeze_time(subscription.end + middle(charge_period)):
        charge_expiring(payment_status=SubscriptionPayment.Status.ERROR)
        assert SubscriptionPayment.objects.count() == 2
        payment = SubscriptionPayment.objects.last()
        assert payment.status == SubscriptionPayment.Status.ERROR
        assert payment.subscription.end == subscription.end

    charge_period = charge_schedule[-3:-1]
    with freeze_time(subscription.end + middle(charge_period)):
        # check that new charge period DOES charge
        charge_expiring()
        assert SubscriptionPayment.objects.count() == 3
        assert SubscriptionPayment.objects.last() != payment


def test_not_reacting_to_other_payments(subscription, payment, now, charge_expiring, charge_schedule):
    charge_period = charge_schedule[-3:-1]

    # create another payment but for other subscription
    other_subscription = Subscription.objects.get(pk=subscription.pk)
    other_subscription.pk = None
    other_subscription.save()

    other_subscription_payment = SubscriptionPayment.objects.get(pk=payment.pk)
    other_subscription_payment.pk = None
    other_subscription_payment.subscription = other_subscription
    other_subscription_payment.created = subscription.end + charge_period[0]
    other_subscription_payment.status = SubscriptionPayment.Status.COMPLETED
    other_subscription_payment.save()

    with freeze_time(subscription.end + middle(charge_period)):
        charge_expiring()
        assert SubscriptionPayment.objects.count() == 3
        assert SubscriptionPayment.objects.last().pk != other_subscription_payment.pk


def test_prolongation(subscription, payment, now, charge_expiring, charge_schedule):
    charge_dates, _ = spy(subscription.iter_charge_dates(), 6)
    assert subscription.end == charge_dates[2]

    # test no prolongation
    with freeze_time(subscription.end + charge_schedule[-3]):
        charge_expiring(payment_status=SubscriptionPayment.Status.ERROR)
        subscription = Subscription.objects.get(pk=subscription.pk)
        assert subscription.end == charge_dates[2]

    # then prolong after successful payment
    with freeze_time(subscription.end + charge_schedule[-2]):
        charge_expiring()
        subscription = Subscription.objects.get(pk=subscription.pk)
        assert subscription.end == charge_dates[3]

    # then prolong after another successful payment
    with freeze_time(subscription.end + charge_schedule[-2]):
        charge_expiring()
        subscription = Subscription.objects.get(pk=subscription.pk)
        assert subscription.end == charge_dates[4]

    # then fail to prolong because of max plan length
    with freeze_time(subscription.end + charge_schedule[-2]):
        charge_expiring()
        subscription = Subscription.objects.get(pk=subscription.pk)
        assert subscription.end == charge_dates[4]


def test_charge_amount(subscription, payment, now, charge_expiring, charge_schedule):
    with freeze_time(subscription.end + charge_schedule[-2]):
        charge_expiring()
        last_payment = subscription.payments.last()
        assert last_payment != payment
        assert last_payment.quantity == subscription.quantity
        assert last_payment.amount == subscription.plan.charge_amount
