from datetime import datetime, timedelta
from time import sleep

from django.utils.timezone import now
from freezegun import freeze_time
from subscriptions.models import Subscription, SubscriptionPayment
from subscriptions.providers import get_provider
from subscriptions.providers.paddle import PaddleProvider
from subscriptions.tasks import check_unfinished_payments


def test_provider(paddle):
    assert isinstance(get_provider(), PaddleProvider)


def test_payment_flow(paddle, user_client, plan, card_number):
    response = user_client.post('/api/subscribe/', {'plan': plan.id})
    assert response.status_code == 200, response.content

    result = response.json()

    redirect_url = result.pop('redirect_url')
    assert 'paddle.com' in redirect_url

    payment = SubscriptionPayment.objects.last()
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
    if payment.status == SubscriptionPayment.Status.COMPLETED:
        payment.status == SubscriptionPayment.Status.PENDING
        payment.save()

    response = user_client.get(f'/api/payments/{payment.id}/')
    assert response.status_code == 200, response.content

    result = response.json()
    assert result == {
        'id': payment.id,
        'status': 'pending',
    }
    sleep(2)

    # ---- test_payment_status_endpoint_post ----
    if payment.status == SubscriptionPayment.Status.COMPLETED:
        payment.status == SubscriptionPayment.Status.PENDING
        payment.save()

    response = user_client.post(f'/api/payments/{payment.id}/')
    assert response.status_code == 200, response.content

    result = response.json()
    assert result == {
        'id': payment.id,
        'status': 'completed',
    }

    # ---- test_check_unfinished_payments ----
    payment = SubscriptionPayment.objects.last()
    payment.status = SubscriptionPayment.Status.PENDING
    payment.save()

    check_unfinished_payments(within=timedelta(hours=1))
    payment = SubscriptionPayment.objects.last()
    assert payment.status == SubscriptionPayment.Status.COMPLETED

    # ---- test_charge_offline ----
    payment.subscription.charge_offline()
    assert SubscriptionPayment.objects.count() == 2

    last_payment = SubscriptionPayment.objects.last()
    subscription = last_payment.subscription

    assert last_payment.provider_codename == payment.provider_codename
    assert last_payment.amount == plan.charge_amount
    assert last_payment.quantity == subscription.quantity
    assert last_payment.user == subscription.user
    assert last_payment.subscription == subscription
    assert last_payment.plan == plan

    # check subsequent offline charge
    payment.subscription.charge_offline()


def test_webhook(paddle, client, user_client, paddle_unconfirmed_payment, paddle_webhook_payload):
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
        start = datetime.fromisoformat(subscription['start'].replace('Z', '+00:00'))
        assert start - webhook_time < timedelta(seconds=10)

        # check that subscription lasts as much as stated in plan description
        end = datetime.fromisoformat(subscription['end'].replace('Z', '+00:00'))
        assert start + paddle_unconfirmed_payment.plan.charge_period == end


def test_webhook_idempotence(paddle, client, paddle_unconfirmed_payment, paddle_webhook_payload):
    assert not Subscription.objects.all().exists()

    response = client.post('/api/webhook/paddle/', paddle_webhook_payload)
    assert response.status_code == 200, response.content
    start_old, end_old = Subscription.objects.values_list('start', 'end').last()

    response = client.post('/api/webhook/paddle/', paddle_webhook_payload)
    assert response.status_code == 200, response.content
    start_new, end_new = Subscription.objects.values_list('start', 'end').last()

    assert start_old == start_new
    assert end_old == end_new
