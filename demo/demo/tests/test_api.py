from datetime import datetime

import pytest
from freezegun import freeze_time
from subscriptions.exceptions import PaymentError
from subscriptions.fields import relativedelta_to_dict
from subscriptions.models import SubscriptionPayment, Usage
from subscriptions.providers import get_providers


def datetime_to_api(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def test_plans(plan, client):
    response = client.get('/api/plans/')
    assert response.status_code == 200
    assert response.json() == [
        {
            'id': plan.id,
            'codename': plan.codename,
            'name': plan.name,
            'charge_amount': int(plan.charge_amount.amount),
            'charge_amount_currency': str(plan.charge_amount.currency),
            'charge_period': relativedelta_to_dict(plan.charge_period),
            'max_duration': relativedelta_to_dict(plan.max_duration),
            'is_recurring': plan.is_recurring(),
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
    with freeze_time(now):
        response = user_client.get('/api/subscriptions/')
        assert response.status_code == 200, response.content
        subscription = two_subscriptions[0]
        assert response.json() == [{
            'id': subscription.id,
            'start': datetime_to_api(subscription.start),
            'end': datetime_to_api(subscription.end),
            'plan': {
                'id': subscription.plan.id,
                'codename': subscription.plan.codename,
                'name': subscription.plan.name,
                'charge_amount': subscription.plan.charge_amount and int(subscription.plan.charge_amount.amount),
                'charge_amount_currency': str(subscription.plan.charge_amount.currency) if subscription.plan.charge_amount else 'USD',
                'charge_period': relativedelta_to_dict(subscription.plan.charge_period),
                'max_duration': relativedelta_to_dict(subscription.plan.max_duration),
                'is_recurring': subscription.plan.is_recurring(),
            }
        }]


def test_unauthorized_subscribe(client, plan):
    response = client.post('/api/subscribe/', {'plan': plan.id})
    assert response.status_code == 403


def test_subscribe(client, user_client, plan, now):
    with freeze_time(now):
        response = user_client.post('/api/subscribe/', {'plan': plan.id})
        assert response.status_code == 200, response.content
        assert response.json() == {
            'plan': plan.id,
            'redirect_url': '/subscribe/success',
        }

        response = user_client.get('/api/subscriptions/')
        assert response.status_code == 200, response.content
        subscriptions = response.json()
        assert len(subscriptions) == 0

        # manually invoke webhook
        payment = SubscriptionPayment.objects.last()
        response = client.post('/api/webhook/dummy/', {'transaction_id': payment.provider_transaction_id})
        assert response.status_code == 200, response.content

        response = user_client.get('/api/subscriptions/')
        assert response.status_code == 200, response.content
        subscriptions = response.json()
        assert len(subscriptions) == 1
        subscription = subscriptions[0]
        assert subscription['start'] == datetime_to_api(now)
        assert subscription['end'] == datetime_to_api(now + plan.charge_period)


def test_resources(user_client, subscription, resource, quota, now):
    with freeze_time(now):
        response = user_client.get('/api/resources/')
        assert response.status_code == 200, response.content
        assert response.json() == {resource.codename: quota.limit}


def test_resources_usage(user, user_client, subscription, resource, quota, now, days):
    with freeze_time(now + days(1)):
        Usage.objects.create(
            user=user,
            resource=resource,
            amount=20,
        )

    with freeze_time(now + days(2)):
        response = user_client.get('/api/resources')
        assert response.status_code == 200, response.content
        assert response.json() == {resource.codename: quota.limit - 20}


def test_resources_expiration(user_client, subscription, resource, now, quota, days):
    with freeze_time(now + quota.burns_in - days(1)):
        response = user_client.get('/api/resources')
        assert response.status_code == 200, response.content
        assert response.json() == {resource.codename: quota.limit}

    with freeze_time(now + quota.burns_in):
        response = user_client.get('/api/resources/')
        assert response.status_code == 200, response.content
        assert response.json() == {}


def test_recurring_plan_switch(user_client, subscription, bigger_plan, now, days):
    with freeze_time(now + days(2)):
        response = user_client.post('/api/subscribe/', {'plan': bigger_plan.id})
        assert response.status_code == 403, response.content
        assert response.json() == {'detail': 'Too many recurring subscriptions'}


def test_recharge_plan_subscription(client, user_client, subscription, quota, recharge_plan, recharge_quota, now, days, resource):
    with freeze_time(now + days(2)):
        response = user_client.post('/api/subscribe/', {'plan': recharge_plan.id})
        assert response.status_code == 200, response.content
        assert response.json() == {
            'plan': recharge_plan.id,
            'redirect_url': '/subscribe/success',
        }

        transaction_id = SubscriptionPayment.objects.last().provider_transaction_id
        response = client.post('/api/webhook/dummy/', {'transaction_id': transaction_id})
        assert response.status_code == 200, response.content

    with freeze_time(now + days(3)):
        response = user_client.get('/api/resources/')
        assert response.status_code == 200, response.content
        assert response.json() == {
            resource.codename: subscription.plan.quotas.last().limit + recharge_quota.limit,
        }


def test_background_charge(subscription, days, now):
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
