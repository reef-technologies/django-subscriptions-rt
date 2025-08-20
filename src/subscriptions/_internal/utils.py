import logging
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager, suppress
from datetime import datetime, timedelta
from functools import partial, wraps
from typing import TypeVar

import pglock
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connections, models, router
from djmoney.money import Money
from environs import Env

from .defaults import DEFAULT_SUBSCRIPTIONS_ADVISORY_LOCK_TIMEOUT, DEFAULT_SUBSCRIPTIONS_CURRENCY
from .exceptions import ConfigurationError

log = logging.getLogger(__name__)
env = Env()

T = TypeVar("T")

# Matching raw(raw_query, params=(), translations=None, using=None) -> RawQuerySet.
RawQueryFunction = Callable


class NonMonothonicSequence(Exception):
    pass


def merge_iter(*iterables: Iterable[T], key: Callable = lambda x: x) -> Iterator[T]:
    values: dict[Iterator[T], T] = {}

    # accumulate first value from each iterable
    for iterable in iterables:
        iterator = iter(iterable)
        with suppress(StopIteration):
            values[iterator] = next(iterator)

    last_min_value = None
    while values:
        # consume from iterator which provides lowest value
        min_value = min(values.values(), key=key)
        if last_min_value is not None and key(last_min_value) > key(min_value):
            raise NonMonothonicSequence(f"{last_min_value=}, {min_value=}")
        yield (last_min_value := min_value)
        iterator = next(it for it, val in values.items() if val == min_value)
        try:
            values[iterator] = next(iterator)
        except StopIteration:
            del values[iterator]


def fromisoformat(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class AdvancedJSONEncoder(DjangoJSONEncoder):
    def default(self, o):
        if isinstance(o, models.Model):
            o = o.pk
        return super().default(o)


default_currency = getattr(settings, "SUBSCRIPTIONS_DEFAULT_CURRENCY", DEFAULT_SUBSCRIPTIONS_CURRENCY)
NO_MONEY = Money(0, default_currency)


@contextmanager
def _nullcontext(*args, **kwargs):
    yield


@contextmanager
def _advisory_lock(lock_id: int | str):
    """A helper function to acquire an advisory lock both inside and outside of a transaction."""
    # https://django-pglock.readthedocs.io/en/1.7.2/advisory/#transaction-level-locks

    using = router.db_for_write(models.Model)
    fn = partial(
        pglock.advisory,
        lock_id,
        using=router.db_for_write(models.Model),
        xact=True,
        timeout=timedelta(
            seconds=env.int("SUBSCRIPTIONS_ADVISORY_LOCK_TIMEOUT", DEFAULT_SUBSCRIPTIONS_ADVISORY_LOCK_TIMEOUT)
        ),
        side_effect=pglock.Raise,
    )

    if connections[using].in_atomic_block:
        fn().acquire()
        yield

    else:
        with fn() as lock:
            yield lock


advisory_lock = _advisory_lock if env.bool("SUBSCRIPTIONS_ENABLE_ADVISORY_LOCK", default=True) else _nullcontext


def get_setting_or_raise(name: str) -> str:
    if not (value := getattr(settings, name, None)):
        raise ConfigurationError(f"Setting {name} is not set or is empty")
    return value


def pre_validate(fn: Callable) -> Callable:
    """A decorator to validate model fields before saving."""

    @wraps(fn)
    def wrapper(self: models.Model, *args, **kwargs):
        self.full_clean()
        return fn(self, *args, **kwargs)

    return wrapper
