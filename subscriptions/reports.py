from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from statistics import median
from typing import Dict, Iterator, List, Optional, Tuple

from djmoney.money import Money
from django.db.models import Q, QuerySet
from django.utils.timezone import now
from django.conf import settings

from dateutil.rrule import rrule
from more_itertools import pairwise
from dateutil.rrule import YEARLY, MONTHLY, WEEKLY, DAILY, HOURLY, MINUTELY, SECONDLY  # noqa

from .models import AbstractTransaction, Plan, Subscription, SubscriptionPayment, \
    SubscriptionPaymentRefund
from .defaults import DEFAULT_SUBSCRIPTIONS_CURRENCY


default_currency = getattr(settings, 'SUBSCRIPTIONS_DEFAULT_CURRENCY', DEFAULT_SUBSCRIPTIONS_CURRENCY)
NO_MONEY = Money(0, default_currency)


class IterPeriodsMixin:

    @classmethod
    def iter_periods(cls, frequency: int, since: datetime, until: datetime, **kwargs) -> Iterator:
        """
        Generate report instances for [since, until) period with desired frequency.

        For frequency, use `subscriptions.reports.[YEARLY|MONTHLY|WEEKLY|DAILY|HOURLY|MINUTELY|SECONDLY]`.
        """
        points_in_time = rrule(frequency, dtstart=since, until=until)
        end = since
        for start, end in pairwise(points_in_time):
            yield cls(since=start, until=end, **kwargs)

        if end != until:  # remains if since-until period doesn't match frequency perfectly
            yield cls(since=end, until=until, **kwargs)


@dataclass
class SubscriptionsReport(IterPeriodsMixin):
    """
    Report for subscriptions. Period's end is excluded: [since, until)
    """

    since: datetime
    until: datetime = field(default_factory=now)

    @property
    def overlapping(self) -> QuerySet:
        return Subscription.objects.overlap(self.since, self.until)

    @property
    def new(self) -> QuerySet:
        return self.overlapping.new(self.since, self.until).order_by('start')

    def get_new_count(self) -> int:
        """ Number of newly created subscriptions within selected period. """
        return self.new.count()

    def get_new_datetimes(self) -> List[datetime]:
        """ List of newly created subscriptions' dates within selected period. """
        return list(self.new.values_list('start', flat=True))

    @property
    def ended_or_ending(self) -> QuerySet:
        """ Subscriptions that end (or gonna end) within selected period. """
        return (
            self.overlapping
            .filter(end__gte=self.since, end__lte=self.until)
            .ended_or_ending()
            .order_by('end')
        )

    def get_ended_count(self) -> int:
        """ Number of subscriptions ending within selected period. """
        return self.ended_or_ending.count()

    def get_ended_datetimes(self) -> List[datetime]:
        """ List of end dates for subscriptions that end within selected period. """
        return list(self.ended_or_ending.values_list('end', flat=True))

    def get_ended_or_ending_ages(self) -> List[timedelta]:
        """ List of ages for ended or ending subscriptions."""
        return self.ended_or_ending.with_ages(at=self.until).values_list('age', flat=True)

    @property
    def active(self) -> QuerySet:
        """ Subscriptions that remain active within selected period. """
        now_ = now()
        return self.overlapping.exclude(
            Q(end__lte=now_) | Q(end__gt=now_, auto_prolong=False),
            end__gte=self.since,
            end__lte=self.until,
        ).order_by('start')

    def get_active_count(self) -> int:
        """ Number of subscriptions that remain active within selected period. """
        return self.active.count()

    def get_active_users_count(self) -> int:
        """ Number of users that have active subscriptions within selected period. """
        return self.active.order_by('user').distinct('user').count()

    def get_active_ages(self) -> List[timedelta]:
        """ List of ages for active subscriptions. """
        return self.active.with_ages(at=self.until).values_list('age', flat=True)

    def get_active_plans_and_quantities(self) -> List[Tuple[Plan, int]]:
        """ List of plan & quantity tuples per subscription. """
        id_to_plan = {plan.id: plan for plan in Plan.objects.all()}
        return [
            (id_to_plan[plan_id], quantity)
            for plan_id, quantity in self.active.values_list('plan', 'quantity')
        ]

    def get_active_plans_total(self) -> Counter[Plan]:
        """ Overall number of quantities per plan. """
        counter = Counter()
        for plan, quantity in self.get_active_plans_and_quantities():
            counter[plan] += quantity
        return counter


