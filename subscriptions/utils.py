from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import Callable, Iterable, Iterator, TypeVar

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, models, transaction
from djmoney.money import Money
from environs import Env

from .defaults import DEFAULT_SUBSCRIPTIONS_CURRENCY

log = logging.getLogger(__name__)
env = Env()

T = TypeVar('T')

# Matching raw(raw_query, params=(), translations=None, using=None) -> RawQuerySet.
RawQueryFunction = Callable


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


class AdvancedJSONEncoder(DjangoJSONEncoder):
    def default(self, o):
        if isinstance(o, models.Model):
            o = o.pk
        return super().default(o)


default_currency = getattr(settings, 'SUBSCRIPTIONS_DEFAULT_CURRENCY', DEFAULT_SUBSCRIPTIONS_CURRENCY)
NO_MONEY = Money(0, default_currency)


class HardDBLock:
    """
    This class is supposed to represent a special lock made on the DB side that stops multiple operations
    on the same entries from happening. Current implementation supports only postgresql via advisory_lock mechanism.

    We don't care about clashes. These locks are to ensure that we're properly blocking operations and if we block
    slightly too many, it's not a bad thing.

    TODO: add support for different databases.
    """
    # Postgres supports up-to-32bit numbers, so the max positive value will be 2**31-1.
    PSQL_MAX_LOCK_VALUE = 2 ** 31 - 1

    def __init__(
        self,
        lock_marker: str,
        lock_value: str | int,
        durable: bool = False,
    ):
        if not self.is_enabled():
            return

        db_type = connection.vendor
        if db_type != 'postgresql':
            log.warning(f'{self.__class__.__name__} works only with postgres right now, {db_type} is unsupported.')

        self.lock_marker = self._pg_str_to_int(lock_marker)
        self.lock_value = self._pg_str_to_int(lock_value)
        self.durable = durable
        self.transaction = None

    def _pg_str_to_int(self, in_value: str | int) -> int:
        # Note: transaction id could be a string representing a number. So, if it's possible to use it as a number
        # we do, and if there's a string, it's ok too. This is e.g.: a case for apple transaction ID.
        try:
            out_value = int(in_value)
        except ValueError:
            out_value = int(hashlib.sha1(in_value.encode('utf-8')).hexdigest(), 16)
        return out_value % self.PSQL_MAX_LOCK_VALUE

    @classmethod
    def is_enabled(cls) -> bool:
        return env.bool('ENABLE_HARD_DB_LOCK', True)

    def __enter__(self):
        if not self.is_enabled():
            return

        # Open our own transaction that will be guarded by the advisory lock.
        self.transaction = transaction.atomic(durable=self.durable)
        self.transaction.__enter__()

        with connection.cursor() as cursor:
            # xact type of lock is automatically released when the transaction ends.
            cursor.execute(
                'SELECT pg_advisory_xact_lock(%s, %s)',
                (self.lock_marker, self.lock_value),
            )
            _ = cursor.fetchone()[0]

        return self

    def __exit__(self, *args, **kwargs):
        if not self.is_enabled():
            return

        self.transaction.__exit__(*args, **kwargs)
