import json
from base64 import b64encode
from datetime import datetime, timedelta
from datetime import timezone as tz
from decimal import Decimal
from functools import wraps
from typing import Callable, List

import pytest
from dateutil.relativedelta import relativedelta
from django.contrib.auth import get_user_model
from django.test import Client
from djmoney.money import Money
from subscriptions.functions import get_remaining_amount, get_remaining_chunks
from subscriptions.models import INFINITY, Plan, Quota, QuotaCache, Resource, Subscription, SubscriptionPayment, Usage
from subscriptions.providers import get_provider, get_providers
from subscriptions.providers.dummy import DummyProvider
from subscriptions.providers.google_in_app import GoogleInAppProvider
from subscriptions.providers.google_in_app.models import GoogleAcknowledgementState, GoogleAutoRenewingPlan, GoogleSubscriptionPurchaseLineItem, GoogleSubscriptionPurchaseV2, GoogleSubscriptionState, GoogleSubscriptionNotificationType
from subscriptions.providers.paddle import PaddleProvider
from subscriptions.tasks import charge_recurring_subscriptions


@pytest.fixture
def days():
    return lambda n: relativedelta(days=n)


@pytest.fixture
def now():
    return datetime(2022, 1, 1, 12, 00, 00, tzinfo=tz.utc)


@pytest.fixture
def user(db):
    return get_user_model().objects.create(
        username='test',
    )


@pytest.fixture
def resource(db) -> Resource:
    return Resource.objects.create(
        codename='resource',
    )


@pytest.fixture
def plan(db, days, resource) -> Plan:
    return Plan.objects.create(
        codename='plan',
        name='Plan',
        charge_amount=Decimal(100),
        charge_period=days(30),
        max_duration=days(120),
        metadata={
            'this': 'that',
        }
    )


@pytest.fixture
def quota(db, plan, resource) -> Quota:
    return Quota.objects.create(
        plan=plan,
        resource=resource,
        limit=50,
    )


@pytest.fixture
def bigger_plan(db, days, resource) -> Plan:
    return Plan.objects.create(
        codename='bigger-plan',
        name='Bigger plan',
        charge_amount=Decimal(200),
        charge_period=days(30),
    )


@pytest.fixture
def bigger_quota(db, bigger_plan, resource) -> Quota:
    return Quota.objects.create(
        plan=bigger_plan,
        resource=resource,
        limit=300,
    )


@pytest.fixture
def recharge_plan(db, days, resource) -> Plan:
    # $10 for 10 resources, expires in 14 days
    return Plan.objects.create(
        codename='recharge-plan',
        name='Recharge plan',
        charge_amount=Decimal(10),
        charge_period=INFINITY,
        max_duration=days(14),
    )


@pytest.fixture
def recharge_quota(db, recharge_plan, resource) -> Quota:
    return Quota.objects.create(
        plan=recharge_plan,
        resource=resource,
        limit=10,
    )


@pytest.fixture
def subscription(db, now, user, plan) -> Subscription:
    return Subscription.objects.create(
        user=user,
        plan=plan,
        start=now,
        quantity=2,  # so limit = 50 * 2 = 100 in total
    )


@pytest.fixture
def remaining_chunks(user) -> Callable:
    @wraps(get_remaining_chunks)
    def wrapped(**kwargs):
        return get_remaining_chunks(user=user, **kwargs)

    return wrapped


@pytest.fixture
def remains(user, resource) -> Callable:
    @wraps(get_remaining_amount)
    def wrapped(**kwargs):
        return get_remaining_amount(user=user, **kwargs).get(resource, 0)

    return wrapped


@pytest.fixture
def get_cache(remaining_chunks) -> Callable:

    def fn(at: datetime) -> QuotaCache:
        return QuotaCache(
            datetime=at,
            chunks=remaining_chunks(at=at),
        )

    return fn


