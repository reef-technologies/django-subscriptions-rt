from datetime import timedelta

import pytest
from constance import config
from django.contrib.auth import get_user_model
from django.utils.timezone import now

from subscriptions.functions import get_default_plan, iter_subscriptions_involved
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


def test__default_plan__shift_if_subscription_prolonged(default_plan, plan, user):
    """
    -----[subscription][default subscription               ]->
                    ^-now
    After growing subscription:
    -----[subscription             ][default subscription  ]->
                    ^-now
    """
    assert user.subscriptions.count() == 1

    now_ = now()
    subscription = Subscription.objects.create(
        user=user,
        plan=plan,
        start=now_,
        end=now_ + timedelta(days=7),
    )

    subscription.end += timedelta(days=7)
    subscription.save()

    subs_before = list(iter_subscriptions_involved(user, now_ - timedelta(milliseconds=1)))
    assert len(subs_before) == 1
    assert subs_before[0].plan == default_plan

    subs_now = list(iter_subscriptions_involved(user, now_ + timedelta(seconds=1)))
    assert len(subs_now) == 1
    assert subs_now[0] == subscription

    subs_end = list(iter_subscriptions_involved(user, subscription.end - timedelta(seconds=1)))
    assert len(subs_end) == 1
    assert subs_end[0] == subscription

    subs_after = list(iter_subscriptions_involved(user, subscription.end + timedelta(seconds=1)))
    assert len(subs_after) == 1
    assert subs_after[0].plan == default_plan


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
