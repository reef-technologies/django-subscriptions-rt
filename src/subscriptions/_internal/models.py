from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import count, islice
from logging import getLogger
from operator import attrgetter
from typing import TYPE_CHECKING, ClassVar, Self
from uuid import uuid4

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.db import models
from django.db.models import (
    DateTimeField,
    ExpressionWrapper,
    F,
    Index,
    Manager,
    Q,
    UniqueConstraint,
)
from django.db.models.functions import Least
from django.urls import reverse
from django.utils.timezone import now
from pydantic import BaseModel

from .exceptions import (
    InconsistentQuotaCache,
    PaymentError,
    ProlongationImpossible,
    ProviderNotFound,
)
from .fields import MoneyField, RelativeDurationField
from .utils import AdvancedJSONEncoder, merge_iter

log = getLogger(__name__)

if TYPE_CHECKING:
    from .providers import Provider

INFINITY = relativedelta(days=365 * 1000)
MAX_DATETIME = datetime.max.replace(tzinfo=UTC)


class SubscriptionsMeta:
    app_label = "subscriptions"


class Resource(models.Model):
    codename = models.CharField(max_length=255)
    units = models.CharField(max_length=255, blank=True)

    class Meta(SubscriptionsMeta):
        db_table = "subscriptions_v0_resource"
        constraints = [
            UniqueConstraint(fields=["codename"], name="unique_resource"),
        ]

    def __str__(self) -> str:
        return self.codename


class Feature(models.Model):
    codename = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True)
    is_negative = models.BooleanField(default=False)

    class Meta(SubscriptionsMeta):
        db_table = "subscriptions_v0_feature"

    def __str__(self) -> str:
        return self.codename


class Tier(models.Model):
    codename = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True)
    is_default = models.BooleanField(db_index=True, default=False)
    level = models.SmallIntegerField(default=0)

    features = models.ManyToManyField(Feature)

    objects: ClassVar[Manager[Tier]] = Manager()  # for mypy

    class Meta(SubscriptionsMeta):
        db_table = "subscriptions_v0_tier"

    def __str__(self) -> str:
        return self.codename


class Plan(models.Model):
    codename = models.CharField(max_length=255)
    name = models.CharField(max_length=255)
    charge_amount = MoneyField(blank=True, null=True)
    charge_period = RelativeDurationField(blank=True, help_text="leave blank for one-time charge")
    max_duration = RelativeDurationField(blank=True, help_text="leave blank to make it an infinite subscription")
    tier = models.ForeignKey(
        Tier,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        help_text="group of features connected to this plan",
        related_name="plans",
    )
    metadata = models.JSONField(blank=True, default=dict, encoder=AdvancedJSONEncoder)
    is_enabled = models.BooleanField(default=True)

    objects: ClassVar[Manager[Plan]] = Manager()  # for mypy

    class Meta(SubscriptionsMeta):
        db_table = "subscriptions_v0_plan"
        constraints = [
            UniqueConstraint(fields=["codename"], name="unique_plan_codename"),
        ]

    def __str__(self) -> str:
        return f"{self.pk} {self.name}"

    def get_absolute_url(self) -> str:
        return reverse("plan", kwargs={"plan_id": self.pk})

    def save(self, *args, **kwargs):
        self.charge_period = self.charge_period or INFINITY
        self.max_duration = self.max_duration or INFINITY
        return super().save(*args, **kwargs)

    def is_recurring(self) -> bool:
        return self.charge_period != INFINITY


@dataclass
class QuotaChunk:
    resource: Resource
    start: datetime
    end: datetime
    amount: int
    remains: int

    def __str__(self) -> str:
        return f"{self.remains}/{self.amount} {self.resource} {self.start} - {self.end}"

    def includes(self, date: datetime) -> bool:
        return self.start <= date < self.end

    def same_lifetime(self, other: QuotaChunk) -> bool:
        return self.start == other.start and self.end == other.end


