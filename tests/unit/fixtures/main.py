from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import wraps

import pytest
from constance import config
from dateutil.relativedelta import relativedelta
from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.test import Client
from django.utils.timezone import now
from djmoney.money import Money
from freezegun import freeze_time

from subscriptions.v0.defaults import DEFAULT_SUBSCRIPTIONS_VALIDATORS
from subscriptions.v0.functions import (
    get_remaining_amount,
    get_remaining_chunks,
    get_resource_refresh_moments,
)
from subscriptions.v0.models import (
    INFINITY,
    Plan,
    Quota,
    QuotaCache,
    Resource,
    Subscription,
    SubscriptionPayment,
    Usage,
)
from subscriptions.v0.providers import get_provider, get_provider_by_codename
from subscriptions.v0.providers.dummy import DummyProvider
from subscriptions.v0.tasks import charge_recurring_subscriptions
from subscriptions.v0.validators import get_validators

from ..helpers import days, usd


@pytest.fixture(autouse=True)
def clear_lru_cache():
    get_validators.cache_clear()
    get_provider.cache_clear()
    get_provider_by_codename.cache_clear()


@pytest.fixture
def eps() -> timedelta:
    return timedelta(microseconds=1)


@pytest.fixture
def user():
    return get_user_model().objects.create(
        username="test",
    )


@pytest.fixture
def other_user():
    return get_user_model().objects.create(
        username="test2",
    )


@pytest.fixture
def resource() -> Resource:
    return Resource.objects.create(
        codename="resource",
    )


@pytest.fixture
def plan(resource) -> Plan:
    return Plan.objects.create(
        name="Plan",
        slug="plan",
        charge_amount=usd(100),  # type: ignore[misc]
        charge_period=relativedelta(months=1),
        max_duration=relativedelta(months=4),
        metadata={
            "this": "that",
        },
    )


@pytest.fixture
def unlimited_plan(plan) -> Plan:
    plan.charge_period = None
    plan.save(update_fields=["charge_period"])


@pytest.fixture
def quota(plan, resource) -> Quota:
    return Quota.objects.create(
        plan=plan,
        resource=resource,
        limit=50,
    )


@pytest.fixture
def bigger_plan(resource) -> Plan:
    return Plan.objects.create(
        name="Bigger plan",
        slug="bigger-plan",
        charge_amount=usd(200),  # type: ignore[misc]
        charge_period=relativedelta(months=1),
    )


@pytest.fixture
def bigger_quota(bigger_plan, resource) -> Quota:
    return Quota.objects.create(
        plan=bigger_plan,
        resource=resource,
        limit=300,
    )


@pytest.fixture
def recharge_plan(resource) -> Plan:
    # $10 for 10 resources, expires in 14 days
    return Plan.objects.create(
        name="Recharge plan",
        slug="recharge-plan",
        charge_amount=usd(10),  # type: ignore[misc]
        charge_period=INFINITY,
        max_duration=days(14),
    )


@pytest.fixture
def recharge_quota(recharge_plan, resource) -> Quota:
    return Quota.objects.create(
        plan=recharge_plan,
        resource=resource,
        limit=10,
    )


@pytest.fixture
def subscription(user, plan) -> Subscription:
    return Subscription.objects.create(
        user=user,
        plan=plan,
        quantity=2,  # so limit = 50 * 2 = 100 in total
        start=datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC),
    )


@pytest.fixture
def validators(settings) -> None:
    settings.SUBSCRIPTIONS_VALIDATORS = DEFAULT_SUBSCRIPTIONS_VALIDATORS


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
def refreshes(user, resource) -> Callable:
    @wraps(get_resource_refresh_moments)
    def wrapped(**kwargs) -> datetime | None:
        return get_resource_refresh_moments(user=user, **kwargs).get(resource, None)

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
def two_subscriptions(user, resource) -> list[Subscription]:
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

    now_ = now()

    plan1 = Plan.objects.create(name="Plan 1")
    subscription1 = Subscription.objects.create(
        user=user,
        plan=plan1,
        start=now_,
        end=now_ + days(10),
    )
    Quota.objects.create(
        plan=plan1,
        resource=resource,
        limit=100,
        recharge_period=days(5),
        burns_in=days(7),
    )

    plan2 = Plan.objects.create(name="Plan 2", charge_amount=Money(10, "EUR"))  # type: ignore[misc]
    subscription2 = Subscription.objects.create(
        user=user,
        plan=plan2,
        start=now_ + days(4),
        end=now_ + days(14),
    )
    Quota.objects.create(
        plan=plan2,
        resource=resource,
        limit=100,
        recharge_period=days(5),
        burns_in=days(7),
    )

    Usage.objects.bulk_create(
        [
            Usage(user=user, resource=resource, amount=50, datetime=now_ + days(1)),
            Usage(user=user, resource=resource, amount=200, datetime=now_ + days(6)),
            Usage(user=user, resource=resource, amount=50, datetime=now_ + days(12)),
        ]
    )

    return [subscription1, subscription2]


