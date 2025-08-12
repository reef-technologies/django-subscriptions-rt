from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import count, islice
from logging import getLogger
from operator import attrgetter
from typing import ClassVar, Self
from uuid import uuid4

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser
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
from django.forms import ValidationError
from django.urls import reverse
from django.utils.timezone import now
from model_utils import FieldTracker
from pydantic import BaseModel

from .defaults import DEFAULT_SUBSCRIPTIONS_SUCCESS_URL, DEFAULT_SUBSCRIPTIONS_TRIAL_PERIOD
from .exceptions import (
    InconsistentQuotaCache,
    PaymentError,
    ProlongationImpossible,
    ProviderNotFound,
    RecurringSubscriptionsAlreadyExist,
)
from .fields import MoneyField, RelativeDurationField
from .utils import AdvancedJSONEncoder, merge_iter, pre_validate

log = getLogger(__name__)


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

    objects: ClassVar[Manager["Tier"]] = Manager()  # for mypy

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

    objects: ClassVar[Manager["Plan"]] = Manager()  # for mypy

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

    def same_lifetime(self, other: "QuotaChunk") -> bool:
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

    tracker = FieldTracker()
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
        quota: "Quota",
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

    def get_reference_payment(
        self,
        lookback: timedelta = timedelta(days=90),
    ) -> "SubscriptionPayment":
        """Find a payment to take credentials from for automatic charging"""

        last_successful_payment = (
            SubscriptionPayment.objects.filter(
                status=SubscriptionPayment.Status.COMPLETED,
                user_id=self.user.pk,
                updated__gte=now() - lookback,
            )
            .order_by("updated")
            .last()
        )
        if not last_successful_payment:
            raise SubscriptionPayment.DoesNotExist

        return last_successful_payment

    def charge_automatically(self) -> "SubscriptionPayment":
        from .providers import get_provider_by_codename

        try:
            reference_payment = self.get_reference_payment()
        except SubscriptionPayment.DoesNotExist:
            raise PaymentError("There is no previous successful payment to take credentials from")

        codename = reference_payment.provider_codename
        try:
            provider = get_provider_by_codename(codename)
        except ProviderNotFound as exc:
            raise PaymentError(f'Could not retrieve provider "{codename}"') from exc

        return provider.charge_automatically(
            plan=self.plan,
            amount=self.plan.charge_amount,
            quantity=self.quantity,
            since=self.end,
            until=self.end + self.plan.charge_period,
            subscription=self,
            reference_payment=reference_payment,
        )

    def adjust_default_subscription(self):
        # this subscription pushes out every default subscription out of its (start,end) period;
        # if this subscription is shrunk, then it fills the gap with default subscription

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
    # TODO: Also, this field is legacy and should be replaced by provider-specific child
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
        get_latest_by: str | tuple | list = "created"

    def save(self, *args, **kwargs) -> None:
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
    def provider(self) -> "Provider":
        from .providers import get_provider_by_codename

        return get_provider_by_codename(self.provider_codename)


class SubscriptionPayment(AbstractTransaction):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="payments")
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="payments")
    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="payments",
        editable=False,
    )
    quantity = models.PositiveIntegerField(default=1)
    paid_since = models.DateTimeField()
    paid_until = models.DateTimeField()

    tracker = FieldTracker()
    objects: ClassVar[Manager["SubscriptionPayment"]] = Manager()  # for mypy

    class Meta(AbstractTransaction.Meta):
        db_table = "subscriptions_v0_subscriptionpayment"
        get_latest_by = (
            "paid_until",
            "created",
        )
        indexes = [
            Index(fields=["subscription", "paid_until", "created"]),  # for `latest()` lookups
        ]

    def __str__(self) -> str:
        return (
            f"{self.short_id} {self.get_status_display()} "
            f"{self.user} {self.amount} from={self.paid_since} until={self.paid_until}"
        )

    def clean_subscription(self) -> None:
        if not self.subscription:
            return

        if self.subscription.plan != self.plan:
            raise ValidationError("Subscription plan does not match payment plan")

        if self.subscription.user != self.user:
            raise ValidationError("Subscription user does not match payment user")

        if self.subscription.quantity != self.quantity:
            raise ValidationError("Subscription quantity does not match payment quantity")

    def clean_paid_until(self) -> None:
        if not self.subscription:
            return

        if self.paid_until < self.subscription.start or self.paid_since > self.subscription.end:
            raise ValidationError("Payment period does not overlap with subscription period")

        if self.paid_since >= self.paid_until:
            raise ValidationError("paid_since must be less than paid_until")

    def extend_or_create_subscription(self) -> None:
        assert self.status == self.Status.COMPLETED, "Payment must be completed to extend or create a subscription"

        if self.subscription:
            # extend subscription period
            self.subscription.start = min(self.paid_since, self.subscription.start)
            self.subscription.end = max(self.paid_until, self.subscription.end)
            self.subscription.save()
        else:
            # create a new subscription
            self.subscription = Subscription.objects.create(
                user=self.user,
                plan=self.plan,
                quantity=self.quantity,
                start=self.paid_since,
                end=self.paid_until,
            )

    @pre_validate
    def save(self, *args, **kwargs) -> None:
        if self.tracker.has_changed("status") and self.status == self.Status.COMPLETED:
            self.extend_or_create_subscription()

        return super().save(*args, **kwargs)

    @property
    def meta(self) -> BaseModel:
        from .providers import get_provider_by_codename

        provider = get_provider_by_codename(self.provider_codename)
        return provider.metadata_class.parse_obj(self.metadata)

    @meta.setter
    def meta(self, value: BaseModel) -> None:
        self.metadata = value.dict()