@dataclass
class QuotaCache:
    datetime: datetime
    chunks: list[QuotaChunk]

    def apply(self, target_chunks: Iterable[QuotaChunk]) -> Iterator[QuotaChunk]:
        """
        Apply itself to `chunks` without intercepting their order,
        and yield application results.
        """

        get_key = attrgetter("resource", "start", "end", "amount")
        cached_chunks = defaultdict(list)
        for chunk in self.chunks:
            cached_chunks[get_key(chunk)].append(chunk)

        for target_chunk in target_chunks:
            key = get_key(target_chunk)
            try:
                yield cached_chunks[key].pop()
            except IndexError:
                yield target_chunk

        if any((non_paired := values) for values in cached_chunks.values()):
            raise InconsistentQuotaCache(f"Non-paired cached chunk(s) detected: {non_paired}")


class SubscriptionQuerySet(models.QuerySet):
    def overlap(self, since: datetime, until: datetime, include_until: bool = False) -> Self:
        """
        Filter subscriptions that overlap with
        - [since, until) period (include_until==False) or
        - [since, until] period (include_until==True).
        """
        return self.filter(
            **{
                "end__gte": since,
                "start__lte" if include_until else "start__lt": until,
            }
        )

    def active(self, at: datetime | None = None) -> Self:
        at = at or now()
        return self.overlap(at, at, include_until=True)

    def inactive(self, at: datetime | None = None) -> Self:
        """
        Lists all subscriptions that are not currently active.
        This is the equivalent of "ended" part of "ended_or_ending".
        """
        at = at or now()
        return self.filter(end__lte=at)

    def expiring(self, within: timedelta, since: datetime | None = None) -> Self:
        since = since or now()
        return self.filter(end__gte=since, end__lte=since + within)

    def recurring(self, predicate: bool = True) -> Self:
        subscriptions = self.select_related("plan")
        return (
            subscriptions.exclude(plan__charge_period=INFINITY)
            if predicate
            else subscriptions.filter(plan__charge_period=INFINITY)
        )

    def charged(self) -> Self:
        """
        Checking for subscriptions that have completed payments with amount more than zero.
        """
        return self.filter(payments__status=SubscriptionPayment.Status.COMPLETED, payments__amount__gt=0)

    def with_ages(self, at: datetime | None = None) -> Self:
        return self.annotate(
            age=ExpressionWrapper(Least(at or now(), F("end")) - F("start"), output_field=DateTimeField()),
        )

    def ended_or_ending(self) -> Self:
        now_ = now()
        return self.filter(Q(end__lte=now_) | Q(end__gt=now_, auto_prolong=False))

    def new(self, since: datetime, until: datetime) -> Self:
        """Newly created subscriptions within selected period."""
        return self.filter(start__gte=since, start__lte=until)


def default_initial_charge() -> relativedelta:
    return relativedelta()


