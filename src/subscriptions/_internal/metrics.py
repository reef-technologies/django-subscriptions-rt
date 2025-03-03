from django.utils.timezone import now
from prometheus_client import Gauge

from .reports import SubscriptionsReport

num_active_subscriptions = Gauge("num_active_subscriptions", "Number of active subscriptions")
num_active_subscriptions.set_function(
    lambda: SubscriptionsReport(
        since=(now_ := now()),
        until=now_,
        include_until=True,
    ).get_active_count()
)
