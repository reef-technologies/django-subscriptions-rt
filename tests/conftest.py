import pytest

from .django_fixtures import *  # noqa: F401, F403


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--enable-cache", action="store_true", help="Enable cache for all tests")


def pytest_runtest_call(item) -> None:
    if item.config.getoption("--enable-cache"):
        item.funcargs["cache_backend"] = item._request.getfixturevalue("cache_backend")