@pytest.fixture
def two_subscriptions(user, now, days, resource) -> List[Subscription]:
    """
                         Subscription 1
    --------------[========================]------------> time

    quota 1.1:    [-----------------]
             0    100             100  0

    quota 1.2:                 [-----------x (subscription ended)
                          0    100       100  0

    days__________0__1______4__5____7______10_______________

                                 Subscription 2
    ------------------------[===========================]-----> time

    quota 2.1:              [-----------------]
                       0    100             100  0

    quota 2.2:                           [--------------x (subscription ended)
                                    0    100          100  0

    -----------------|------------|-----------------|----------------
    usage:           50          200               50

    """

    plan1 = Plan.objects.create(codename='plan1', name='Plan 1')
    subscription1 = Subscription.objects.create(
        user=user,
        plan=plan1,
        start=now,
        end=now + days(10),
    )
    Quota.objects.create(
        plan=plan1,
        resource=resource,
        limit=100,
        recharge_period=days(5),
        burns_in=days(7),
    )

    plan2 = Plan.objects.create(codename='plan2', name='Plan 2', charge_amount=Money(10, 'EUR'))
    subscription2 = Subscription.objects.create(
        user=user,
        plan=plan2,
        start=now + days(4),
        end=now + days(14),
    )
    Quota.objects.create(
        plan=plan2,
        resource=resource,
        limit=100,
        recharge_period=days(5),
        burns_in=days(7),
    )

    Usage.objects.bulk_create([
        Usage(user=user, resource=resource, amount=50, datetime=now + days(1)),
        Usage(user=user, resource=resource, amount=200, datetime=now + days(6)),
        Usage(user=user, resource=resource, amount=50, datetime=now + days(12)),
    ])

    return [subscription1, subscription2]


@pytest.fixture
def five_subscriptions(db, plan, user, now, days) -> List[Subscription]:
    """
    Subscriptions:                    |now
    ----------------------------------[====sub0=====]-----> overlaps with "now"
    --------------------[======sub1=======]---------------> overlaps with "sub0"
    -------------[=sub2=]---------------------------------> does not overlap with "sub1"
    -----------------------[=sub3=]-----------------------> overlaps with "sub1"
    ----[=sub4=]------------------------------------------> does not overlap with anything
    """

    sub0 = Subscription.objects.create(user=user, plan=plan, start=now - days(5), end=now + days(2))
    sub1 = Subscription.objects.create(user=user, plan=plan, start=sub0.start - days(5), end=sub0.start + days(2))
    sub2 = Subscription.objects.create(user=user, plan=plan, start=sub1.start - days(5), end=sub1.start)
    sub3 = Subscription.objects.create(user=user, plan=plan, start=sub1.start + days(1), end=sub0.start - days(1))
    sub4 = Subscription.objects.create(user=user, plan=plan, start=sub2.start - days(5), end=sub2.start - days(1))
    return [sub0, sub1, sub2, sub3, sub4]


@pytest.fixture
def user_client(client, user) -> Client:
    client.force_login(user)
    return client


@pytest.fixture
def dummy(settings) -> str:
    settings.SUBSCRIPTIONS_PAYMENT_PROVIDERS = [
        'subscriptions.providers.dummy.DummyProvider',
    ]
    get_provider.cache_clear()
    get_providers.cache_clear()
    provider = get_provider()
    assert isinstance(provider, DummyProvider)
    return provider


@pytest.fixture
def paddle(settings) -> str:
    settings.SUBSCRIPTIONS_PAYMENT_PROVIDERS = [
        'subscriptions.providers.paddle.PaddleProvider',
    ]
    get_provider.cache_clear()
    get_providers.cache_clear()
    provider = get_provider()
    assert isinstance(provider, PaddleProvider)
    return provider


@pytest.fixture
def google_in_app(settings) -> str:
    settings.SUBSCRIPTIONS_PAYMENT_PROVIDERS = [
        'subscriptions.providers.google_in_app.GoogleInAppProvider',
    ]
    get_provider.cache_clear()
    get_providers.cache_clear()
    provider = get_provider()
    assert isinstance(provider, GoogleInAppProvider)
    return provider


@pytest.fixture
def paddle_unconfirmed_payment(db, paddle, plan, user) -> SubscriptionPayment:
    return SubscriptionPayment.objects.create(
        user=user,
        plan=plan,
        subscription=None,
        provider_codename=paddle.codename,
        provider_transaction_id='12345',
        amount=Money(100, 'USD'),
    )


@pytest.fixture
def payment(dummy, subscription) -> SubscriptionPayment:
    return SubscriptionPayment.objects.create(
        user=subscription.user,
        plan=subscription.plan,
        subscription=subscription,
        provider_codename=dummy.codename,
        provider_transaction_id='12345',
        amount=subscription.plan.charge_amount,
        quantity=2,  # so limit = 50 * 2 = 100 in total
        status=SubscriptionPayment.Status.COMPLETED,
        metadata={
            'subscription_id': 'some-dummy-uid',
        },
        created=subscription.end,
    )