@pytest.fixture
def five_subscriptions(plan, user) -> list[Subscription]:
    """
    Subscriptions:                    |now
    ----------------------------------[====sub0=====]-----> overlaps with "now"
    --------------------[======sub1=======]---------------> overlaps with "sub0"
    -------------[=sub2=]---------------------------------> does not overlap with "sub1"
    -----------------------[=sub3=]-----------------------> overlaps with "sub1"
    ----[=sub4=]------------------------------------------> does not overlap with anything
    """

    now_ = now()

    sub0 = Subscription.objects.create(user=user, plan=plan, start=now_ - days(5), end=now_ + days(2))
    sub1 = Subscription.objects.create(user=user, plan=plan, start=sub0.start - days(5), end=sub0.start + days(2))
    sub2 = Subscription.objects.create(user=user, plan=plan, start=sub1.start - days(5), end=sub1.start)
    sub3 = Subscription.objects.create(user=user, plan=plan, start=sub1.start + days(1), end=sub0.start - days(1))
    sub4 = Subscription.objects.create(user=user, plan=plan, start=sub2.start - days(5), end=sub2.start - days(1))
    return [sub0, sub1, sub2, sub3, sub4]


@pytest.fixture
def user_client(settings, client, user) -> Client:
    settings.SESSION_COOKIE_AGE = timedelta(days=365).total_seconds()
    client.force_login(user)
    return client


@pytest.fixture
def dummy(settings) -> DummyProvider:
    settings.SUBSCRIPTIONS_PAYMENT_PROVIDERS = [
        "subscriptions.v0.providers.dummy.DummyProvider",
    ]
    return get_provider_by_codename("dummy")


@pytest.fixture
def payment(dummy, subscription) -> SubscriptionPayment:
    return SubscriptionPayment.objects.create(
        user=subscription.user,
        plan=subscription.plan,
        subscription=subscription,
        provider_codename=dummy.codename,
        provider_transaction_id="12345",
        amount=subscription.plan.charge_amount,
        quantity=2,  # so limit = 50 * 2 = 100 in total
        status=SubscriptionPayment.Status.COMPLETED,
        paid_since=subscription.end,
        paid_until=subscription.prolong(),
        metadata={
            "subscription_id": "some-dummy-uid",
        },
        created=subscription.end,
    )


@pytest.fixture
def card_number() -> str:
    return " ".join(["4242"] * 4)


@pytest.fixture
def charge_schedule() -> list[timedelta]:
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
    """
    Charge expiring subscriptions.

    Call: charge_expiring(payment_status=SubscriptionPayment.Status.PENDING)
    """

    def wrapper(payment_status: SubscriptionPayment.Status = SubscriptionPayment.Status.COMPLETED):
        with monkeypatch.context() as monkey:
            # here we don't allow setting any status except `payment_status` to SubscriptionPayment
            monkey.setattr(
                "subscriptions.v0.models.SubscriptionPayment.__setattr__",
                lambda obj, name, value: super(SubscriptionPayment, obj).__setattr__(
                    name, payment_status if name == "status" else value
                ),
            )

            return charge_recurring_subscriptions(
                schedule=charge_schedule,
                num_threads=1,
            )

    return wrapper


@pytest.fixture
def cache_backend(settings):
    settings.CACHES["subscriptions"] = {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "subscriptions",
    }
    caches["subscriptions"].clear()


@pytest.fixture
def default_plan(settings) -> Plan:
    plan = Plan.objects.create(
        name="Default Plan",
        charge_amount=usd(0),  # type: ignore[misc]
    )
    with freeze_time(datetime(2000, 1, 1, 0, 0, 0, tzinfo=UTC)):
        config.SUBSCRIPTIONS_DEFAULT_PLAN_ID = plan.pk
    return plan


@pytest.fixture
def enable_advisory_lock(request, monkeypatch):
    """Set advisory lock, this fixture must be used with `parametrize`"""
    if request.param is not None:
        monkeypatch.setenv("SUBSCRIPTIONS_ENABLE_ADVISORY_LOCK", request.param)
    return request.param
