from datetime import timedelta

import pytest
from dateutil.relativedelta import relativedelta
from django.utils.timezone import now
from freezegun import freeze_time
from more_itertools import one

from subscriptions.v0.models import INFINITY, Subscription, SubscriptionPayment, get_trial_period
from subscriptions.v0.tasks import check_unfinished_payments

from ..helpers import days


@pytest.mark.django_db(databases=["actual_db"])
def test__get_trial_period__disabled(plan, user):
    assert get_trial_period(user, plan) == relativedelta()


@pytest.mark.django_db(databases=["actual_db"])
def test__get_trial_period__no_charge_amount(trial_period, plan, user):
    plan.charge_amount *= 0
    plan.save()

    assert get_trial_period(user, plan) == relativedelta()


@pytest.mark.django_db(databases=["actual_db"])
def test__get_trial_period__not_recurring(trial_period, plan, user):
    plan.charge_period = INFINITY
    plan.save()

    assert get_trial_period(user, plan) == relativedelta()


@pytest.mark.django_db(databases=["actual_db"])
def test__get_trial_period__already_paid(trial_period, plan, user):
    payment = SubscriptionPayment.objects.create(
        user=user,
        plan=plan,
        provider_codename="some",
        paid_since=now(),
        paid_until=now() + plan.charge_period,
    )
    assert get_trial_period(user, plan) == trial_period

    payment.status = SubscriptionPayment.Status.COMPLETED
    payment.save()
    assert get_trial_period(user, plan) == relativedelta()


@pytest.mark.django_db(databases=["actual_db"])
def test__get_trial_period__had_no_recurring(trial_period, plan, user):
    assert get_trial_period(user, plan) == trial_period

    Subscription.objects.create(plan=plan, user=user)
    assert get_trial_period(user, plan) == relativedelta()


@pytest.mark.django_db(databases=["actual_db"])
@pytest.mark.skip()
def test__get_trial_period__cheating__multiacc__paddle(
    trial_period,
    plan,
    user,
    other_user,
    client,
    paddle,
    card_number,
):
    raise NotImplementedError()  # TODO

    assert not Subscription.objects.exists()

    # ---- pay as "user" ----
    client.force_login(user)
    response = client.post("/api/subscribe/", {"plan": plan.pk, "provider": paddle.codename})
    assert response.status_code == 200, response.content

    result = response.json()
    redirect_url = result.pop("redirect_url")
    input(f"Enter card {card_number} here: {redirect_url}\nThen press Enter")

    check_unfinished_payments(within=timedelta(hours=1))
    payment = SubscriptionPayment.objects.latest()
    assert payment.status == SubscriptionPayment.Status.COMPLETED
    assert payment.amount == plan.charge_amount * 0
    assert payment.subscription.start + trial_period == payment.subscription.end
    assert payment.subscription.start == payment.paid_since

    # ---- pay as "other_user" with same credit card ----
    client.force_login(other_user)
    response = client.post("/api/subscribe/", {"plan": plan.pk, "provider": paddle.codename})
    assert response.status_code == 200, response.content

    result = response.json()
    redirect_url = result.pop("redirect_url")
    input(f"Enter SAME CARD DETAILS here: {redirect_url}\nThen press Enter")

    check_unfinished_payments(within=timedelta(hours=1))
    payment = SubscriptionPayment.objects.latest()
    assert payment.status == SubscriptionPayment.Status.COMPLETED
    assert payment.amount == plan.charge_amount * 0
    assert payment.subscription.start + trial_period == payment.subscription.end
    assert payment.subscription.start == payment.paid_since


@pytest.mark.django_db(databases=["actual_db"], transaction=True)
def test__trial_period__only_once__subsequent(trial_period, dummy, plan, user, user_client):
    assert user.subscriptions.active().count() == 0

    # create new subscription
    response = user_client.post("/api/subscribe/", {"plan": plan.pk, "provider": dummy.codename})
    assert response.status_code == 200, response.content
    response = user_client.post(
        "/api/webhook/dummy/",
        {
            "transaction_id": SubscriptionPayment.objects.latest().provider_transaction_id,
        },
    )
    assert response.status_code == 200, response.content
    assert user.subscriptions.active().count() == 1

    subscription = user.subscriptions.latest()
    assert subscription.payments.count() == 1
    payment = subscription.payments.first()
    assert payment.amount == plan.charge_amount * 0
    assert payment.paid_since + trial_period == payment.paid_until

    # end subscription
    response = user_client.delete(f"/api/subscriptions/{subscription.uid}/")
    assert response.status_code == 204, response.content
    assert user.subscriptions.latest().auto_prolong is False

    # create another subscription and ensure no trial period is there
    response = user_client.post("/api/subscribe/", {"plan": plan.pk, "provider": dummy.codename})
    assert response.status_code == 200, response.content
    assert user.subscriptions.active().count() == 1

    subscription = user.subscriptions.latest()
    assert subscription.payments.count() == 1
    payment = subscription.payments.latest()
    assert payment.amount == plan.charge_amount
    assert payment.paid_since + plan.charge_period == payment.paid_until


