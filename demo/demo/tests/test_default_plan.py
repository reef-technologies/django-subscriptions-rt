from datetime import timedelta

import pytest
from constance import config
from django.contrib.auth import get_user_model
from django.utils.timezone import now

from subscriptions.functions import get_default_plan
from subscriptions.models import Plan, Subscription


def test__default_plan__does_not_exist(settings, plan):
    assert get_default_plan() is None

    with pytest.raises(Plan.DoesNotExist):
        config.SUBSCRIPTIONS_DEFAULT_PLAN_ID = 12345


def test__default_plan__exists(default_plan):
    assert get_default_plan() == default_plan


def test__default_plan__created_for_new_user(default_plan, plan):
    assert not Subscription.objects.exists()

    User = get_user_model()
    user = User.objects.create(username='hehetrololo', email='donald@trump.com')
    assert Subscription.objects.count() == 1

    subscription = Subscription.objects.first()
    assert subscription.plan == default_plan
    assert subscription.user == user
    now_ = now()
    assert now_ - timedelta(seconds=1) < subscription.start < now_
    assert subscription.end > now_ + timedelta(days=365*5)  # I will probably work somewhere else in 5 years, so no need to check further :D


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
        end=now_ + timedelta(days=7),
    )

    Subscription.objects.create(
        user=user,
        plan=plan,
        start=now_ + timedelta(days=3),
        end=now_ + timedelta(days=10),
    )

    assert user.subscriptions.count() == 4
    default_sub_before, default_sub_after = user.subscriptions.filter(plan=default_plan).order_by('end')

    assert default_sub_before.start < now_
    assert default_sub_before.end == now_
    assert default_sub_after.start == now_ + timedelta(days=10)
    assert default_sub_after.end > now_ + timedelta(days=356*5)


def test__default_plan__shift_if_subscription_prolonged(default_plan, plan, user, subscription):
    """
    -----[subscription][default subscription               ]->
                            ^-now
    After growing subscription:
    -----[subscription             ][default subscription  ]->
                            ^-now
    """

    assert user.subscriptions.count() == 2
    assert user.subscriptions.active().count() == 1
    default_subscription = user.subscriptions.filter(plan=default_plan).first()
    default_subscription.start = subscription.end
    default_subscription.save()

    subscription.end = now() + timedelta(days=7)
    subscription.save()

    assert user.subscriptions.active().count() == 1
    assert user.subscriptions.active().first().plan == subscription.plan

    default_subscription = user.subscriptions.filter(plan=default_plan).first()
    assert default_subscription.start == subscription.end


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

    subscription = Subscription.objects.create(
        user=user,
        plan=plan,
    )
    assert user.subscriptions.active().count() == 1
    assert user.subscriptions.active().first() == subscription

    assert user.subscriptions.count() == 3
    sub_before, sub_now, sub_after = user.subscriptions.order_by('start')

    assert sub_before.plan == default_plan
    assert sub_before.start == default_subscription_old.start
    assert sub_before.end == sub_now.start

    assert sub_after.plan == default_plan
    assert sub_after.start == sub_now.end
    assert sub_after.end >= now() + timedelta(days=365*5)


def test__default_plan__enable__old_subscription(user, subscription, settings):
    """
    -----[subscription]--------------------->
                            ^-now
    After adding default plan:
    -----[subscription]-----[default subscription   ]->
                            ^-now
    """

    assert user.subscriptions.count() == 1

    default_plan = Plan.objects.create(codename='default', charge_amount=0)
    now_ = now()

    config.SUBSCRIPTIONS_DEFAULT_PLAN_ID = default_plan.id
    subscriptions = user.subscriptions.order_by('end')
    assert subscriptions.count() == 2
    assert subscriptions[0] == subscription
    assert subscriptions[1].plan == default_plan
    assert now_ - timedelta(seconds=1) < subscriptions[1].start < now_ + timedelta(seconds=1)
    assert subscriptions[1].end > now_ + timedelta(days=365*5)


def test__default_plan__enable__active_subscription(user, subscription, settings):
    """
    -----[subscription        ]------------------->
                        ^-now
    After adding default plan:
    -----[subscription        ][default subscription    ]->
                         ^-now
    """
    assert user.subscriptions.count() == 1
    subscription.end = now() + timedelta(days=7)
    subscription.save()

    default_plan = Plan.objects.create(codename='default', charge_amount=0)
    config.SUBSCRIPTIONS_DEFAULT_PLAN_ID = default_plan.id

    subscriptions = user.subscriptions.order_by('end')
    assert subscriptions.count() == 2
    assert subscriptions[0] == subscription
    assert subscriptions[1].plan == default_plan
    assert subscriptions[1].start == subscription.end
    assert subscriptions[1].end > subscription.end + timedelta(days=365*5)


