from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum, auto
from typing import Iterator, Optional

from django.conf import settings
from django.db import models
from django.db.models import Index, QuerySet, UniqueConstraint
from django.utils.timezone import now

from .fields import MoneyField

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

    def iter_charge_dates(self, from_: datetime) -> Iterator[datetime]:
        if self.charge_period == INFINITY:
            return

        i = 1
        while True:
            yield from_ + self.charge_period * i
            i += 1


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
        return f'{self.user} @ {self.plan}'

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


class Resource(models.Model):
    codename = models.CharField(max_length=255)
    units = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [
            UniqueConstraint(fields=['codename'], name='unique_resource'),
        ]

    def __str__(self) -> str:
        return self.codename


@dataclass
class QuotaEvent:
    class Type(Enum):
        RECHARGE = auto()
        BURN = auto()
        USAGE = auto()

    datetime: datetime
    resource: Resource
    type: Type
    value: int


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
        return f'{self.resource}: {self.limit:,}{self.resource.units}/{self.recharge_period}'

    def save(self, *args, **kwargs):
        self.recharge_period = self.recharge_period or self.plan.charge_period
        self.burns_in = self.burns_in or self.recharge_period
        return super().save(*args, **kwargs)

    @classmethod
    def iter_events(cls, user, since: Optional[datetime] = None, until: Optional[datetime] = None) -> Iterator[QuotaEvent]:
        active_subscriptions = Subscription.objects.active().filter(user=user)
        since = since or active_subscriptions.values_list('start').order_by('start').first()
        until = until or now()

        resources_with_quota = set()

        for subscription in active_subscriptions.select_related('plan__quotas'):
            for quota in subscription.plan.quotas.all():
                resources_with_quota.add(quota.resource)

                i = 0
                while True:
                    recharge_time = subscription.start + i * quota.recharge_period
                    if recharge_time > until:
                        break

                    if recharge_time >= since:
                        yield QuotaEvent(
                            datetime=recharge_time,
                            resource=quota.resource,
                            type=QuotaEvent.Type.RECHARGE,
                            value=quota.limit,
                        )

                    burn_time = recharge_time + quota.burns_in
                    if since <= burn_time <= until:
                        yield QuotaEvent(
                            datetime=burn_time,
                            resource=quota.resource,
                            type=QuotaEvent.Type.BURN,
                            value=-quota.limit,
                        )

                    i += 1

        for resource in resources_with_quota:
            for usage_time, amount in Usage.objects.filter(user=user, resource=resource, datetime__gte=since, datetime__lte=until).order_by('datetime').values_list('datetime', 'amount'):
                yield QuotaEvent(
                    datetime=usage_time,
                    resource=resource,
                    type=QuotaEvent.Type.USAGE,
                    value=-amount,  # type: ignore
                )


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
        return f'{self.amount:,}{self.resource.units} {self.resource}'

    def save(self, *args, **kwargs):
        self.datetime = self.datetime or now()
        return super().save(*args, **kwargs)