class Subscription(models.Model):
    uid = models.UUIDField(primary_key=True, default=uuid4)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="subscriptions")
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscriptions")
    auto_prolong = models.BooleanField()
    quantity = models.PositiveIntegerField(default=1)
    initial_charge_offset = RelativeDurationField(blank=True, default=default_initial_charge)
    start = models.DateTimeField(blank=True)
    end = models.DateTimeField(blank=True)

    objects = SubscriptionQuerySet.as_manager()

    class Meta(SubscriptionsMeta):
        db_table = "subscriptions_v0_subscription"
        get_latest_by = "start"

    @property
    def id(self) -> str | None:
        if self.uid:
            return str(self.uid)

    @property
    def short_id(self) -> str | None:
        with suppress(TypeError):
            return str(self.pk)[:8]

    def __str__(self) -> str:
        return f"{self.short_id} {self.user} {self.plan}, {self.start} - {self.end}"

    @property
    def max_end(self) -> datetime:
        return self.start + self.plan.max_duration

    def save(self, *args, **kwargs):
        self.start = self.start or now()
        self.end = self.end or min(self.start + self.plan.charge_period, self.max_end)
        if self.auto_prolong is None:
            self.auto_prolong = self.plan.is_recurring()
        super().save(*args, **kwargs)
        self.adjust_default_subscription()

    def stop(self):
        self.end = now()
        self.auto_prolong = False
        self.save(update_fields=["end", "auto_prolong"])

    def prolong(self) -> datetime:
        """Returns next uncovered charge_date or subscription.max_end"""

        next_charge_dates = islice(self.iter_charge_dates(since=self.end), 2)
        if (first_charge_date := next(next_charge_dates)) and self.end == first_charge_date:
            try:
                end = next(next_charge_dates)
            except StopIteration as exc:
                raise ProlongationImpossible("No next charge date") from exc
        else:
            end = first_charge_date

        if end > (max_end := self.max_end):
            if self.end >= max_end:
                raise ProlongationImpossible("Current subscription end is already the maximum end")

            end = max_end

        return end

    def iter_quota_chunks(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
        sort_by: Callable = attrgetter("start"),
    ) -> Iterator[QuotaChunk]:
        quotas = self.plan.quotas.all()
        yield from merge_iter(
            *(self._iter_single_quota_chunks(quota=quota, since=since, until=until) for quota in quotas),
            key=sort_by,
        )

    def _iter_single_quota_chunks(
        self,
        quota: Quota,
        since: datetime | None = None,
        until: datetime | None = None,
    ):
        epsilon = timedelta(
            milliseconds=1
        )  # we use epsilon to exclude chunks which start right at `since - quota.burns_in`
        min_start_time = (
            max(since - quota.burns_in + epsilon, self.start) if since else self.start
        )  # quota chunks starting after this are OK
        until = min(until, self.end) if until else self.end

        for i in count(start=0):
            start = self.start + i * quota.recharge_period
            if start < min_start_time:
                continue

            if start > until:
                return

            amount = quota.limit * self.quantity
            yield QuotaChunk(
                resource=quota.resource,
                start=start,
                end=min(start + quota.burns_in, self.end),
                amount=amount,
                remains=amount,
            )

    def iter_charge_dates(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> Iterator[datetime]:
        """Including first charge"""

        charge_period = self.plan.charge_period
        since = since or self.start

        for i in count(start=0):
            charge_date = self.start + self.initial_charge_offset + charge_period * i

            if charge_date < since:
                continue

            if until and charge_date > until:
                return

            if charge_period == INFINITY and i != 0:
                return

            yield charge_date

    def get_reference_payment(self) -> SubscriptionPayment:
        return self.payments.filter(status=SubscriptionPayment.Status.COMPLETED).latest()

    def charge_offline(self) -> SubscriptionPayment:
        from .providers import get_provider

        try:
            reference_payment = self.get_reference_payment()
        except SubscriptionPayment.DoesNotExist:
            raise PaymentError("There is no previous successful payment to take credentials from")

        provider_codename = reference_payment.provider_codename
        try:
            provider = get_provider(provider_codename)
        except ProviderNotFound as exc:
            raise PaymentError(f'Could not retrieve provider "{provider_codename}"') from exc

        return provider.charge_offline(
            user=self.user,
            plan=self.plan,
            subscription=self,
            quantity=self.quantity,
            reference_payment=reference_payment,
        )

    def adjust_default_subscription(self):
        # this subscription pushes out every default subscription out of its (start,end) period;
        # if this subscription is shrink, then it fills the gap with default subscription

        from .functions import get_default_plan

        try:
            default_plan = get_default_plan()
            if not default_plan:
                return
        except Plan.DoesNotExist:
            return

        if self.plan == default_plan or not self.plan.is_recurring():
            return

        # adjust overlapping default subscriptions
        default_subscriptions = self.user.subscriptions.overlap(self.start, self.end, include_until=True).filter(
            plan=default_plan
        )

        for default_subscription in default_subscriptions:
            if default_subscription.start >= self.start:
                if default_subscription.end <= self.end:
                    # if default subscription is fully covered with current subscription -> delete default
                    default_subscription.delete()
                else:
                    # otherwise just shift default subscription to end of current subscription
                    default_subscription.start = self.end
                    default_subscription.save()

            # here default_subscription.start < self.start
            elif default_subscription.end != self.start:
                # split default subscription into two parts
                default_subscription.end = self.start
                default_subscription.save()

                default_subscription.pk = None
                default_subscription._state.adding = True
                default_subscription.start = self.end
                default_subscription.end = datetime.max
                default_subscription.save()

        # create a default subscription if there is none afterwards;
        # note that this will not create a default subscription if ANY
        # afterward subscription exists (either recurring or not)
        if not self.user.subscriptions.active(at=self.end).exclude(end=self.end).exists():
            next_subscription = self.user.subscriptions.filter(start__gt=self.end).order_by("start").first()
            if next_subscription and next_subscription.plan == default_plan:
                next_subscription.start = self.end
                next_subscription.save()
            else:
                Subscription.objects.create(
                    user=self.user,
                    plan=default_plan,
                    start=self.end,
                    end=next_subscription.start if next_subscription else MAX_DATETIME,
                )


class Quota(models.Model):
    plan = models.ForeignKey(Plan, on_delete=models.CASCADE, related_name="quotas")
    resource = models.ForeignKey(Resource, on_delete=models.CASCADE, related_name="quotas")
    limit = models.PositiveIntegerField()
    recharge_period = RelativeDurationField(
        blank=True, help_text="leave blank for recharging only after each subscription prolongation (charge)"
    )
    burns_in = RelativeDurationField(blank=True, help_text="leave blank to burn each recharge period")

    class Meta(SubscriptionsMeta):
        db_table = "subscriptions_v0_quota"
        constraints = [
            UniqueConstraint(fields=["plan", "resource"], name="unique_quota"),
        ]

    def __str__(self) -> str:
        return (
            f"{self.pk} {self.resource} {self.limit:,}"
            f"{self.resource.units}/{self.recharge_period}, "
            f"burns in {self.burns_in}"
        )

    def save(self, *args, **kwargs):
        self.recharge_period = self.recharge_period or self.plan.charge_period
        self.burns_in = self.burns_in or self.recharge_period
        return super().save(*args, **kwargs)


class Usage(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="usages")
    resource = models.ForeignKey(Resource, on_delete=models.PROTECT, related_name="usages")
    amount = models.PositiveIntegerField(default=1)
    datetime = models.DateTimeField(blank=True)

    class Meta(SubscriptionsMeta):
        db_table = "subscriptions_v0_usage"
        indexes = [
            Index(fields=["user", "resource"]),
        ]

    def __str__(self) -> str:
        return f"{self.pk} {self.amount:,}{self.resource.units} {self.resource} at {self.datetime}"

    def save(self, *args, **kwargs):
        self.datetime = self.datetime or now()
        return super().save(*args, **kwargs)


class AbstractTransaction(models.Model):
    class Status(models.IntegerChoices):
        PENDING = 0
        PREAUTH = 1
        COMPLETED = 2
        CANCELLED = 3
        ERROR = 4

    uid = models.UUIDField(primary_key=True, blank=True)

    # This field should go away once we add provider-specific child models (see below)
    provider_codename = models.CharField(max_length=255)
    # Sometimes there is no information about internal provider's transaction ID.
    # For such cases we set `provider_transaction_id` to None and fill it in later.
    # Also, this field is legacy and should be replaced by provider-specific child
    # model, see https://github.com/reef-technologies/django-subscriptions-rt/issues/13
    provider_transaction_id = models.CharField(max_length=255, blank=True, null=True)
    status = models.PositiveSmallIntegerField(choices=Status.choices, default=Status.PENDING)
    amount = MoneyField(
        blank=True, null=True
    )  # set None for services where the payment information is completely out of reach
    # source = models.ForeignKey(MoneyStorage, on_delete=models.PROTECT, related_name='transactions_out')
    # destination = models.ForeignKey(MoneyStorage, on_delete=models.PROTECT, related_name='transactions_in')
    metadata = models.JSONField(blank=True, default=dict, encoder=AdvancedJSONEncoder)
    created = models.DateTimeField(blank=True, editable=False)
    updated = models.DateTimeField(blank=True, editable=False)

    class Meta(SubscriptionsMeta):
        abstract = True
        indexes = [
            Index(fields=("provider_codename", "provider_transaction_id")),
        ]
        get_latest_by = "created"

    def save(self, *args, **kwargs):
        now_ = now()
        self.uid = self.uid or uuid4()
        self.created = self.created or now_
        self.updated = now_
        return super().save(*args, **kwargs)

    @property
    def id(self) -> str | None:
        if self.uid:
            return str(self.uid)

    @property
    def short_id(self) -> str | None:
        with suppress(TypeError):
            return str(self.pk)[:8]

    def __str__(self) -> str:
        return f"{self.short_id} {self.get_status_display()} {self.amount}"

    @property
    def provider(self) -> Provider:
        from .providers import get_provider

        return get_provider(self.provider_codename)


class SubscriptionPayment(AbstractTransaction):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="payments")
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="payments")
    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.PROTECT,
        blank=True,
        null=True,  # TODO: make this field required?
        related_name="payments",
    )
    quantity = models.PositiveIntegerField(default=1)

    # If not specifying following fields, they are set automatically after Subscription
    # creation / prolongation; however, it works other way round as well: if these fields
    # are set, their values will be used to adjust subscription duration; this is handy
    # when payment and subscription info comes from external source and is out of control
    # of the application.
    # TODO: make these fields required?
    subscription_start = models.DateTimeField(blank=True, null=True)  # TODO: paid from
    subscription_end = models.DateTimeField(blank=True, null=True)  # TODO: paid until

    objects: ClassVar[Manager[SubscriptionPayment]] = Manager()  # for mypy

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._initial_status = self.uid and self.status

    class Meta(AbstractTransaction.Meta):
        db_table = "subscriptions_v0_subscriptionpayment"
        # TODO: changing latest() to `subscription_end` may not work well when subscription_end is None
        # get_latest_by = 'subscription_end'

    def __str__(self) -> str:
        return (
            f"{self.short_id} {self.get_status_display()} "
            f"{self.user} {self.amount} from={self.subscription_start} until={self.subscription_end}"
        )

    def save(self, *args, **kwargs):
        assert bool(self.subscription_start) == bool(self.subscription_end), (
            "subscription_start and subscription_end should both be either set or not"
        )

        if self.status == self.Status.COMPLETED:
            if subscription := self.subscription:
                self.subscription_start = self.subscription_start or subscription.end

                if self.subscription_end:
                    assert self.subscription_end > self.subscription_start

                    # change existing subscription duration based on payment's end
                    if self.subscription_end <= subscription.end:
                        log.warning(
                            "Payment's end (%s) is earlier than current subscription's end (%s) "
                            "-> payment has no effect",
                            self.subscription_end,
                            subscription.end,
                        )
                    else:
                        subscription.end = self.subscription_end

                else:
                    # prolong existing subscription and set payment's (start, end)
                    self.subscription_end = subscription.end = subscription.prolong()  # TODO: what if this fails?

                subscription.save()

            else:
                self.subscription = Subscription.objects.create(
                    user=self.user,
                    plan=self.plan,
                    quantity=self.quantity,
                    start=self.subscription_start,
                    end=self.subscription_end,
                )
                # in case subscription_start and subscription_end are empty:
                self.subscription_start = self.subscription.start
                self.subscription_end = self.subscription.end

            new_status = self.status != self._initial_status and self.status
            if new_status == self.Status.COMPLETED:
                pass  # TODO: send email if not silent

        return super().save(*args, **kwargs)

    @property
    def meta(self) -> BaseModel:
        from .providers import get_provider

        provider = get_provider(self.provider_codename)
        return provider.metadata_class.parse_obj(self.metadata)

    @meta.setter
    def meta(self, value: BaseModel):
        self.metadata = value.dict()


class SubscriptionPaymentRefund(AbstractTransaction):
    original_payment = models.ForeignKey(SubscriptionPayment, on_delete=models.PROTECT, related_name="refunds")
    # TODO: add support by providers

    objects: ClassVar[Manager[SubscriptionPaymentRefund]] = Manager()  # for mypy

    class Meta(AbstractTransaction.Meta):
        db_table = "subscriptions_v0_subscriptionpaymentrefund"


class Tax(models.Model):
    subscription_payment = models.ForeignKey(SubscriptionPayment, on_delete=models.PROTECT, related_name="taxes")
    amount = MoneyField()

    objects: ClassVar[Manager[Tax]] = Manager()  # for mypy

    class Meta(SubscriptionsMeta):
        db_table = "subscriptions_v0_tax"

    def __str__(self) -> str:
        return f"{self.pk} {self.amount}"


from .signals import create_default_subscription_for_new_user  # noqa
