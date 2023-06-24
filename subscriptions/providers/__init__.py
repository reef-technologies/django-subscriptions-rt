from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from logging import getLogger
from typing import ClassVar, Iterable, List, Optional, Tuple

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser
from django.forms import Form
from django.utils.module_loading import import_string
from djmoney.money import Money
from more_itertools import first, one
from pydantic import BaseModel
from rest_framework.request import Request
from rest_framework.response import Response

from ..defaults import DEFAULT_SUBSCRIPTIONS_PAYMENT_PROVIDERS
from ..exceptions import ProviderNotFound
from ..models import Plan, Subscription, SubscriptionPayment

log = getLogger(__name__)


@dataclass
class Provider:
    codename: ClassVar[str]
    is_external: ClassVar[bool]
    is_enabled: ClassVar[bool] = True
    form: ClassVar[Optional[Form]] = None
    metadata_class: ClassVar[BaseModel] = BaseModel

    def get_amount(self, user: AbstractBaseUser, plan: Plan) -> Money:
        return plan.charge_amount

    def charge_online(
        self,
        user: AbstractBaseUser,
        plan: Plan,
        subscription: Optional[Subscription] = None,
        amount: Optional[Money] = None,
        quantity: int = 1,
        subscription_start: Optional[datetime] = None,
        subscription_end: Optional[datetime] = None,
    ) -> Tuple[SubscriptionPayment, str]:
        raise NotImplementedError()

    def charge_offline(
        self,
        user: AbstractBaseUser,
        plan: Plan,
        subscription: Optional[Subscription] = None,
        amount: Optional[Money] = None,
        quantity: int = 1,
        reference_payment: Optional[SubscriptionPayment] = None,
    ) -> SubscriptionPayment:
        raise NotImplementedError()

    def webhook(self, request: Request, payload: dict) -> Response:
        log.warning(f'Webhook for "{self.codename}" triggered without explicit handler')
        return Response(payload)

    def check_payments(self, payments: Iterable[SubscriptionPayment]):
        raise NotImplementedError()


@lru_cache
def get_providers() -> List[Provider]:
    providers = []
    seen_codenames = set()

    payment_providers = getattr(settings, 'SUBSCRIPTIONS_PAYMENT_PROVIDERS', DEFAULT_SUBSCRIPTIONS_PAYMENT_PROVIDERS)

    for class_path in payment_providers:
        provider = import_string(class_path)()
        assert provider.codename not in seen_codenames, f'Duplicate codename "{provider.codename}"'
        providers.append(provider)
        seen_codenames.add(provider.codename)

    return providers


@lru_cache
def get_provider(codename: Optional[str] = None) -> Provider:
    if not (providers := get_providers()):
        raise ProviderNotFound('No providers defined')

    if not codename:
        return first(providers)

    try:
        return one(provider for provider in providers if provider.codename == codename)
    except (ValueError, IndexError) as exc:
        raise ProviderNotFound(f'Provider with codename "{codename}" not found') from exc
