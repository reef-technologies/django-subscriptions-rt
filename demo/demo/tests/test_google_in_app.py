from dataclasses import dataclass
from typing import Any, Optional
from unittest import mock

import pytest
from django.utils.timezone import now
from djmoney.money import Money
from freezegun import freeze_time
from more_itertools import one
from subscriptions.models import Plan, Subscription, SubscriptionPayment
from subscriptions.providers.google_in_app.models import GoogleAcknowledgementState, GoogleSubscription, GoogleSubscriptionNotificationType, GoogleSubscriptionState, GoogleSubscriptionPurchaseV2
from subscriptions.utils import fromisoformat

from .helpers import days


def test__google_in_app__iter_subscriptions(google_in_app):
    list(google_in_app.iter_subscriptions())


@dataclass
class Executable:
    result: Optional[Any] = None

    def execute(self) -> Any:
        return self.result


@pytest.mark.skip()
def test__google_in_app__plan__push(google_in_app, plan_with_google):
    # when the plan in not in google -> push it

    with mock.patch(
        google_in_app.subscriptions_api,
        'list',
        return_value=Executable([
            GoogleSubscription(productId='trololo'),  # some other ID
        ]),
    ), mock.patch(
        google_in_app.subscriptions_api,
        'create',
        return_value=Executable(),
    ):
        google_subscription_dict = google_in_app.as_google_subscription(plan_with_google).dict()

        google_in_app.sync_plans()
        google_in_app.subscriptions_api.create.assert_called_with(
            package_name=google_in_app.package_name,
            body=google_subscription_dict,
        )

        plan = Plan.objects.get(pk=plan_with_google.pk)
        assert plan.metadata[google_in_app.codename] == google_subscription_dict


@pytest.mark.skip()
def test__google_in_app__plan__sync(google_in_app, plan_with_google):
    # when plan differs from what is in google -> push updates to google
    ...  # TODO


@pytest.mark.skip()
def test__google_in_app__subscription_deactivation(google_in_app, plan_with_google):
    # google has a subscription but there's no enabled plan -> archive the subscription
    ...  # TODO


def test__google_in_app__dismiss_token(google_in_app, user, subscription, purchase_token):
    # dismissing a token means that we end payments and subscriptions that exceed now()

    payment1 = SubscriptionPayment.objects.create(
        provider_codename=google_in_app.codename,
        provider_transaction_id=purchase_token,
        status=SubscriptionPayment.Status.COMPLETED,
        user=user,
        plan=subscription.plan,
        subscription=subscription,
    )
    payment2 = SubscriptionPayment.objects.create(
        provider_codename=google_in_app.codename,
        provider_transaction_id=purchase_token,
        status=SubscriptionPayment.Status.COMPLETED,
        user=user,
        plan=subscription.plan,
        subscription=subscription,
    )

    assert subscription.end == payment2.subscription_end
    with freeze_time(payment2.subscription_start + (payment2.subscription_end - payment2.subscription_start) / 2):
        payment1_end = payment1.subscription_end
        payment2_end = payment2.subscription_end

        assert payment1_end < now() < payment2_end
        google_in_app.dismiss_token(purchase_token)

        # fetch updated objects
        subscription = Subscription.objects.get(pk=subscription.pk)
        payment1 = SubscriptionPayment.objects.get(pk=payment1.pk)
        payment2 = SubscriptionPayment.objects.get(pk=payment2.pk)

        assert payment1.subscription_end == payment1_end
        assert payment2.subscription_end != payment2_end
        assert payment2.subscription_end == now()
        assert subscription.end == now()


def test__google_in_app__get_user_by_token(google_in_app, payment):
    assert google_in_app.get_user_by_token(payment.provider_transaction_id) is None

    payment.provider_codename = google_in_app.codename
    payment.save()
    assert google_in_app.get_user_by_token(payment.provider_transaction_id) == payment.user


