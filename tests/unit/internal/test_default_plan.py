from datetime import timedelta

import pytest
from constance import config
from django.contrib.auth import get_user_model
from django.utils.timezone import now
from freezegun import freeze_time
from more_itertools import one

from subscriptions.v0.functions import get_default_plan
from subscriptions.v0.models import Plan, Subscription, SubscriptionPayment

from ..helpers import days


@pytest.mark.django_db(databases=["actual_db"])
def test__default_plan__does_not_exist(settings, plan):
    assert get_default_plan() is None

    with pytest.raises(Plan.DoesNotExist):
        config.SUBSCRIPTIONS_DEFAULT_PLAN_ID = 12345


@pytest.mark.django_db(databases=["actual_db"])
def test__default_plan__exists(default_plan):
    assert get_default_plan() == default_plan


@pytest.mark.django_db(databases=["actual_db"])
def test__default_plan__created_for_new_user(default_plan, plan):
    assert not Subscription.objects.exists()

    User = get_user_model()
    user = User.objects.create(username="hehetrololo", email="donald@trump.com")
    assert Subscription.objects.count() == 1

    subscription = Subscription.objects.first()
    assert subscription.plan == default_plan
    assert subscription.user == user
    now_ = now()
    assert now_ - timedelta(seconds=1) < subscription.start < now_
    assert subscription.end > now_ + days(
        365 * 5
    )  # I will probably work somewhere else in 5 years, so no need to check further :D


@pytest.mark.django_db(databases=["actual_db"])
def test__default_plan__no_overlap_with_subscriptions(default_plan, plan, user):
    """
    -----[default subscription                      ]->
         ^-now

    After adding 2 subscriptions:
    -----[subscription 1]---------------------------->
    ----------[subscription 2]----------------------->
    --------------------------[default subscription]->
         ^-now
    """
    assert user.subscriptions.count() == 1
    assert user.subscriptions.first().plan == default_plan

    now_ = now()

    Subscription.objects.create(
        user=user,
        plan=plan,
        start=now_,
        end=now_ + days(7),
    )

    Subscription.objects.create(
        user=user,
        plan=plan,
        start=now_ + days(3),
        end=now_ + days(10),
    )

    assert user.subscriptions.count() == 4
    default_sub_before, default_sub_after = user.subscriptions.filter(plan=default_plan).order_by("end")

    assert default_sub_before.start < now_
    assert default_sub_before.end == now_
    assert default_sub_after.start == now_ + days(10)
    assert default_sub_after.end > now_ + days(356 * 5)


@pytest.mark.django_db(databases=["actual_db"])
def test__default_plan__split_if_subscription_appears(default_plan, plan, user):
    """
    -----[default subscription               ]->
         ^-now
    After adding subscription:
    -----[default][new subscription][default ]->
                  ^-now
    """

    assert user.subscriptions.active().count() == 1
    default_subscription_old = user.subscriptions.filter(plan=default_plan).first()

    now_ = now()
    subscription = Subscription.objects.create(
        user=user,
        plan=plan,
        start=now_,
        end=now_ + days(7),
    )
    assert user.subscriptions.active().count() == 1
    assert user.subscriptions.active().first() == subscription

    assert user.subscriptions.count() == 3
    sub_before, sub_now, sub_after = user.subscriptions.order_by("start")

    assert sub_before.plan == default_plan
    assert sub_before.start == default_subscription_old.start
    assert sub_before.end == sub_now.start

    assert sub_after.plan == default_plan
    assert sub_after.start == sub_now.end
    assert sub_after.end >= now() + timedelta(days=365 * 5)


@pytest.mark.django_db(databases=["actual_db"])
def test__default_plan__enable__old_subscription(user, subscription, settings):
    """
    -----[subscription]--------------------->
                            ^-now
    After adding default plan:
    -----[subscription]-----[default subscription   ]->
                            ^-now
    """

    with freeze_time(subscription.end + days(10), tick=True):
        assert user.subscriptions.count() == 1

        default_plan = Plan.objects.create(codename="default", charge_amount=0)
        now_ = now()

        config.SUBSCRIPTIONS_DEFAULT_PLAN_ID = default_plan.pk
        subscriptions = user.subscriptions.order_by("end")
        assert subscriptions.count() == 2
        assert subscriptions[0] == subscription
        assert subscriptions[1].plan == default_plan
        assert now_ - timedelta(seconds=1) < subscriptions[1].start < now_ + timedelta(seconds=1)
        assert subscriptions[1].end > now_ + days(365 * 5)


