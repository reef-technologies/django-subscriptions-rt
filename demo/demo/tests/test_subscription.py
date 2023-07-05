from datetime import datetime, timedelta
from datetime import timezone as tz
from itertools import islice

import pytest
from dateutil.relativedelta import relativedelta
from django.utils.timezone import now
from subscriptions.exceptions import PaymentError, ProlongationImpossible
from subscriptions.models import Quota, QuotaChunk, Subscription, SubscriptionPayment

from .helpers import days


def test_limited_plan_duration(db, user, plan, now):
    plan.max_duration = days(30)
    plan.charge_period = days(10)
    plan.save(update_fields=['max_duration', 'charge_period'])

    subscription = Subscription.objects.create(
        user=user,
        plan=plan,
        start=now,
    )

    assert subscription.end == now + plan.charge_period

    subscription.end = subscription.prolong()
    assert subscription.end == now + 2 * plan.charge_period

    subscription.end = subscription.prolong()
    assert subscription.end == now + 3 * plan.charge_period

    with pytest.raises(ProlongationImpossible):
        subscription.prolong()


def test_unlimited_plan_duration(db, user, plan, now):
    plan.max_duration = None
    plan.charge_period = days(300)
    plan.save(update_fields=['max_duration'])

    subscription = Subscription.objects.create(
        user=user,
        plan=plan,
        start=now,
    )

    assert subscription.end == now + days(300)

    for i in range(2, 11):
        subscription.end = subscription.prolong()
        assert subscription.end == now + i * days(300)


def test_subscription_charge_dates(db, plan, subscription):
    plan.charge_period = relativedelta(months=1)
    plan.save(update_fields=['charge_period'])

    subscription.start = datetime(2021, 11, 30, 12, 00, 00, tzinfo=tz.utc)
    subscription.end = subscription.start + days(65)
    subscription.save(update_fields=['start', 'end'])

    expected_charge_dates = [
        subscription.start,
        datetime(2021, 12, 30, 12, 00, 00, tzinfo=tz.utc),
        datetime(2022, 1, 30, 12, 00, 00, tzinfo=tz.utc),
        datetime(2022, 2, 28, 12, 00, 00, tzinfo=tz.utc),
        datetime(2022, 3, 30, 12, 00, 00, tzinfo=tz.utc),
    ]

    assert list(islice(subscription.iter_charge_dates(), 3)) == expected_charge_dates[:3]
    assert list(islice(subscription.iter_charge_dates(since=subscription.start), 3)) == expected_charge_dates[:3]
    assert list(islice(subscription.iter_charge_dates(since=subscription.start + days(1)), 3)) == expected_charge_dates[1:4]
    assert list(islice(subscription.iter_charge_dates(since=subscription.start + days(30)), 3)) == expected_charge_dates[1:4]
    assert list(islice(subscription.iter_charge_dates(since=subscription.start + days(31)), 3)) == expected_charge_dates[2:5]
    assert list(islice(subscription.iter_charge_dates(since=subscription.start + days(60)), 3)) == expected_charge_dates[2:5]


def test_subscription_iter_charge_dates_performance(db, subscription, django_assert_num_queries):
    with django_assert_num_queries(0):
        list(islice(subscription.iter_charge_dates(), 10))


def test_subscription_charge_dates_with_no_charge_period(db, plan, subscription, now):
    plan.charge_period = None
    plan.save(update_fields=['charge_period'])
    assert list(subscription.iter_charge_dates()) == [subscription.start]


def test_active_subscription_filter(db, subscription, now):
    subscription.start = now - days(2)
    subscription.end = now - days(1)
    subscription.save(update_fields=['start', 'end'])
    assert subscription not in Subscription.objects.active(at=now)

    subscription.end = now + days(1)
    subscription.save(update_fields=['end'])
    assert subscription in Subscription.objects.active(at=now)


