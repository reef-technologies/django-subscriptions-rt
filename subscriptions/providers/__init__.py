from dataclasses import dataclass
from functools import lru_cache
from logging import getLogger
from typing import ClassVar, List, Optional

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser
from django.forms import Form
from django.http import HttpResponseRedirect
from django.utils.module_loading import import_string
from more_itertools import first, one
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.serializers import Serializer

from ..api.serializers import WebhookSerializer
from ..defaults import DEFAULT_SUBSCRIPTIONS_PAYMENT_PROVIDERS
from ..exceptions import ProviderNotFound
from ..models import Plan, Subscription

log = getLogger(__name__)


@dataclass
class Provider:
    codename: ClassVar[str] = 'default'
    is_enabled: ClassVar[bool] = True
    form: ClassVar[Optional[Form]] = None
    webhook_serializer_class: ClassVar[Serializer] = WebhookSerializer

    def charge_online(self, user: AbstractBaseUser, plan: Plan, subscription: Optional[Subscription] = None) -> HttpResponseRedirect:
        raise NotImplementedError()

    def charge_offline(self, user: AbstractBaseUser, plan: Plan, subscription: Optional[Subscription] = None):
        raise NotImplementedError()

    def webhook(self, request: Request, serializer: WebhookSerializer) -> Response:
        log.warning(f'Webhook for "{self.codename}" triggered without explicit handler')
        return Response(serializer.data)

    # TODO: what to do with this?
    # def process_subscription_request(self, request: Request, serializer: PaymentSerializer) -> Response:

    #
    #

    #     try:
    #         active_recurring_subscription = one(active_recurring_subscriptions)
    #     except ValueError:
    #         log.warning(f'Multiple active recurring subscriptions detected: {active_recurring_subscriptions}')
    #         active_recurring_subscription = active_recurring_subscriptions[-1]

    #     if not active_recurring_subscription:
    #         if not plan.is_enabled():
    #             raise SubscriptionError('Selected plan is no longer available')

    #         return

    #         payment_url = self._api.generate_payment_link(
    #             product_id=self._plan['id'],
    #             prices=[plan.charge_amount],
    #             email=request.user.email,
    #             metadata={
    #                 'user': request.user.pk,
    #                 'email': request.user.email,
    #                 'plan': plan.pk,
    #                 'charge_amount': plan.charge_amount,
    #             },
    #         )
    #         return HttpResponseRedirect(payment_url)
    #         # TODO: create a webhook

    #     if plan == active_recurring_subscription.plan:
    #         raise SubscriptionError('Selected plan is already active')

    #     # TODO: switch subscription
    #     raise NotImplementedError()

    #
    #
    #

    #     now_ = now()
    #     subscription = request.user.subscriptions.active().filter(plan=plan).order_by('-end').last()
    #     if created := not bool(subscription):
    #         if not plan.is_enabled:
    #             raise PermissionDenied(detail='Cannot register new subscription because the plan is disabled')

    #         subscription = Subscription.objects.create(
    #             user=request.user,
    #             plan=plan,
    #             start=now_,
    #         )

    #     try:
    #         charge_date = next(subscription.iter_charge_dates(since=now_))
    #     except StopIteration as exc:
    #         raise PermissionDenied(detail='No ongoing charge dates found') from exc

    #     SubscriptionPayment.objects.create(
    #         provider_codename=self.codename,
    #         provider_transaction_id=uuid4(),
    #         status=SubscriptionPayment.Status.COMPLETED,
    #         amount=plan.charge_amount or 0,
    #         user=request.user,
    #         subscription=subscription,
    #         subscription_charge_date=charge_date,
    #     )

    #     if not created:
    #         try:
    #             subscription.prolong()
    #         except ProlongationImpossible as exc:
    #             raise PermissionDenied(detail='Cannot prolong subscription') from exc

    #     result = PaymentSerializer({
    #         'plan': plan,
    #     })

    #     return Response(result.data)


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