@pytest.mark.django_db(databases=["actual_db"])
def test__default_plan__enable__active_subscription(user, subscription, settings):
    """
    -----[subscription        ]------------------->
                        ^-now
    After adding default plan:
    -----[subscription        ][default subscription    ]->
                         ^-now
    """
    assert user.subscriptions.count() == 1
    subscription.end = now() + days(7)
    subscription.save()

    default_plan = Plan.objects.create(codename="default", charge_amount=0)
    config.SUBSCRIPTIONS_DEFAULT_PLAN_ID = default_plan.pk

    subscriptions = user.subscriptions.order_by("end")
    assert subscriptions.count() == 2
    assert subscriptions[0] == subscription
    assert subscriptions[1].plan == default_plan
    assert subscriptions[1].start == subscription.end
    assert subscriptions[1].end > subscription.end + days(365 * 5)


@pytest.mark.django_db(databases=["actual_db"])
def test__default_plan__disabling__active(user, subscription, default_plan):
    """
    -----[subscription][default plan]----->
                               ^-now
    After disabling default plan:
    -----[subscription][default]----->
                               ^-now
    """

    with freeze_time(subscription.end + days(10), tick=True):
        assert user.subscriptions.count() == 2
        assert user.subscriptions.active().count() == 1

        active_subscription = one(user.subscriptions.active())
        assert active_subscription.plan == default_plan
        assert active_subscription.start < now()
        assert active_subscription.end > now()

        config.SUBSCRIPTIONS_DEFAULT_PLAN_ID = 0
        assert user.subscriptions.count() == 2
        assert not user.subscriptions.active().exists()
        last_subscription = user.subscriptions.order_by("end").last()
        assert last_subscription.plan == default_plan
        assert now() - timedelta(seconds=2) < last_subscription.end < now()


@pytest.mark.django_db(databases=["actual_db"])
def test__default_plan__disabling__future(user, subscription, default_plan):
    """
    -----[subscription        ][default plan]----->
                        ^-now
    After disabling default plan:
    -----[subscription        ]------------------->
                         ^-now
    """
    with freeze_time(subscription.end - days(1), tick=True):
        assert user.subscriptions.count() == 2
        assert user.subscriptions.active().count() == 1

        default_subscription = user.subscriptions.order_by("end").last()
        assert default_subscription.plan == default_plan
        assert default_subscription.start > now()

        config.SUBSCRIPTIONS_DEFAULT_PLAN_ID = 0
        assert user.subscriptions.count() == 1
        assert not user.subscriptions.filter(plan=default_plan).exists()


@pytest.mark.django_db(databases=["actual_db"])
def test__default_plan__switch__active(user, default_plan):
    """
    -----[default plan                    ]->
                        ^-now
    After switching default plan:
    -----[default plan  ][new default plan]->
                         ^-now
    """
    assert user.subscriptions.count() == 1
    subscription = user.subscriptions.first()
    assert subscription.plan == default_plan
    assert subscription.end > now() + days(365)

    new_default_plan = Plan.objects.create(codename="new default", charge_amount=0)
    config.SUBSCRIPTIONS_DEFAULT_PLAN_ID = new_default_plan.pk

    assert user.subscriptions.count() == 2

    subscription = Subscription.objects.get(pk=subscription.pk)
    assert subscription.plan == default_plan
    assert now() - timedelta(seconds=1) < subscription.end < now()

    new_subscription = Subscription.objects.active().first()
    assert new_subscription.plan == new_default_plan
    assert now() - timedelta(seconds=1) < new_subscription.start < now()
    assert new_subscription.end > now() + days(365)


@pytest.mark.django_db(databases=["actual_db"])
def test__default_plan__switch__future(user, subscription, default_plan):
    """
    --[subscription][default plan    ]->
        ^-now
    After switching default plan:
    --[subscription][new default plan]->
        ^-now
    """
    assert user.subscriptions.count() == 2

    with freeze_time(subscription.end - days(1), tick=True):
        default_subscription = user.subscriptions.order_by("end").last()
        assert default_subscription.plan == default_plan

        new_default_plan = Plan.objects.create(codename="new default", name="New default", charge_amount=0)
        config.SUBSCRIPTIONS_DEFAULT_PLAN_ID = new_default_plan.pk

        assert user.subscriptions.count() == 2
        new_subscription = user.subscriptions.order_by("end").last()

        assert new_subscription.pk == default_subscription.pk
        assert new_subscription.plan == new_default_plan
        assert new_subscription.start == default_subscription.start
        assert new_subscription.end == default_subscription.end


