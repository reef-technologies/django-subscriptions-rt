from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import median
from typing import Self

from dateutil.rrule import DAILY, HOURLY, MINUTELY, MONTHLY, SECONDLY, WEEKLY, YEARLY, rrule  # noqa
from django.db.models import Q, QuerySet
from django.utils.timezone import now
from djmoney.money import Money
from more_itertools import one, pairwise

from .models import (
    AbstractTransaction,
    Plan,
    Subscription,
    SubscriptionPayment,
    SubscriptionPaymentRefund,
    SubscriptionQuerySet,
)
from .utils import NO_MONEY


def _iter_periods(frequency: int, since: datetime, until: datetime) -> Iterator[tuple[datetime, datetime]]:
    """
    Generate report instances for [since, until) period with desired frequency.

    For frequency, use `subscriptions.reports.[YEARLY|MONTHLY|WEEKLY|DAILY|HOURLY|MINUTELY|SECONDLY]`.
    """
    assert since.microsecond == until.microsecond == 0, (
        "iter_periods would truncate microseconds, use .replace(microsecond=0) for `since` and `until`"
    )
    points_in_time = rrule(frequency, dtstart=since, until=until)  # type: ignore[arg-type]
    end = since
    for start, end in pairwise(points_in_time):
        yield (start, end)

    if end != until:  # remains if since-until period doesn't match frequency perfectly
        yield (end, until)


def get_average(values: list[Money]) -> Money:
    currency = one({value.currency for value in values})
    average = median(value.amount for value in values)
    return Money(average, currency) if values else NO_MONEY


@dataclass
class SubscriptionsReport:
    """
    Report for subscriptions. Period's end is excluded: [since, until)
    """

    since: datetime
    until: datetime = field(default_factory=now)
    include_until: bool = False

    @classmethod
    def iter_periods(cls, frequency: int, since: datetime, until: datetime, **kwargs) -> Iterator[Self]:
        for start, end in _iter_periods(frequency, since, until):
            yield cls(since=start, until=end, **kwargs)

    @property
    def overlapping(self) -> SubscriptionQuerySet:
        return Subscription.objects.overlap(self.since, self.until, include_until=self.include_until)

    @property
    def new(self) -> SubscriptionQuerySet:
        return self.overlapping.new(self.since, self.until).order_by("start")

    def get_new_count(self) -> int:
        """Number of newly created subscriptions within selected period."""
        return self.new.count()

    def get_new_datetimes(self) -> list[datetime]:
        """List of newly created subscriptions' dates within selected period."""
        return list(self.new.values_list("start", flat=True))

    @property
    def ended_or_ending(self) -> SubscriptionQuerySet:
        """Subscriptions that end (or gonna end) within selected period."""
        return self.overlapping.filter(end__gte=self.since, end__lte=self.until).ended_or_ending().order_by("end")

    def get_ended_count(self) -> int:
        """Number of subscriptions ending within selected period."""
        return self.ended_or_ending.count()

    def get_ended_datetimes(self) -> list[datetime]:
        """List of end dates for subscriptions that end within selected period."""
        return list(self.ended_or_ending.values_list("end", flat=True))

    def get_ended_or_ending_ages(self) -> list[timedelta]:
        """List of ages for ended or ending subscriptions."""
        return self.ended_or_ending.with_ages(at=self.until).values_list("age", flat=True)

    @property
    def active(self) -> SubscriptionQuerySet:
        """Subscriptions that remain active within selected period."""
        now_ = now()
        return self.overlapping.exclude(
            Q(end__lte=now_) | Q(end__gt=now_, auto_prolong=False),
            end__gte=self.since,
            end__lte=self.until,
        ).order_by("start")

    def get_active_count(self) -> int:
        """Number of subscriptions that remain active within selected period."""
        return self.active.count()

    def get_active_users_count(self) -> int:
        """Number of users that have active subscriptions within selected period."""
        return self.active.order_by("user").distinct("user").count()

    def get_active_ages(self) -> list[timedelta]:
        """List of ages for active subscriptions."""
        return self.active.with_ages(at=self.until).values_list("age", flat=True)

    def get_active_plans_and_quantities(self) -> list[tuple[Plan, int]]:
        """List of plan & quantity tuples per subscription."""
        id_to_plan = {plan.pk: plan for plan in Plan.objects.all()}
        return [(id_to_plan[plan_id], quantity) for plan_id, quantity in self.active.values_list("plan", "quantity")]

    def get_active_plans_total(self) -> Counter[Plan]:
        """Overall number of quantities per plan."""
        counter: Counter[Plan] = Counter()
        for plan, quantity in self.get_active_plans_and_quantities():
            counter[plan] += quantity
        return counter


