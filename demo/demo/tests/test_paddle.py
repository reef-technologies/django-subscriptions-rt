import json
from datetime import timedelta

import pytest
from dateutil.relativedelta import relativedelta
from django.test.client import MULTIPART_CONTENT
from django.utils.timezone import now
from djmoney.money import Money
from freezegun import freeze_time
from tenacity import Retrying, TryAgain, stop_after_attempt, wait_incrementing

from subscriptions.exceptions import BadReferencePayment
from subscriptions.models import Plan, Subscription, SubscriptionPayment
from subscriptions.providers import get_provider
from subscriptions.providers.paddle import PaddleProvider
from subscriptions.tasks import check_unfinished_payments
from subscriptions.utils import fromisoformat


def test__paddle__payment_flow__regular(paddle, user_client, plan, card_number):
    response = user_client.post('/api/subscribe/', {'plan': plan.id})
    assert response.status_code == 200, response.content

    result = response.json()

    redirect_url = result.pop('redirect_url')
    assert 'paddle.com' in redirect_url

    payment = SubscriptionPayment.objects.latest()
    assert result == {
        'plan': plan.id,
        'quantity': 1,
        'background_charge_succeeded': False,
        'payment_id': payment.id,
    }

    assert 'payment_url' in payment.metadata

    # TODO: automate this
    input(f'Use card {card_number} to pay here: {redirect_url}\nThen press Enter')

    # ensure that status didn't change because webhook didn't go through
    assert payment.status == SubscriptionPayment.Status.PENDING

    # ---- test_payment_status_endpoint_get ----
    response = user_client.get(f'/api/payments/{payment.id}/')
    assert response.status_code == 200, response.content

    result = response.json()
    assert result == {
        'id': payment.id,
        'status': 'pending',
        'quantity': 1,
        'amount': float(payment.amount.amount),
        'currency': str(payment.amount.currency),
        'total': float((payment.amount * 1).amount),
        'paid_from': None,
        'paid_to': None,
        'created': payment.created.isoformat().replace('+00:00', 'Z'),
        'subscription': None,
    }

    # ---- test_payment_status_endpoint_post ----
    for attempt in Retrying(wait=wait_incrementing(start=2, increment=2), stop=stop_after_attempt(10)):
        with attempt:
            response = user_client.post(f'/api/payments/{payment.id}/')
            assert response.status_code == 200, response.content
            result = response.json()
            if result['status'] != 'completed':
                raise TryAgain()

    payment = SubscriptionPayment.objects.get(pk=payment.pk)

    assert result == {
        'id': payment.id,
        'status': 'completed',
        'quantity': 1,
        'amount': float(payment.amount.amount),
        'currency': str(payment.amount.currency),
        'total': float((payment.amount * 1).amount),
        'paid_from': payment.subscription_start.isoformat().replace('+00:00', 'Z'),
        'paid_to': payment.subscription_end.isoformat().replace('+00:00', 'Z'),
        'created': payment.created.isoformat().replace('+00:00', 'Z'),
        'subscription': {
            'id': payment.subscription.id,
            'quantity': 1,
            'start': payment.subscription.start.isoformat().replace('+00:00', 'Z'),
            'end': payment.subscription.end.isoformat().replace('+00:00', 'Z'),
            'next_charge_date': next(payment.subscription.iter_charge_dates(since=now())).isoformat().replace('+00:00', 'Z'),
            'plan': {
                'charge_amount': 100,
                'charge_amount_currency': 'USD',
                'charge_period': {'days': 30},
                'codename': 'plan',
                'id': plan.id,
                'is_recurring': True,
                'max_duration': {'days': 120},
                'metadata': {'this': 'that'},
                'name': 'Plan',
            },
        },
    }

    # ---- test_check_unfinished_payments ----
    payment = SubscriptionPayment.objects.latest()
    payment.status = SubscriptionPayment.Status.PENDING
    payment.save()

    check_unfinished_payments(within=timedelta(hours=1))
    payment = SubscriptionPayment.objects.latest()
    assert payment.status == SubscriptionPayment.Status.COMPLETED

    # ---- test_charge_offline ----
    assert 'subscription_id' in payment.metadata
    payment.subscription.charge_offline()
    assert SubscriptionPayment.objects.count() == 2

    last_payment = SubscriptionPayment.objects.latest()
    subscription = last_payment.subscription

    assert last_payment.provider_codename == payment.provider_codename
    provider = get_provider(last_payment.provider_codename)
    assert last_payment.amount == provider.get_amount(
        user=last_payment.user,
        plan=plan,
    )
    assert last_payment.quantity == subscription.quantity
    assert last_payment.user == subscription.user
    assert last_payment.subscription == subscription
    assert last_payment.plan == plan

    # check subsequent offline charge
    payment.subscription.charge_offline()


