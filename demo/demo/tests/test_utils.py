import pytest
from subscriptions.utils import merge_iter, NonMonothonicSequence


def test_merge_iter():
    assert list(merge_iter(
        (1, 5, 10),
        (3, 4, 10),
        (2, 6, 7, 8, 9),
    )) == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 10]


def test_merge_iter_non_monothonic():
    with pytest.raises(NonMonothonicSequence):
        list(merge_iter(
            (1, 5, 10),
            (5, 6, 3),
        ))
