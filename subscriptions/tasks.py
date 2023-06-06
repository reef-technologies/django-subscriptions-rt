from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timedelta
from functools import partial
from logging import getLogger
from operator import or_
from typing import Iterable, Optional

from django.conf import settings
from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils.timezone import now
from more_itertools import first, pairwise

from .defaults import DEFAULT_SUBSCRIPTIONS_OFFLINE_CHARGE_ATTEMPTS_SCHEDULE
from .exceptions import PaymentError, ProlongationImpossible
from .models import Subscription, SubscriptionPayment
from .providers import get_provider

log = getLogger(__name__)

DEFAULT_CHARGE_ATTEMPTS_SCHEDULE = getattr(
    settings,
    'SUBSCRIPTIONS_OFFLINE_CHARGE_ATTEMPTS_SCHEDULE',
    DEFAULT_SUBSCRIPTIONS_OFFLINE_CHARGE_ATTEMPTS_SCHEDULE,
)


@transaction.atomic
def _charge_recurring_subscription(
    subscription: Subscription,
    schedule: Iterable[timedelta],
    at: datetime,
    lock: bool = True,
):
    if lock:
        # here we lock specific subscription object, so that we don't try charging it twice
        # at the same time
        _ = Subscription.objects.filter(pk=subscription.pk).select_for_update(of=('self',))  # TODO: skip_locked=True?

    log.debug('Processing subscription %s', subscription)

    # expiration_date = next(subscription.iter_charge_dates(since=now_))
    # TODO: what if `subscription.end_date` expiration_date doesn't match `subscription.iter_charge_dates()`?
    expiration_date = subscription.end

    charge_dates = [expiration_date + delta for delta in schedule]
    charge_periods = pairwise(charge_dates)

    try:
        charge_period = first(period for period in charge_periods if period[0] <= at < period[1])
    except ValueError:
        log.warning('Current time %s doesn\'t fall within any charge period, skipping', at)
        return

    log.debug(
        'Current time %s falls within period %s (delta: %s)',
        at,
        [date.isoformat() for date in charge_period],
        expiration_date - at,
    )

    previous_payment_attempts = subscription.payments.filter(
        or_(
            # any attempt in this period
            Q(created__gte=charge_period[0], created__lt=charge_period[1]),
            # or any PENDING or COMPLETED attempt in previous periods
            Q(
                created__gte=charge_dates[0], created__lt=charge_period[0],
                status__in={
                    SubscriptionPayment.Status.PENDING,
                    SubscriptionPayment.Status.COMPLETED,
                }
            ),
        )
    )

    if previous_payment_attempts.exists():
        previous_payment_attempts = list(previous_payment_attempts)
        log.debug('Skipping this payment, because there already exists payment attempt within specified charge period: %s', previous_payment_attempts)

        if len(previous_payment_attempts) > 1:
            log.warning('Multiple payment attempts detected for period %s (should be at most 1 attempt): %s', charge_period, previous_payment_attempts)

        if (successful_attempts := [
            attempt for attempt in previous_payment_attempts
            if attempt.status == SubscriptionPayment.Status.COMPLETED
        ]):
            log.warning('Previous payment attempt was successful but subscription end is still approaching: %s', successful_attempts)

        return  # don't try to charge one more time in this period

    log.debug('Trying to prolong subscription %s', subscription)
    try:
        subscription.prolong()  # try extending end date of subscription
        log.debug('Prolongation of subscription is possible')
    except ProlongationImpossible as exc:
        # cannot prolong anymore, disable auto_prolong for this subscription
        log.debug('Prolongation of subscription is impossible: %s', exc)
        subscription.auto_prolong = False
        subscription.save()
        log.debug('Turned off auto-prolongation of subscription %s', subscription)
        # TODO: send email to user
        return

    try:
        log.debug('Offline-charging subscription %s', subscription)
        subscription.charge_offline()
    except PaymentError as exc:
        log.debug('Failed to offline-charge subscription %s: %s', subscription, exc)

        # here we create a failed SubscriptionPayment to indicate that we tried
        # to charge but something went wrong, so that subsequent task calls
        # won't try charging and sending email again within same charge_period
        SubscriptionPayment.objects.create(
            provider_codename='',
            status=SubscriptionPayment.Status.ERROR,
            plan=subscription.plan,
            subscription=subscription,
            quantity=subscription.quantity,
        )
        return

    log.debug('Offline charge successfully created for subscription %s', subscription)
    # even if offline subscription succeeds, we are not sure about its status,
    # so we don't prolong the subscription here but instead let setting
    # `subscription.status = COMPLETED` (by charge_offline or webhook or whatever)
    # to auto-prolong subscription itself


def charge_recurring_subscriptions(
    subscriptions: Optional[QuerySet] = None,
    schedule: Iterable[timedelta] = DEFAULT_CHARGE_ATTEMPTS_SCHEDULE,
    num_threads: Optional[int] = None,
    lock: bool = True,
    # TODO: dry-run
):
    # TODO: management command
    log.debug('Background charging according to schedule %s', schedule)
    schedule = sorted(schedule)
    if not schedule:
        return

    now_ = now()

    subscriptions = Subscription.objects.all() if subscriptions is None else subscriptions
    expiring_subscriptions = subscriptions\
    .filter(  # noqa
        auto_prolong=True,
    ).expiring(
        since=now_ - schedule[-1],
        within=schedule[-1] - schedule[0],
    ).select_related(
        'user', 'plan',
    )

    if not expiring_subscriptions.exists():
        log.debug('No subscriptions to charge')
        return

    charge = partial(
        _charge_recurring_subscription,
        schedule=schedule,
        at=now_,
        lock=lock,
    )

    if num_threads is not None and num_threads < 2:
        for subscription in expiring_subscriptions:
            try:
                charge(subscription)
            except Exception:
                log.exception('Failed to charge subscription %s', subscription)
    else:
        with ThreadPoolExecutor(max_workers=num_threads) as pool:
            wait(
                pool.submit(charge, subscription)
                for subscription in expiring_subscriptions
            )


def check_unfinished_payments(within: timedelta = timedelta(hours=12)):
    """
    Reverse-check payment status: if payment webhook didn't pass through
    for some reason, ask payment provider about payment status, and
    update SubscriptionPayment status if needed.
    """

    log.debug('Fetching status of unfinished payments')
    now_ = now()
    unfinished_payments = SubscriptionPayment.objects.filter(
        created__gte=now_ - within,
        status=SubscriptionPayment.Status.PENDING,
    )

    codenames = set(unfinished_payments.order_by('provider_codename').values_list('provider_codename', flat=True))

    for codename in codenames:
        get_provider(codename).check_payments(
            unfinished_payments.filter(provider_codename=codename)
        )


# TODO: check for concurrency issues, probably add transactions
