from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timedelta
from functools import partial
from logging import getLogger
from typing import Iterable, Iterator
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils.timezone import now
from more_itertools import first, pairwise

from .defaults import (
    DEFAULT_NOTIFY_PENDING_PAYMENTS_AFTER,
    DEFAULT_SUBSCRIPTIONS_OFFLINE_CHARGE_ATTEMPTS_SCHEDULE,
)
from .exceptions import DryRunRollback, PaymentError, ProlongationImpossible
from .models import Subscription, SubscriptionPayment
from .providers import get_provider
from .utils import suppress

log = getLogger(__name__)

DEFAULT_CHARGE_ATTEMPTS_SCHEDULE = getattr(
    settings,
    'SUBSCRIPTIONS_OFFLINE_CHARGE_ATTEMPTS_SCHEDULE',
    DEFAULT_SUBSCRIPTIONS_OFFLINE_CHARGE_ATTEMPTS_SCHEDULE,
)


@suppress(DryRunRollback)
@transaction.atomic
def _charge_recurring_subscription(
    subscription_uid: UUID,
    schedule: Iterable[timedelta],
    at: datetime,
    lock: bool = True,
    dry_run: bool = False,
):
    query = Subscription.objects.filter(uid=subscription_uid)
    if lock:
        # here we lock specific subscription object, so that we don't try charging it twice
        # at the same time; also we always get latest subscription object from DB
        query = query.select_for_update(of=('self',))  # TODO: skip_locked=True?

    subscription = query.first()
    log.debug('Processing subscription %s', subscription)

    first_charge_date = next(subscription.iter_charge_dates())
    if at < first_charge_date:
        log.debug('Current time %s is before first charge date %s, skipping', at, first_charge_date)
        return

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

    # we don't want to try charging if
    # 1) there is already ANY charge attempt (successful or not) in this charge period
    # (so if there was ERROR charge in this period, we will try again only in next period)
    # 2) there is already any PENDING charge attempt; all charge attempts should end up
    # being in COMPLETED/ERROR/ABANDONED etc state, and PENDING payments will be garbage-collected
    # by a separate task
    previous_payment_attempts = subscription.payments.filter(
        Q(created__gte=charge_period[0], created__lt=charge_period[1]) |  # any attempt in this period
        Q(status=SubscriptionPayment.Status.PENDING)  # any pending attempt
    )
    if previous_payment_attempts.exists():
        previous_payment_attempts = list(previous_payment_attempts)
        log.debug('Skipping this payment, because of already existing payment attempt(s): %s', previous_payment_attempts)

        if len(previous_payment_attempts) > 1:
            log.warning('Multiple payment attempts detected (should be at most 1 attempt): %s', previous_payment_attempts)

        if (successful_attempts := [
            attempt for attempt in previous_payment_attempts
            if attempt.status == SubscriptionPayment.Status.COMPLETED
        ]):
            log.warning('Previous payment attempt was successful but subscription end is still approaching: %s', successful_attempts)

        return

    # TODO: remove this after fixing
    attempts_within_charge_schedule = subscription.payments.filter(
        created__gte=charge_dates[0],  # ~1 day before subscription ends
        created__lt=charge_dates[-1],  # ~29 days after subscription ends
    )
    if attempts_within_charge_schedule.exists():
        log.warning('Found already existing payment attempts within charge schedule, switching to dry run: %s', attempts_within_charge_schedule)
        dry_run = True

    log.debug('Trying to prolong subscription %s', subscription)
    try:
        _ = subscription.prolong()  # try extending end date of subscription
        log.debug('Prolongation of subscription is possible')
    except ProlongationImpossible as exc:
        # cannot prolong anymore, disable auto_prolong for this subscription
        log.debug('Prolongation of subscription is impossible: %s', exc)
        subscription.auto_prolong = False
        subscription.save(update_fields=['auto_prolong'])
        log.debug('Turned off auto-prolongation of subscription %s', subscription)
        # TODO: send email to user
        if dry_run:
            raise DryRunRollback()
        return

    try:
        log.debug('Offline-charging subscription %s', subscription)
        payment = subscription.charge_offline(_dry_run=dry_run)
        log.debug('Created successful payment: %s', payment)
    except PaymentError as exc:
        log.warning('Failed to offline-charge subscription', extra=exc.debug_info)

        # here we create a failed SubscriptionPayment to indicate that we tried
        # to charge but something went wrong, so that subsequent task calls
        # won't try charging and sending email again within same charge_period
        payment = SubscriptionPayment.objects.create(
            provider_codename='',  # TODO: FIX THIS
            user=subscription.user,
            status=SubscriptionPayment.Status.ERROR,
            plan=subscription.plan,
            subscription=subscription,
            quantity=subscription.quantity,
            metadata=exc.debug_info,
        )
        log.debug('Created failed payment: %s', payment)

    log.debug('Offline charge attempted for subscription %s', subscription)
    # even if offline subscription succeeds, we are not sure about its status,
    # so we don't prolong the subscription here but instead let setting
    # `subscription.status = COMPLETED` (by charge_offline or webhook or whatever)
    # to auto-prolong subscription itself

    # TODO: remove this after fix
    subscription = Subscription.objects.get(pk=subscription.pk)
    log.debug('Real subscription value after successful offline charge: %s', subscription)

    if dry_run:
        raise DryRunRollback()


