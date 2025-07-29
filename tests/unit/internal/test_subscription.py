from datetime import UTC, datetime, timedelta
from itertools import islice

import pytest
from dateutil.relativedelta import relativedelta
from django.db import connections
from django.utils.timezone import now

from subscriptions.v0.exceptions import PaymentError, ProlongationImpossible
from subscriptions.v0.models import Quota, QuotaChunk, Subscription, SubscriptionPayment

from ..helpers import days


@pytest.mark.django_db(databases=["actual_db"])
def test__subscription__limited_plan_duration(user, plan):
    plan.max_duration = days(30)
    plan.charge_period = days(10)
    plan.save(update_fields=["max_duration", "charge_period"])

    subscription = Subscription.objects.create(
        user=user,
        plan=plan,
    )

    assert subscription.end == subscription.start + plan.charge_period

    subscription.end = subscription.prolong()
    assert subscription.end == subscription.start + 2 * plan.charge_period

    subscription.end = subscription.prolong()
    assert subscription.end == subscription.start + 3 * plan.charge_period

    with pytest.raises(ProlongationImpossible):
        subscription.prolong()


@pytest.mark.django_db(databases=["actual_db"])
def test__subscription__unlimited_plan_duration(user, plan):
    plan.max_duration = None
    plan.charge_period = days(300)
    plan.save(update_fields=["max_duration"])

    subscription = Subscription.objects.create(
        user=user,
        plan=plan,
    )

    assert subscription.end == subscription.start + days(300)

    for i in range(2, 11):
        subscription.end = subscription.prolong()
        assert subscription.end == subscription.start + i * days(300)


@pytest.mark.django_db(databases=["actual_db"])
def test__subscription__iter_charge_dates__main(plan, subscription):
    plan.charge_period = relativedelta(months=1)
    plan.save()

    subscription.start = datetime(2021, 11, 30, 12, 00, 00, tzinfo=UTC)
    subscription.end = subscription.start + days(65)
    subscription.save()

    expected_charge_dates = [
        datetime(2021, 11, 30, 12, 00, 00, tzinfo=UTC),
        datetime(2021, 12, 30, 12, 00, 00, tzinfo=UTC),
        datetime(2022, 1, 30, 12, 00, 00, tzinfo=UTC),
        datetime(2022, 2, 28, 12, 00, 00, tzinfo=UTC),
        datetime(2022, 3, 30, 12, 00, 00, tzinfo=UTC),
    ]

    assert list(islice(subscription.iter_charge_dates(), 3)) == expected_charge_dates[:3]
    assert list(islice(subscription.iter_charge_dates(since=subscription.start), 3)) == expected_charge_dates[:3]
    assert (
        list(islice(subscription.iter_charge_dates(since=subscription.start + days(1)), 3))
        == expected_charge_dates[1:4]
    )
    assert (
        list(islice(subscription.iter_charge_dates(since=subscription.start + days(30)), 3))
        == expected_charge_dates[1:4]
    )
    assert (
        list(islice(subscription.iter_charge_dates(since=subscription.start + days(31)), 3))
        == expected_charge_dates[2:5]
    )
    assert (
        list(islice(subscription.iter_charge_dates(since=subscription.start + days(60)), 3))
        == expected_charge_dates[2:5]
    )


@pytest.mark.django_db(databases=["actual_db"])
def test__subscription__iter_charge_dates__initial_charge_offset(plan, subscription):
    plan.charge_period = relativedelta(months=1)
    plan.save()

    subscription.start = datetime(2021, 11, 30, 12, 00, 00, tzinfo=UTC)
    subscription.end = subscription.start + days(65)
    subscription.initial_charge_offset = relativedelta(days=10)
    subscription.save()

    expected_charge_dates = [
        datetime(2021, 12, 10, 12, 00, 00, tzinfo=UTC),
        datetime(2022, 1, 10, 12, 00, 00, tzinfo=UTC),
        datetime(2022, 2, 10, 12, 00, 00, tzinfo=UTC),
        datetime(2022, 3, 10, 12, 00, 00, tzinfo=UTC),
        datetime(2022, 4, 10, 12, 00, 00, tzinfo=UTC),
    ]

    assert list(islice(subscription.iter_charge_dates(), 5)) == expected_charge_dates


@pytest.mark.django_db(databases=["actual_db"])
def test__subscription__iter_charge_dates__performance(subscription, django_assert_num_queries):
    with django_assert_num_queries(0, connection=connections["actual_db"]):
        list(islice(subscription.iter_charge_dates(), 10))


