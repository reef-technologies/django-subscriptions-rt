import pytest

from subscriptions.models import Subscription, SubscriptionPayment
from subscriptions.tasks import check_duplicated_payments


@pytest.mark.django_db(databases=["actual_db"])
def test__duplicates__no_duplicates_in_transaction_id(user, plan):
    subscription = Subscription.objects.create(
        user=user,
        plan=plan,
    )
    SubscriptionPayment.objects.create(
        user=user,
        plan=plan,
        subscription=subscription,
        provider_codename="test-1",
        provider_transaction_id="transaction-1",
    )
    SubscriptionPayment.objects.create(
        user=user,
        plan=plan,
        subscription=subscription,
        provider_codename="test-1",
        provider_transaction_id="transaction-2",
    )

    results = check_duplicated_payments()
    assert len(results) == 0


@pytest.mark.django_db(databases=["actual_db"])
def test__duplicates__no_duplicates_in_providers(user, plan):
    subscription = Subscription.objects.create(
        user=user,
        plan=plan,
    )
    SubscriptionPayment.objects.create(
        user=user,
        plan=plan,
        subscription=subscription,
        provider_codename="test-1",
        provider_transaction_id="transaction-1",
    )
    SubscriptionPayment.objects.create(
        user=user,
        plan=plan,
        subscription=subscription,
        provider_codename="test-2",
        provider_transaction_id="transaction-1",
    )

    results = check_duplicated_payments()
    assert len(results) == 0


@pytest.mark.django_db(databases=["actual_db"])
def test__duplicates__duplicated_transactions(user, plan):
    subscription = Subscription.objects.create(
        user=user,
        plan=plan,
    )
    payment_1 = SubscriptionPayment.objects.create(
        user=user,
        plan=plan,
        subscription=subscription,
        provider_codename="test-1",
        provider_transaction_id="transaction-1",
    )
    payment_2 = SubscriptionPayment.objects.create(
        user=user,
        plan=plan,
        subscription=subscription,
        provider_codename="test-1",
        provider_transaction_id="transaction-1",
    )
    SubscriptionPayment.objects.create(
        user=user,
        plan=plan,
        subscription=subscription,
        provider_codename="test-1",
        provider_transaction_id="transaction-2",
    )
    SubscriptionPayment.objects.create(
        user=user,
        plan=plan,
        subscription=subscription,
        provider_codename="test-1",
        provider_transaction_id="transaction-3",
    )

    results = check_duplicated_payments()
    assert len(results) == 1
    assert ("test-1", "transaction-1") in results
    entries = results[("test-1", "transaction-1")]
    assert {payment_1.uid, payment_2.uid} == set([entry.uid for entry in entries])
