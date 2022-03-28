from functools import lru_cache
from logging import getLogger
from typing import List, Optional

from django.conf import settings
from django.forms import Form
from django.http import HttpRequest
from django.utils.module_loading import import_string
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.serializers import Serializer

from ..api.serializers import PaymentSerializer, WebhookSerializer
from ..exceptions import ProviderNotFound

log = getLogger(__name__)


class Provider:
    codename: str = 'default'
    is_enabled: bool = True
    form: Optional[Form] = None
    payment_serializer_class: Serializer = PaymentSerializer
    webhook_serializer_class: Serializer = WebhookSerializer

    def process_payment(self, request: Optional[HttpRequest], serializer: PaymentSerializer) -> Response:
        log.warning(f'Processing for "{self.codename}" triggered without explicit handler')
        return Response(serializer.data)

    def handle_webhook(self, request: Request, serializer: WebhookSerializer) -> Response:
        log.warning(f'Webhook for "{self.codename}" triggered without explicit handler')
        return Response(serializer.data)


@lru_cache
def get_providers() -> List[Provider]:  # codename -> Provider() instance
    providers = []
    seen_codenames = set()

    for class_path in settings.PAYMENT_PROVIDERS:
        provider = import_string(class_path)()
        assert provider.codename not in seen_codenames, f'Duplicate codename "{provider.codename}"'
        providers.append(provider)
        seen_codenames.add(provider.codename)

    return providers


@lru_cache
def get_provider(codename: Optional[str] = None) -> Provider:
    if not (providers := get_providers()):
        raise ProviderNotFound('No providers defined in settings.PAYMENT_PROVIDERS')

    if not codename:
        return providers[0]

    try:
        return next(provider for provider in providers if provider.codename == codename)
    except StopIteration as exc:
        raise ProviderNotFound(f'Provider with codename "{codename}" not found in settings.PAYMENT_PROVIDERS') from exc
