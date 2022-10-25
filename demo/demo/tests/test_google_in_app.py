from dataclasses import dataclass
from typing import Any, Optional
from unittest import mock

import pytest
from django.utils.timezone import now
from freezegun import freeze_time
from more_itertools import one
from subscriptions.models import Plan, Subscription, SubscriptionPayment
from subscriptions.providers.google_in_app.models import GoogleAcknowledgementState, GoogleSubscription, GoogleSubscriptionNotificationType, GoogleSubscriptionState
from subscriptions.utils import fromisoformat


def test_iter_subscriptions(google_in_app):
    list(google_in_app.iter_subscriptions())


@dataclass
class Executable:
    result: Optional[Any] = None

    def execute(self) -> Any:
        return self.result


@pytest.mark.skip()
def test_plan_push(google_in_app, plan):
    # when the plan in not in google -> push it

    with mock.patch(
        google_in_app.subscriptions_api,
        'list',
        return_value=Executable([
            GoogleSubscription(productId=plan.codename + '-trololo'),  # some other ID
        ]),
    ), mock.patch(
        google_in_app.subscriptions_api,
        'create',
        return_value=Executable(),
    ):
        google_subscription_dict = google_in_app.as_google_subscription(plan).dict()

        google_in_app.sync_plans()
        google_in_app.subscriptions_api.create.assert_called_with(
            package_name=google_in_app.package_name,
            body=google_subscription_dict,
        )

        plan = Plan.objects.get(pk=plan.pk)
        assert plan.metadata[google_in_app.codename] == google_subscription_dict


@pytest.mark.skip()
def test_plan_sync(google_in_app, plan):
    # when plan differs from what is in google -> push updates to google
    ...  # TODO


@pytest.mark.skip()
def test_subscription_deactivation(google_in_app, plan):
    # google has a subscription but there's no enabled plan -> archive the subscription
    ...  # TODO


def test_dismiss_token(google_in_app, user, subscription, purchase_token):
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


def test_get_user_by_token(google_in_app, payment):
    assert google_in_app.get_user_by_token(payment.provider_transaction_id) is None

    payment.provider_codename = google_in_app.codename
    payment.save()
    assert google_in_app.get_user_by_token(payment.provider_transaction_id) == payment.user


def test_webhook_test_notification(google_in_app, test_notification, client):
    response = client.post('/api/webhook/google_in_app/', test_notification, content_type="application/json")
    assert response.status_code == 200, response.content


def test_webhook_for_app_notification_unauthorized(google_in_app, app_notification, client):
    response = client.post('/api/webhook/google_in_app/', app_notification, content_type="application/json")
    assert response.status_code == 403, response.content


def test_webhook_for_app_notification(google_in_app, app_notification, user_client, google_subscription_purchase):
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


def test_webhook_for_app_notification_duplicate(google_in_app, app_notification, user_client, google_subscription_purchase):
    assert not SubscriptionPayment.objects.exists()
    assert not Subscription.objects.exists()

    with mock.patch('subscriptions.providers.google_in_app.GoogleInAppProvider.get_purchase', return_value=google_subscription_purchase):
        for _ in range(3):
            response = user_client.post('/api/webhook/google_in_app/', app_notification, content_type="application/json")
            assert response.status_code == 200, response.content

            assert SubscriptionPayment.objects.count() == 1
            assert Subscription.objects.count() == 1


def test_webhook_linked_token_dismissing(google_in_app, app_notification, user_client, google_subscription_purchase, user, plan):
    linked_token = 'trololo'

    payment = SubscriptionPayment.objects.create(
        provider_codename=google_in_app.codename,
        provider_transaction_id=linked_token,
        status=SubscriptionPayment.Status.COMPLETED,
        user=user,
        plan=plan,
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


def test_google_notification_without_app_notification(google_in_app, client, google_subscription_purchase, google_rtdn_notification):
    with mock.patch('subscriptions.providers.google_in_app.GoogleInAppProvider.get_purchase', return_value=google_subscription_purchase):
        response = client.post('/api/webhook/google_in_app/', google_rtdn_notification, content_type="application/json")
        assert response.status_code == 200, response.content


def test_event_status_check(google_in_app, purchase_token, user, plan, client, google_subscription_purchase, google_rtdn_notification):
    SubscriptionPayment.objects.create(
        provider_codename=google_in_app.codename,
        provider_transaction_id=purchase_token,
        status=SubscriptionPayment.Status.COMPLETED,
        user=user,
        plan=plan,
    )

    # TODO: not all cases covered
    with mock.patch.object(google_subscription_purchase, 'subscriptionState', GoogleSubscriptionState.PAUSED):
        with mock.patch('subscriptions.providers.google_in_app.GoogleInAppProvider.get_purchase', return_value=google_subscription_purchase):
            response = client.post('/api/webhook/google_in_app/', google_rtdn_notification, content_type="application/json")
            # google posted notification with PURCHASED state, but real purchase has PAUSED state
            # -> something went wrong
            assert response.status_code == 400, response.content


def test_purchase_acknowledgement(google_in_app, user_client, google_subscription_purchase, app_notification, plan):
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
                subscriptionId=str(plan.codename),
                token=app_notification['purchase_token'],
                body=mock.ANY,
            )


def test_check_event():
    ...  # TODO


def test_purchase_flow(google_in_app, purchase_token, user, plan, client, user_client, app_notification, google_subscription_purchase, google_rtdn_notification_factory, now, days):
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

    google_subscription_purchase.lineItems[0].expiryTime = (now + days(10)).isoformat()
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

    # TODO: not all cases covered


def test_check_payments(google_in_app):
    ...  # TODO
