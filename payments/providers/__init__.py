from abc import ABC, abstractmethod
from functools import lru_cache
from typing import Optional

from django.conf import settings
from django.forms import Form
from django.http import HttpRequest
from django.utils.module_loading import import_string
from rest_framework.request import Request

from ..exceptions import ProviderNotFound
from ..models import Plan, SubscriptionPayment


class Provider(ABC):
    name: Optional[str] = None
    form: Optional[Form] = None
    redirect_url: Optional[str] = None

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

    @abstractmethod
    def process_payment(self, form_data: dict, request: Optional[HttpRequest], plan: Plan) -> SubscriptionPayment:
        ...

    @abstractmethod
    def handle_webhook(self, request: Request):
        ...


@lru_cache
def get_provider(provider_name: str) -> Provider:
    try:
        info = settings.PAYMENT_PROVIDERS[provider_name]
    except KeyError as exc:
        raise ProviderNotFound(f'Provider "{provider_name}" not found in settings.PAYMENT_PROVIDERS') from exc

    try:
        class_ = import_string(info['class'])
    except ImportError as exc:
        raise ProviderNotFound(f'Provider "{provider_name}" not found: cannot import module "{info["class"]}"') from exc

    kwargs = {k: v for k, v in info.items() if k != 'class'}
    assert 'name' not in kwargs
    kwargs['name'] = provider_name
    return class_(**kwargs)
