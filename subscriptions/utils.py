from __future__ import annotations

from typing import Callable, Iterable, Iterator, TypeVar
from datetime import datetime
from django.conf import settings
from djmoney.money import Money

from .defaults import DEFAULT_SUBSCRIPTIONS_CURRENCY

T = TypeVar('T')


class NonMonothonicSequence(Exception):
    pass


def merge_iter(*iterables: Iterable[T], key: Callable = lambda x: x) -> Iterator[T]:
    values: dict[Iterable[T], T] = {}

    # accumulate first value from each iterable
    for iterable in iterables:
        iterable = iter(iterable)
        try:
            values[iterable] = next(iterable)
        except StopIteration:
            pass

    last_min_value = None
    while values:
        # consume from iterator which provides lowest value
        min_value = min(values.values(), key=key)
        if last_min_value is not None and key(last_min_value) > key(min_value):
            raise NonMonothonicSequence(f'{last_min_value=}, {min_value=}')
        yield (last_min_value := min_value)
        iterable = next(it for it, val in values.items() if val == min_value)
        try:
            values[iterable] = next(iterable)
        except StopIteration:
            del values[iterable]


def fromisoformat(value: str) -> datetime:
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


default_currency = getattr(settings, 'SUBSCRIPTIONS_DEFAULT_CURRENCY', DEFAULT_SUBSCRIPTIONS_CURRENCY)
NO_MONEY = Money(0, default_currency)