@pytest.mark.django_db(databases=["actual_db"])
def test__default_plan__non_recurring__ignore_save(user, default_plan, recharge_plan):
    """
    ---[=default plan=============]->
       ^--now
    After adding non-recurring subscription:
    ---[=default plan=============]->
    ----[=recharge plan======]------->
        ^--now
    """

    assert user.subscriptions.active().count() == 1
    default_subscription_old = user.subscriptions.active().first()

    Subscription.objects.create(
        user=user,
        plan=recharge_plan,
    )
    assert user.subscriptions.active().count() == 2
    default_subscription_new = user.subscriptions.active().filter(plan=default_plan).first()

    assert default_subscription_old.start == default_subscription_new.start
    assert default_subscription_old.end == default_subscription_new.end


@pytest.mark.django_db(databases=["actual_db"])
def test__default_plan__non_recurring__ignore_when_adding_default(user, recharge_plan):
    """
    ----[recharge plan      ]------->
        ^--now
    After enabling default subscription:
    ----[recharge plan      ]------->
    -----[default plan            ]->
         ^--now
    """
    assert user.subscriptions.active().count() == 0

    subscription_old = Subscription.objects.create(
        user=user,
        plan=recharge_plan,
    )
    assert user.subscriptions.active().count() == 1

    new_default_plan = Plan.objects.create(codename="new default", charge_amount=0)
    config.SUBSCRIPTIONS_DEFAULT_PLAN_ID = new_default_plan.pk

    assert user.subscriptions.active().count() == 2

    subscription_new = user.subscriptions.filter(plan=recharge_plan).first()
    assert subscription_old.start == subscription_new.start
    assert subscription_old.end == subscription_new.end

    default_subscription = user.subscriptions.active().filter(plan=recharge_plan).first()
    assert now() - timedelta(seconds=1) < default_subscription.start < now()


@pytest.mark.django_db(databases=["actual_db"])
def test__default_plan__subscription_payment__num_subscriptions(user, default_plan, subscription, plan):
    """
    Before payment:
    --[==default plan==][==subscription==][===========default plan===========]->
                                       ^-now
    After payment:
    --[==default plan==][==subscription===================][==default plan==]->
                                       ^-now
    """

    assert user.subscriptions.count() == 3
    SubscriptionPayment.objects.create(
        user=user,
        subscription=subscription,
        plan=plan,
        status=SubscriptionPayment.Status.COMPLETED,
        paid_since=subscription.end,
        paid_until=subscription.prolong(),
        provider_codename="dummy",
    )
    assert user.subscriptions.count() == 3


@pytest.mark.django_db(databases=["actual_db"])
def test__default_plan__subscription__shrink__before_default(user, default_plan, subscription, plan):
    """
    Before:
    --[==default plan==][=======subscription=======][======default plan======]->

    After:
    --[==default plan==][==subscription==][===========default plan===========]->

    """

    subscriptions_before = list(user.subscriptions.order_by("end"))
    assert len(subscriptions_before) == 3
    assert subscriptions_before[0].plan == subscriptions_before[2].plan == default_plan
    assert subscriptions_before[1].plan == plan
    assert subscriptions_before[0].end == subscriptions_before[1].start
    assert subscriptions_before[1].end == subscriptions_before[2].start

    subscription.end -= days(3)
    subscription.save()

    subscriptions_after = list(user.subscriptions.order_by("end"))
    assert len(subscriptions_after) == 3
    assert subscriptions_after[0].plan == subscriptions_after[2].plan == default_plan
    assert subscriptions_after[1].plan == plan
    assert subscriptions_after[1].start == subscriptions_before[1].start
    assert subscriptions_after[1].end == subscriptions_before[1].end - days(3)
    assert subscriptions_after[0].end == subscriptions_after[1].start
    assert subscriptions_after[1].end == subscriptions_after[2].start


