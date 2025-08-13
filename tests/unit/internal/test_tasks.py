import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest import mock

import pytest
from django.utils.timezone import now
from freezegun import freeze_time
from more_itertools import spy

from subscriptions.v0.exceptions import PaymentError
from subscriptions.v0.models import Subscription, SubscriptionPayment
from subscriptions.v0.tasks import (
    charge_recurring_subscriptions,
    notify_stuck_pending_payments,
)

from ..helpers import days


def middle(period: list[timedelta]) -> timedelta:
    return (period[0] + period[1]) / 2


@pytest.mark.django_db(databases=["actual_db"])
def test__tasks__charge_expiring__not_charged_beyond_schedule(
    subscription,
    payment,
    charge_expiring,
    charge_schedule,
):
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


@pytest.mark.django_db(databases=["actual_db"])
def test__tasks__charge_expiring__not_charging_twice_in_same_period(
    subscription,
    payment,
    charge_expiring,
    charge_schedule,
):
    assert SubscriptionPayment.objects.count() == 1
    charge_period = charge_schedule[1:3]

    with freeze_time(subscription.end + charge_period[0]):
        charge_expiring(payment_status=SubscriptionPayment.Status.PENDING)
        assert SubscriptionPayment.objects.count() == 2

    with freeze_time(subscription.end + middle(charge_period)):  # middle of charge period
        charge_expiring(payment_status=SubscriptionPayment.Status.PENDING)
        assert SubscriptionPayment.objects.count() == 2


@pytest.mark.django_db(databases=["actual_db"])
def test__tasks__charge_expiring__not_charging_twice_if_pending_exists(
    subscription,
    payment,
    charge_expiring,
    charge_schedule,
):
    assert SubscriptionPayment.objects.count() == 1
    charge_period = charge_schedule[1:3]

    with freeze_time(subscription.end + charge_period[0]):
        pending_payment = SubscriptionPayment.objects.create(
            provider_codename=payment.provider_codename,
            subscription=subscription,
            plan=subscription.plan,
            user=subscription.user,
            status=SubscriptionPayment.Status.PENDING,
            paid_since=payment.paid_since,
            paid_until=payment.paid_until,
        )
        assert SubscriptionPayment.objects.count() == 2

    with freeze_time(subscription.end + charge_period[1]):
        charge_expiring(payment_status=SubscriptionPayment.Status.COMPLETED)
        assert SubscriptionPayment.objects.count() == 2

    pending_payment.status = SubscriptionPayment.Status.CANCELLED
    pending_payment.save()
    with freeze_time(subscription.end + charge_period[1]):
        charge_expiring(payment_status=SubscriptionPayment.Status.COMPLETED)
        assert SubscriptionPayment.objects.count() == 3


@pytest.mark.django_db(transaction=True, databases=["actual_db"])
@pytest.mark.parametrize("enable_advisory_lock", [None, "1", "true", "0", "false"], indirect=True)
def test__tasks__charge_expiring__multiple_threads__not_charge_twice(
    subscription,
    payment,
    charge_schedule,
    enable_advisory_lock,
):
    assert SubscriptionPayment.objects.count() == 1
    charge_period = charge_schedule[1:3]

    num_parallel_threads = 8
    with freeze_time(subscription.end + middle(charge_period)):
        with ThreadPoolExecutor(max_workers=num_parallel_threads) as pool:
            for _ in range(num_parallel_threads):
                pool.submit(charge_recurring_subscriptions, schedule=charge_schedule, num_threads=1)

    if enable_advisory_lock in {None, "1", "true"}:
        assert SubscriptionPayment.objects.count() == 2
    else:
        assert SubscriptionPayment.objects.count() >= 2


@pytest.mark.django_db(databases=["actual_db"])
def test__tasks__charge_expiring__previous_attempt_failed(
    subscription,
    payment,
    charge_expiring,
    charge_schedule,
):
    # make previous charge period have FAILED attempt
    charge_period = charge_schedule[-4:-2]
    with freeze_time(subscription.end + middle(charge_period)):
        charge_expiring(payment_status=SubscriptionPayment.Status.ERROR)
        assert SubscriptionPayment.objects.count() == 2
        payment = SubscriptionPayment.objects.latest()
        assert payment.status == SubscriptionPayment.Status.ERROR
        assert payment.subscription.end == subscription.end

    charge_period = charge_schedule[-3:-1]
    with freeze_time(subscription.end + middle(charge_period)):
        # check that new charge period DOES charge
        charge_expiring()
        assert SubscriptionPayment.objects.count() == 3
        assert SubscriptionPayment.objects.latest() != payment


@pytest.mark.django_db(databases=["actual_db"])
def test__tasks__charge_expiring__not_reacting_to_other_payments(
    subscription,
    payment,
    charge_expiring,
    charge_schedule,
):
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
        assert SubscriptionPayment.objects.latest().pk != other_subscription_payment.pk


