from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.utils.deprecation import MiddlewareMixin

from .functions import get_remaining_amount
from .models import Subscription


class SubscriptionsMiddleware(MiddlewareMixin):
    def process_request(self, request):
        if settings.HEADER_VALIDATION_FUNCTION is None:
            is_auth = request.user.is_authenticated
        else:
            # There, most probably, is no user. If that's the case, we need to create him.
            user_id = settings.HEADER_VALIDATION_FUNCTION(request.headers)
            is_auth = user_id is not None
            if is_auth:
                request.user = User.objects.get_or_create(username=user_id, password=make_password(None))

        request.user.active_subscriptions = Subscription.objects.filter(user=request.user).active() if is_auth else []
        request.user.quotas = get_remaining_amount(user=request.user) if is_auth else {}
