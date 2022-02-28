from datetime import datetime
from itertools import chain
from operator import attrgetter
from typing import Iterable, Iterator, List, Literal, NamedTuple, Optional, TypeVar, Dict

from django.contrib.auth.models import AbstractUser
from django.db.models import QuerySet
from django.utils.timezone import now

from payments.exceptions import QuotaLimitExceeded
from payments.models import QuotaChunk, Resource, Subscription

T = TypeVar('T')


def merge_iter(*iterables: Iterable[T], sort_by: callable = lambda x: x) -> Iterator[T]:
    values: Dict[Iterable[T], T] = {}
    iterables = [iter(it) for it in iterables]
    for iterable in iterables:
        try:
            values[iterable] = next(iterable)
        except StopIteration:
            pass

    last_min_value = None
    while values:
        # consume from iterator which provides lowest value
        min_value = min(values.values(), key=sort_by)
        if last_min_value is not None:
            assert last_min_value <= min_value, 'Iterables are not monothonic'
        yield (last_min_value := min_value)
        iterable = next(it for it, val in values.items() if val == min_value)
        try:
            values[iterable] = next(iterable)
        except StopIteration:
            del values[iterable]


def get_subscriptions_involved(user: AbstractUser, at: datetime, resource: 'Resource') -> QuerySet['Subscription']:
    from_ = at

    raise NotImplementedError('Arg "resource" not implemented')

    while True:
        starts = Subscription.objects.filter(user=user, start__lte=from_, end__gt=from_).values_list('start', flat=True)
        if not starts:
            return Subscription.objects.none()

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
    if_exceeds_limit: Literal['raise', 'warn', 'ignore'] = 'raise',
) -> List[QuotaChunk]:

    at = at or now()
    subscriptions_involved = get_subscriptions_involved(user=user, at=at, resource=resource)

    if quota_cache:
        assert at >= quota_cache.datetime
        subscriptions_involved = subscriptions_involved.filter(end__gt=quota_cache.datetime)

    # TODO: following code materializes all quota chunks which may be redundant - may just iterate over
    quota_chunks = chain.from_iterable(
        subscription.iter_quota_chunks(since=quota_cache and quota_cache.datetime, until=at, resource=resource)
        for subscription in subscriptions_involved
    )
    quota_chunks = sorted(quota_chunks, key=attrgetter('start'))

    if quota_cache:
        raise NotImplementedError()
        # TODO: invalidate quota cache, if it fails then call the function without cache

    if quota_chunks:
        breakpoint()

    chunks_start = quota_chunks[0].start if quota_chunks else None
    assert chunks_start <= at
    usages = Usage.objects.filter(user=user, resource=resource, datetime__gte=chunks_start, datetime__lte=at)

    consume_from_chunk = 0
    num_chunks = len(quota_chunks)
    for datetime, amount in usages.values_list('datetime', 'amount'):
        if usage.amount and consume_from_chunk == num_chunks:
            pass


    return 0