@dataclass
class TransactionsReport(IterPeriodsMixin):
    """
    Report for transactions. Period's end is excluded: [since, until)
    """

    provider_codename: str
    since: datetime
    until: datetime = field(default_factory=now)

    # TODO: some methods will explode when there are multiple currencies

    @property
    def payments(self) -> QuerySet:
        return (
            SubscriptionPayment.objects
            .filter(
                provider_codename=self.provider_codename,
                created__gte=self.since,
                created__lte=self.until,
            )
            .order_by('created')
        )

    def get_payments_count_by_status(self) -> Counter[AbstractTransaction.Status]:
        """ Payments' statuses and their respective counts."""
        return Counter(self.payments.values_list('status', flat=True))

    @property
    def completed_payments(self) -> QuerySet:
        return self.payments.filter(status=AbstractTransaction.Status.COMPLETED)

    def get_completed_payments_amounts(self) -> List[Optional[Money]]:
        """ List of amounts for completed payments. """
        return [
            Money(amount, amount_currency) * quantity if amount is not None else None
            for amount, amount_currency, quantity
            in self.completed_payments.values_list('amount', 'amount_currency', 'quantity')
        ]

    def get_completed_payments_average(self) -> Optional[Money]:
        """ Median amount for completed payments. """
        amounts = [amount for amount in self.get_completed_payments_amounts() if amount is not None]
        if amounts:
            return median(amounts)

    def get_completed_payments_total(self) -> Money:
        """ Total amount for completed payments. """
        amounts = [amount for amount in self.get_completed_payments_amounts() if amount is not None]
        return sum(amounts) if amounts else NO_MONEY

    def get_incompleted_payments_amounts(self) -> List[Optional[Decimal]]:
        """ List of amounts for incompleted payments. """
        incompleted_payments = self.payments.exclude(status=AbstractTransaction.Status.COMPLETED)
        return [
            Money(amount, amount_currency) * quantity if amount is not None else None
            for amount, amount_currency, quantity
            in incompleted_payments.values_list('amount', 'amount_currency', 'quantity')
        ]

    def get_incompleted_payments_total(self) -> Money:
        """ Total amount for incompleted payments. """
        amounts = [amount for amount in self.get_incompleted_payments_amounts() if amount is not None]
        return sum(amounts) if amounts else NO_MONEY

    @property
    def refunds(self) -> QuerySet:
        return (
            SubscriptionPaymentRefund.objects
            .filter(
                created__gte=self.since,
                created__lte=self.until,
                status=SubscriptionPaymentRefund.Status.COMPLETED,
            )
            .order_by('created')
        )

    def get_refunds_count(self) -> int:
        """ Total number of refunds."""
        return self.refunds.count()

    def get_refunds_amounts(self) -> List[Optional[Money]]:
        """ List of refunds' amounts. """
        return [
            Money(amount, currency) if amount is not None else None
            for amount, currency in self.refunds.values_list('amount', 'amount_currency')
        ]

    def get_refunds_average(self) -> Optional[Money]:
        """ Median amount for refunds. """
        amounts = [amount for amount in self.get_refunds_amounts() if amount is not None]
        if amounts:
            return median(amounts)

    def get_refunds_total(self) -> Optional[Money]:
        """ Total amount for refunds. """
        amounts = [amount for amount in self.get_refunds_amounts() if amount is not None]
        return sum(amounts) if amounts else NO_MONEY

    def get_estimated_recurring_charge_amounts_by_time(self) -> Dict[datetime, Money]:
        """
        Estimated charge amount by datetime.

        This works even for past periods, so that one can compare difference
        between estimated and real charges.
        """
        subscriptions = (
            Subscription.objects
            .overlap(self.since, self.until)
            .recurring()
            .select_related('plan')
        )

        estimated_charges = defaultdict(int)
        for subscription in subscriptions:
            if subscription.plan.charge_amount is None:
                continue

            amount = subscription.plan.charge_amount * subscription.quantity
            for charge_date in subscription.iter_charge_dates(self.since, self.until):
                estimated_charges[charge_date] += amount

        return estimated_charges

    def get_estimated_recurring_charge_total(self) -> Money:
        """ Total estimated charge amount. """
        if (amounts_by_time := self.get_estimated_recurring_charge_amounts_by_time()):
            return sum(amounts_by_time.values())

        return NO_MONEY