@pytest.mark.django_db(databases=["actual_db"])
def test__subscription__iter_charge_dates___no_charge_period(plan, subscription):
    plan.charge_period = None
    plan.save(update_fields=["charge_period"])
    assert list(subscription.iter_charge_dates()) == [subscription.start]


@pytest.mark.django_db(databases=["actual_db"])
def test__subscription__active_subscription_filter(subscription):
    now_ = now()

    subscription.start = now_ - days(2)
    subscription.end = now_ - days(1)
    subscription.save(update_fields=["start", "end"])
    assert subscription not in Subscription.objects.active(at=now_)

    subscription.end = now_ + days(1)
    subscription.save(update_fields=["end"])
    assert subscription in Subscription.objects.active(at=now_)


@pytest.mark.django_db(databases=["actual_db"])
def test__subscription__iter_quota_chunks(subscription, resource):
    """
                       Subscription
    ----------[=========================]-------------> time

    quota 1:  [----------------]
              recharge (+100)  burn

    quota 2:               [-----------------]
                           recharge (+100)   burn
    """
    subscription.end = subscription.start + days(10)
    subscription.save(update_fields=["end"])

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


@pytest.mark.django_db(databases=["actual_db"])
def test__subscription__expiring__performance(django_assert_num_queries, two_subscriptions):
    with django_assert_num_queries(1, connection=connections["actual_db"]):
        list(Subscription.objects.expiring(within=days(5)))


@pytest.mark.django_db(databases=["actual_db"])
def test__subscription__charge_offline__without_prev_payments(subscription):
    with pytest.raises(PaymentError):
        subscription.charge_offline()


@pytest.mark.django_db(databases=["actual_db"])
def test__subscription__charge_offline__with_unconfirmed_payment(subscription, paddle_unconfirmed_payment):
    with pytest.raises(PaymentError):
        subscription.charge_offline()


@pytest.mark.django_db(databases=["actual_db"])
def test__subscription__charge_offline(subscription, payment):
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


@pytest.mark.django_db(databases=["actual_db"])
def test__subscription__payment_from_until_auto_set(plan, subscription, user, dummy):
    initial_subscription_start = subscription.start
    initial_subscription_end = subscription.end

    payment = SubscriptionPayment.objects.create(
        provider_codename=dummy,
        provider_transaction_id="test",
        status=SubscriptionPayment.Status.PENDING,
        user=user,
        plan=plan,
        subscription=subscription,
        paid_since=None,
        paid_until=None,
    )
    # check that PENDING doesn't affect anything
    assert payment.paid_since is None
    assert payment.paid_until is None

    # check that paid_since and paid_until should be set / not set together
    payment.status = SubscriptionPayment.Status.COMPLETED
    with pytest.raises(AssertionError):
        payment.paid_since = initial_subscription_end
        payment.save()

    payment.paid_since = payment.paid_until = None
    payment.save()
    # check that paid_since and paid_until are auto-filled
    assert payment.paid_since == initial_subscription_end
    assert payment.paid_until > payment.paid_since
    # check that subscription is prolonged
    assert payment.subscription.start == initial_subscription_start
    assert payment.subscription.end == payment.paid_until


@pytest.mark.django_db(databases=["actual_db"])
def test__subscription__auto_creation_on_payment(plan, user, dummy):
    assert not Subscription.objects.exists()

    payment = SubscriptionPayment.objects.create(
        provider_codename=dummy,
        provider_transaction_id="test",
        status=SubscriptionPayment.Status.COMPLETED,
        user=user,
        plan=plan,
        paid_since=None,
        paid_until=None,
    )
    assert payment.subscription
    assert now() - payment.subscription.start < timedelta(seconds=1)
    assert payment.subscription.end == payment.subscription.start + plan.charge_period

    assert payment.paid_since == payment.subscription.start
    assert payment.paid_until == payment.subscription.end


@pytest.mark.django_db(databases=["actual_db"])
def test__subscription__duration_set_by_payment(plan, user, dummy):
    assert not Subscription.objects.exists()

    now_ = now()

    payment = SubscriptionPayment.objects.create(
        provider_codename=dummy,
        provider_transaction_id="test",
        status=SubscriptionPayment.Status.COMPLETED,
        user=user,
        plan=plan,
        paid_since=now_,
        paid_until=now_ + days(5),
    )
    assert payment.subscription
    assert payment.subscription.start == payment.paid_since
    assert payment.subscription.end == payment.paid_until

    # check that subscription may be prolonged by payment
    payment.paid_until = now_ + days(6)
    payment.save()
    assert payment.subscription.end == payment.paid_until

    # check that subscription cannot be shrunk by shrunk payment
    payment.paid_until = now_ + days(3)
    payment.save()
    assert payment.subscription.end == now_ + days(6)
