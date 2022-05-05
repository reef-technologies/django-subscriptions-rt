from datetime import datetime
from logging import getLogger
from operator import attrgetter
from typing import Dict, Iterable, Iterator, List, Optional

from django.contrib.auth.models import AbstractUser
from django.db.models import Prefetch
from django.utils.timezone import now
from more_itertools import spy

from .exceptions import QuotaLimitExceeded
from .models import Quota, QuotaCache, QuotaChunk, Resource, Subscription, Usage
from .utils import merge_iter

log = getLogger(__name__)


def iter_subscriptions_involved(user: AbstractUser, at: datetime) -> Iterator['Subscription']:
    subscriptions = (
        Subscription.objects
        .select_related('plan')
        .prefetch_related(Prefetch(
            'plan__quotas',
            queryset=Quota.objects.select_related('resource'),
        ))
        .filter(user=user)
        .exclude(start__gt=at)
        .order_by('-end')
    )

    from_ = at
    for subscription in subscriptions:
        if subscription.end <= from_:
            break

        yield subscription
        from_ = min(from_, subscription.start)


def iter_subscriptions_quota_chunks(
    subscriptions: Iterable[Subscription],
    since: datetime,
    until: datetime,
    sort_by: callable = attrgetter('start'),
) -> Iterator[QuotaChunk]:
    return merge_iter(
        *(
            subscription.iter_quota_chunks(
                since=since,
                until=until,
                sort_by=sort_by,
            )
            for subscription in subscriptions
        ),
        key=sort_by,
    )


def get_remaining_chunks(
    user: AbstractUser,
    at: Optional[datetime] = None,
    quota_cache: Optional[QuotaCache] = None,
) -> List[QuotaChunk]:

    at = at or now()
    subscriptions_involved = iter_subscriptions_involved(user=user, at=at)

    if quota_cache:
        assert at >= quota_cache.datetime
        subscriptions_involved = filter(lambda subscription: subscription.end > quota_cache.datetime, subscriptions_involved)

    first_subscriptions_involved, subscriptions_involved = spy(subscriptions_involved, 1)
    if not first_subscriptions_involved:
        return []

    quota_chunks = iter_subscriptions_quota_chunks(
        subscriptions_involved,
        since=quota_cache and quota_cache.datetime,
        until=at,
        sort_by=attrgetter('start'),
    )
    if quota_cache:
        quota_chunks = quota_cache.apply(quota_chunks)

    first_quota_chunks, quota_chunks = spy(quota_chunks, 1)
    if not first_quota_chunks:
        return []

    assert first_quota_chunks[0].start <= at

    # ---- for each usage, consume chunks ----

    usages = Usage.objects.filter(
        user=user,
        **({'datetime__gt': quota_cache.datetime} if quota_cache else {'datetime__gte': first_quota_chunks[0].start}),
        datetime__lte=at,
    ).order_by('datetime')

    active_chunks = []
    for date, resource_id, amount in usages.values_list('datetime', 'resource', 'amount'):

        # add chunks to active_chunks until they bypass "date"
        if not active_chunks or active_chunks[-1].start <= date:
            for chunk in quota_chunks:
                active_chunks.append(chunk)
                if chunk.start > date:
                    break

        # remove stale chunks
        active_chunks = [chunk for chunk in active_chunks if chunk.end >= date and chunk.remains]

        # select & sort chunks to consume from
        chunks_to_consume = sorted(
            (chunk for chunk in active_chunks if chunk.start <= date < chunk.end and chunk.resource.id == resource_id),
            key=attrgetter('end'),
        )

        # consume chunks
        for chunk in chunks_to_consume:
            if amount <= chunk.remains:
                chunk.remains -= amount
                amount = 0
                break
            else:
                amount -= chunk.remains
                chunk.remains = 0

        # check whether limit was exceeded (== amount was fully covered by chunks consumed)
        if amount:
            raise QuotaLimitExceeded(f'Quota limit exceeded: {date=} {amount=}')

    # ---- now calculate remaining amount at `at` ----

    # leave chunks that exist at `at`
    active_chunks = [chunk for chunk in active_chunks if chunk.includes(at)]

    # add chunks to active_chunks until they bypass "date"
    for chunk in quota_chunks:
        if chunk.start > at:
            break

        if chunk.includes(at):
            active_chunks.append(chunk)

    return active_chunks


def get_remaining_amount(
    user: AbstractUser,
    at: Optional[datetime] = None,
    quota_cache: Optional[QuotaCache] = None,
) -> Dict[Resource, int]:
    # TODO: auto-fetch cache
    amount = {}
    for chunk in get_remaining_chunks(user=user, at=at, quota_cache=quota_cache):
        amount[chunk.resource] = amount.setdefault(chunk.resource, 0) + chunk.remains

    return amount