def test__paddle__payment_flow__trial_period(trial_period, paddle, user, user_client, plan, card_number):
    assert not user.subscriptions.exists()

    response = user_client.post('/api/subscribe/', {'plan': plan.id})
    assert response.status_code == 200, response.content

    result = response.json()

    redirect_url = result.pop('redirect_url')
    assert 'paddle.com' in redirect_url

    payment = SubscriptionPayment.objects.latest()
    assert result == {
        'plan': plan.id,
        'quantity': 1,
        'background_charge_succeeded': False,
        'payment_id': payment.id,
    }

    assert 'payment_url' in payment.metadata

    assert user.subscriptions.count() == 1
    subscription = user.subscriptions.first()
    assert subscription.start == subscription.end
    assert subscription.initial_charge_offset == trial_period

    # TODO: automate this
    input(f'Enter card {card_number} here: {redirect_url}\nThen press Enter')

    # ensure that status didn't change because webhook didn't go through
    assert payment.status == SubscriptionPayment.Status.PENDING

    # ---- test_check_unfinished_payments ----
    payment = SubscriptionPayment.objects.latest()
    payment.status = SubscriptionPayment.Status.PENDING
    payment.save()

    check_unfinished_payments(within=timedelta(hours=1))
    payment = SubscriptionPayment.objects.latest()
    assert payment.status == SubscriptionPayment.Status.COMPLETED
    assert payment.amount == plan.charge_amount * 0
    assert payment.subscription.start + trial_period == payment.subscription.end
    assert payment.subscription.start == payment.subscription_start

    # ---- test_charge_offline ----
    assert 'subscription_id' in payment.metadata
    payment.subscription.charge_offline()
    assert SubscriptionPayment.objects.count() == 2

    last_payment = SubscriptionPayment.objects.latest()
    subscription = last_payment.subscription

    assert last_payment.provider_codename == payment.provider_codename
    provider = get_provider(last_payment.provider_codename)
    assert last_payment.amount == provider.get_amount(
        user=last_payment.user,
        plan=plan,
    )
    assert last_payment.quantity == subscription.quantity
    assert last_payment.user == subscription.user
    assert last_payment.subscription == subscription
    assert last_payment.plan == plan

    # check subsequent offline charge
    payment.subscription.charge_offline()


def test__paddle__webhook(paddle, client, user_client, paddle_unconfirmed_payment, paddle_webhook_payload):
    response = user_client.get('/api/subscriptions/')
    assert response.status_code == 200, response.content
    assert len(response.json()) == 0

    webhook_time = now() + timedelta(hours=2)
    with freeze_time(webhook_time):
        response = client.post('/api/webhook/paddle/', paddle_webhook_payload)
        assert response.status_code == 200, response.content

    with freeze_time(webhook_time + timedelta(hours=1)):
        response = user_client.get('/api/subscriptions/')
        assert response.status_code == 200, response.content
        subscriptions = response.json()
        assert len(subscriptions) == 1

        # check that subscription started when webhook arrived
        subscription = subscriptions[0]
        start = fromisoformat(subscription['start'])
        assert start - webhook_time < timedelta(seconds=10)

        # check that subscription lasts as much as stated in plan description
        end = fromisoformat(subscription['end'])
        assert start + paddle_unconfirmed_payment.plan.charge_period == end


def test__paddle__webhook_idempotence(paddle, client, paddle_unconfirmed_payment, paddle_webhook_payload):
    assert not Subscription.objects.all().exists()

    response = client.post('/api/webhook/paddle/', paddle_webhook_payload)
    assert response.status_code == 200, response.content
    start_old, end_old = Subscription.objects.values_list('start', 'end').latest()

    response = client.post('/api/webhook/paddle/', paddle_webhook_payload)
    assert response.status_code == 200, response.content
    start_new, end_new = Subscription.objects.values_list('start', 'end').latest()

    assert start_old == start_new
    assert end_old == end_new


def test__paddle__webhook_payload_as_form_data(paddle, client, paddle_unconfirmed_payment, paddle_webhook_payload):
    assert not Subscription.objects.all().exists()

    response = client.post('/api/webhook/paddle/', paddle_webhook_payload, content_type=MULTIPART_CONTENT)
    assert response.status_code == 200, response.content

    payment = SubscriptionPayment.objects.get(pk=paddle_unconfirmed_payment.pk)
    assert not isinstance(payment.metadata['subscription_id'], list)