def test__default_plan__disabling__active(user, default_plan, subscription):
    """
    -----[subscription]-----[default plan]----->
                                    ^-now
    After disabling default plan:
    -----[subscription]-----[default]----->
                                    ^-now
    """

    assert user.subscriptions.count() == 2
    assert user.subscriptions.active().count() == 1

    default_subscription = user.subscriptions.active().first()
    assert default_subscription.plan == default_plan
    assert default_subscription.start < now()
    assert default_subscription.end > now()

    config.SUBSCRIPTIONS_DEFAULT_PLAN_ID = 0
    assert user.subscriptions.count() == 2
    assert not user.subscriptions.active().exists()
    default_subscription = user.subscriptions.order_by('end').last()
    assert default_subscription.plan == default_plan
    assert now() - timedelta(seconds=2) < default_subscription.end < now()


def test__default_plan__disabling__future(user, default_plan, subscription):
    """
    -----[subscription        ][default plan]----->
                        ^-now
    After disabling default plan:
    -----[subscription        ]------------------->
                         ^-now
    """

    subscription.end = now() + timedelta(days=7)
    subscription.save()

    assert user.subscriptions.count() == 2
    assert user.subscriptions.active().count() == 1

    default_subscription = user.subscriptions.order_by('end').last()
    assert default_subscription.plan == default_plan
    assert default_subscription.start > now()

    config.SUBSCRIPTIONS_DEFAULT_PLAN_ID = 0
    assert user.subscriptions.count() == 1
    assert not user.subscriptions.filter(plan=default_plan).exists()


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
    assert subscription.end > now() + timedelta(days=365)

    new_default_plan = Plan.objects.create(codename='new default', charge_amount=0)
    config.SUBSCRIPTIONS_DEFAULT_PLAN_ID = new_default_plan.id

    assert user.subscriptions.count() == 2

    subscription = Subscription.objects.get(pk=subscription.pk)
    assert subscription.plan == default_plan
    assert now() - timedelta(seconds=1) < subscription.end < now()

    new_subscription = Subscription.objects.active().first()
    assert new_subscription.plan == new_default_plan
    assert now() - timedelta(seconds=1) < new_subscription.start < now()
    assert new_subscription.end > now() + timedelta(days=365)


def test__default_plan__switch__future(user, default_plan, subscription):
    """
    --[subscription][default plan    ]->
        ^-now
    After switching default plan:
    --[subscription][new default plan]->
        ^-now
    """
    assert user.subscriptions.count() == 2

    subscription = user.subscriptions.first()
    subscription.end = now() + timedelta(days=7)
    subscription.save()

    default_subscription = user.subscriptions.order_by('end').last()

    new_default_plan = Plan.objects.create(codename='new default', name='New default', charge_amount=0)
    config.SUBSCRIPTIONS_DEFAULT_PLAN_ID = new_default_plan.id

    assert user.subscriptions.count() == 3
    new_subscription = user.subscriptions.order_by('end').last()

    assert new_subscription.pk != default_subscription.pk
    assert new_subscription.plan == new_default_plan
    assert now() - timedelta(seconds=1) < new_subscription.start < now()
    assert new_subscription.end == default_subscription.end


def test__default_plan__non_recurring__ignore_save(user, default_plan, recharge_plan):
    """
    ---[default plan              ]->
       ^--now
    After adding non-recurring subscription:
    ---[default plan              ]->
    ----[recharge plan      ]------->
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

    new_default_plan = Plan.objects.create(codename='new default', charge_amount=0)
    config.SUBSCRIPTIONS_DEFAULT_PLAN_ID = new_default_plan.id

    assert user.subscriptions.active().count() == 2

    subscription_new = user.subscriptions.filter(plan=recharge_plan).first()
    assert subscription_old.start == subscription_new.start
    assert subscription_old.end == subscription_new.end

    default_subscription = user.subscriptions.active().filter(plan=recharge_plan).first()
    assert now() - timedelta(seconds=1) < default_subscription.start < now()
