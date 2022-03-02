from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import count
from math import ceil
from operator import attrgetter
from typing import Iterable, Iterator, List, NamedTuple, Optional

from django.conf import settings
from django.db import models
from django.db.models import Index, QuerySet, UniqueConstraint
from django.utils.timezone import now

from .fields import MoneyField
from .utils import merge_iter

#
#  |--------subscription-------------------------------------------->
#  start             (subscription duration)                end or inf
#
#  |-----------------------------|---------------------------|------>
#  charge   (charge period)    charge                      charge
#
#  |------------------------------x
#  quota   (quota lifetime)       quota burned
#
#  (quota recharge period) |------------------------------x
#
#  (quota recharge period) (quota recharge period) |----------------->


INFINITY = timedelta(days=365 * 1000)


class Resource(models.Model):
    codename = models.CharField(max_length=255)
    units = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [
            UniqueConstraint(fields=['codename'], name='unique_resource'),
        ]

    def __str__(self) -> str:
        return self.codename


class Plan(models.Model):
    codename = models.CharField(max_length=255)
    name = models.CharField(max_length=255)
    charge_amount = MoneyField(blank=True, null=True)
    charge_period = models.DurationField(blank=True, help_text='leave blank for one-time charge')
    subscription_duration = models.DurationField(blank=True, help_text='leave blank to make it an infinite subscription')
    is_enabled = models.BooleanField(default=True)

    class Meta:
        constraints = [
            UniqueConstraint(fields=['codename'], name='unique_plan'),
        ]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        self.charge_period = self.charge_period or INFINITY
        self.subscription_duration = self.subscription_duration or INFINITY
        return super().save(*args, **kwargs)


@dataclass
class QuotaChunk:
    resource: Resource
    start: datetime
    end: datetime
    remains: int

    def __str__(self) -> str:
        return f'{self.remains} {self.resource} {self.start} - {self.end}'

    def includes(self, date: datetime) -> bool:
        return self.start <= date < self.end

    def same_lifetime(self, other: 'QuotaChunk') -> bool:
        return self.start == other.start and self.end == other.end


class QuotaCache(NamedTuple):
    datetime: datetime
    chunks: List[QuotaChunk]


class SubscriptionManager(models.Manager):
    def active(self, as_of: Optional[datetime] = None):
        now_ = as_of or now()
        return self.filter(start__lte=now_, end__gte=now_)


class Subscription(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='subscriptions')
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name='subscriptions')
    # amount = MoneyField()  # should match plan.charge_amount
    start = models.DateTimeField(blank=True)
    end = models.DateTimeField(blank=True)

    objects = SubscriptionManager()

    def __str__(self) -> str:
        return f'{self.user} @ {self.plan}, {self.start} - {self.end}'

    def save(self, *args, **kwargs):
        self.start = self.start or now()
        self.end = self.end or (self.start + self.plan.subscription_duration)
        return super().save(*args, **kwargs)

    def stop(self):
        self.end = now()
        self.save(update_fields=['end'])

    @classmethod
    def get_expiring(cls, within: timedelta) -> QuerySet:
        return cls.objects.active().filter(end__lte=now() + within)

    def iter_quota_chunks(self, since: Optional[datetime] = None, until: Optional[datetime] = None, resource: Optional[Resource] = None, sort_by: callable = attrgetter('start')) -> Iterator[QuotaChunk]:
        quotas = self.plan.quotas.all()
        if resource:
            quotas = quotas.filter(resource=resource)

        yield from merge_iter(*(self._iter_single_quota_chunks(quota=quota, since=since, until=until) for quota in quotas), key=sort_by)

    def _iter_single_quota_chunks(self, quota: 'Quota', since: Optional[datetime] = None, until: Optional[datetime] = None):
        min_start_time = max(since - quota.burns_in, self.start) if since else self.start  # quota chunks starting after this are OK
        until = min(until, self.end) if until else self.end

        count_start = ceil((min_start_time - self.start) / quota.recharge_period)  # index of first quota chunk starting after min_start_time
        for i in count(start=count_start):
            start = self.start + i * quota.recharge_period
            if start > until:
                return

            yield QuotaChunk(
                resource=quota.resource,
                start=start,
                end=min(start + quota.burns_in, self.end),
                remains=quota.limit,
            )

    @classmethod
    def iter_subscriptions_quota_chunks(cls, subscriptions: Iterable['Subscription'], since: datetime, until: datetime, resource: Resource, sort_by: callable = attrgetter('start')) -> Iterator[QuotaChunk]:
        return merge_iter(
            *(
                subscription.iter_quota_chunks(
                    since=since,
                    until=until,
                    resource=resource,
                    sort_by=sort_by,
                )
                for subscription in subscriptions
            ),
            key=sort_by,
        )

    def iter_charge_dates(self, since: Optional[datetime] = None) -> Iterator[datetime]:
        """ Including first charge (i.e. charge to create subscription) """
        charge_period = self.plan.charge_period

        since = since or self.start
        start_index = ceil((max(since, self.start) - self.start) / charge_period)

        for i in count(start=start_index):
            charge_date = self.start + charge_period * i
            if charge_date >= self.end:
                return

            yield charge_date
            if charge_period == INFINITY:
                break


class Quota(models.Model):
    plan = models.ForeignKey(Plan, on_delete=models.CASCADE, related_name='quotas')
    resource = models.ForeignKey(Resource, on_delete=models.CASCADE, related_name='quotas')
    limit = models.PositiveIntegerField()
    recharge_period = models.DurationField(blank=True, help_text='leave blank for recharging only after each subscription prolongation (charge)')
    burns_in = models.DurationField(blank=True, help_text='leave blank to burn each recharge period')

    class Meta:
        constraints = [
            UniqueConstraint(fields=['plan', 'resource'], name='unique_quota'),
        ]

    def __str__(self) -> str:
        return f'{self.resource} {self.limit:,}{self.resource.units}/{self.recharge_period}, burns in {self.burns_in}'

    def save(self, *args, **kwargs):
        self.recharge_period = self.recharge_period or self.plan.charge_period
        self.burns_in = self.burns_in or self.recharge_period
        return super().save(*args, **kwargs)


class Usage(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='usages')
    resource = models.ForeignKey(Resource, on_delete=models.PROTECT, related_name='usages')
    amount = models.PositiveIntegerField(default=1)
    datetime = models.DateTimeField(blank=True)

    class Meta:
        indexes = [
            Index(fields=['user', 'resource']),
        ]

    def __str__(self) -> str:
        return f'{self.amount:,}{self.resource.units} {self.resource} at {self.datetime}'

    def save(self, *args, **kwargs):
        self.datetime = self.datetime or now()
        return super().save(*args, **kwargs)
