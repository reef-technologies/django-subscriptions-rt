from datetime import datetime, timedelta
from datetime import timezone as tz
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from payments.models import Plan, Resource, Subscription


@pytest.fixture
def now():
    return datetime(2021, 12, 30, 12, 00, 00, tzinfo=tz.utc)


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
def plan(db) -> Plan:
    return Plan.objects.create(
        codename='plan',
        name='Plan',
        charge_amount=Decimal(100),
        charge_period=timedelta(days=30),
    )


@pytest.fixture
def subscription(db, now, user, plan) -> Subscription:
    return Subscription.objects.create(
        user=user,
        plan=plan,
        start=now,
    )