def test__paddle__webhook_non_existing_payment(paddle, client, paddle_unconfirmed_payment, paddle_webhook_payload, settings):
    paddle_webhook_payload['passthrough'] = json.dumps({
        "subscription_payment_id": "84e9a5a1-cbca-4af5-a7b7-719f8f2fb772",
    })

    response = client.post('/api/webhook/paddle/', paddle_webhook_payload)
    assert response.status_code == 200, response.content


def test__paddle__subscription_charge_online_avoid_duplicates(paddle, user_client, plan):
    assert not SubscriptionPayment.objects.all().exists()

    response = user_client.post('/api/subscribe/', {'plan': plan.id})
    assert response.status_code == 200, response.content
    assert SubscriptionPayment.objects.count() == 1
    payment = SubscriptionPayment.objects.last()
    payment_url = payment.metadata['payment_url']

    response = user_client.post('/api/subscribe/', {'plan': plan.id})
    assert response.status_code == 200, response.content
    assert SubscriptionPayment.objects.count() == 1  # additinal payment was not created
    assert SubscriptionPayment.objects.last().metadata['payment_url'] == payment_url  # url hasn't changed


def test__paddle__subscription_charge_online_new_payment_after_duplicate_lookup_time(paddle, user_client, plan):
    assert not SubscriptionPayment.objects.all().exists()

    response = user_client.post('/api/subscribe/', {'plan': plan.id})
    assert response.status_code == 200, response.content
    assert SubscriptionPayment.objects.count() == 1
    payment = SubscriptionPayment.objects.last()

    payment.created = now() - PaddleProvider.ONLINE_CHARGE_DUPLICATE_LOOKUP_TIME
    payment.save()

    response = user_client.post('/api/subscribe/', {'plan': plan.id})
    assert response.status_code == 200, response.content
    assert SubscriptionPayment.objects.count() == 2


def test__paddle__subscription_charge_online_new_payment_if_no_pending(paddle, user_client, plan):
    assert not SubscriptionPayment.objects.all().exists()

    response = user_client.post('/api/subscribe/', {'plan': plan.id})
    assert response.status_code == 200, response.content
    assert SubscriptionPayment.objects.count() == 1
    payment = SubscriptionPayment.objects.last()

    payment.status = SubscriptionPayment.Status.ERROR
    payment.save()

    response = user_client.post('/api/subscribe/', {'plan': plan.id})
    assert response.status_code == 200, response.content
    assert SubscriptionPayment.objects.count() == 2


def test__paddle__subscription_charge_online_new_payment_if_no_payment_url(paddle, user_client, plan):
    assert not SubscriptionPayment.objects.all().exists()

    response = user_client.post('/api/subscribe/', {'plan': plan.id})
    assert response.status_code == 200, response.content
    assert SubscriptionPayment.objects.count() == 1
    payment = SubscriptionPayment.objects.last()

    payment.metadata = {'foo': 'bar'}
    payment.save()

    response = user_client.post('/api/subscribe/', {'plan': plan.id})
    assert response.status_code == 200, response.content
    assert SubscriptionPayment.objects.count() == 2


def test__paddle__reference_payment_non_matching_currency(paddle, user_client, paddle_unconfirmed_payment):
    paddle_unconfirmed_payment.status = SubscriptionPayment.Status.COMPLETED
    paddle_unconfirmed_payment.save()

    other_currency_plan = Plan.objects.create(
        codename='other',
        name='Other',
        charge_amount=Money(30, 'EUR'),
        charge_period=relativedelta(days=30),
    )

    provider = get_provider()
    with pytest.raises(BadReferencePayment):
        provider.charge_offline(
            user=paddle_unconfirmed_payment.user,
            plan=other_currency_plan,
        )


def test__paddle__subscription_charge_offline_zero_amount(paddle, user_client, paddle_unconfirmed_payment):
    paddle_unconfirmed_payment.status = SubscriptionPayment.Status.COMPLETED
    paddle_unconfirmed_payment.save()

    assert SubscriptionPayment.objects.count() == 1

    free_plan = Plan.objects.create(
        codename='other',
        name='Other',
        charge_amount=None,
        charge_period=relativedelta(days=30),
    )

    provider = get_provider()
    provider.charge_offline(
        user=paddle_unconfirmed_payment.user,
        plan=free_plan,
    )
    assert SubscriptionPayment.objects.count() == 2
    last_payment = SubscriptionPayment.objects.order_by('subscription_end').last()
    assert last_payment.plan == free_plan
    assert last_payment.amount is None
