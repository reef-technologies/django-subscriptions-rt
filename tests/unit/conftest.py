from .fixtures import *  # noqa


def pytest_collection_modifyitems(items: list):
    # some tests require manual intervention; move them to the end of queue
    items.sort(key=lambda item: item.name.startswith("test__paddle__payment_flow"))