class SubscriptionPaymentRefund(AbstractTransaction):
    original_payment = models.ForeignKey(SubscriptionPayment, on_delete=models.PROTECT, related_name="refunds")
    # TODO: add support by providers

    tracker = FieldTracker()
    objects: ClassVar[Manager["SubscriptionPaymentRefund"]] = Manager()  # for mypy

    class Meta(AbstractTransaction.Meta):
        db_table = "subscriptions_v0_subscriptionpaymentrefund"


class Tax(models.Model):
    subscription_payment = models.ForeignKey(SubscriptionPayment, on_delete=models.PROTECT, related_name="taxes")
    amount = MoneyField()

    objects: ClassVar[Manager["Tax"]] = Manager()  # for mypy

    class Meta(SubscriptionsMeta):
        db_table = "subscriptions_v0_tax"

    def __str__(self) -> str:
        return f"{self.pk} {self.amount}"


from .signals import create_default_subscription_for_new_user  # noqa


def get_trial_period(user: AbstractBaseUser, plan: Plan) -> relativedelta:
    trial_period = getattr(settings, "SUBSCRIPTIONS_TRIAL_PERIOD", DEFAULT_SUBSCRIPTIONS_TRIAL_PERIOD)

    if (
        trial_period
        and plan.charge_amount
        and plan.is_recurring()
        and not user.payments.filter(status=SubscriptionPayment.Status.COMPLETED).exists()
        and not user.subscriptions.recurring().exists()
    ):
        return trial_period

    return relativedelta()


def subscribe(user: AbstractBaseUser, plan: Plan, quantity: int, provider: "Provider") -> tuple[SubscriptionPayment, str, str]:
    from .validators import get_validators

    now_ = now()
    active_subscriptions = user.subscriptions.active().order_by("end")
    for validator in get_validators():
        try:
            validator(active_subscriptions, plan)
        except RecurringSubscriptionsAlreadyExist as exc:
            # if there are recurring subscriptions and they are conflicting,
            # we force-terminate them and go on with creating a new one
            for subscription in exc.subscriptions:
                subscription.end = now_
                subscription.save()

    automatic_charge_succeeded = False
    reference_payment = (
        user.payments.filter(
            provider_codename=provider.codename,
            status=SubscriptionPayment.Status.COMPLETED,
        )
        .order_by("created")
        .last()
    )
    if reference_payment:
        try:
            payment = provider.charge_automatically(
                plan=plan,
                amount=plan.charge_amount,
                quantity=quantity,
                since=now_,
                until=now_ + plan.charge_period,
                reference_payment=reference_payment,
            )
            automatic_charge_succeeded = True
            redirect_url = getattr(settings, "SUBSCRIPTIONS_SUCCESS_URL", DEFAULT_SUBSCRIPTIONS_SUCCESS_URL)
        except (PaymentError, NotImplementedError):
            pass
        except Exception:
            log.exception("Background charge error")

    if not automatic_charge_succeeded:
        trial_period = get_trial_period(user=user, plan=plan)
        payment, redirect_url = provider.charge_interactively(
            user=user,
            plan=plan,
            amount=plan.charge_amount * (0 if trial_period else 1),  # zero with currency
            quantity=quantity,
            since=now_,
            until=now_ + (trial_period or plan.charge_period),
        )

        if trial_period:
            assert not payment.subscription
            payment.subscription = Subscription.objects.create(
                user=user,
                plan=plan,
                quantity=quantity,
                start=now_,
                end=now_,
                initial_charge_offset=trial_period,  # TODO: ugly
            )
            payment.subscription.save()
            payment.save()

    return payment, redirect_url, automatic_charge_succeeded
