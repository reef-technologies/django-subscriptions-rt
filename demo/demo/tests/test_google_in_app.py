from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest import mock

import pytest
from django.utils.timezone import now
from freezegun import freeze_time
from more_itertools import one
from subscriptions.models import Plan, Subscription, SubscriptionPayment
from subscriptions.providers.google_in_app.schemas import GoogleAcknowledgementState, GoogleSubscription, GoogleSubscriptionNotificationType, GoogleSubscriptionState, GoogleSubscriptionPurchaseV2
from subscriptions.utils import fromisoformat

from .helpers import days


def test__google__iter_subscriptions(google_in_app):
    list(google_in_app.iter_subscriptions())


@dataclass
class Executable:
    result: Any | None = None

    def execute(self) -> Any:
        return self.result


@pytest.mark.skip()
def test__google__plan_push(google_in_app, plan_with_google):
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
def test__google__plan_sync(google_in_app, plan_with_google):
    # when plan differs from what is in google -> push updates to google
    ...  # TODO


@pytest.mark.skip()
def test__google__subscription_deactivation(google_in_app, plan_with_google):
    # google has a subscription but there's no enabled plan -> archive the subscription
    ...  # TODO


def test__google__dismiss_token(google_in_app, user, subscription, purchase_token):
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


def test__google__get_user_by_token(google_in_app, payment):
    assert google_in_app.get_user_by_token(payment.provider_transaction_id) is None

    payment.provider_codename = google_in_app.codename
    payment.save()
    assert google_in_app.get_user_by_token(payment.provider_transaction_id) == payment.user


def test__google__webhook_test__google__notification(google_in_app, google_test__google__notification, client):
    response = client.post('/api/webhook/google_in_app/', google_test__google__notification, content_type="application/json")
    assert response.status_code == 200, response.content


def test__google__webhook_for_app_notification_unauthorized(google_in_app, app_notification, client):
    response = client.post('/api/webhook/google_in_app/', app_notification, content_type="application/json")
    assert response.status_code == 403, response.content


def test__google__webhook_for_app_notification(google_in_app, app_notification, user_client, google_subscription_purchase, plan_with_google):
    assert not Subscription.objects.exists()
    assert not SubscriptionPayment.objects.exists()

    with mock.patch('subscriptions.providers.google_in_app.GoogleInAppProvider.get_purchase', return_value=google_subscription_purchase):
        response = user_client.post('/api/webhook/google_in_app/', app_notification, content_type="application/json")
        assert response.status_code == 200, response.content
        google_in_app.get_purchase.assert_called_with(app_notification['purchase_token'])

    payment = one(SubscriptionPayment.objects.all())
    subscription = payment.subscription

    assert payment.subscription_start == subscription.start == fromisoformat(google_subscription_purchase.startTime)
    assert payment.subscription_end == subscription.end == fromisoformat(one(google_subscription_purchase.lineItems).expiryTime)
    assert payment.status == SubscriptionPayment.Status.COMPLETED
    assert payment.provider_codename == google_in_app.codename
    assert payment.provider_transaction_id == app_notification['purchase_token']


def test__google__webhook_for_app_notification_duplicate(google_in_app, app_notification, user_client, google_subscription_purchase, plan_with_google):
    assert not SubscriptionPayment.objects.exists()
    assert not Subscription.objects.exists()

    with mock.patch('subscriptions.providers.google_in_app.GoogleInAppProvider.get_purchase', return_value=google_subscription_purchase):
        for _ in range(3):
            response = user_client.post('/api/webhook/google_in_app/', app_notification, content_type="application/json")
            assert response.status_code == 200, response.content

            assert SubscriptionPayment.objects.count() == 1
            assert Subscription.objects.count() == 1


def test__google__webhook_linked_token_dismissing(google_in_app, app_notification, user_client, google_subscription_purchase, user, plan_with_google):
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

    google_subscription_purchase.linkedPurchaseToken = linked_token
    with mock.patch('subscriptions.providers.google_in_app.GoogleInAppProvider.get_purchase', return_value=google_subscription_purchase):
        response = user_client.post('/api/webhook/google_in_app/', app_notification, content_type="application/json")
        assert response.status_code == 200, response.content

    assert SubscriptionPayment.objects.count() == 2
    assert Subscription.objects.count() == 2

    payment = SubscriptionPayment.objects.get(pk=payment.pk)
    assert payment.subscription_end < now()
    assert payment.subscription.end < now()


def test__google__google_notification_without_app_notification(db, google_in_app, client, google_subscription_purchase, google_rtdn_notification):
    with mock.patch('subscriptions.providers.google_in_app.GoogleInAppProvider.get_purchase', return_value=google_subscription_purchase):
        response = client.post('/api/webhook/google_in_app/', google_rtdn_notification, content_type="application/json")
        assert response.status_code == 200, response.content


