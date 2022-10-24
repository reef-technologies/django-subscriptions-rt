from django.conf import settings
from django.utils.deprecation import MiddlewareMixin

from .functions import get_remaining_amount
from .models import Subscription


class SubscriptionsMiddleware(MiddlewareMixin):
    def process_request(self, request):
        if settings.HEADER_TO_USER_DATA_FUNCTION is None:
            self._process_request_with_user_object(request)
        else:
            self._process_request_with_id_function(request)

    def _process_request_with_id_function(self, request):
        # For now, we don't care about the user.
        request.user = object()
        request.user.active_subscriptions = []
        request.user.quotas = {}

    def _process_request_with_user_object(self, request):
        is_auth = request.user.is_authenticated
        request.user.active_subscriptions = Subscription.objects.filter(user=request.user).active() if is_auth else []
        request.user.quotas = get_remaining_amount(user=request.user) if is_auth else {}
