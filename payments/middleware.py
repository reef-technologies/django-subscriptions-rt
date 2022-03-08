from typing import Dict

from django.utils.deprecation import MiddlewareMixin
from django.utils.timezone import now

from payments.models import Resource, Subscription


class SubscriptionsMiddleware(MiddlewareMixin):
    def process_request(self, request):
        now_ = now()
        request.subscriptions: Dict[Subscription: Dict[Resource, int]] = {
            subscription: subscription.get_remaining_amount(at=now_)
            for subscription in request.user.subscriptions.active()
        }
