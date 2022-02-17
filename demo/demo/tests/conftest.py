from datetime import timedelta
from decimal import Decimal

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model

from payments.models import Plan, Resource, Subscription


@pytest.fixture
def user(db) -> settings.AUTH_USER_MODEL:
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
def subscription(db, user, plan) -> Subscription:
    return Subscription.objects.create(
        user=user,
        plan=plan,
    )