def test__google_in_app__webhook_test_notification(google_in_app, google_test_notification, client):
    response = client.post('/api/webhook/google_in_app/', google_test_notification, content_type="application/json")
    assert response.status_code == 200, response.content


def test__google_in_app__webhook_for_app_notification__unauthorized(google_in_app, app_notification, client):
    response = client.post('/api/webhook/google_in_app/', app_notification, content_type="application/json")
    assert response.status_code == 403, response.content


def test__google_in_app__webhook_for_app_notification__authorized(
    google_in_app,
    app_notification,
    user_client,
    google_subscription_purchase,
    google_subscription_purchase_v2,
    plan_with_google,
):
    assert not Subscription.objects.exists()
    assert not SubscriptionPayment.objects.exists()

    with mock.patch(
        'subscriptions.providers.google_in_app.GoogleInAppProvider.get_purchase_v2',
        return_value=google_subscription_purchase_v2,
    ), mock.patch(
        'subscriptions.providers.google_in_app.GoogleInAppProvider.get_purchase',
        return_value=google_subscription_purchase,
    ):
        response = user_client.post('/api/webhook/google_in_app/', app_notification, content_type="application/json")
        assert response.status_code == 200, response.content
        google_in_app.get_purchase_v2.assert_called_with(app_notification['purchase_token'])

        payment = one(SubscriptionPayment.objects.all())
        subscription = payment.subscription

        assert payment.amount == None
        assert payment.subscription_start == subscription.start == fromisoformat(google_subscription_purchase_v2.startTime)
        assert payment.subscription_end == subscription.end == fromisoformat(one(google_subscription_purchase_v2.lineItems).expiryTime)
        assert payment.status == SubscriptionPayment.Status.COMPLETED
        assert payment.provider_codename == google_in_app.codename
        assert payment.provider_transaction_id == app_notification['purchase_token']


def test__google_in_app__webhook_for_app_notification__duplicate(
    google_in_app,
    app_notification,
    user_client,
    plan_with_google,
    google_subscription_purchase,
    google_subscription_purchase_v2,
):
    assert not SubscriptionPayment.objects.exists()
    assert not Subscription.objects.exists()

    with mock.patch(
        'subscriptions.providers.google_in_app.GoogleInAppProvider.get_purchase_v2',
        return_value=google_subscription_purchase_v2,
    ), mock.patch(
        'subscriptions.providers.google_in_app.GoogleInAppProvider.get_purchase',
        return_value=google_subscription_purchase,
    ):
        for _ in range(3):
            response = user_client.post('/api/webhook/google_in_app/', app_notification, content_type="application/json")
            assert response.status_code == 200, response.content

            assert SubscriptionPayment.objects.count() == 1
            assert Subscription.objects.count() == 1


def test__google_in_app__webhook_linked_token_dismissing(
    google_in_app,
    app_notification,
    user_client,
    google_subscription_purchase,
    google_subscription_purchase_v2,
    user,
    plan_with_google,
):

    linked_token = 'trololo'
    payment = SubscriptionPayment.objects.create(
        provider_codename=google_in_app.codename,
        provider_transaction_id=linked_token,
        status=SubscriptionPayment.Status.COMPLETED,
        user=user,
        plan=plan_with_google,
    )
    assert payment.subscription_end > now()
    assert payment.subscription.end > now()

    google_subscription_purchase_v2.linkedPurchaseToken = linked_token
    with mock.patch(
        'subscriptions.providers.google_in_app.GoogleInAppProvider.get_purchase_v2',
        return_value=google_subscription_purchase_v2,
    ), mock.patch(
        'subscriptions.providers.google_in_app.GoogleInAppProvider.get_purchase',
        return_value=google_subscription_purchase,
    ):
        response = user_client.post('/api/webhook/google_in_app/', app_notification, content_type="application/json")
        assert response.status_code == 200, response.content

    assert SubscriptionPayment.objects.count() == 2
    assert Subscription.objects.count() == 2

    payment = SubscriptionPayment.objects.get(pk=payment.pk)
    assert payment.subscription_end < now()
    assert payment.subscription.end < now()