@dataclass
class TransactionsReport:
    """
    Report for transactions. Period's end is excluded: [since, until)
    """

    provider_codename: str
    since: datetime
    until: datetime = field(default_factory=now)

    @classmethod
    def iter_periods(cls, frequency: int, since: datetime, until: datetime, **kwargs) -> Iterator[Self]:
        for start, end in _iter_periods(frequency, since, until):
            yield cls(since=start, until=end, **kwargs)

    @property
    def payments(self) -> QuerySet:
        return SubscriptionPayment.objects.filter(
            provider_codename=self.provider_codename,
            created__gte=self.since,
            created__lte=self.until,
        ).order_by("created")

    def get_payments_count_by_status(self) -> Counter[AbstractTransaction.Status]:
        """Payments' statuses and their respective counts."""
        return Counter(self.payments.values_list("status", flat=True))

    @property
    def completed_payments(self) -> QuerySet:
        return self.payments.filter(status=AbstractTransaction.Status.COMPLETED)

    def get_completed_payments_amounts(self) -> list[Money | None]:
        """List of amounts for completed payments."""
        return [
            Money(amount, amount_currency) * quantity if amount is not None else None
            for amount, amount_currency, quantity in self.completed_payments.values_list(
                "amount", "amount_currency", "quantity"
            )
        ]

    def get_completed_payments_average(self) -> Money | None:
        """Median amount for completed payments."""
        amounts = [amount for amount in self.get_completed_payments_amounts() if amount is not None]
        if amounts:
            return get_average(amounts)

    def get_completed_payments_total(self) -> Money:
        """Total amount for completed payments."""
        amounts = [amount for amount in self.get_completed_payments_amounts() if amount is not None]
        return sum(amounts, start=NO_MONEY)

    def get_incompleted_payments_amounts(self) -> list[Money | None]:
        """List of amounts for incompleted payments."""
        incompleted_payments = self.payments.exclude(status=AbstractTransaction.Status.COMPLETED)
        return [
            Money(amount, amount_currency) * quantity if amount is not None else None
            for amount, amount_currency, quantity in incompleted_payments.values_list(
                "amount", "amount_currency", "quantity"
            )
        ]

    def get_incompleted_payments_total(self) -> Money:
        """Total amount for incompleted payments."""
        amounts = [amount for amount in self.get_incompleted_payments_amounts() if amount is not None]
        return sum(amounts, start=NO_MONEY)

    @property
    def refunds(self) -> QuerySet:
        return SubscriptionPaymentRefund.objects.filter(
            created__gte=self.since,
            created__lte=self.until,
            status=SubscriptionPaymentRefund.Status.COMPLETED,
        ).order_by("created")

    def get_refunds_count(self) -> int:
        """Total number of refunds."""
        return self.refunds.count()

    def get_refunds_amounts(self) -> list[Money | None]:
        """List of refunds' amounts."""
        return [
            Money(amount, currency) if amount is not None else None
            for amount, currency in self.refunds.values_list("amount", "amount_currency")
        ]

    def get_refunds_average(self) -> Money | None:
        """Median amount for refunds."""
        amounts = [amount for amount in self.get_refunds_amounts() if amount is not None]
        if amounts:
            return get_average(amounts)

    def get_refunds_total(self) -> Money | None:
        """Total amount for refunds."""
        amounts = [amount for amount in self.get_refunds_amounts() if amount is not None]
        return sum(amounts, start=NO_MONEY)

    def get_estimated_recurring_charge_amounts_by_time(self) -> dict[datetime, Money]:
        """
        Estimated charge amount by datetime.

        This works even for past periods, so that one can compare difference
        between estimated and real charges.
        """
        subscriptions = Subscription.objects.overlap(self.since, self.until).recurring().select_related("plan")

        estimated_charges: dict[datetime, Money] = defaultdict(lambda: NO_MONEY)
        for subscription in subscriptions:
            if subscription.plan.charge_amount is None:
                continue

            amount = subscription.plan.charge_amount * subscription.quantity
            for charge_date in subscription.iter_charge_dates(self.since, self.until):
                estimated_charges[charge_date] += amount

        return estimated_charges

    def get_estimated_recurring_charge_total(self) -> Money:
        """Total estimated charge amount."""
        if amounts_by_time := self.get_estimated_recurring_charge_amounts_by_time():
            return sum(amounts_by_time.values(), start=NO_MONEY)

        return NO_MONEY