@pytest.mark.django_db(databases=["actual_db"], transaction=True)
def test__trial_period__only_once__simultaneous(
    settings, trial_period, dummy, plan, bigger_plan, recharge_plan, user, user_client
):
    settings.SUBSCRIPTIONS_VALIDATORS = [
        "subscriptions.v0.validators.OnlyEnabledPlans",
        "subscriptions.v0.validators.AtLeastOneRecurringSubscription",
    ]

    assert user.subscriptions.active().count() == 0

    # create new subscription
    response = user_client.post("/api/subscribe/", {"plan": plan.pk, "provider": dummy.codename})
    assert response.status_code == 200, response.content
    response = user_client.post(
        "/api/webhook/dummy/",
        {
            "transaction_id": SubscriptionPayment.objects.latest().provider_transaction_id,
        },
    )
    assert response.status_code == 200, response.content
    assert user.subscriptions.active().count() == 1

    subscription = user.subscriptions.latest()
    payment = one(subscription.payments.all())
    assert payment.amount == plan.charge_amount * 0
    assert payment.paid_since + trial_period == payment.paid_until

    # add resources and ensure no trial period is there
    response = user_client.post("/api/subscribe/", {"plan": recharge_plan.pk, "provider": dummy.codename})
    assert response.status_code == 200, response.content
    assert user.subscriptions.active().count() == 2

    subscription = user.subscriptions.latest()
    payment = one(subscription.payments.all())
    assert payment.amount == recharge_plan.charge_amount
    assert payment.paid_since + recharge_plan.charge_period == payment.paid_until

    # create another subscription and ensure no trial period is there
    response = user_client.post("/api/subscribe/", {"plan": bigger_plan.pk, "provider": dummy.codename})
    assert response.status_code == 200, response.content
    assert user.subscriptions.active().count() == 3

    subscription = user.subscriptions.latest()
    assert subscription.payments.count() == 1
    payment = subscription.payments.latest()
    assert payment.amount == bigger_plan.charge_amount
    assert payment.paid_since + bigger_plan.charge_period == payment.paid_until


@pytest.mark.django_db(databases=["actual_db"], transaction=True)
def test__get_trial_period__cheating__simultaneous_payments(
    trial_period,
    plan,
    user,
    user_client,
    dummy,
):
    assert not SubscriptionPayment.objects.exists()

    response = user_client.post("/api/subscribe/", {"plan": plan.pk, "provider": dummy.codename})
    assert response.status_code == 200, response.content

    response = user_client.post("/api/subscribe/", {"plan": plan.pk, "provider": dummy.codename})
    assert response.status_code == 200, response.content

    payments = SubscriptionPayment.objects.all()
    assert len(payments) == 2

    for payment in payments:
        payment.status = SubscriptionPayment.Status.COMPLETED
        payment.save()

    assert Subscription.objects.count() == 2
    assert payments[0].subscription.charge_offset == trial_period
    assert payments[1].subscription.charge_offset == relativedelta(0)


@pytest.mark.django_db(databases=["actual_db"], transaction=True)
def test__get_trial_period__not_cheating__multiacc(
    trial_period,
    plan,
    user,
    client,
    other_user,
    dummy,
):
    assert not Subscription.objects.exists()

    client.force_login(user)
    response = client.post("/api/subscribe/", {"plan": plan.pk, "provider": dummy.codename})
    assert response.status_code == 200, response.content

    client.force_login(other_user)
    response = client.post("/api/subscribe/", {"plan": plan.pk, "provider": dummy.codename})
    assert response.status_code == 200, response.content

    payments = SubscriptionPayment.objects.all().order_by("subscription__user_id")
    assert len(payments) == 2
    for payment in payments:
        payment.status = SubscriptionPayment.Status.COMPLETED
        payment.save()

    assert Subscription.objects.count() == 2
    assert payments[0].subscription.charge_offset == trial_period
    assert payments[0].subscription.user == user
    assert payments[1].subscription.charge_offset == trial_period
    assert payments[1].subscription.user == other_user


@pytest.mark.django_db(databases=["actual_db"], transaction=True)
def test__trial_period__full_charge_after_trial(
    dummy, plan, charge_expiring, charge_schedule, user_client, user, trial_period
):
    response = user_client.post("/api/subscribe/", {"plan": plan.pk, "provider": dummy.codename})
    assert response.status_code == 200, response.content

    assert user.subscriptions.count() == 1
    subscription = user.subscriptions.latest()
    payment = subscription.payments.latest()
    payment.status = SubscriptionPayment.Status.COMPLETED
    payment.save()
    assert payment.amount == plan.charge_amount * 0
    assert subscription.start + trial_period == subscription.end

    old_end = subscription.end
    with freeze_time(subscription.end - days(1)):
        charge_expiring()
        assert user.subscriptions.count() == 1
        subscription = user.subscriptions.latest()
        assert subscription.end == old_end + plan.charge_period

        payment = subscription.payments.latest()
        assert payment.paid_until == old_end + plan.charge_period
        assert payment.amount == plan.charge_amount
