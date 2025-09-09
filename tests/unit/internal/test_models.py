from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from dateutil.relativedelta import relativedelta
from django.contrib.auth.models import User
from django.forms import ValidationError
from django.utils.timezone import now
from moneyed import Money
from more_itertools import one

from subscriptions.v0.models import Plan, Subscription, SubscriptionPayment

from ..helpers import days


@pytest.mark.django_db(databases=["actual_db"])
def test__plan__immutability(plan, user):
    # w/o subscriptions -> can modify the plan
    assert not plan.subscriptions.exists()
    plan.name = "Plan 7"
    plan.charge_amount = Money(7, "USD")
    plan.charge_period = days(7)
    plan.save()

    Subscription.objects.create(user=user, plan=plan)

    # w/ subscriptions -> can modify name and max duration
    plan.name = "Plan 8"
    plan.max_duration = relativedelta(days=100)
    plan.save()

    # w/ subscriptions -> cannot modify charge amount
    plan.charge_amount = Money(8, "USD")
    with pytest.raises(ValidationError):
        plan.save()

    plan.refresh_from_db()
    plan.charge_period = relativedelta(days=8)
    with pytest.raises(ValidationError):
        plan.save()


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
        quantity=subscription.quantity,
        provider_codename=dummy.codename,
        provider_transaction_id="12345",
        status=SubscriptionPayment.Status.PENDING,
        paid_since=subscription.end,
        paid_until=subscription.prolong(),
    )
    subscription = one(Subscription.objects.all())
    assert subscription.start == subsciption_initial_start
    assert subscription.end == subscription_initial_end


@pytest.mark.django_db(databases=["actual_db"])
def test__payment__clean_subscription__clean__called(plan, user, dummy):
    with patch.object(SubscriptionPayment, "clean") as mock:
        SubscriptionPayment.objects.create(
            user=user,
            plan=plan,
            subscription=None,
            provider_codename=dummy.codename,
            provider_transaction_id="12345",
            status=SubscriptionPayment.Status.PENDING,
            paid_since=now(),
            paid_until=now() + timedelta(days=7),
        )
    mock.assert_called_once()


@pytest.mark.django_db(databases=["actual_db"])
def test__payment__clean_subscription__clean__plan_mismatch(subscription, user, dummy):
    other_plan = Plan.objects.create(name="Other plan")
    with pytest.raises(ValidationError):
        SubscriptionPayment.objects.create(
            user=user,
            plan=other_plan,
            subscription=subscription,
            quantity=subscription.quantity,
            provider_codename=dummy.codename,
            provider_transaction_id="12345",
            status=SubscriptionPayment.Status.PENDING,
            paid_since=now(),
            paid_until=now() + timedelta(days=7),
        )


@pytest.mark.django_db(databases=["actual_db"])
def test__payment__clean_subscription__clean__user_mismatch(subscription, user, dummy):
    other_user = User.objects.create(username="other_user")
    with pytest.raises(ValidationError):
        SubscriptionPayment.objects.create(
            user=other_user,
            plan=subscription.plan,
            quantity=subscription.quantity,
            subscription=subscription,
            provider_codename=dummy.codename,
            provider_transaction_id="12345",
            status=SubscriptionPayment.Status.PENDING,
            paid_since=now(),
            paid_until=now() + timedelta(days=7),
        )


@pytest.mark.django_db(databases=["actual_db"])
def test__payment__clean_subscription__clean__quantity_mismatch(subscription, user, dummy):
    with pytest.raises(ValidationError):
        SubscriptionPayment.objects.create(
            user=subscription.user,
            plan=subscription.plan,
            quantity=subscription.quantity + 1,
            subscription=subscription,
            provider_codename=dummy.codename,
            provider_transaction_id="12345",
            status=SubscriptionPayment.Status.PENDING,
            paid_since=now(),
            paid_until=now() + timedelta(days=7),
        )


@pytest.mark.django_db(databases=["actual_db"])
def test__payment__clean_subscription__clean__period_mismatch(subscription, user, dummy):
    with pytest.raises(ValidationError):
        SubscriptionPayment.objects.create(
            user=subscription.user,
            plan=subscription.plan,
            quantity=subscription.quantity,
            subscription=subscription,
            provider_codename=dummy.codename,
            provider_transaction_id="12345",
            status=SubscriptionPayment.Status.PENDING,
            paid_since=subscription.end + timedelta(seconds=1),
            paid_until=subscription.end + timedelta(days=7),
        )

    with pytest.raises(ValidationError):
        SubscriptionPayment.objects.create(
            user=subscription.user,
            plan=subscription.plan,
            quantity=subscription.quantity,
            subscription=subscription,
            provider_codename=dummy.codename,
            provider_transaction_id="12345",
            status=SubscriptionPayment.Status.PENDING,
            paid_since=subscription.start - timedelta(days=7),
            paid_until=subscription.start - timedelta(seconds=1),
        )

    SubscriptionPayment.objects.create(
        user=subscription.user,
        plan=subscription.plan,
        quantity=subscription.quantity,
        subscription=subscription,
        provider_codename=dummy.codename,
        provider_transaction_id="12345",
        status=SubscriptionPayment.Status.PENDING,
        paid_since=subscription.end,
        paid_until=subscription.end + timedelta(days=7),
    )


@pytest.mark.django_db(databases=["actual_db"])
def test__payment__clean_subscription__clean__bad_period(subscription, user, dummy):
    with pytest.raises(ValidationError):
        SubscriptionPayment.objects.create(
            user=subscription.user,
            plan=subscription.plan,
            quantity=subscription.quantity,
            subscription=subscription,
            provider_codename=dummy.codename,
            provider_transaction_id="12345",
            status=SubscriptionPayment.Status.PENDING,
            paid_since=subscription.end - timedelta(days=1),
            paid_until=subscription.end - timedelta(days=2),
        )


@pytest.mark.django_db(databases=["actual_db"])
def test__get_reference_payment(dummy, plan, user):
    other_user = User.objects.create(username="other_user")

    params = dict(
        user=user,
        plan=plan,
        provider_codename=dummy.codename,
        provider_transaction_id="12345",
        status=SubscriptionPayment.Status.COMPLETED,
        paid_since=now(),
        paid_until=now() + timedelta(days=7),
        created=now(),
        updated=now(),
    )

    SubscriptionPayment.objects.bulk_create(
        [
            SubscriptionPayment(**params | {"uid": uuid4(), "user": other_user}),
            SubscriptionPayment(**params | {"uid": uuid4(), "status": SubscriptionPayment.Status.ERROR}),
            SubscriptionPayment(**params | {"uid": uuid4(), "status": SubscriptionPayment.Status.PENDING}),
            SubscriptionPayment(**params | {"uid": uuid4(), "provider_codename": "other-provider"}),
        ]
    )

    with pytest.raises(SubscriptionPayment.DoesNotExist):
        SubscriptionPayment.get_reference_payment(user=user, provider_codename=dummy.codename)

    SubscriptionPayment.objects.bulk_create(
        [
            SubscriptionPayment(**params | {"uid": uuid4(), "created": now() - timedelta(days=1)}),
            SubscriptionPayment(**params | {"uid": uuid4()}),
        ]
    )
    reference_payment = SubscriptionPayment.get_reference_payment(user=user, provider_codename=dummy.codename)
    assert reference_payment.user == user
    assert reference_payment.provider_codename == dummy.codename
    assert reference_payment.status == SubscriptionPayment.Status.COMPLETED
    assert reference_payment.created > now() - timedelta(minutes=1)
