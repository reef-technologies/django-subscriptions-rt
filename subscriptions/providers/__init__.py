from dataclasses import dataclass
from functools import lru_cache
from logging import getLogger
from typing import Optional

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


@dataclass
class Provider:
    name: Optional[str] = None
    form: Optional[Form] = None
    redirect_url: Optional[str] = None
    payment_serializer_class: Serializer = PaymentSerializer
    webhook_serializer_class: Serializer = WebhookSerializer

    def process_payment(self, request: Optional[HttpRequest], serializer: PaymentSerializer) -> Response:
        log.warning(f'Processing for "{self.name}" triggered without explicit handler')
        return Response(serializer.data)

    def handle_webhook(self, request: Request, serializer: WebhookSerializer) -> Response:
        log.warning(f'Webhook for "{self.name}" triggered without explicit handler')
        return Response(serializer.data)


@lru_cache
def get_provider(provider_name: str) -> Provider:
    try:
        class_path = settings.PAYMENT_PROVIDERS[provider_name]
    except KeyError as exc:
        raise ProviderNotFound(f'Provider "{provider_name}" not found in settings.PAYMENT_PROVIDERS') from exc

    try:
        class_ = import_string(class_path)
    except ImportError as exc:
        raise ProviderNotFound(f'Provider "{provider_name}" not found: cannot import module "{class_path}"') from exc

    return class_(name=provider_name)