def notify_stuck_pending_payments(older_than: timedelta = DEFAULT_NOTIFY_PENDING_PAYMENTS_AFTER):
    stuck_payments = SubscriptionPayment.objects.filter(
        created__lte=now() - older_than,
        status=SubscriptionPayment.Status.PENDING,
        subscription__isnull=False,  # ignore initial payments (abandoned carts)
    )
    for payment in stuck_payments:
        log.error('Payment stuck in pending state: %s', payment)


def charge_recurring_subscriptions(
    subscriptions: QuerySet | None = None,
    schedule: Iterable[timedelta] = DEFAULT_CHARGE_ATTEMPTS_SCHEDULE,
    num_threads: int | None = 0,
    lock: bool = True,
    dry_run: bool = False,
):
    # TODO: management command
    log.debug('Background charging according to schedule %s', schedule)
    schedule = sorted(schedule)
    if not schedule:
        return

    now_ = now()

    subscriptions = subscriptions or Subscription.objects.all()
    expiring_subscriptions_uids = list(
        subscriptions
        .filter(
            auto_prolong=True,
        ).expiring(
            since=now_ - schedule[-1],
            within=schedule[-1] - schedule[0],
        ).values_list('uid', flat=True)
    )

    if not expiring_subscriptions_uids:
        log.debug('No subscriptions to charge')
        return

    charge = partial(
        _charge_recurring_subscription,
        schedule=schedule,
        at=now_,
        lock=lock,
        dry_run=dry_run,
    )

    if num_threads == 0:
        for subscription_uid in expiring_subscriptions_uids:
            try:
                charge(subscription_uid)
            except Exception:
                log.exception('Failed to charge subscription %s', subscription_uid)
    else:
        with ThreadPoolExecutor(max_workers=num_threads) as pool:
            wait(
                pool.submit(charge, subscription_uid)
                for subscription_uid in expiring_subscriptions_uids
            )

    # TODO: remove this after fix
    if not dry_run:
        for subscription_uid in expiring_subscriptions_uids:
            subscription = Subscription.objects.get(uid=subscription_uid)
            log.debug('Real subscription value after all tasks excution: %s', subscription)


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


def check_duplicated_payments() -> dict[tuple[str, str], list[SubscriptionPayment]]:
    # This is rather massive as it's checking all operations.
    all_entries = SubscriptionPayment.objects.prefetch_related('subscription').all()

    transaction_id_to_entries: defaultdict[tuple[str, str], list[SubscriptionPayment]] = defaultdict(list)
    for entry in all_entries:
        # This happens for e.g.: unconfirmed paddle. We don't worry about these.
        if entry.provider_transaction_id is None:
            continue
        key = (entry.provider_codename, entry.provider_transaction_id)
        transaction_id_to_entries[key].append(entry)

    result = {}
    for (provider_codename, transaction_id), transaction_id_entries in transaction_id_to_entries.items():
        # Single entry – no issue.
        if len(transaction_id_entries) == 1:
            continue

        log.info('Found transaction ID: %s provider: %s with %s duplicates.',
                 transaction_id, provider_codename, len(transaction_id_entries))

        for idx, entry in enumerate(transaction_id_entries):
            log.info('\t%s: Subscription UID: %s, payment UID: %s',
                     (idx + 1), entry.subscription.uid, entry.uid)

        result[(provider_codename, transaction_id)] = transaction_id_entries

    return result


def check_not_extended_subscriptions(
    auto_fix: bool = True,
    tolerance: timedelta = timedelta(seconds=1),  # sometimes difference is microseconds
) -> Iterator[SubscriptionPayment]:
    # this task goes through all subscriptions which have completed payments
    # but were not extended properly, and yields it; if auto_fix is enabled,
    # the payments are re-saved - this extends subscription correctly based on the payment

    def is_outstanding(payment) -> bool:
        return (
            payment.status == payment.Status.COMPLETED
            and payment.subscription_end - payment.subscription.end > tolerance
        )

    for subscription in Subscription.objects.order_by('start').prefetch_related('payments'):
        for payment in subscription.payments.all():
            if (
                payment.amount
                and payment.amount.amount != 0
                and is_outstanding(payment)
            ):
                yield payment
                if auto_fix:
                    with transaction.atomic():
                        # fetch latest subscription and payment data from DB
                        payment.subscription = Subscription.objects.select_for_update().get(pk=subscription.pk)
                        payment.refresh_from_db()
                        payment.save()
                break
