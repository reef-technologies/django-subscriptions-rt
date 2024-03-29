import logging
import re
from datetime import timedelta

import pytest
from dateutil.relativedelta import relativedelta
from django.utils.timezone import now
from freezegun import freeze_time
from more_itertools import one

from subscriptions.exceptions import PaymentError
from subscriptions.fields import relativedelta_to_dict
from subscriptions.functions import use_resource
from subscriptions.models import Subscription, SubscriptionPayment, Usage
from subscriptions.providers import get_providers

from .helpers import datetime_to_api, days


@pytest.mark.django_db(databases=['actual_db'])
def test__api__plans(plan, client):
    response = client.get('/api/plans/')
    assert response.status_code == 200
    assert response.json() == [
        {
            'id': plan.id,
            'codename': plan.codename,
            'name': plan.name,
            'charge_amount': plan.charge_amount.amount,
            'charge_amount_currency': str(plan.charge_amount.currency),
            'charge_period': relativedelta_to_dict(plan.charge_period),
            'max_duration': relativedelta_to_dict(plan.max_duration),
            'is_recurring': plan.is_recurring(),
            'metadata': {
                'this': 'that',
            },
        },
    ]


# def test_payment_providers(client):
#     response = client.get('/api/payment-providers/')
#     assert response.status_code == 200
#     assert response.json() == {
#         'providers': [
#             {'name': 'dummy'},
#         ],
#     }


@pytest.mark.django_db(databases=['actual_db'])
def test__api__subscriptions__unauthorized(client, two_subscriptions):
    response = client.get('/api/subscriptions/')
    assert response.status_code == 403


@pytest.mark.django_db(databases=['actual_db'])
def test__api__subscriptions__authorized(user_client, two_subscriptions):
    response = user_client.get('/api/subscriptions/')
    assert response.status_code == 200, response.content
    subscription = two_subscriptions[0]
    assert response.json() == [{
        'id': subscription.id,
        'start': datetime_to_api(subscription.start),
        'end': datetime_to_api(subscription.end),
        'quantity': 1,
        'next_charge_date': None,
        'payment_provider_class': None,
        'plan': {
            'id': subscription.plan.id,
            'codename': subscription.plan.codename,
            'name': subscription.plan.name,
            'charge_amount': subscription.plan.charge_amount and subscription.plan.charge_amount.amount,
            'charge_amount_currency': str(subscription.plan.charge_amount.currency) if subscription.plan.charge_amount else 'USD',
            'charge_period': relativedelta_to_dict(subscription.plan.charge_period),
            'max_duration': relativedelta_to_dict(subscription.plan.max_duration),
            'is_recurring': subscription.plan.is_recurring(),
            'metadata': {},
        }
    }]


@pytest.mark.django_db(databases=['actual_db'])
def test__api__subscriptions__next_charge_date(user_client, subscription):
    subscription.end = now() + relativedelta(days=90)
    subscription.save()

    with freeze_time(subscription.start):
        response = user_client.get('/api/subscriptions/')
        assert response.status_code == 200, response.content
        assert response.json()[0]['next_charge_date'] == datetime_to_api(subscription.start)

    with freeze_time(subscription.start + timedelta(days=1)):
        response = user_client.get('/api/subscriptions/')
        assert response.status_code == 200, response.content
        assert response.json()[0]['next_charge_date'] == datetime_to_api(subscription.start + relativedelta(days=30))

    with freeze_time(subscription.start + timedelta(days=31)):
        response = user_client.get('/api/subscriptions/')
        assert response.status_code == 200, response.content
        assert response.json()[0]['next_charge_date'] == datetime_to_api(subscription.start + relativedelta(days=60))


@pytest.mark.django_db(databases=['actual_db'])
def test__api__subscriptions__next_charge_date__not_prolong(user_client, subscription):
    subscription.end = now() + relativedelta(days=90)
    subscription.save()

    with freeze_time(subscription.start):
        response = user_client.get('/api/subscriptions/')
        assert response.status_code == 200, response.content
        assert response.json()[0]['next_charge_date'] == datetime_to_api(subscription.start)

    subscription.auto_prolong = False
    subscription.save()

    with freeze_time(subscription.start):
        response = user_client.get('/api/subscriptions/')
        assert response.status_code == 200, response.content
        assert response.json()[0]['next_charge_date'] is None


@pytest.mark.django_db(databases=['actual_db'])
def test__api__subscribe__unauthorized(client, plan):
    response = client.post('/api/subscribe/', {'plan': plan.id})
    assert response.status_code == 403