@pytest.fixture
def paddle_webhook_payload(db, paddle, paddle_unconfirmed_payment) -> dict:
    return {
        'alert_id': 970811351,
        'alert_name': 'subscription_payment_succeeded',
        'balance_currency': 'USD',
        'balance_earnings': '80.00',
        'balance_fee': '20.00',
        'balance_gross': '100.00',
        'balance_tax': '33.00',
        'checkout_id': '2-6cad1ee6f850e26-243da69933',
        'country': 'DE',
        'coupon': 'Coupon 8',
        'currency': 'USD',
        'customer_name': 'customer_name',
        'earnings': '577.96',
        'email': 'feil.jackson@example.net',
        'event_time': '2022-06-03 18:20:23',
        'fee': '0.28',
        'initial_payment': False,
        'instalments': 4,
        'marketing_consent': 1,
        'next_bill_date': '2022-06-24',
        'next_payment_amount': '200.00',
        'order_id': 6,
        'passthrough': f'{{"SubscriptionPayment.id": "{paddle_unconfirmed_payment.id}"}}',
        'payment_method': 'paypal',
        'payment_tax': '0.94',
        'plan_name': 'Example String',
        'quantity': 9,
        'receipt_url': 'https://sandbox-my.paddle.com/receipt/5/93efff2bc9436b9-4fbe55cfe6',
        'sale_gross': '328.85',
        'status': 'active',
        'subscription_id': 4,
        'subscription_payment_id': 2,
        'subscription_plan_id': 8,
        'unit_price': 'unit_price',
        'user_id': 3,
        'p_signature': 'abracadabra',
    }


@pytest.fixture
def card_number() -> str:
    return ' '.join(['4242'] * 4)


@pytest.fixture
def charge_schedule() -> List[timedelta]:
    return [
        timedelta(days=-7),
        timedelta(days=-3),
        timedelta(days=-1),
        timedelta(hours=-1),
        timedelta(0),
        timedelta(days=1),
        timedelta(days=3),
        timedelta(days=7),
    ]


@pytest.fixture
def charge_expiring(charge_schedule, monkeypatch):
    """ Call: charge_expiring(payment_status=SubscriptionPayment.Status.PENDING) """

    def wrapper(payment_status: SubscriptionPayment.Status = SubscriptionPayment.Status.COMPLETED):
        with monkeypatch.context() as monkey:
            # here we don't allow setting any status except `payment_status` to SubscriptionPayment
            monkey.setattr(
                'subscriptions.models.SubscriptionPayment.__setattr__',
                lambda obj, name, value: super(SubscriptionPayment, obj).__setattr__(name, payment_status if name == 'status' else value)
            )

            return charge_recurring_subscriptions(
                schedule=charge_schedule,
                num_threads=1,
            )

    return wrapper


@pytest.fixture
def cache_backend(settings):
    settings.CACHES['subscriptions'] = {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'subscriptions',
    }


@pytest.fixture
def purchase_token() -> str:
    return '12345'


@pytest.fixture
def app_notification(purchase_token) -> dict:
    return {
        'purchase_token': purchase_token,
    }


@pytest.fixture
def google_subscription_purchase(plan, now, days) -> GoogleSubscriptionPurchaseV2:
    return GoogleSubscriptionPurchaseV2(
        lineItems=[GoogleSubscriptionPurchaseLineItem(
            productId=plan.id,
            expiryTime=(now + days(5)).isoformat(),
            plan_type=GoogleAutoRenewingPlan(autoRenewEnabled=True),
        )],
        startTime=now.isoformat(),
        subscriptionState=GoogleSubscriptionState.ACTIVE,
        linkedPurchaseToken=None,
        acknowledgementState=GoogleAcknowledgementState.ACKNOWLEDGED,
    )


@pytest.fixture
def google_rtdn_notification_factory(settings, purchase_token, plan) -> Callable:

    def build_google_rtdn_notification(type_: GoogleSubscriptionNotificationType):
        return {
            "message": {
                "attributes": {
                    "key": "value"
                },
                "data": b64encode(json.dumps({
                    "version": '1.0',
                    "packageName": settings.GOOGLE_PLAY_PACKAGE_NAME,
                    "eventTimeMillis": 100,
                    "subscriptionNotification": {
                        'version': '1.0',
                        'notificationType': type_,
                        'purchaseToken': purchase_token,
                        'subscriptionId': plan.id,
                    },
                }).encode('utf8')).decode('utf8'),
                "messageId": "136969346945"
            },
            "subscription": "projects/myproject/subscriptions/mysubscription",
        }

    return build_google_rtdn_notification


@pytest.fixture
def google_rtdn_notification(google_rtdn_notification_factory) -> dict:
    return google_rtdn_notification_factory(GoogleSubscriptionNotificationType.PURCHASED)
