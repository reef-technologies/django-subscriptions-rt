"""
Test the notifications.

These are mainly used for win back emails.
"""
from unittest import mock
import pytest

from subscriptions.models import Subscription
from subscriptions.logic.notifications import NotificationManager, get_default_notification_manager
from subscriptions import tasks
from datetime import timedelta
from django.utils.timezone import now


@pytest.fixture()
def notifications(monkeypatch) -> NotificationManager:
    """
    The notifications use a single instance whose state changes,
    to avoid conflicts.
    """
    _notifications = NotificationManager._notifications
    NotificationManager._notifications = {}
    yield get_default_notification_manager()
    NotificationManager._notifications = _notifications


@pytest.fixture()
def ended_subscription(user, plan) -> Subscription:
    """
    A subscription ended a few days ago
    """
    return Subscription.objects.create(
        user=user,
        plan=plan,
        end=now() - timedelta(5, hours=-3),
        start=now() - timedelta(15)
    )


@pytest.fixture()
def default_plan_subscription(user, default_plan) -> Subscription:
    """
    Default plan with end in the future.
    """
    return Subscription.objects.create(
        user=user,
        plan=default_plan,
        end=now() + timedelta(90),
        start=now()
    )


@pytest.mark.django_db(transaction=True, databases=['actual_db'])
def test_user_notification_simple_case(
        notifications: NotificationManager,
        ended_subscription: Subscription
):
    """
    Scenario:
    The user has an expired subscription 5 days ago.
    No default subscription.

    The notification function is called.
    """
    mocked = mock.Mock()
    # will remove a few hours to be sure.
    notifications.register(
        'test',
        days_since_subscriptions_end=5
    )(mocked)
    notifications.execute('test')
    mocked.assert_called_once_with(ended_subscription.user)


@pytest.mark.django_db(transaction=True, databases=['actual_db'])
def test_user_notification_prevented_existing_subscription(
        notifications: NotificationManager,
        ended_subscription: Subscription,
        subscription: Subscription
):
    """
    Scenario:
    The user has an expired subscription 5 days ago,
    but they have a subscription that whose end is more than 5 days ago,
    possibly but not necessarily in some future date

    The notification function is never called.
    """
    mocked = mock.Mock()
    notifications.register(
        f'test',
        days_since_subscriptions_end=5
    )(mocked)
    notifications.execute('test')
    mocked.assert_not_called()


@pytest.mark.django_db(transaction=True, databases=['actual_db'])
def test_user_notification_only_once(
        notifications: NotificationManager,
        ended_subscription: Subscription
):
    """
    Scenario:
    The scheduler runs multiple times a day.

    The execute method must call the user at most one time.
    """
    mocked = mock.Mock()
    notifications.register(
        'test',
        days_since_subscriptions_end=5
    )(mocked)
    notifications.execute('test')
    notifications.execute('test')
    mocked.assert_called_once_with(ended_subscription.user)


@pytest.mark.this
class TestDefaultPlanScenario:
    """
    Scenarios related to the default plan
    """
    @pytest.mark.django_db(transaction=True, databases=['actual_db'])
    def test_default_plan_is_ignored(
            self,
            ended_subscription: Subscription,
            default_plan_subscription: Subscription,
            notifications: NotificationManager,

    ):
        """
        Scenario:
        The user paid_subscription expired, he still has a default subscription

        The user will be notified.
        """
        mocked = mock.Mock()
        notifications.register(
            'test_default_plan_is_ignored',
            days_since_subscriptions_end=5
        )(mocked)
        notifications.execute('test_default_plan_is_ignored')
        mocked.assert_called_once_with(ended_subscription.user)


@pytest.mark.django_db(transaction=True, databases=['actual_db'])
def test_clear_notification(
        notifications: NotificationManager,
        ended_subscription: Subscription
):
    """
    Scenario:
    The scheduler runs multiple times a day.

    The execute method must call the user at most one time.
    """
    mocked = mock.Mock()
    notifications.register(
        'test',
        days_since_subscriptions_end=5,
        forget_after=0
    )(mocked)
    notifications.execute('test')
    notifications.execute('test')
    mocked.assert_has_calls([
        mock.call(ended_subscription.user),
        mock.call(ended_subscription.user),
    ])


class TestDispatchNotificationTask:
    @pytest.mark.django_db(transaction=True, databases=['actual_db'])
    def test_dispatch_all_notification(
            self,
            notifications: NotificationManager,
            ended_subscription: Subscription
    ):
        """
        Scenario:
        The scheduler runs multiple times a day.

        The execute method must call the user at most one time.
        """
        mocked_1 = mock.Mock()
        mocked_2 = mock.Mock()
        notifications.register(
            'test',
            days_since_subscriptions_end=5
        )(mocked_1)
        notifications.register(
            'test_2',
            days_since_subscriptions_end=5
        )(mocked_2)
        tasks.dispatch_notifications()
        mocked_1.assert_called_once_with(ended_subscription.user)
        mocked_2.assert_called_once_with(ended_subscription.user)

    @pytest.mark.django_db(transaction=True, databases=['actual_db'])
    def test_dispatch_single_notification(
            self,
            notifications: NotificationManager,
            ended_subscription: Subscription
    ):
        """
        Scenario:
        The scheduler runs multiple times a day.

        The execute method must call the user at most one time.
        """
        mocked_1 = mock.Mock()
        mocked_2 = mock.Mock()
        notifications.register(
            'test_1',
            days_since_subscriptions_end=5
        )(mocked_1)
        notifications.register(
            'test_2',
            days_since_subscriptions_end=5
        )(mocked_2)
        tasks.dispatch_notifications('test_1')
        mocked_1.assert_called_once_with(ended_subscription.user)
        mocked_2.assert_not_called()
