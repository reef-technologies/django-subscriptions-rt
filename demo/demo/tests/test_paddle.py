import json
from datetime import date, timedelta
from urllib import parse

import pytest
import requests
from dateutil.relativedelta import relativedelta
from django.conf import settings
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


def automate_payment(url, card):
    """
    This function replicates all the steps that are done at client level in the paddle payment page.
    """
    email = settings.PADDLE_TEST_EMAIL
    assert email, 'Please configure the `PADDLE_TEST_EMAIL` environment variable.'
    # get the browser info token, which will be used later on.
    browser_info = url.strip('/').split('/')[-1]
    session = requests.Session()

    # hit the redirect url, and start the payment procedure
    payment_page = session.get(url, allow_redirects=True)

    # the checkout id has been initialized.
    payment_page_query = parse.parse_qs(parse.urlsplit(payment_page.url).query)
    checkout_id = payment_page_query['checkout_id'][0]
    assert 'checkout_id' in payment_page_query

    # parse the react to get the needed urls
    checkout_api_url = payment_page.text.split("REACT_APP_CHECKOUT_API_URL: '")[-1].split("',")[0]
    ld_proxy_domain = payment_page.text.split("REACT_APP_LD_PROXY_URL: '")[-1].split("',")[0]
    spreedly_api_url = payment_page.text.split("REACT_APP_SPREEDLY_API_URL: '")[-1].split("',")[0]

    # Sanity checks on the variables
    assert 'paddle' in checkout_api_url
    checkout_api_url = f'{checkout_api_url}/checkout/{checkout_id}'
    if not checkout_api_url.endswith('/'):
        checkout_api_url += '/'

    assert 'paddle' in ld_proxy_domain

    assert 'spreedly' in spreedly_api_url
    assert spreedly_api_url.endswith('v1'), 'Unexpected version of the spreedly API, this test might not work correctly'
    if not spreedly_api_url.endswith('/'):
        spreedly_api_url += '/'

    # prepare the customer info request
    customer_info_url = parse.urljoin(checkout_api_url, 'customer-info')
    customer_info = requests.post(
        customer_info_url,
        json={
            'data': {
                'email': email,
                'audience_optin': False,
                'country_code': 'PL',
                'postcode': 12345
            }
        }
    )
    payment_methods = customer_info.json().get(
        'data', {}
    ).get(
        'available_payment_methods', []
    )

    # fetch the CARD payment method
    for payment_method in payment_methods:
        if payment_method['type'] == 'CARD':
            assert 'spreedly_options' in payment_method
            assert 'spreedly_environment_key' in payment_method['spreedly_options']
            break
    else:
        assert False, 'Unexpected response from Paddle, payment method not found'

    # Now we should have received the spreedly environment token.
    spreedly_environment_key = payment_method['spreedly_options']['spreedly_environment_key']
    assert spreedly_environment_key

    # Set the payment method to CARD
    set_payment_method_url = parse.urljoin(checkout_api_url, 'payment-method')
    requests.patch(
        set_payment_method_url,
        json={
            'data': {
                "payment_method_type": "CARD"
            }
        }
    )

    # Send the card details to spreedly adn fetch the transaction token
    spreedly_url = parse.urljoin(spreedly_api_url, f'payment_methods.json?environment_key={spreedly_environment_key}')
    expire_date = date.today() + timedelta(90)
    payment_response = session.post(spreedly_url, json={
        "payment_method": {
            "allow_blank_name": False,
            "eligible_for_card_updater": False,
            "credit_card": {
                "country": "PL",
                "email": email,
                "full_name": "Test Example",
                "kind": "credit_card",
                "month": expire_date.strftime('%m'),
                "number": card,
                "verification_value": "123",
                "year": expire_date.strftime('%Y'),
                "zip": "12345"
            }
        }
    })
    transaction_token = payment_response.json().get('transaction', {}).get('payment_method', {}).get('token')
    assert transaction_token, 'Unexpected response from spreedly'

    # Send the payment information to paddle
    make_payment_url = parse.urljoin(checkout_api_url, 'pay-card')
    make_payment_response = session.post(make_payment_url, json={
        "data": {
            "cardholder_name": "Test Example",
            "first_six_digits": card[:6],
            "last_four_digits": card[-4:],
            "month": expire_date.strftime('%m'),
            "year": expire_date.strftime('%Y'),
            "card_type": "visa",
            "three_d_s": {
                "spreedly": {
                    "browser_info": browser_info
                }},
            "token": transaction_token
        }
    })

    # not sure if we need it, but I will still replicate the client behaviour
    session.get(parse.urljoin(ld_proxy_domain, f'/sdk/evalx/{browser_info}'))

    # 3D Secure - assuming it is needed
    three_d_s_url = make_payment_response.json()['data']['three_d_s']['spreedly']['checkout_url']
    three_d_s = session.get(three_d_s_url)

    # This page contains (your payment is successful, click here to return to the merchant)
    # in general this step might be necessary to finalize the transaction.
    finalize_three_d_s_url = three_d_s.text.split('href="')[-1].split('"')[0]
    assert 'spreedly' in finalize_three_d_s_url
    session.get(finalize_three_d_s_url)
    session.post(parse.urljoin(checkout_api_url, 'pay-card/3ds-finished'))