@pytest.mark.django_db(databases=["actual_db"])
def test__tasks__charge_expiring__prolongation(
    subscription,
    payment,
    charge_expiring,
    charge_schedule,
):
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


@pytest.mark.django_db(databases=["actual_db"])
def test__tasks__charge_expiring__amount(
    subscription,
    payment,
    charge_expiring,
    charge_schedule,
):
    with freeze_time(subscription.end + charge_schedule[-2]):
        charge_expiring()
        last_payment = subscription.payments.latest()
        assert last_payment != payment
        assert last_payment.quantity == subscription.quantity
        assert last_payment.amount == subscription.plan.charge_amount


@pytest.mark.django_db(databases=["actual_db"])
def test__tasks__charge_expiring__not_charging_after_cancellation(
    subscription,
    payment,
    charge_expiring,
    charge_schedule,
    user_client,
):
    with freeze_time(subscription.end - days(3)):
        response = user_client.delete(f"/api/subscriptions/{subscription.uid}/")
        assert response.status_code == 204, response.content
        old_num_payments = subscription.payments.count()

    with freeze_time(subscription.end - days(2)):
        charge_expiring()
        assert subscription.payments.count() == old_num_payments

    with freeze_time(subscription.end - days(4)):
        charge_expiring()
        assert subscription.payments.count() == old_num_payments


@pytest.mark.django_db(databases=["actual_db"])
def test__tasks__charge_expiring__payment_failure(
    subscription,
    payment,
    charge_schedule,
    dummy,
):
    assert SubscriptionPayment.objects.count() == 1

    def raise_payment_error(*args, **kwargs):
        raise PaymentError(
            "Something went wrong",
            debug_info={
                "subscription": subscription,
                "foo": "bar",
            },
        )

    with freeze_time(subscription.end + charge_schedule[-2], tick=True):
        with mock.patch.object(dummy, "charge_automatically", raise_payment_error):
            charge_recurring_subscriptions(schedule=charge_schedule, num_threads=1)

            assert SubscriptionPayment.objects.count() == 2
            last_payment = SubscriptionPayment.objects.order_by("created").last()
            assert last_payment.status == SubscriptionPayment.Status.ERROR
            assert last_payment.metadata == {
                "subscription": str(subscription.pk),
                "foo": "bar",
            }

            charge_recurring_subscriptions(schedule=charge_schedule, num_threads=1)
            assert SubscriptionPayment.objects.count() == 2


@pytest.mark.django_db(databases=["actual_db"])
def test__tasks__notify_stuck_pending_payments(subscription, user, caplog, dummy):
    min_age = days(3)

    with freeze_time(now() - min_age - days(10)):
        _ = SubscriptionPayment.objects.create(
            provider_codename=dummy,
            plan=subscription.plan,
            user=user,
            status=SubscriptionPayment.Status.PENDING,
            paid_since=now(),
            paid_until=now() + subscription.plan.charge_period,
        )

    with freeze_time(now() - min_age - days(10)):
        very_old_payment = SubscriptionPayment.objects.create(
            provider_codename=dummy,
            subscription=subscription,
            plan=subscription.plan,
            user=user,
            status=SubscriptionPayment.Status.PENDING,
            paid_since=now(),
            paid_until=now() + subscription.plan.charge_period,
        )

    with freeze_time(now() - min_age - timedelta(seconds=1)):
        slightly_old_payment = SubscriptionPayment.objects.create(
            provider_codename=dummy,
            subscription=subscription,
            plan=subscription.plan,
            user=user,
            status=SubscriptionPayment.Status.PENDING,
            paid_since=now(),
            paid_until=now() + subscription.plan.charge_period,
        )

    with freeze_time(now() - min_age + timedelta(seconds=30)):
        _ = SubscriptionPayment.objects.create(
            provider_codename=dummy,
            subscription=subscription,
            plan=subscription.plan,
            user=user,
            status=SubscriptionPayment.Status.PENDING,
            paid_since=now(),
            paid_until=now() + subscription.plan.charge_period,
        )

    with freeze_time(now()):
        _ = SubscriptionPayment.objects.create(
            provider_codename=dummy,
            subscription=subscription,
            plan=subscription.plan,
            user=user,
            status=SubscriptionPayment.Status.PENDING,
            paid_since=now(),
            paid_until=now() + subscription.plan.charge_period,
        )

    with caplog.at_level(logging.ERROR):
        notify_stuck_pending_payments(older_than=min_age)
        assert len(caplog.records) == 2
        assert caplog.records[0].message == f"Payment stuck in pending state: {very_old_payment}"
        assert caplog.records[1].message == f"Payment stuck in pending state: {slightly_old_payment}"