def test__google_in_app__google_notification_without_app_notification(
    db,
    google_in_app,
    client,
    google_subscription_purchase,
    google_subscription_purchase_v2,
    google_rtdn_notification,
):
    with mock.patch(
        'subscriptions.providers.google_in_app.GoogleInAppProvider.get_purchase_v2',
        return_value=google_subscription_purchase_v2,
    ), mock.patch(
        'subscriptions.providers.google_in_app.GoogleInAppProvider.get_purchase',
        return_value=google_subscription_purchase,
    ):
        response = client.post('/api/webhook/google_in_app/', google_rtdn_notification, content_type="application/json")
        assert response.status_code == 200, response.content


def test__google_in_app__event_status_check(
    google_in_app,
    purchase_token,
    user,
    plan_with_google,
    client,
    google_subscription_purchase,
    google_subscription_purchase_v2,
    google_rtdn_notification,
):
    SubscriptionPayment.objects.create(
        provider_codename=google_in_app.codename,
        provider_transaction_id=purchase_token,
        status=SubscriptionPayment.Status.COMPLETED,
        user=user,
        plan=plan_with_google,
    )

    # TODO: not all cases covered
    google_subscription_purchase_v2.subscriptionState = GoogleSubscriptionState.PAUSED
    with mock.patch(
        'subscriptions.providers.google_in_app.GoogleInAppProvider.get_purchase_v2',
        return_value=google_subscription_purchase_v2,
    ), mock.patch(
        'subscriptions.providers.google_in_app.GoogleInAppProvider.get_purchase',
        return_value=google_subscription_purchase,
    ):
        response = client.post('/api/webhook/google_in_app/', google_rtdn_notification, content_type="application/json")
        # google posted notification with PURCHASED state, but real purchase has PAUSED state
        # -> something went wrong
        assert response.status_code == 400, response.content


def test__google_in_app__purchase_acknowledgement(
    google_in_app,
    user_client,
    google_subscription_purchase,
    google_subscription_purchase_v2,
    app_notification,
    plan_with_google,
):
    google_subscription_purchase_v2.acknowledgementState = GoogleAcknowledgementState.PENDING
    with mock.patch(
        'subscriptions.providers.google_in_app.GoogleInAppProvider.get_purchase_v2',
        return_value=google_subscription_purchase_v2,
    ), mock.patch(
        'subscriptions.providers.google_in_app.GoogleInAppProvider.get_purchase',
        return_value=google_subscription_purchase,
    ), mock.patch(
        'subscriptions.providers.google_in_app.GoogleInAppProvider.acknowledge',
        return_value=Executable(),
    ):
        response = user_client.post('/api/webhook/google_in_app/', app_notification, content_type="application/json")
        assert response.status_code == 200, response.content
        assert Subscription.objects.exists()
        google_in_app.acknowledge.assert_called_with(
            packageName=google_in_app.package_name,
            subscriptionId=plan_with_google.metadata[google_in_app.codename]['productId'],
            token=app_notification['purchase_token'],
            body=mock.ANY,
        )


@pytest.mark.skip
def test__google_in_app__check_event():
    ...  # TODO


