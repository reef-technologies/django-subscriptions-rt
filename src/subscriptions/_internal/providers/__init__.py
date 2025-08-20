from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from functools import cached_property, lru_cache
from logging import getLogger
from typing import ClassVar
from uuid import uuid4

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser
from django.utils.module_loading import import_string
from djmoney.money import Money
from more_itertools import all_unique, first
from pydantic import BaseModel
from rest_framework.request import Request
from rest_framework.response import Response

from ..defaults import DEFAULT_SUBSCRIPTIONS_PAYMENT_PROVIDERS
from ..exceptions import ProviderNotFound
from ..models import Plan, Subscription, SubscriptionPayment

log = getLogger(__name__)


@dataclass
class Provider:
    metadata_class: ClassVar[type[BaseModel]] = BaseModel

    @property
    def fqn(self) -> str:
        return f"{self.__module__}.{self.__class__.__name__}"

    @cached_property
    def codename(self) -> str:
        return self.__class__.__name__.lower().removesuffix("provider")

    def charge_interactively(
        self,
        user: AbstractBaseUser,
        plan: Plan,
        amount: Money,
        quantity: int,
        since: datetime,
        until: datetime,
        subscription: Subscription | None = None,
    ) -> tuple[SubscriptionPayment, str]:
        raise NotImplementedError

    def charge_automatically(
        self,
        plan: Plan,
        amount: Money,
        quantity: int,
        since: datetime,
        until: datetime,
        reference_payment: SubscriptionPayment,
        subscription: Subscription | None = None,
    ) -> SubscriptionPayment:
        """Returns a new SubscriptionPayment (PENDING or COMPLETED) or raises PaymentError / InvalidOperationError."""
        raise NotImplementedError

    def cancel(self, subscription: Subscription) -> None:
        subscription.auto_prolong = False
        subscription.save()

    def webhook(self, request: Request, payload: dict) -> Response:
        log.warning(f'Webhook for "{self.codename}" triggered without explicit handler')
        return Response(payload)

    def check_payments(self, payments: Iterable[SubscriptionPayment]):
        raise NotImplementedError


@lru_cache
def get_provider(fqn: str) -> Provider:
    return import_string(fqn)()


def get_providers_fqns() -> list[str]:
    return getattr(settings, "SUBSCRIPTIONS_PAYMENT_PROVIDERS", DEFAULT_SUBSCRIPTIONS_PAYMENT_PROVIDERS)


@lru_cache
def get_provider_by_codename(name: str) -> Provider:
    try:
        return first(provider for fqn in get_providers_fqns() if (provider := get_provider(fqn)).codename == name)
    except ValueError as exc:
        raise ProviderNotFound(f'Provider with codename "{name}" not found') from exc


codenames = [get_provider(fqn).codename for fqn in get_providers_fqns()]
assert all_unique(codenames), f"Duplicate providers codenames found: {codenames}"
