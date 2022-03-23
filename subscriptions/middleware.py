from django.utils.deprecation import MiddlewareMixin

from subscriptions.functions import get_remaining_amount
from subscriptions.models import Subscription


class SubscriptionsMiddleware(MiddlewareMixin):
    def process_request(self, request):
        is_auth = request.user.is_authenticated
        request.user.active_subscriptions = Subscription.objects.filter(user=request.user).active() if is_auth else []
        request.user.quotas = get_remaining_amount(user=request.user) if is_auth else {}
