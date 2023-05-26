from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from statistics import median
from typing import Dict, List, Optional, Tuple

from djmoney.money import Money
from django.db.models import Q, QuerySet
from django.db.models.aggregates import Count
from django.utils.timezone import now

from .models import AbstractTransaction, Plan, Subscription, SubscriptionPayment, \
    SubscriptionPaymentRefund


@dataclass
class SubscriptionsReport:

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
class TransactionsReport:

    provider_codename: str
    since: datetime
    until: datetime = field(default_factory=now)

    # TODO: some methods will explode when there are multiple currencies

    @property
    def payments(self) -> QuerySet:
        return (
            SubscriptionPayment.objects
            .overlap(self.since, self.until)
            .filter(provider_codename=self.provider_codename)
            .order_by('created')
        )

    def get_payments_count_by_status(self) -> Dict[AbstractTransaction.Status, int]:
        """ Payments' statuses and their respective counts."""
        queryset = self.payments.values('status').annotate(count=Count('status'))
        return {item['status']: item['count'] for item in queryset}

    @property
    def completed_payments(self) -> QuerySet:
        return self.payments.filter(status=AbstractTransaction.Status.COMPLETED)

    def get_completed_payments_amounts(self) -> List[Optional[Money]]:
        """ List of amounts for completed payments. """
        return [
            amount * quantity if amount is not None else None
            for amount, quantity in self.completed_payments.values_list('amount', 'quantity')
        ]

    def get_completed_payments_average(self) -> Money:
        """ Median amount for completed payments. """
        return median(
            amount
            for amount in self.get_completed_payments_amounts()
            if amount is not None
        )

    def get_completed_payments_total(self) -> Decimal:
        """ Total amount for completed payments. """
        return sum(
            amount
            for amount in self.get_completed_payments_amounts()
            if amount is not None
        )

    def get_incompleted_payments_amounts(self) -> List[Optional[Decimal]]:
        """ List of amounts for incompleted payments. """
        incompleted_payments = self.payments.exclude(status=AbstractTransaction.Status.COMPLETED)
        return [
            amount * quantity if amount is not None else None
            for amount, quantity in incompleted_payments.values_list('amount', 'quantity')
        ]

    def get_incompleted_payments_total(self) -> Decimal:
        """ Total amount for incompleted payments. """
        return sum(amount for amount in self.get_incompleted_payments_amounts() if amount is not None)

    @property
    def refunds(self) -> QuerySet:
        return SubscriptionPaymentRefund.objects.overlap(self.since, self.until).order_by('created')

    def get_refunds_count(self) -> int:
        """ Total number of refunds."""
        return self.refunds.count()

    def get_refunds_amounts(self) -> List[Optional[Money]]:
        """ List of refunds' amounts. """
        return list(self.refunds.values_list('amount', flat=True))

    def get_refunds_average(self) -> Money:
        """ Median amount for refunds. """
        return median(amount for amount in self.get_refunds_amounts() if amount is not None)

    def get_refunds_total(self) -> Money:
        """ Total amount for refunds. """
        return sum(amount for amount in self.get_refunds_amounts() if amount is not None)

    def get_estimated_charge_amounts_by_time(self) -> Dict[datetime, Money]:
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

        return {
            charge_date: subscription.plan.charge_amount * subscription.quantity
            for subscription in subscriptions
            for charge_date in subscription.iter_charge_dates(self.since, self.until)
            if subscription.plan.charge_amount is not None
        }

    def get_estimated_charge_total(self) -> Money:
        """ Total estimated charge amount. """
        return sum(self.get_estimated_charge_amounts_by_time().values())
