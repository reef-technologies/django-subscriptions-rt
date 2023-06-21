import logging
import re
from datetime import timedelta
from django.utils.timezone import now

import pytest
from freezegun import freeze_time
from dateutil.relativedelta import relativedelta

from subscriptions.exceptions import PaymentError
from subscriptions.fields import relativedelta_to_dict
from subscriptions.functions import use_resource
from subscriptions.models import SubscriptionPayment, Usage
from subscriptions.providers import get_providers

from .helpers import days, datetime_to_api


def test_plans(plan, client):
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


def test_unauthorized_subscriptions(client, two_subscriptions):
    response = client.get('/api/subscriptions/')
    assert response.status_code == 403


def test_subscriptions(user_client, two_subscriptions, now):
    with freeze_time(now + timedelta(seconds=1)):
        response = user_client.get('/api/subscriptions/')
        assert response.status_code == 200, response.content
        subscription = two_subscriptions[0]
        assert response.json() == [{
            'id': subscription.id,
            'start': datetime_to_api(subscription.start),
            'end': datetime_to_api(subscription.end),
            'quantity': 1,
            'next_charge_date': None,
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


def test_subscriptions__next_charge_date(user_client, subscription, now):
    subscription.end = now + relativedelta(days=90)
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


def test_unauthorized_subscribe(client, plan):
    response = client.post('/api/subscribe/', {'plan': plan.id})
    assert response.status_code == 403


def test_subscribe(client, user_client, plan, now):
    with freeze_time(now):
        response = user_client.post('/api/subscribe/', {'plan': plan.id, 'quantity': 2})
        assert response.status_code == 200, response.content
        assert response.json() == {
            'plan': plan.id,
            'payment_id': SubscriptionPayment.objects.latest().id,
            'quantity': 2,
            'redirect_url': '/subscribe/success',
            'background_charge_succeeded': True,
        }

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
        assert subscription['start'] == datetime_to_api(now)
        assert subscription['end'] == datetime_to_api(now + plan.charge_period)
        assert subscription['quantity'] == 2


def test__webhook_logging(client, caplog):
    with caplog.at_level(logging.INFO):
        client.post('/api/webhook/dummy/', {'webhook-key': 'webhook-value'})
    assert re.search(r"INFO .+? Webhook at http://testserver/api/webhook/dummy/ received payload {'webhook-key': 'webhook-value'}", caplog.text)


def test_resources(user_client, subscription, resource, quota, now):
    with freeze_time(now):
        response = user_client.get('/api/resources/')
        assert response.status_code == 200, response.content
        assert response.json() == {resource.codename: quota.limit * subscription.quantity}


def test_resources_usage(user, user_client, subscription, resource, quota, now):
    with freeze_time(now + days(1)):
        Usage.objects.create(
            user=user,
            resource=resource,
            amount=20,
        )

    with freeze_time(now + days(2)):
        response = user_client.get('/api/resources')
        assert response.status_code == 200, response.content
        assert response.json() == {resource.codename: quota.limit * subscription.quantity - 20}


def test_resources_expiration(user_client, subscription, resource, now, quota):
    with freeze_time(now + quota.burns_in - days(1)):
        response = user_client.get('/api/resources')
        assert response.status_code == 200, response.content
        assert response.json() == {resource.codename: quota.limit * subscription.quantity}

    with freeze_time(now + quota.burns_in):
        response = user_client.get('/api/resources/')
        assert response.status_code == 200, response.content
        assert response.json() == {}


def test_recurring_plan_switch(user_client, subscription, bigger_plan, now):
    with freeze_time(now + days(2)):
        response = user_client.post('/api/subscribe/', {'plan': bigger_plan.id})
        assert response.status_code == 403, response.content
        assert response.json() == {'detail': ''}  # TODO {'detail': 'Too many recurring subscriptions'}


def test_recharge_plan_subscription(client, user_client, subscription, quota, recharge_plan, recharge_quota, now, resource):
    with freeze_time(now + days(2)):
        response = user_client.post('/api/subscribe/', {'plan': recharge_plan.id})
        assert response.status_code == 200, response.content
        assert response.json() == {
            'plan': recharge_plan.id,
            'quantity': 1,
            'payment_id': SubscriptionPayment.objects.latest().id,
            'redirect_url': '/subscribe/success',
            'background_charge_succeeded': True,
        }

        transaction_id = SubscriptionPayment.objects.latest().provider_transaction_id
        response = client.post('/api/webhook/dummy/', {'transaction_id': transaction_id})
        assert response.status_code == 200, response.content

    with freeze_time(now + days(3)):
        response = user_client.get('/api/resources/')
        assert response.status_code == 200, response.content
        assert response.json() == {
            resource.codename: subscription.plan.quotas.last().limit * subscription.quantity + recharge_quota.limit,
        }


def test_background_charge(subscription, now):
    with freeze_time(now + days(1)):
        payment = SubscriptionPayment.objects.create(
            provider_codename=get_providers()[0].codename,
            provider_transaction_id='0000',
            amount=subscription.plan.charge_amount,
            user=subscription.user,
            plan=subscription.plan,
            subscription=subscription,
        )

    with freeze_time(now + days(2)):
        with pytest.raises(PaymentError, match='no previous successful payment'):
            subscription.charge_offline()

    payment.status = SubscriptionPayment.Status.COMPLETED
    payment.save()

    with freeze_time(now + days(2)):
        subscription.charge_offline()


def test_payments(user_client, payment):
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
        },
        "quantity": 2,
        "amount": 100.0,
        "currency": "USD",
        "total": 200.0,
        "paid_from": datetime_to_api(payment.subscription_start),
        "paid_to": datetime_to_api(payment.subscription_end),
        "created": datetime_to_api(payment.created),
    }


def test__api__resource_headers_mixin__anonymous(client, resource):
    response = client.get('/api/headers_mixin/')
    assert response.status_code == 200
    assert not any(header.startswith('X-Resource-') for header in response.headers)


def test__api__resource_headers_mixin__empty(user_client, resource):
    response = user_client.get('/api/headers_mixin/')
    assert response.status_code == 200
    assert f'X-Resource-{resource.codename}' not in response.headers


def test__api__resource_headers_mixin__exists(user, user_client, resource, subscription, quota, now):
    available = quota.limit * subscription.quantity

    with freeze_time(now):
        response = user_client.get('/api/headers_mixin/')
        assert response.status_code == 200
        assert response.headers[f'X-Resource-{resource.codename}'] == str(available)

    with freeze_time(now + days(1)):
        with use_resource(user, resource, 10):
            response = user_client.get('/api/headers_mixin/')
            assert response.status_code == 200
            assert response.headers[f'X-Resource-{resource.codename}'] == str(available - 10)


def test_subscriptions__cancel(user, user_client, subscription, now):
    subscription.start = now
    subscription.end = now + relativedelta(days=90)
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
        assert user.subscriptions.active().count() == 0
        assert user.subscriptions.last().end == subscription.start + timedelta(days=1)
        assert user.subscriptions.last().auto_prolong is False
