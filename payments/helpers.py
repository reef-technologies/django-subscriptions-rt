from collections import defaultdict
from datetime import datetime
from functools import reduce, chain
from typing import List, NamedTuple, Optional

from django.contrib.auth.models import AbstractUser
from django.db.models import QuerySet
from django.utils.timezone import now

from payments.models import Quota, QuotaChunk, Resource, Subscription


def get_subscriptions_involved(user: AbstractUser, at: datetime) -> QuerySet[Subscription]:
    from_ = at

    while True:
        starts = Subscription.objects.filter(user=user, start__lte=from_, end__gt=from_).values_list('start', flat=True)
        min_start = min(starts)
        if min_start == from_:
            break

        from_ = min_start

    return Subscription.objects.filter(user=user, start__lte=at, end__gt=from_)


class QuotaCache(NamedTuple):
    datetime: datetime
    quota_chunks: List[QuotaChunk]


def get_remaining(
    user: AbstractUser,
    resource: Resource,
    at: Optional[datetime] = None,
    quota_cache: Optional[QuotaCache] = None,
) -> List[QuotaChunk]:

    at = at or now()
    subscriptions_involved = get_subscriptions_involved(user, at)
    if quota_cache:
        assert at >= quota_cache.datetime
        subscriptions_involved = subscriptions_involved.filter(end__gt=quota_cache.datetime)

    quota_chunks = chain.from_iterable(
        subscription.iter_quota_chunks(since=quota_cache and quota_cache.datetime, until=at, resource=resource)
        for subscription in subscriptions_involved
    )
    quota_chunks = sorted(quota_chunks, key=attrgetter('end'))

    # TODO: invalidate quota cache, if it fails then call the function without cache

    ......
