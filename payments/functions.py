from datetime import datetime
from itertools import zip_longest
from logging import getLogger
from operator import attrgetter
from typing import Iterable, Iterator, List, Optional

from django.contrib.auth.models import AbstractUser
from django.db.models import QuerySet
from django.utils.timezone import now

from payments.exceptions import InconsistentQuotaCache, NoActiveSubscription, NoQuotaApplied, QuotaLimitExceeded
from payments.models import QuotaCache, QuotaChunk, Resource, Subscription, Usage

log = getLogger(__name__)


def get_subscriptions_involved(user: AbstractUser, at: datetime, resource: 'Resource') -> QuerySet['Subscription']:
    from_ = at

    while True:
        subscriptions = Subscription.objects.prefetch_related('plan__quotas').filter(
            user=user, end__gt=from_, start__lte=at, plan__quotas__resource=resource,
        ).order_by('pk').distinct()
        starts = subscriptions.values_list('start', flat=True)
        if not starts:
            return Subscription.objects.none()

        min_start = min(starts)
        if min_start == from_:
            break

        from_ = min_start

    return subscriptions


def apply_cache(chunks: Iterable[QuotaChunk], cache: QuotaCache) -> Iterator[QuotaChunk]:
    cached_chunks = iter(cache.chunks)

    # match chunks and cached_chunks one-by-one
    check_cached_pair = True
    for i, (chunk, cached_chunk) in enumerate(zip_longest(chunks, cached_chunks, fillvalue=None)):
        if not chunk and cached_chunk:
            raise InconsistentQuotaCache(f'Non-paired cached chunk detected at position {i}: {cached_chunk}')

        elif chunk and cached_chunk:
            if not chunk.same_lifetime(cached_chunk):
                raise InconsistentQuotaCache(f'Non-matched cached chunk detected at position {i}: {chunk=}, {cached_chunk=}')

            yield cached_chunk

        elif chunk and not cached_chunk:
            if check_cached_pair:
                if chunk.includes(cache.datetime):
                    raise InconsistentQuotaCache(f'No cached chunk for {chunk}')
                check_cached_pair = False

            yield chunk


def get_remaining_chunks(
    user: AbstractUser,
    resource: Resource,
    at: Optional[datetime] = None,
    quota_cache: Optional[QuotaCache] = None,
) -> List[QuotaChunk]:

    at = at or now()
    subscriptions_involved = get_subscriptions_involved(user=user, at=at, resource=resource)

    if quota_cache:
        assert at >= quota_cache.datetime
        subscriptions_involved = subscriptions_involved.filter(end__gt=quota_cache.datetime)

    if not subscriptions_involved:
        raise NoActiveSubscription()

    quota_chunks = Subscription.iter_subscriptions_quota_chunks(
        subscriptions_involved,
        since=quota_cache and quota_cache.datetime,
        until=at,
        resource=resource,
        sort_by=attrgetter('start'),
    )
    if quota_cache:
        quota_chunks = apply_cache(quota_chunks, quota_cache)

    try:
        first_quota_chunk = next(quota_chunks)
        assert first_quota_chunk.start <= at
    except StopIteration as exc:
        raise NoQuotaApplied() from exc

    active_chunks = [first_quota_chunk]

    # ---- for each usage, consume chunks ----

    usages = Usage.objects.filter(
        user=user,
        resource=resource,
        **({'datetime__gt': quota_cache.datetime} if quota_cache else {'datetime__gte': first_quota_chunk.start}),
        datetime__lte=at,
    ).order_by('datetime')

    for date, amount in usages.values_list('datetime', 'amount'):

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
            (chunk for chunk in active_chunks if chunk.start <= date < chunk.end),
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
    resource: Resource,
    at: Optional[datetime] = None,
    quota_cache: Optional[QuotaCache] = None,
) -> int:
    return sum(
        chunk.remains for chunk in get_remaining_chunks(
            user=user,
            resource=resource,
            at=at,
            quota_cache=quota_cache,
        )
    )
