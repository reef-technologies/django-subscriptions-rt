from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import count, zip_longest
from math import ceil
from operator import attrgetter
from typing import Iterable, Iterator, List, Optional

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.db import models
from django.db.models import Index, QuerySet, UniqueConstraint
from django.urls import reverse
from django.utils.timezone import now

from payments.exceptions import InconsistentQuotaCache, QuotaLimitExceeded

from .fields import MoneyField, RelativeDurationField
from .signals import subscription_expires_soon
from .utils import merge_iter

INFINITY = relativedelta(days=365 * 1000)


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
    slug = models.SlugField()
    charge_amount = MoneyField(blank=True, null=True)
    charge_period = RelativeDurationField(blank=True, help_text='leave blank for one-time charge')
    subscription_duration = RelativeDurationField(blank=True, help_text='DD HH:MM:SS; leave blank to make it an infinite subscription')
    is_enabled = models.BooleanField(default=True)

    class Meta:
        constraints = [
            UniqueConstraint(fields=['codename'], name='unique_plan'),
        ]

    def __str__(self) -> str:
        return self.name

    def get_absolute_url(self) -> str:
        return reverse('plan', kwargs={'plan_slug': self.slug})

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


@dataclass
class QuotaCache:
    datetime: datetime
    chunks: List[QuotaChunk]

    def apply(self, chunks: Iterable[QuotaChunk]) -> Iterator[QuotaChunk]:
        cached_chunks = iter(self.chunks)

        # match chunks and cached_chunks one-by-one
        check_cached_pair = True
        for i, (chunk, cached_chunk) in enumerate(zip_longest(chunks, cached_chunks, fillvalue=None)):
            if not chunk and cached_chunk:
                raise InconsistentQuotaCache(f'Non-paired cached chunk detected at position {i}: {cached_chunk}')

            elif chunk and cached_chunk:
                if not chunk.same_lifetime(cached_chunk):
                    raise InconsistentQuotaCache(f'Non-matched cached chunk detected at position {i}: {chunk=}, {cached_chunk=}')

                yield cached_chunk

            elif chunk and not cached_chunk:
                if check_cached_pair:
                    if chunk.includes(self.datetime):
                        raise InconsistentQuotaCache(f'No cached chunk for {chunk}')
                    check_cached_pair = False

                yield chunk


class SubscriptionQuerySet(models.QuerySet):
    def active(self, as_of: Optional[datetime] = None) -> QuerySet:
        now_ = as_of or now()
        return self.filter(start__lte=now_, end__gte=now_)

    def expiring(self, in_: datetime, from_: Optional[datetime] = None) -> QuerySet:
        from_ = from_ or now()
        return self.filter(start__lte=from_, end__gte=from_ + in_)


class Subscription(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='subscriptions')
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name='subscriptions')
    # amount = MoneyField()  # should match plan.charge_amount
    start = models.DateTimeField(blank=True)
    end = models.DateTimeField(blank=True)

    objects = SubscriptionQuerySet.as_manager()

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

    def iter_quota_chunks(
        self,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        sort_by: callable = attrgetter('start'),
    ) -> Iterator[QuotaChunk]:

        quotas = self.plan.quotas.all()
        yield from merge_iter(
            *(self._iter_single_quota_chunks(quota=quota, since=since, until=until) for quota in quotas),
            key=sort_by,
        )

    def _iter_single_quota_chunks(self, quota: 'Quota', since: Optional[datetime] = None, until: Optional[datetime] = None):

        epsilon = timedelta(milliseconds=1)  # we use epsilon to exclude chunks which start right at `since - quota.burns_in`
        min_start_time = max(since - quota.burns_in + epsilon, self.start) if since else self.start  # quota chunks starting after this are OK
        until = min(until, self.end) if until else self.end

        for i in count(start=0):
            start = self.start + i * quota.recharge_period
            if start < min_start_time:
                continue

            if start > until:
                return

            yield QuotaChunk(
                resource=quota.resource,
                start=start,
                end=min(start + quota.burns_in, self.end),
                remains=quota.limit,
            )

    def iter_charge_dates(self, since: Optional[datetime] = None) -> Iterator[datetime]:
        """ Including first charge (i.e. charge to create subscription) """
        charge_period = self.plan.charge_period
        since = since or self.start

        for i in count(start=0):
            charge_date = self.start + charge_period * i

            if charge_date < since:
                continue

            if charge_date >= self.end:
                return

            yield charge_date
            if charge_period == INFINITY:
                break

    @classmethod
    def send_expiring_signal(cls, expire_in: timedelta):
        subscription_expires_soon.send(sender=cls, subscriptions=cls.objects.expiring(in_=expire_in))


class Quota(models.Model):
    plan = models.ForeignKey(Plan, on_delete=models.CASCADE, related_name='quotas')
    resource = models.ForeignKey(Resource, on_delete=models.CASCADE, related_name='quotas')
    limit = models.PositiveIntegerField()
    recharge_period = RelativeDurationField(blank=True, help_text='leave blank for recharging only after each subscription prolongation (charge)')
    burns_in = RelativeDurationField(blank=True, help_text='leave blank to burn each recharge period')

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
        from .functions import get_remaining_amount

        self.datetime = self.datetime or now()

        remains = get_remaining_amount(user=self.user, at=self.datetime).get(self.resource, 0)
        if remains < self.amount:
            raise QuotaLimitExceeded(f'Tried to use {self.amount} {self.resource}(s) while only {remains} is allowed')

        return super().save(*args, **kwargs)