def test__payment_flow__regular(paddle, user_client, plan, card_number):
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

    automate_payment(redirect_url, card_number)

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


def test__payment_flow__trial_period(trial_period, paddle, user, user_client, plan, card_number):
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
        start = fromisoformat(subscription['start'])
        assert start - webhook_time < timedelta(seconds=10)

        # check that subscription lasts as much as stated in plan description
        end = fromisoformat(subscription['end'])
        assert start + paddle_unconfirmed_payment.plan.charge_period == end


def test_webhook_idempotence(paddle, client, paddle_unconfirmed_payment, paddle_webhook_payload):
    assert not Subscription.objects.all().exists()

    response = client.post('/api/webhook/paddle/', paddle_webhook_payload)
    assert response.status_code == 200, response.content
    start_old, end_old = Subscription.objects.values_list('start', 'end').latest()

    response = client.post('/api/webhook/paddle/', paddle_webhook_payload)
    assert response.status_code == 200, response.content
    start_new, end_new = Subscription.objects.values_list('start', 'end').latest()

    assert start_old == start_new
    assert end_old == end_new


def test_webhook_payload_as_form_data(paddle, client, paddle_unconfirmed_payment, paddle_webhook_payload):
    assert not Subscription.objects.all().exists()

    response = client.post('/api/webhook/paddle/', paddle_webhook_payload, content_type=MULTIPART_CONTENT)
    assert response.status_code == 200, response.content

    payment = SubscriptionPayment.objects.get(pk=paddle_unconfirmed_payment.pk)
    assert not isinstance(payment.metadata['subscription_id'], list)


def test_webhook_non_existing_payment(paddle, client, paddle_unconfirmed_payment, paddle_webhook_payload, settings):
    paddle_webhook_payload['passthrough'] = json.dumps({
        "subscription_payment_id": "84e9a5a1-cbca-4af5-a7b7-719f8f2fb772",
    })

    response = client.post('/api/webhook/paddle/', paddle_webhook_payload)
    assert response.status_code == 200, response.content


def test_subscription_charge_online_avoid_duplicates(paddle, user_client, plan):
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


def test_subscription_charge_online_new_payment_after_duplicate_lookup_time(paddle, user_client, plan):
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


def test_subscription_charge_online_new_payment_if_no_pending(paddle, user_client, plan):
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


def test_subscription_charge_online_new_payment_if_no_payment_url(paddle, user_client, plan):
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


def test_reference_payment_non_matching_currency(paddle, user_client, paddle_unconfirmed_payment):
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


def test_subscription_charge_offline_zero_amount(paddle, user_client, paddle_unconfirmed_payment):
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
