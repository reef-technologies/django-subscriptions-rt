from rest_framework.exceptions import ValidationError

from ..exceptions import ProviderNotFound
from ..providers import get_provider_by_codename


def validate_provider(value: str) -> None:
    try:
        get_provider_by_codename(value)
    except ProviderNotFound as exc:
        raise ValidationError('Invalid payment provider', code='invalid_payment_provider') from exc