def test__google__event_status_check(google_in_app, purchase_token, user, plan_with_google, client, google_subscription_purchase, google_rtdn_notification):
    SubscriptionPayment.objects.create(
        provider_codename=google_in_app.codename,
        provider_transaction_id=purchase_token,
        status=SubscriptionPayment.Status.COMPLETED,
        user=user,
        plan=plan_with_google,
    )

    # TODO: not all cases covered
    with mock.patch.object(google_subscription_purchase, 'subscriptionState', GoogleSubscriptionState.PAUSED):
        with mock.patch('subscriptions.providers.google_in_app.GoogleInAppProvider.get_purchase', return_value=google_subscription_purchase):
            response = client.post('/api/webhook/google_in_app/', google_rtdn_notification, content_type="application/json")
            # google posted notification with PURCHASED state, but real purchase has PAUSED state
            # -> something went wrong
            assert response.status_code == 400, response.content


def test__google__purchase_acknowledgement(google_in_app, user_client, google_subscription_purchase, app_notification, plan_with_google):
    with mock.patch.object(google_subscription_purchase, 'acknowledgementState', GoogleAcknowledgementState.PENDING):
        with mock.patch(
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


def test__google__check_event():
    ...  # TODO


def test__google__purchase_flow(google_in_app, purchase_token, user, plan_with_google, client, user_client, app_notification, google_subscription_purchase, google_rtdn_notification_factory):
    """ Test initial purchase and renewal """

    with mock.patch('subscriptions.providers.google_in_app.GoogleInAppProvider.get_purchase', return_value=google_subscription_purchase):
        response = user_client.post('/api/webhook/google_in_app/', app_notification, content_type="application/json")
        assert response.status_code == 200, response.content

    assert SubscriptionPayment.objects.exists()

    with mock.patch('subscriptions.providers.google_in_app.GoogleInAppProvider.get_purchase', return_value=google_subscription_purchase):
        response = client.post(
            '/api/webhook/google_in_app/',
            google_rtdn_notification_factory(GoogleSubscriptionNotificationType.PURCHASED),
            content_type="application/json",
        )
        assert response.status_code == 200, response.content

    payment1 = SubscriptionPayment.objects.latest()
    subscription = payment1.subscription
    assert subscription
    assert subscription.end == payment1.subscription_end

    google_subscription_purchase.lineItems[0].expiryTime = (now() + days(10)).isoformat()
    with mock.patch('subscriptions.providers.google_in_app.GoogleInAppProvider.get_purchase', return_value=google_subscription_purchase):
        response = client.post(
            '/api/webhook/google_in_app/',
            google_rtdn_notification_factory(GoogleSubscriptionNotificationType.RENEWED),
            content_type="application/json",
        )
        assert response.status_code == 200, response.content

    assert SubscriptionPayment.objects.count() == 2
    payment2 = SubscriptionPayment.objects.latest()
    assert payment2.subscription.end == payment2.subscription_end


def test__google__expiration_notification(google_in_app, purchase_token, user, plan_with_google, client, google_rtdn_notification_factory, google_in_app_payment, google_subscription_purchase):

    assert google_in_app_payment.subscription.end == google_in_app_payment.subscription_end
    initial_subscription_end = google_in_app_payment.subscription_end

    # test that subscription end date is set to expiration time even if it's BEFORE payment end date
    new_expiry_time = initial_subscription_end - days(1)
    google_subscription_purchase.lineItems[0].expiryTime = new_expiry_time.isoformat()
    google_subscription_purchase.subscriptionState = GoogleSubscriptionState.EXPIRED

    with mock.patch('subscriptions.providers.google_in_app.GoogleInAppProvider.get_purchase', return_value=google_subscription_purchase):
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


def test__google__check_payments(google_in_app):
    ...  # TODO


def test__google__subscription_notification_models(google_in_app__subscription_purchase_dict):
    GoogleSubscriptionPurchaseV2.parse_obj(google_in_app__subscription_purchase_dict)


def test__google__subscriptions__cancel__google(
    user,
    client,
    user_client,
    subscription,
    google_in_app,
    google_subscription_purchase,
    google_rtdn_notification_factory,
):
    subscription.end = now() + days(10)
    subscription.save()

    SubscriptionPayment.objects.create(
        user=user,
        plan=subscription.plan,
        subscription=subscription,
        provider_codename=google_in_app.codename,
        provider_transaction_id='12345',
        status=SubscriptionPayment.Status.PENDING,
        subscription_start=now() + days(10),
        subscription_end=now() + days(40),
    )

    assert user.subscriptions.active().count() == 1

    # manual cancellation doesn't work
    response = user_client.delete(f'/api/subscriptions/{subscription.uid}/')
    assert response.status_code == 400, response.content
    assert user.subscriptions.active().count() == 1
    assert user.subscriptions.active().latest().auto_prolong is True

    # google play store cancellation works
    google_subscription_purchase.subscriptionState = GoogleSubscriptionState.CANCELED
    with mock.patch('subscriptions.providers.google_in_app.GoogleInAppProvider.get_purchase', return_value=google_subscription_purchase):
        notification = google_rtdn_notification_factory(GoogleSubscriptionNotificationType.CANCELED)
        response = client.post('/api/webhook/google_in_app/', notification, content_type="application/json")
        assert response.status_code == 200, response.content

    assert user.subscriptions.active().count() == 1
    assert user.subscriptions.active().latest().auto_prolong is False
