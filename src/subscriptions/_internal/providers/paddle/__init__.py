from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from functools import cached_property
from logging import getLogger
from operator import itemgetter
from typing import ClassVar

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction
from django.utils.timezone import now
from djmoney.money import Money
from more_itertools import one, unique_everseen
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.status import HTTP_200_OK

from ...exceptions import BadReferencePayment, PaymentError
from ...models import Plan, Subscription, SubscriptionPayment
from .. import Provider
from .api import Paddle, PaddleError
from .exceptions import AmbiguousPlanList, MissingPlan
from .schemas import Alert, Passthrough

log = getLogger(__name__)


@dataclass
class PaddleProvider(Provider):
    codename: ClassVar[str] = "paddle"

    vendor_id: ClassVar[int] = int(str(settings.PADDLE_VENDOR_ID))
    vendor_auth_code: ClassVar[str] = str(settings.PADDLE_VENDOR_AUTH_CODE)
    endpoint: ClassVar[str] = str(settings.PADDLE_ENDPOINT)

    # we assume that first webhook will arrive within this period after payment
    WEBHOOK_LOOKUP_PERIOD = timedelta(hours=6)

    # if user already created a SubscriptionPayment within this period, reuse it
    ONLINE_CHARGE_DUPLICATE_LOOKUP_TIME = timedelta(hours=1)

    @cached_property
    def _api(self) -> Paddle:
        return Paddle(
            vendor_id=self.vendor_id,
            vendor_auth_code=self.vendor_auth_code,
            endpoint=self.endpoint,
        )

    @cached_property
    def _plan(self) -> dict:
        plans = self._api.list_subscription_plans()
        return one(plans, too_long=AmbiguousPlanList, too_short=MissingPlan)

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
        payment, is_new = SubscriptionPayment.objects.get_or_create(
            created__gte=now() - self.ONLINE_CHARGE_DUPLICATE_LOOKUP_TIME,
            status=SubscriptionPayment.Status.PENDING,
            metadata__payment_url__isnull=False,
            provider_codename=self.codename,
            amount=amount,
            user=user,
            plan=plan,
            subscription=subscription,
            quantity=quantity,
            defaults=dict(
                provider_transaction_id=None,
                paid_since=since,
                paid_until=until,
            ),
        )

        if is_new:
            payment_link = self._api.generate_payment_link(
                product_id=self._plan["id"],
                prices=[amount * quantity] if amount else [],
                email=getattr(user, "email", ""),
                metadata=Passthrough(
                    subscription_payment_id=str(payment.pk),
                ).dict(),
            )["url"]

            payment.metadata = {
                "payment_url": payment_link,
            }
            payment.save()

        return payment, payment.metadata["payment_url"]

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
        assert quantity > 0
        assert reference_payment.provider_codename == self.codename, (
            f"Reference payment belongs to provider '{reference_payment.provider_codename}' "
            f"while expected to belong to '{self.codename}'"
        )

        if amount.amount <= 0:
            status = SubscriptionPayment.Status.COMPLETED
            metadata = {}

        else:
            try:
                subscription_id = reference_payment.metadata["subscription_id"]
            except KeyError as exc:
                log.error('Reference payment (%s) metadata has no "subscription_id" field', reference_payment)
                raise BadReferencePayment(
                    f'Reference payment {reference_payment.uid} metadata has no "subscription_id" field'
                ) from exc

            # paddle doesn't allow one-off charges with different currencies
            if reference_payment.amount.currency != amount.currency:
                raise BadReferencePayment("Reference payment has different currency")

            try:
                metadata = self._api.one_off_charge(
                    subscription_id=subscription_id,
                    amount=amount.amount * quantity,
                    name=plan.name,
                )
            except PaddleError as exc:
                raise PaymentError(
                    "Failed to offline-charge Paddle",
                    debug_info={
                        "paddle_msg": str(exc),
                        "paddle_code": exc.code,
                        "user": reference_payment.user,
                        "subscription": subscription,
                    },
                ) from exc

            status_mapping = {
                "success": SubscriptionPayment.Status.COMPLETED,
                "pending": SubscriptionPayment.Status.PENDING,
            }
            paddle_status = metadata.get("status")
            try:
                status = status_mapping[paddle_status]
            except KeyError as exc:
                raise PaymentError(
                    "Unknown status from Paddle one-off charge",
                    debug_info={
                        "paddle_status": paddle_status,
                        "user": reference_payment.user,
                        "subscription": subscription,
                    },
                ) from exc

        return SubscriptionPayment.objects.create(
            provider_codename=self.codename,
            provider_transaction_id=None,  # paddle doesn't return anything
            amount=amount,  # type: ignore[misc]
            quantity=quantity,
            status=status,
            user=reference_payment.user,
            plan=plan,
            subscription=subscription,
            paid_since=since,
            paid_until=until,
            metadata=metadata,
        )

    WEBHOOK_ACTION_TO_PAYMENT_STATUS: ClassVar[dict] = {
        "subscription_payment_succeeded": SubscriptionPayment.Status.COMPLETED,
        "subscription_payment_failed": SubscriptionPayment.Status.ERROR,
    }

    def webhook(self, request: Request | None, payload: dict) -> Response:
        alert = Alert.parse_obj(payload)

        try:
            status = self.WEBHOOK_ACTION_TO_PAYMENT_STATUS[alert.alert_name]
        except KeyError:
            log.warning(f"No handler for {alert.alert_name=}")
            return Response(status=HTTP_200_OK)

        with transaction.atomic():
            try:
                payment = SubscriptionPayment.objects.get(
                    provider_codename=self.codename,
                    uid=alert.passthrough.subscription_payment_id,
                )
                payment.provider_transaction_id = alert.subscription_payment_id
                payment.metadata.update(payload)
                payment.status = status
                payment.save()
            except SubscriptionPayment.DoesNotExist:
                log.debug("Payment not found for payload %s", payload)

        return Response(status=HTTP_200_OK)

    def check_payments(self, payments: Iterable[SubscriptionPayment]):
        self.check_payments_using_webhook_history(payments)

    # def check_payments_using_payments_endpoint(self, payments: Iterable[SubscriptionPayment]):
    #     records = self._api.get_payments(
    #         from_=min(payment.created for payment in payments),
    #         to=max(payment.created for payment in payments) + self.WEBHOOK_LOOKUP_PERIOD,
    #     )

    def check_payments_using_webhook_history(self, payments: Iterable[SubscriptionPayment]):
        payment_ids = {payment.pk for payment in payments}

        alerts = self._api.iter_webhook_history(
            start_date=min(payment.created for payment in payments),
            end_date=max(payment.created for payment in payments) + self.WEBHOOK_LOOKUP_PERIOD,
        )

        # don't process alerts with same `id`
        for alert_dict in unique_everseen(alerts, key=itemgetter("id")):
            alert_dict.update(**alert_dict.pop("fields"))  # flatten alert structure

            try:
                alert = Alert.parse_obj(alert_dict)
                if alert.passthrough.subscription_payment_id not in payment_ids:
                    continue

                self.webhook(None, alert_dict)
            except Exception:
                log.exception(f"Could not process alert {alert_dict}")
