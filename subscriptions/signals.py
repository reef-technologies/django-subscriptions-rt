
import logging

from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils.timezone import now

from .functions import get_default_plan
from .models import INFINITY, Subscription

log = logging.getLogger(__name__)


@receiver(post_save, sender=get_user_model())
def create_default_subscription_for_new_user(sender, instance, created, **kwargs):
    if created and (default_plan := get_default_plan()):
        Subscription.objects.create(
            user=instance,
            plan=default_plan,
            start=(now_ := now()),
            end=now_+INFINITY,
        )