@pytest.mark.django_db(databases=["actual_db"])
def test__default_plan__subscription__shrink__before_other_subscription(user, subscription, default_plan, plan):
    """
    Before:
    --[=======subscription=======][=======subscription=======][======default plan======]->

    After:
    --[=subscription=][=default==][=======subscription=======][======default plan======]->
    """

    # add second subscription to match the "before" image
    Subscription.objects.create(
        user=user,
        plan=plan,
        start=subscription.end,
    )

    # check initial configuration
    subscriptions_before = list(user.subscriptions.order_by("end"))
    assert len(subscriptions_before) == 3
    assert subscriptions_before[0].plan == subscriptions_before[1].plan == plan
    assert subscriptions_before[2].plan == default_plan
    assert subscriptions_before[0].end == subscriptions_before[1].start
    assert subscriptions_before[1].end == subscriptions_before[2].start

    # shrink subscription
    subscription.end -= days(3)
    subscription.save()

    # check after configuration
    subscriptions_after = list(user.subscriptions.order_by("end"))
    assert len(subscriptions_after) == 4
    assert subscriptions_after[0].plan == subscriptions_after[2].plan == plan
    assert subscriptions_after[1].plan == subscriptions_after[3].plan == default_plan
    assert subscriptions_after[0].end == subscriptions_after[1].start == subscriptions_before[0].end - days(3)
    assert subscriptions_after[1].end == subscriptions_after[2].start
    assert subscriptions_after[2].end == subscriptions_after[3].start


@pytest.mark.django_db(databases=["actual_db"])
def test__default_plan__subscription__eaten(user, subscription, default_plan, plan):
    """
    Before:
    --[==subscription1==][==default plan==][==subscription2==][====default plan====]>

    After:
    --[==subscription1==========================================]------------------->
    ---------------------------------------[==subscription2==]--[===default plan===]>

    """

    # add second subscription to match the "before" image
    subscription2 = Subscription.objects.create(
        user=user,
        plan=plan,
        start=subscription.end + days(10),
    )

    # check initial configuration
    subscriptions_before = list(user.subscriptions.order_by("end"))
    assert len(subscriptions_before) == 4
    assert subscriptions_before[0].plan == subscriptions_before[2].plan == plan
    assert subscriptions_before[1].plan == subscriptions_before[3].plan == default_plan
    assert subscriptions_before[0].end == subscriptions_before[1].start
    assert subscriptions_before[1].end == subscriptions_before[2].start
    assert subscriptions_before[2].end == subscriptions_before[3].start

    # shrink subscription
    subscription.end = subscription2.end + days(1)
    subscription.save()

    # check after configuration
    subscriptions_after = list(user.subscriptions.order_by("start"))
    assert len(subscriptions_after) == 3
    assert subscriptions_after[0].plan == subscriptions_after[1].plan == plan
    assert subscriptions_after[2].plan == default_plan
    assert subscriptions_after[0].end == subscriptions_after[1].end + days(1)
    assert subscriptions_after[0].end == subscriptions_after[1].end + days(1)
    assert subscriptions_after[2].start == subscriptions_after[0].end


@pytest.mark.django_db(databases=["actual_db"])
def test__default_plan__subscription_renewal(user, default_plan, subscription, payment, plan):
    """
    Before renewal:
    --[==default plan==][==subscription==][===========default plan===========]->
                                       ^-now
    After renewal:
    --[==default plan==][==subscription===================][==default plan==]->
                                       ^-now
    """
    assert payment.paid_until == subscription.end

    subscriptions_before = list(user.subscriptions.order_by("end"))
    assert len(subscriptions_before) == 3
    assert subscriptions_before[0].plan == subscriptions_before[2].plan == default_plan
    assert subscriptions_before[1].plan == plan
    assert subscriptions_before[0].end == subscriptions_before[1].start
    assert subscriptions_before[1].end == subscriptions_before[2].start

    with freeze_time(subscription.end - days(1), tick=True):
        last_payment = subscription.charge_automatically()

        assert last_payment.paid_since == subscriptions_before[1].end
        assert last_payment.paid_until > subscriptions_before[1].end

        subscriptions_after = list(user.subscriptions.order_by("end"))
        assert len(subscriptions_after) == 3

        assert subscriptions_after[0].start == subscriptions_before[0].start
        assert subscriptions_after[0].end == subscriptions_before[0].end
        assert subscriptions_after[0].plan == subscriptions_before[0].plan == default_plan

        assert subscriptions_after[1].start == subscriptions_before[1].start
        assert subscriptions_after[1].end == subscriptions_before[1].end + plan.charge_period
        assert subscriptions_after[1].plan == subscriptions_before[1].plan == plan

        assert subscriptions_after[2].start == subscriptions_before[2].start + plan.charge_period
        assert subscriptions_after[2].end == subscriptions_before[2].end
        assert subscriptions_after[2].plan == subscriptions_before[2].plan == default_plan
