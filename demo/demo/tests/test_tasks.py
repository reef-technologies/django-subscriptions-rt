from datetime import timedelta
from typing import List

from freezegun import freeze_time
from subscriptions.models import SubscriptionPayment


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


def test_charging_based_on_previous_attempt_status(subscription, payment, now, charge_expiring, charge_schedule):
    assert SubscriptionPayment.objects.count() == 1

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

        # make previous charge period have COMPLETED attempt;
        # don't call `save()` to not trigger subscription end date shift
        SubscriptionPayment.objects.filter(pk=payment.pk).update(
            status=SubscriptionPayment.Status.COMPLETED,
        )

        # check that new charge period doesn't try to charge
        charge_expiring()
        assert SubscriptionPayment.objects.count() == 2
        assert SubscriptionPayment.objects.last() == payment

        # make previous charge period have ERROR attempt;
        # don't call `save()` to not trigger subscription end date shift
        SubscriptionPayment.objects.filter(pk=payment.pk).update(
            status=SubscriptionPayment.Status.ERROR,
        )

        # check that new charge period DOES charge
        charge_expiring()
        assert SubscriptionPayment.objects.count() == 3
        assert SubscriptionPayment.objects.last() != payment


# def test_not_charging_if_previous_attempt_succeeded


# def test_not_reacting_to_other_payments


# def test_charge_amount()


# def test_prolongation_after_charge()
