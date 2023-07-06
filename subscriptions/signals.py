
import logging
from contextlib import suppress

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils.timezone import now

from .functions import add_default_plan_to_users, get_default_plan
from .models import INFINITY, Plan, Subscription

log = logging.getLogger(__name__)


@receiver(post_save, sender=get_user_model())
def create_default_subscription_for_new_user(sender, instance, created, **kwargs):
    with suppress(Plan.DoesNotExist):
        if created and (default_plan := get_default_plan()):
            Subscription.objects.create(
                user=instance,
                plan=default_plan,
                auto_prolong=False,
                start=(now_ := now()),
                end=now_+INFINITY,
            )


with suppress(ImportError):
    from constance.signals import config_updated

    @receiver(config_updated)
    @transaction.atomic
    def constance_updated(sender, key, old_value, new_value, **kwargs):
        if key != 'SUBSCRIPTIONS_DEFAULT_PLAN_ID':
            return

        if not old_value and not new_value:
            return

        assert old_value != new_value

        if new_value:
            _ = get_default_plan()  # check if the new value is valid

        now_ = now()

        # if we switch from no default plan to some default plan, then
        # we need to create a default subscription for all users
        if not old_value and new_value:
            add_default_plan_to_users()
            return

        assert old_value
        # now we are in situation where we had some default plan but we're
        # switching to no default plan or new default plan

        # future default subscriptions will just change plan from old one to new one
        future_subscriptions = Subscription.objects.filter(
            plan_id=old_value,
            start__gt=now_,
        )
        for subscription in future_subscriptions:
            if new_value:
                subscription.plan_id = new_value
                subscription.save()
            else:
                subscription.delete()

        # current subscriptions will be split into two groups:
        # old default plan until now, new default plan since now
        current_subscriptions = Subscription.objects.filter(
            plan_id=old_value,
            auto_prolong=False,
            start__lte=now_,
            end__gt=now_,
        )
        for subscription in current_subscriptions:
            end = subscription.end

            subscription.end = now_
            subscription.save()

            if new_value:
                subscription.pk = None
                subscription.plan_id = new_value
                subscription.start = now_
                subscription.end = end
                subscription.save()
