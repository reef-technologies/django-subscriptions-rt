from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils.timezone import now

from subscriptions.functions import get_default_plan
from subscriptions.models import INFINITY, Subscription


class Command(BaseCommand):
    def handle(self, *args, **options):

        User = get_user_model()

        default_plan = get_default_plan()
        if not default_plan:
            raise ValueError('No default plan found')

        now_ = now()
        for user in User.objects.all():
            last_subscription = user.subscriptions.order_by('end').last()
            if last_subscription.plan == default_plan:
                continue

            subscription = Subscription.objects.create(
                user=user,
                plan=default_plan,
                start=max(last_subscription.end, now_) if last_subscription else now_,
                end=now_+INFINITY,
            )
            self.stdout.write(f'Created {subscription} for user {user}')
