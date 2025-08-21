import logging
import re
from datetime import UTC, datetime, timedelta

import pytest
from dateutil.relativedelta import relativedelta
from django.utils.timezone import now
from freezegun import freeze_time
from more_itertools import one

from subscriptions.v0.exceptions import PaymentError
from subscriptions.v0.fields import relativedelta_to_dict
from subscriptions.v0.functions import use_resource
from subscriptions.v0.models import Subscription, SubscriptionPayment, Usage

from ..helpers import datetime_to_api, days


@pytest.mark.django_db(databases=["actual_db"])
def test__migrations():
    from django.core.management import call_command

    print(call_command("showmigrations", "--database", "actual_db"))


@pytest.mark.django_db(databases=["actual_db"])
def test__api__plans(plan, client):
    response = client.get("/api/plans/")
    assert response.status_code == 200
    assert response.json() == [
        {
            "id": plan.pk,
            "name": plan.name,
            "charge_amount": plan.charge_amount.amount,
            "charge_amount_currency": str(plan.charge_amount.currency),
            "charge_period": relativedelta_to_dict(plan.charge_period),
            "max_duration": relativedelta_to_dict(plan.max_duration),
            "is_recurring": plan.is_recurring(),
            "metadata": {
                "this": "that",
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


@pytest.mark.django_db(databases=["actual_db"])
def test__api__subscriptions__unauthorized(client, two_subscriptions):
    response = client.get("/api/subscriptions/")
    assert response.status_code == 403


@pytest.mark.django_db(databases=["actual_db"])
def test__api__subscriptions__authorized(user_client, two_subscriptions):
    response = user_client.get("/api/subscriptions/")
    assert response.status_code == 200, response.content
    subscription = two_subscriptions[0]
    assert response.json() == [
        {
            "id": str(subscription.pk),
            "start": datetime_to_api(subscription.start),
            "end": datetime_to_api(subscription.end),
            "quantity": 1,
            "next_charge_date": None,
            "provider": None,
            "plan": {
                "id": subscription.plan.pk,
                "name": subscription.plan.name,
                "charge_amount": subscription.plan.charge_amount and subscription.plan.charge_amount.amount,
                "charge_amount_currency": str(subscription.plan.charge_amount.currency)
                if subscription.plan.charge_amount
                else "USD",
                "charge_period": relativedelta_to_dict(subscription.plan.charge_period),
                "max_duration": relativedelta_to_dict(subscription.plan.max_duration),
                "is_recurring": subscription.plan.is_recurring(),
                "metadata": {},
            },
        }
    ]


@pytest.mark.django_db(databases=["actual_db"])
def test__api__subscriptions__next_charge_date(user_client, subscription):
    assert subscription.start == datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
    subscription.end = datetime(2025, 3, 1, 12, 0, 0, tzinfo=UTC)
    subscription.save()

    with freeze_time(datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)):
        response = user_client.get("/api/subscriptions/")
        assert response.status_code == 200, response.content
        assert response.json()[0]["next_charge_date"] == datetime_to_api(subscription.start)

    with freeze_time(datetime(2025, 1, 2, 12, 0, 0, tzinfo=UTC)):
        response = user_client.get("/api/subscriptions/")
        assert response.status_code == 200, response.content
        assert response.json()[0]["next_charge_date"] == datetime_to_api(subscription.start + relativedelta(months=1))

    with freeze_time(datetime(2025, 2, 2, 12, 0, 0, tzinfo=UTC)):
        response = user_client.get("/api/subscriptions/")
        assert response.status_code == 200, response.content
        assert response.json()[0]["next_charge_date"] == datetime_to_api(subscription.start + relativedelta(months=2))


@pytest.mark.django_db(databases=["actual_db"])
def test__api__subscriptions__next_charge_date__not_prolong(user_client, subscription):
    subscription.end = subscription.start + relativedelta(days=90)
    subscription.save()

    with freeze_time(subscription.start):
        response = user_client.get("/api/subscriptions/")
        assert response.status_code == 200, response.content
        assert response.json()[0]["next_charge_date"] == datetime_to_api(subscription.start)

    subscription.auto_prolong = False
    subscription.save()

    with freeze_time(subscription.start):
        response = user_client.get("/api/subscriptions/")
        assert response.status_code == 200, response.content
        assert response.json()[0]["next_charge_date"] is None


@pytest.mark.django_db(databases=["actual_db"], transaction=True)
def test__api__subscribe__unauthorized(client, plan, dummy):
    response = client.post("/api/subscribe/", {"plan": plan.pk, "provider": dummy.codename})
    assert response.status_code == 403


@pytest.mark.django_db(databases=["actual_db"], transaction=True)
def test__api__subscribe__authorized(client, user_client, plan, dummy):
    response = user_client.post("/api/subscribe/", {"provider": dummy.codename, "plan": plan.pk, "quantity": 2})
    assert response.status_code == 200, response.content
    result = response.json()

    assert {
        "plan": plan.pk,
        "payment_id": str(SubscriptionPayment.objects.latest().pk),
        "quantity": 2,
        "redirect_url": result["redirect_url"],
        "status": "pending",
    }.items() <= result.items()

    response = user_client.get("/api/subscriptions/")
    assert response.status_code == 200, response.content
    subscriptions = response.json()
    assert len(subscriptions) == 0

    # manually invoke webhook
    payment = SubscriptionPayment.objects.latest()
    response = client.post("/api/webhook/dummy/", {"transaction_id": payment.provider_transaction_id})
    assert response.status_code == 200, response.content

    response = user_client.get("/api/subscriptions/")
    assert response.status_code == 200, response.content
    subscriptions = response.json()
    assert len(subscriptions) == 1
    subscription = subscriptions[0]
    subscription_start = Subscription.objects.first().start
    assert now() - timedelta(seconds=5) < subscription_start < now()
    assert subscription["start"] == datetime_to_api(subscription_start)
    assert subscription["end"] == datetime_to_api(subscription_start + plan.charge_period)
    assert subscription["quantity"] == 2


@pytest.mark.django_db(databases=["actual_db"])
def test__api__webhook_logging(client, caplog):
    with caplog.at_level(logging.INFO):
        client.post("/api/webhook/dummy/", {"webhook-key": "webhook-value"})
    assert re.search(
        r"INFO .+? Webhook at http://testserver/api/webhook/dummy/ received payload {'webhook-key': 'webhook-value'}",
        caplog.text,
    )


@pytest.mark.django_db(databases=["actual_db"])
@pytest.mark.parametrize(
    "use_cache",
    [
        pytest.param(True, id="cache:ON"),
        pytest.param(False, id="cache:OFF"),
    ],
)
def test__api__resources__initial(request, use_cache, user_client, subscription, resource, quota):
    request.getfixturevalue("cache_backend") if use_cache else None
    with freeze_time(subscription.start, tick=True):
        response = user_client.get("/api/resources/")
        assert response.status_code == 200, response.content
        assert response.json() == {
            "resources": [{"codename": resource.codename, "amount": quota.limit * subscription.quantity}],
        }


@pytest.mark.django_db(databases=["actual_db"])
@pytest.mark.parametrize(
    "use_cache",
    [
        pytest.param(True, id="cache:ON"),
        pytest.param(False, id="cache:OFF"),
    ],
)
def test__api__resources__usage(request, use_cache, user, user_client, subscription, resource, quota):
    request.getfixturevalue("cache_backend") if use_cache else None
    with freeze_time(subscription.start + days(1), tick=True):
        Usage.objects.create(
            user=user,
            resource=resource,
            amount=20,
        )

    with freeze_time(subscription.start + days(2), tick=True):
        response = user_client.get("/api/resources")
        assert response.status_code == 200, response.content
        assert response.json() == {
            "resources": [{"codename": resource.codename, "amount": quota.limit * subscription.quantity - 20}]
        }


@pytest.mark.django_db(databases=["actual_db"])
@pytest.mark.parametrize(
    "use_cache",
    [
        pytest.param(True, id="cache:ON"),
        pytest.param(False, id="cache:OFF"),
    ],
)
def test__api__resources__expiration(request, use_cache, user_client, subscription, resource, quota):
    request.getfixturevalue("cache_backend") if use_cache else None

    with freeze_time(subscription.start + quota.burns_in - days(1)):
        response = user_client.get("/api/resources")
        assert response.status_code == 200, response.content
        assert response.json() == {
            "resources": [{"codename": resource.codename, "amount": quota.limit * subscription.quantity}]
        }

    with freeze_time(subscription.start + quota.burns_in):
        response = user_client.get("/api/resources/")
        assert response.status_code == 200, response.content
        assert response.json() == {
            "resources": [],
        }


@pytest.mark.django_db(databases=["actual_db"], transaction=True)
def test__api__recurring_plan_switch(user, user_client, subscription, payment, bigger_plan, dummy):
    with freeze_time(subscription.start):
        assert one(user.subscriptions.active()).plan == subscription.plan

    with freeze_time(subscription.start + days(2), tick=True):
        response = user_client.post("/api/subscribe/", {"plan": bigger_plan.pk, "provider": dummy.codename})
        assert response.status_code == 200, response.content
        assert one(user.subscriptions.active()).plan == bigger_plan


@pytest.mark.django_db(databases=["actual_db"], transaction=True)
@pytest.mark.parametrize(
    "use_cache",
    [
        pytest.param(True, id="cache:ON"),
        pytest.param(False, id="cache:OFF"),
    ],
)
def test__api__recharge_plan_subscription(
    request, use_cache, client, user_client, subscription, quota, recharge_plan, recharge_quota, resource, dummy
):
    request.getfixturevalue("cache_backend") if use_cache else None

    with freeze_time(subscription.start + days(2), tick=True):
        response = user_client.post("/api/subscribe/", {"plan": recharge_plan.pk, "provider": dummy.codename})
        assert response.status_code == 200, response.content
        result = response.json()

        assert {
            "plan": recharge_plan.pk,
            "quantity": 1,
            "payment_id": str(SubscriptionPayment.objects.latest().pk),
            "redirect_url": result["redirect_url"],
            "status": "pending",
        }.items() <= result.items()

        transaction_id = SubscriptionPayment.objects.latest().provider_transaction_id
        response = client.post("/api/webhook/dummy/", {"transaction_id": transaction_id})
        assert response.status_code == 200, response.content

    with freeze_time(subscription.start + days(3), tick=True):
        response = user_client.get("/api/resources/")
        assert response.status_code == 200, response.content
        assert response.json() == {
            "resources": [
                {
                    "codename": resource.codename,
                    "amount": subscription.plan.quotas.last().limit * subscription.quantity + recharge_quota.limit,
                }
            ]
        }


@pytest.mark.django_db(databases=["actual_db"])
def test__charge_automatically(subscription, dummy):
    with freeze_time(subscription.start + days(1)):
        payment = SubscriptionPayment.objects.create(
            provider_codename=dummy.codename,
            provider_transaction_id="0000",
            amount=subscription.plan.charge_amount,
            user=subscription.user,
            plan=subscription.plan,
            subscription=subscription,
            paid_since=subscription.start,
            paid_until=subscription.end,
        )

    with freeze_time(subscription.start + days(2)):
        with pytest.raises(PaymentError, match="no previous successful payment"):
            subscription.charge_automatically()

    payment.status = SubscriptionPayment.Status.COMPLETED
    payment.save()

    with freeze_time(subscription.start + days(2)):
        subscription.charge_automatically()


@pytest.mark.django_db(databases=["actual_db"])
def test__api__payment(user_client, payment):
    response = user_client.get(f"/api/payments/{payment.pk}/")
    assert response.status_code == 200, response.content
    assert response.json() == {
        "id": str(payment.pk),
        "status": "completed",
        "subscription": {
            "id": str(payment.subscription.pk),
            "plan": {
                "id": payment.subscription.plan.pk,
                "name": "Plan",
                "charge_amount": 100.0,
                "charge_amount_currency": "USD",
                "charge_period": {"months": 1},
                "max_duration": {"months": 4},
                "is_recurring": True,
                "metadata": {"this": "that"},
            },
            "quantity": 2,
            "start": datetime_to_api(payment.subscription.start),
            "end": datetime_to_api(payment.subscription.end),
            "next_charge_date": datetime_to_api(next(payment.subscription.iter_charge_dates(since=now()))),
            "provider": "dummy",
        },
        "quantity": 2,
        "amount": 100.0,
        "currency": "USD",
        "total": 200.0,
        "paid_since": datetime_to_api(payment.paid_since),
        "paid_until": datetime_to_api(payment.paid_until),
        "created": datetime_to_api(payment.created),
    }


@pytest.mark.django_db(databases=["actual_db"])
@pytest.mark.parametrize(
    "use_cache",
    [
        pytest.param(True, id="cache:ON"),
        pytest.param(False, id="cache:OFF"),
    ],
)
def test__api__resource_headers_mixin__anonymous(request, use_cache, client, resource):
    request.getfixturevalue("cache_backend") if use_cache else None

    response = client.get("/api/headers_mixin/")
    assert response.status_code == 200
    assert not any(header.startswith("X-Resource-") for header in response.headers)


@pytest.mark.django_db(databases=["actual_db"])
@pytest.mark.parametrize(
    "use_cache",
    [
        pytest.param(True, id="cache:ON"),
        pytest.param(False, id="cache:OFF"),
    ],
)
def test__api__resource_headers_mixin__empty(request, use_cache, user_client, resource):
    request.getfixturevalue("cache_backend") if use_cache else None

    response = user_client.get("/api/headers_mixin/")
    assert response.status_code == 200
    assert f"X-Resource-{resource.codename}" not in response.headers


@pytest.mark.django_db(databases=["actual_db"])
@pytest.mark.parametrize(
    "use_cache",
    [
        pytest.param(True, id="cache:ON"),
        pytest.param(False, id="cache:OFF"),
    ],
)
def test__api__resource_headers_mixin__exists(request, use_cache, user, user_client, resource, subscription, quota):
    request.getfixturevalue("cache_backend") if use_cache else None

    available = quota.limit * subscription.quantity

    with freeze_time(subscription.start, tick=True):
        response = user_client.get("/api/headers_mixin/")
        assert response.status_code == 200
        assert response.headers[f"X-Resource-{resource.codename}"] == str(available)

    with freeze_time(subscription.start + days(1), tick=True):
        with use_resource(user, resource, 10):
            response = user_client.get("/api/headers_mixin/")
            assert response.status_code == 200
            assert response.headers[f"X-Resource-{resource.codename}"] == str(available - 10)


@pytest.mark.django_db(databases=["actual_db"])
def test__api__subscriptions__cancel__dummy(user, user_client, subscription, payment, dummy):
    subscription.end = subscription.start + relativedelta(days=90)
    subscription.auto_prolong = True
    subscription.save()

    with freeze_time(subscription.end + timedelta(days=1)):
        response = user_client.delete(f"/api/subscriptions/{subscription.uid}/")
        assert response.status_code == 404, response.content
        assert user.subscriptions.active().count() == 0

    with freeze_time(subscription.start + timedelta(days=1)):
        response = user_client.delete(f"/api/subscriptions/{subscription.uid}/")
        assert response.status_code == 204, response.content

    with freeze_time(subscription.start + timedelta(days=1, seconds=1)):
        assert user.subscriptions.active().count() == 1
        assert user.subscriptions.latest().end == subscription.start + relativedelta(days=90)
        assert user.subscriptions.latest().auto_prolong is False

    with freeze_time(subscription.end + timedelta(seconds=1)):
        assert user.subscriptions.active().count() == 0