@pytest.mark.django_db(databases=['actual_db'])
def test__api__subscribe__authorized(client, user_client, plan, dummy):
    response = user_client.post('/api/subscribe/', {'plan': plan.id, 'quantity': 2})
    assert response.status_code == 200, response.content
    result = response.json()
    assert result['plan'] == plan.id
    assert result['payment_id'] == SubscriptionPayment.objects.latest().id
    assert result['quantity'] == 2
    assert result['redirect_url'].startswith('/payment/')
    assert result['background_charge_succeeded'] is False

    response = user_client.get('/api/subscriptions/')
    assert response.status_code == 200, response.content
    subscriptions = response.json()
    assert len(subscriptions) == 0

    # manually invoke webhook
    payment = SubscriptionPayment.objects.latest()
    response = client.post('/api/webhook/dummy/', {'transaction_id': payment.provider_transaction_id})
    assert response.status_code == 200, response.content

    response = user_client.get('/api/subscriptions/')
    assert response.status_code == 200, response.content
    subscriptions = response.json()
    assert len(subscriptions) == 1
    subscription = subscriptions[0]
    subscription_start = Subscription.objects.first().start
    assert now() - timedelta(seconds=5) < subscription_start < now()
    assert subscription['start'] == datetime_to_api(subscription_start)
    assert subscription['end'] == datetime_to_api(subscription_start + plan.charge_period)
    assert subscription['quantity'] == 2


@pytest.mark.django_db(databases=['actual_db'])
def test__api__webhook_logging(client, caplog):
    with caplog.at_level(logging.INFO):
        client.post('/api/webhook/dummy/', {'webhook-key': 'webhook-value'})
    assert re.search(r"INFO .+? Webhook at http://testserver/api/webhook/dummy/ received payload {'webhook-key': 'webhook-value'}", caplog.text)


@pytest.mark.django_db(databases=['actual_db'])
def test__api__resources__initial(user_client, subscription, resource, quota):
    with freeze_time(subscription.start):
        response = user_client.get('/api/resources/')
        assert response.status_code == 200, response.content
        assert response.json() == {resource.codename: quota.limit * subscription.quantity}


@pytest.mark.django_db(databases=['actual_db'])
def test__api__resources__usage(user, user_client, subscription, resource, quota):
    with freeze_time(subscription.start + days(1)):
        Usage.objects.create(
            user=user,
            resource=resource,
            amount=20,
        )

    with freeze_time(subscription.start + days(2)):
        response = user_client.get('/api/resources')
        assert response.status_code == 200, response.content
        assert response.json() == {resource.codename: quota.limit * subscription.quantity - 20}


@pytest.mark.django_db(databases=['actual_db'])
def test__api__resources__expiration(user_client, subscription, resource, quota):
    with freeze_time(subscription.start + quota.burns_in - days(1)):
        response = user_client.get('/api/resources')
        assert response.status_code == 200, response.content
        assert response.json() == {resource.codename: quota.limit * subscription.quantity}

    with freeze_time(subscription.start + quota.burns_in):
        response = user_client.get('/api/resources/')
        assert response.status_code == 200, response.content
        assert response.json() == {}


@pytest.mark.django_db(databases=['actual_db'])
def test__api__recurring_plan_switch(user, user_client, subscription, payment, bigger_plan):
    with freeze_time(subscription.start):
        assert one(user.subscriptions.active()).plan == subscription.plan

    with freeze_time(subscription.start + days(2)):
        response = user_client.post('/api/subscribe/', {'plan': bigger_plan.id})
        assert response.status_code == 200, response.content

    with freeze_time(subscription.start + days(2) + timedelta(seconds=1)):
        assert one(user.subscriptions.active()).plan == bigger_plan


@pytest.mark.django_db(databases=['actual_db'])
def test__api__recharge_plan_subscription(client, user_client, subscription, quota, recharge_plan, recharge_quota, resource):
    with freeze_time(subscription.start + days(2)):
        response = user_client.post('/api/subscribe/', {'plan': recharge_plan.id})
        assert response.status_code == 200, response.content
        result = response.json()
        assert result['plan'] == recharge_plan.id
        assert result['quantity'] == 1
        assert result['payment_id'] == SubscriptionPayment.objects.latest().id
        assert result['redirect_url'].startswith('/payment/')
        assert result['background_charge_succeeded'] is False

        transaction_id = SubscriptionPayment.objects.latest().provider_transaction_id
        response = client.post('/api/webhook/dummy/', {'transaction_id': transaction_id})
        assert response.status_code == 200, response.content

    with freeze_time(subscription.start + days(3)):
        response = user_client.get('/api/resources/')
        assert response.status_code == 200, response.content
        assert response.json() == {
            resource.codename: subscription.plan.quotas.last().limit * subscription.quantity + recharge_quota.limit,
        }