def test__google_in_app__purchase_flow(
    google_in_app,
    purchase_token,
    user,
    plan_with_google,
    client,
    user_client,
    app_notification,
    google_subscription_purchase,
    google_subscription_purchase_v2,
    google_rtdn_notification_factory,
    now,
):
    """ Test initial purchase and renewal """

    with mock.patch(
        'subscriptions.providers.google_in_app.GoogleInAppProvider.get_purchase_v2',
        return_value=google_subscription_purchase_v2,
    ), mock.patch(
        'subscriptions.providers.google_in_app.GoogleInAppProvider.get_purchase',
        return_value=google_subscription_purchase,
    ):
        response = user_client.post('/api/webhook/google_in_app/', app_notification, content_type="application/json")
        assert response.status_code == 200, response.content

        assert SubscriptionPayment.objects.exists()
        payment = SubscriptionPayment.objects.latest()
        assert payment.user == user
        assert payment.quantity == 1
        assert payment.amount is None

        response = client.post(
            '/api/webhook/google_in_app/',
            google_rtdn_notification_factory(GoogleSubscriptionNotificationType.PURCHASED),
            content_type="application/json",
        )
        assert response.status_code == 200, response.content

        payment1 = SubscriptionPayment.objects.latest()
        assert payment1 == payment
        assert payment1.user == user
        assert payment1.quantity == 1
        assert payment1.amount == plan_with_google.charge_amount

        subscription = payment1.subscription
        assert subscription
        assert subscription.end == payment1.subscription_end

    google_subscription_purchase.priceAmountMicros = 5_000_000
    google_subscription_purchase.priceCurrencyCode = 'GEL'
    google_subscription_purchase_v2.lineItems[0].expiryTime = (now + days(10)).isoformat()
    with mock.patch(
        'subscriptions.providers.google_in_app.GoogleInAppProvider.get_purchase_v2',
        return_value=google_subscription_purchase_v2,
    ), mock.patch(
        'subscriptions.providers.google_in_app.GoogleInAppProvider.get_purchase',
        return_value=google_subscription_purchase,
    ):
        response = client.post(
            '/api/webhook/google_in_app/',
            google_rtdn_notification_factory(GoogleSubscriptionNotificationType.RENEWED),
            content_type="application/json",
        )
        assert response.status_code == 200, response.content

        assert SubscriptionPayment.objects.count() == 2
        payment2 = SubscriptionPayment.objects.latest()
        assert payment2.user == user
        assert payment2.quantity == 1
        assert payment2.amount == Money(5, 'GEL')
        assert payment2.subscription.end == payment2.subscription_end


def test__google_in_app__expiration_notification(
    google_in_app,
    purchase_token,
    user,
    plan_with_google,
    client,
    google_rtdn_notification_factory,
    google_in_app_payment,
    google_subscription_purchase,
    google_subscription_purchase_v2,
):

    assert google_in_app_payment.subscription.end == google_in_app_payment.subscription_end
    initial_subscription_end = google_in_app_payment.subscription_end

    # test that subscription end date is set to expiration time even if it's BEFORE payment end date
    new_expiry_time = initial_subscription_end - days(1)
    google_subscription_purchase_v2.lineItems[0].expiryTime = new_expiry_time.isoformat()
    google_subscription_purchase_v2.subscriptionState = GoogleSubscriptionState.EXPIRED

    with mock.patch(
        'subscriptions.providers.google_in_app.GoogleInAppProvider.get_purchase_v2',
        return_value=google_subscription_purchase_v2,
    ), mock.patch(
        'subscriptions.providers.google_in_app.GoogleInAppProvider.get_purchase',
        return_value=google_subscription_purchase,
    ):
        response = client.post(
            '/api/webhook/google_in_app/',
            google_rtdn_notification_factory(GoogleSubscriptionNotificationType.EXPIRED),
            content_type="application/json",
        )
        assert response.status_code == 200, response.content

    subscription = Subscription.objects.get(pk=google_in_app_payment.subscription.pk)
    payment = SubscriptionPayment.objects.get(pk=google_in_app_payment.pk)

    assert payment.subscription_end == initial_subscription_end
    assert subscription.end == new_expiry_time
    assert subscription.end != payment.subscription_end


# TODO: not all cases covered


@pytest.mark.skip
def test__google_in_app__check_payments(google_in_app):
    ...  # TODO


def test__google_in_app__subscription_notification_models(
    google_in_app__subscription_purchase_v2_dict,
):
    GoogleSubscriptionPurchaseV2.parse_obj(google_in_app__subscription_purchase_v2_dict)
