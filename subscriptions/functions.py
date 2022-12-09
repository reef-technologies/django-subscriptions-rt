from contextlib import contextmanager
from datetime import datetime
from logging import getLogger
from operator import attrgetter
from typing import Callable, Dict, Iterable, Iterator, List, Optional, Set
from itertools import chain
from functools import cache

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.cache import InvalidCacheBackendError, caches
from django.db import transaction
from django.db.models import Prefetch
from django.utils.timezone import now
from more_itertools import spy

from .defaults import DEFAULT_SUBSCRIPTIONS_CACHE_NAME
from .exceptions import InconsistentQuotaCache, QuotaLimitExceeded
from .models import Quota, QuotaCache, QuotaChunk, Resource, Subscription, Usage, Feature, Tier
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
    sort_by: Callable = attrgetter('start'),
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

    if quota_cache and quota_cache.datetime > at:
        log.warning('Not using quota cache %s because it is newer than requested time %s', quota_cache, at)
        quota_cache = None

    if quota_cache:
        subscriptions_involved = (
            sub for sub in subscriptions_involved
            if sub.end > quota_cache.datetime
        )

    first_subscriptions_involved, subscriptions_involved = spy(subscriptions_involved, 1)
    if not first_subscriptions_involved:
        return []

    quota_chunks = iter_subscriptions_quota_chunks(
        subscriptions_involved,
        since=quota_cache and quota_cache.datetime,
        until=at,
        sort_by=attrgetter('start', 'end'),
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
        active_chunks = [chunk for chunk in active_chunks if chunk.end >= date]

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
            log.error('Quota limit exceeded: usage date=%s overused=%s', date, amount)

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
) -> Dict[Resource, int]:
    at = at or now()

    try:
        cache = caches[getattr(settings, 'SUBSCRIPTIONS_CACHE_NAME', DEFAULT_SUBSCRIPTIONS_CACHE_NAME)]
    except InvalidCacheBackendError:
        cache = None
    quota_cache = cache and cache.get(user.pk, None)

    try:
        remaining_chunks = get_remaining_chunks(user=user, at=at, quota_cache=quota_cache)
    except InconsistentQuotaCache:
        log.exception('Dropping inconsistent quota cache for user %s', user.pk)
        cache.delete(user.pk)
        remaining_chunks = get_remaining_chunks(user=user, at=at)

    if cache and (not quota_cache or quota_cache.datetime < at < now()):
        cache.set(user.pk, QuotaCache(
            datetime=at,
            chunks=remaining_chunks,
        ))

    amount = {}
    for chunk in remaining_chunks:
        amount[chunk.resource] = amount.setdefault(chunk.resource, 0) + chunk.remains

    return amount


@contextmanager
def use_resource(user: AbstractUser, resource: Resource, amount: int = 1, raises: bool = True) -> int:
    with transaction.atomic():
        available = get_remaining_amount(user).get(resource, 0)
        remains = available - amount

        if remains < 0 and raises:
            raise QuotaLimitExceeded(f'Not enough {resource}: tried to use {amount}, but only {available} is available')

        Usage.objects.create(
            user=user,
            resource=resource,
            amount=amount,
        )
        yield remains


def merge_feature_sets(*feature_sets: Iterable[Feature]) -> Set[Feature]:
    """
    Merge features from different subscriptions in human-meaningful way.
    Positive feature stays if it appears in at least one subscription,
    negative feature stays if it appears in all subscriptions.
    """
    features = set(chain(*feature_sets))

    # remove negative feature if there is at least one set without it;
    # for example, if there are sets {SHOW_ADS, ...}, {SHOW_ADS, ...}, {...},
    # then result won't contain SHOW_ADS feature
    negative_features = {feature for feature in features if feature.is_negative}
    for negative_feature in negative_features:
        if any(negative_feature not in feature_set for feature_set in feature_sets):
            features.remove(negative_feature)

    return features


@cache
def get_default_features() -> Set[Feature]:
    default_tiers = Tier.objects.filter(is_default=True).prefetch_related('features')
    return merge_feature_sets(*(tier.features.all() for tier in default_tiers))
