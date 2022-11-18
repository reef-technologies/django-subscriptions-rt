"""
Content of this test is based of off https://www.caktusgroup.com/blog/2016/02/02/writing-unit-tests-django-migrations/
"""
import datetime
import uuid
from decimal import Decimal
from typing import (
    Any,
    Callable,
)

import pytest
from dateutil.relativedelta import relativedelta
from django.apps.registry import Apps
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


BASIC_PAYMENT_KWARGS = {
    'status': 2,  # It used to mean "COMPLETED".
    'amount': None,
    'subscription_start': datetime.datetime(2022, 3, 1, tzinfo=datetime.timezone.utc),
    'subscription_end': datetime.datetime(2022, 3, 2, tzinfo=datetime.timezone.utc),
    'created': datetime.datetime(2022, 3, 1, tzinfo=datetime.timezone.utc),
    'updated': datetime.datetime(2022, 3, 1, tzinfo=datetime.timezone.utc),
}


def run_migration(app: str, migrate_from: str, migrate_to: str, pre_migration_run: Callable[[Apps], None]) -> Apps:
    migrate_from = [(app, migrate_from)]
    migrate_to = [(app, migrate_to)]
    executor = MigrationExecutor(connection)
    old_apps = executor.loader.project_state(migrate_from).apps

    # Reverse to the original migration
    executor.migrate(migrate_from)

    pre_migration_run(old_apps)

    # Run the migration to test
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()  # reload.
    executor.migrate(migrate_to)

    return executor.loader.project_state(migrate_to).apps


def make_user_plans_and_subscriptions(apps: Apps,
                                      num_plans: int = 1,
                                      num_subscriptions_per_plan: int = 1) -> tuple[Any, list[Any], list[Any]]:
    user_model = apps.get_model('auth', 'User')
    user = user_model.objects.create(username='test1')

    plan_model = apps.get_model('subscriptions', 'Plan')
    result_plans = [
        plan_model.objects.create(
            codename=f'plan-{idx}',
            name=f'Plan-{idx}',
            charge_amount=Decimal(100),
            charge_period=relativedelta(days=30),
            max_duration=relativedelta(days=120),
            metadata={
                'this': 'that',
            }
        )
        for idx in range(num_plans)
    ]

    subscription_model = apps.get_model('subscriptions', 'Subscription')
    result_subscriptions = [
        subscription_model.objects.create(
            uid=str(uuid.uuid4()),
            user=user,
            plan=plan,
            quantity=1,
            start=datetime.datetime(2022, 3, 1, tzinfo=datetime.timezone.utc),
            end=datetime.datetime(2022, 3, 2, tzinfo=datetime.timezone.utc),
        )
        for plan in result_plans
        # Duplicated subscriptions.
        for _ in range(num_subscriptions_per_plan)
    ]

    return user, result_plans, result_subscriptions


@pytest.mark.django_db
def test__check_migration_deduplication(apple_in_app, days):
    expected_count = 0

    def pre_migration_run(pre_apps: Apps) -> None:
        nonlocal expected_count
        pre_model = pre_apps.get_model('subscriptions', 'SubscriptionPayment')

        user, plans, subscriptions = make_user_plans_and_subscriptions(pre_apps, num_subscriptions_per_plan=6)

        kwargs = {
            'provider_codename': apple_in_app.codename,
            'user': user,
            'plan': plans[0],
            **BASIC_PAYMENT_KWARGS
        }

        def make_payment_with_subscription(id_: str, index: int) -> None:
            pre_model.objects.create(
                uid=str(uuid.uuid4()),
                provider_transaction_id=id_,
                subscription=subscriptions[index],
                **kwargs
            )

        # Case 1: single subscription, this one should stay as is
        expected_count += 1
        make_payment_with_subscription('transaction_id_1', index=0)

        # Case 2: double subscription and a single subscription, a single entry from double subscriptions should stay
        expected_count += 2  # Basic transaction and renewal transaction stays.
        make_payment_with_subscription('transaction_id_2', index=1)
        make_payment_with_subscription('transaction_id_3', index=1)  # This is e.g. renewal.
        make_payment_with_subscription('transaction_id_2', index=2)

        # Case 3: three single subscriptions, any of these should stay but only one.
        expected_count += 1
        make_payment_with_subscription('transaction_id_4', index=3)
        make_payment_with_subscription('transaction_id_4', index=4)
        make_payment_with_subscription('transaction_id_4', index=5)

    apps = run_migration(
        'subscriptions',
        '0026_alter_subscriptionpayment_status_and_more',
        '0027_auto_20221109_1525',
        pre_migration_run,
    )

    # We should have no more duplicates in the DB.
    model = apps.get_model('subscriptions', 'SubscriptionPayment')
    all_transaction_ids = [entry.provider_transaction_id for entry in model.objects.all()]
    assert len(set(all_transaction_ids)) == len(all_transaction_ids)
    assert len(all_transaction_ids) == expected_count


@pytest.mark.django_db
def test__fail__same_transaction_different_plan(apple_in_app, days):
    def pre_migration_run(pre_apps) -> None:
        pre_model = pre_apps.get_model('subscriptions', 'SubscriptionPayment')

        user, plans, subscriptions = make_user_plans_and_subscriptions(pre_apps, num_plans=2)

        kwargs = {
            'provider_codename': apple_in_app.codename,
            'user': user,
            **BASIC_PAYMENT_KWARGS
        }

        for plan, subscription in zip(plans, subscriptions):
            pre_model.objects.create(
                uid=str(uuid.uuid4()),
                provider_transaction_id='transaction_id_1',
                plan=plan,
                subscription=subscription,
                **kwargs
            )

    with pytest.raises(AssertionError):
        run_migration(
            'subscriptions',
            '0026_alter_subscriptionpayment_status_and_more',
            '0027_auto_20221109_1525',
            pre_migration_run,
        )


@pytest.mark.django_db
def test__fail__multiple_reused_subscriptions(apple_in_app, days):
    def pre_migration_run(pre_apps) -> None:
        pre_model = pre_apps.get_model('subscriptions', 'SubscriptionPayment')

        user, plans, subscriptions = make_user_plans_and_subscriptions(pre_apps, num_subscriptions_per_plan=2)

        kwargs = {
            'provider_codename': apple_in_app.codename,
            'user': user,
            'plan': plans[0],
            **BASIC_PAYMENT_KWARGS
        }

        pre_model.objects.create(
            uid=str(uuid.uuid4()),
            provider_transaction_id='transaction_id_1',
            subscription=subscriptions[0],
            **kwargs
        )
        pre_model.objects.create(
            uid=str(uuid.uuid4()),
            provider_transaction_id='transaction_id_1',
            subscription=subscriptions[0],
            **kwargs
        )
        pre_model.objects.create(
            uid=str(uuid.uuid4()),
            provider_transaction_id='transaction_id_1',
            subscription=subscriptions[1],
            **kwargs
        )
        pre_model.objects.create(
            uid=str(uuid.uuid4()),
            provider_transaction_id='transaction_id_1',
            subscription=subscriptions[1],
            **kwargs
        )

    with pytest.raises(AssertionError):
        run_migration(
            'subscriptions',
            '0026_alter_subscriptionpayment_status_and_more',
            '0027_auto_20221109_1525',
            pre_migration_run,
        )