@pytest.mark.django_db(databases=['actual_db'])
def test__background_charge(subscription):
    with freeze_time(subscription.start + days(1)):
        payment = SubscriptionPayment.objects.create(
            provider_codename=get_providers()[0].codename,
            provider_transaction_id='0000',
            amount=subscription.plan.charge_amount,
            user=subscription.user,
            plan=subscription.plan,
            subscription=subscription,
        )

    with freeze_time(subscription.start + days(2)):
        with pytest.raises(PaymentError, match='no previous successful payment'):
            subscription.charge_offline()

    payment.status = SubscriptionPayment.Status.COMPLETED
    payment.save()

    with freeze_time(subscription.start + days(2)):
        subscription.charge_offline()


@pytest.mark.django_db(databases=['actual_db'])
def test__api__payment(user_client, payment):
    response = user_client.get(f'/api/payments/{payment.id}/')
    assert response.status_code == 200, response.content
    assert response.json() == {
        "id": payment.id,
        "status": "completed",
        "subscription": {
            "id": payment.subscription.id,
            "plan": {
                "id": payment.subscription.plan.id,
                "codename": "plan",
                "name": "Plan",
                "charge_amount": 100.0,
                "charge_amount_currency": "USD",
                "charge_period": {
                    "days": 30
                },
                "max_duration": {
                    "days": 120
                },
                "is_recurring": True,
                "metadata": {
                    "this": "that"
                },
            },
            "quantity": 2,
            "start": datetime_to_api(payment.subscription.start),
            "end": datetime_to_api(payment.subscription.end),
            "next_charge_date": datetime_to_api(next(payment.subscription.iter_charge_dates(since=now()))),
            "payment_provider_class": "DummyProvider",
        },
        "quantity": 2,
        "amount": 100.0,
        "currency": "USD",
        "total": 200.0,
        "paid_from": datetime_to_api(payment.subscription_start),
        "paid_to": datetime_to_api(payment.subscription_end),
        "created": datetime_to_api(payment.created),
    }


@pytest.mark.django_db(databases=['actual_db'])
def test__api__resource_headers_mixin__anonymous(client, resource):
    response = client.get('/api/headers_mixin/')
    assert response.status_code == 200
    assert not any(header.startswith('X-Resource-') for header in response.headers)


@pytest.mark.django_db(databases=['actual_db'])
def test__api__resource_headers_mixin__empty(user_client, resource):
    response = user_client.get('/api/headers_mixin/')
    assert response.status_code == 200
    assert f'X-Resource-{resource.codename}' not in response.headers


@pytest.mark.django_db(databases=['actual_db'])
def test__api__resource_headers_mixin__exists(user, user_client, resource, subscription, quota):
    available = quota.limit * subscription.quantity

    with freeze_time(subscription.start):
        response = user_client.get('/api/headers_mixin/')
        assert response.status_code == 200
        assert response.headers[f'X-Resource-{resource.codename}'] == str(available)

    with freeze_time(subscription.start + days(1)):
        with use_resource(user, resource, 10):
            response = user_client.get('/api/headers_mixin/')
            assert response.status_code == 200
            assert response.headers[f'X-Resource-{resource.codename}'] == str(available - 10)


@pytest.mark.django_db(databases=['actual_db'])
def test__api__subscriptions__cancel__dummy(user, user_client, subscription, payment, dummy):
    subscription.end = subscription.start + relativedelta(days=90)
    subscription.auto_prolong = True
    subscription.save()

    with freeze_time(subscription.end + timedelta(days=1)):
        response = user_client.delete(f'/api/subscriptions/{subscription.uid}/')
        assert response.status_code == 404, response.content
        assert user.subscriptions.active().count() == 0

    with freeze_time(subscription.start + timedelta(days=1)):
        response = user_client.delete(f'/api/subscriptions/{subscription.uid}/')
        assert response.status_code == 204, response.content

    with freeze_time(subscription.start + timedelta(days=1, seconds=1)):
        assert user.subscriptions.active().count() == 1
        assert user.subscriptions.latest().end == subscription.start + relativedelta(days=90)
        assert user.subscriptions.latest().auto_prolong is False

    with freeze_time(subscription.end + timedelta(seconds=1)):
        assert user.subscriptions.active().count() == 0
