import pytest

from subscriptions.v0.providers import get_provider_by_codename
from subscriptions.v0.providers.apple_in_app import AppleInAppProvider


@pytest.fixture
def apple_bundle_id() -> str:
    return "test-bundle-id"


@pytest.fixture
def apple_in_app(settings, apple_bundle_id) -> AppleInAppProvider:
    settings.SUBSCRIPTIONS_PAYMENT_PROVIDERS = [
        "subscriptions.v0.providers.apple_in_app.AppleInAppProvider",
    ]
    AppleInAppProvider.bundle_id = apple_bundle_id
    return get_provider_by_codename("apple")
