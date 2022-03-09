from typing import Dict, Iterable, Iterator, TypeVar

T = TypeVar('T')


class NonMonothonicSequence(Exception):
    pass


def merge_iter(*iterables: Iterable[T], key: callable = lambda x: x) -> Iterator[T]:
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
        min_value = min(values.values(), key=key)
        if last_min_value is not None and key(last_min_value) > key(min_value):
            raise NonMonothonicSequence(f'{last_min_value=}, {min_value=}')
        yield (last_min_value := min_value)
        iterable = next(it for it, val in values.items() if val == min_value)
        try:
            values[iterable] = next(iterable)
        except StopIteration:
            del values[iterable]
