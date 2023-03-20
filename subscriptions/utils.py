import hashlib
import logging
from typing import Callable, Dict, Iterable, Iterator, Optional, TypeVar, Union
from datetime import datetime

from django.db import connection

logger = logging.getLogger(__name__)

T = TypeVar('T')

# Matching raw(raw_query, params=(), translations=None, using=None) -> RawQuerySet.
RawQueryFunction = Callable


class NonMonothonicSequence(Exception):
    pass


def merge_iter(*iterables: Iterable[T], key: Callable = lambda x: x) -> Iterator[T]:
    values: Dict[Iterable[T], T] = {}

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


class HardDBLock:
    """
    This class is supposed to represent a special lock made on the DB side that stops multiple operations
    on the same entries from happening. Current implementation supports only postgresql via advisory_lock mechanism.
    """
    # Postgres supports up to 64bit numbers.
    PSQL_MAX_LOCK_VALUE = 2 ** 64

    def __init__(
        self,
        lock_value: str,
        raw_query_function: RawQueryFunction,
        raw_query_kwargs: Optional[dict] = None,
    ):
        db_type = connection.vendor
        assert db_type == 'postgresql', \
            f'{self.__class__.__name__} works only with postgres right now, {db_type} is unsupported.'

        # Note: transaction id could be a string representing a number. So, if it's possible to use it as a number
        # we do, and if there's a string it's ok too. This is e.g. a case for apple transaction ID.
        try:
            lock_int_value = int(lock_value)
        except ValueError:
            lock_int_value = int(hashlib.sha1(lock_value.encode('utf-8')).hexdigest(), 16)

        self.trimmed_lock_value = lock_int_value % self.PSQL_MAX_LOCK_VALUE
        self.query_function = raw_query_function
        self.query_kwargs = raw_query_kwargs or {}

        if self.trimmed_lock_value != lock_int_value:
            logger.warning(f'{self.__class__.__name__} lock is trimming {lock_int_value} '
                           f'to {self.trimmed_lock_value}. Collisions are possible now.')

    def __enter__(self):
        self.query_function('SELECT pg_advisory_lock(%s)', (self.trimmed_lock_value,), **self.query_kwargs)
        return self

    def __exit__(self, *_args, **_kwargs):
        self.query_function('SELECT pg_advisory_unlock(%s)', (self.trimmed_lock_value,), **self.query_kwargs)