def test_iter_quota_chunks(db, subscription, resource):
    """
                       Subscription
    ----------[=========================]-------------> time

    quota 1:  [----------------]
              recharge (+100)  burn

    quota 2:               [-----------------]
                           recharge (+100)   burn
    """
    subscription.end = subscription.start + days(10)
    subscription.save(update_fields=['end'])

    Quota.objects.create(
        plan=subscription.plan,
        resource=resource,
        limit=50,  # but quantity == 2 -> real limit == 100
        recharge_period=days(5),
        burns_in=days(7),
    )

    start = subscription.start
    chunks = [
        QuotaChunk(resource=resource, start=start, end=start + days(7), amount=100, remains=100),
        QuotaChunk(resource=resource, start=start + days(5), end=start + days(10), amount=100, remains=100),
        QuotaChunk(resource=resource, start=start + days(10), end=start + days(10), amount=100, remains=100),
    ]

    assert list(subscription.iter_quota_chunks(since=start - days(1), until=start - days(1))) == []
    assert list(subscription.iter_quota_chunks(until=start)) == chunks[0:1]
    assert list(subscription.iter_quota_chunks(until=start + days(2))) == chunks[0:1]
    assert list(subscription.iter_quota_chunks(until=start + days(5))) == chunks[0:2]
    assert list(subscription.iter_quota_chunks(until=start + days(11))) == chunks
    assert list(subscription.iter_quota_chunks(since=subscription.end - days(1))) == chunks[1:]


def test_subscription_expiring_performance(django_assert_num_queries, two_subscriptions):
    with django_assert_num_queries(1):
        list(Subscription.objects.expiring(within=days(5)))


def test_subscription_charge_offline_without_prev_payments(db, subscription):
    with pytest.raises(PaymentError):
        subscription.charge_offline()


def test_subscription_charge_offline_with_unconfirmed_payment(db, subscription, paddle_unconfirmed_payment):
    with pytest.raises(PaymentError):
        subscription.charge_offline()


def test_subscription_charge_offline(db, subscription, payment):
    assert SubscriptionPayment.objects.all().count() == 1
    subscription.charge_offline()
    assert SubscriptionPayment.objects.all().count() == 2

    last_payment = SubscriptionPayment.objects.latest()
    assert last_payment.provider_codename == payment.provider_codename
    assert last_payment.amount == subscription.plan.charge_amount
    assert last_payment.quantity == subscription.quantity
    assert last_payment.user == subscription.user
    assert last_payment.subscription == subscription
    assert last_payment.plan == subscription.plan


def test_payment_from_until_auto_set(db, plan, subscription, user, dummy):
    initial_subscription_start = subscription.start
    initial_subscription_end = subscription.end

    payment = SubscriptionPayment.objects.create(
        provider_codename=dummy,
        provider_transaction_id='test',
        status=SubscriptionPayment.Status.PENDING,
        user=user,
        plan=plan,
        subscription=subscription,
        subscription_start=None,
        subscription_end=None,
    )
    # check that PENDING doesn't affect anything
    assert payment.subscription_start is None
    assert payment.subscription_end is None

    # check that paid_from and paid_until should be set / not set together
    payment.status = SubscriptionPayment.Status.COMPLETED
    with pytest.raises(AssertionError):
        payment.subscription_start = initial_subscription_end
        payment.save()

    payment.subscription_start = payment.subscription_end = None
    payment.save()
    # check that paid_from and paid_until are auto-filled
    assert payment.subscription_start == initial_subscription_end
    assert payment.subscription_end > payment.subscription_start
    # check that subscription is prolonged
    assert payment.subscription.start == initial_subscription_start
    assert payment.subscription.end == payment.subscription_end


def test_subscription_auto_creation_on_payment(db, plan, user, dummy):
    assert not Subscription.objects.exists()

    payment = SubscriptionPayment.objects.create(
        provider_codename=dummy,
        provider_transaction_id='test',
        status=SubscriptionPayment.Status.COMPLETED,
        user=user,
        plan=plan,
        subscription_start=None,
        subscription_end=None,
    )
    assert payment.subscription
    assert now() - payment.subscription.start < timedelta(seconds=1)
    assert payment.subscription.end == payment.subscription.start + plan.charge_period

    assert payment.subscription_start == payment.subscription.start
    assert payment.subscription_end == payment.subscription.end


def test_subscription_duration_set_by_payment(db, plan, user, dummy, now):
    assert not Subscription.objects.exists()

    payment = SubscriptionPayment.objects.create(
        provider_codename=dummy,
        provider_transaction_id='test',
        status=SubscriptionPayment.Status.COMPLETED,
        user=user,
        plan=plan,
        subscription_start=now,
        subscription_end=now + days(5),
    )
    assert payment.subscription
    assert payment.subscription.start == payment.subscription_start
    assert payment.subscription.end == payment.subscription_end

    # check that subscription may be prolonged by payment
    payment.subscription_end = now + days(6)
    payment.save()
    assert payment.subscription.end == payment.subscription_end

    # check that subscription cannot be shrinked by shrinked payment
    payment.subscription_end = now + days(3)
    payment.save()
    assert payment.subscription.end == now + days(6)
