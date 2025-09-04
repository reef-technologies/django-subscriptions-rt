from collections import defaultdict
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timedelta
from functools import partial
from logging import getLogger
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils.timezone import now
from more_itertools import first, pairwise

from .defaults import (
    DEFAULT_NOTIFY_PENDING_PAYMENTS_AFTER,
    DEFAULT_SUBSCRIPTIONS_CHARGE_ATTEMPTS_SCHEDULE,
)
from .exceptions import PaymentError, ProlongationImpossible
from .models import Subscription, SubscriptionPayment, SubscriptionQuerySet
from .providers import get_provider_by_codename

log = getLogger(__name__)

DEFAULT_CHARGE_ATTEMPTS_SCHEDULE = getattr(
    settings,
    "SUBSCRIPTIONS_CHARGE_ATTEMPTS_SCHEDULE",
    DEFAULT_SUBSCRIPTIONS_CHARGE_ATTEMPTS_SCHEDULE,
)


@transaction.atomic
def charge_recurring_subscription(
    subscription_uid: UUID,
    schedule: Iterable[timedelta],
    at: datetime,
    dry_run: bool,
) -> None:
    # here we lock specific subscription object, so that we don't try charging it twice
    # at the same time
    subscription = Subscription.objects.filter(pk=subscription_uid).select_for_update(of=("self",)).get()
    log.debug("Processing subscription %s", subscription)

    charge_dates = [subscription.end + delta for delta in schedule]
    charge_periods = pairwise(charge_dates)

    try:
        charge_period = first(period for period in charge_periods if period[0] <= at < period[1])
        log.debug(
            "Current time %s falls within period %s (delta: %s)",
            at,
            [date.isoformat() for date in charge_period],
            subscription.end - at,
        )
    except ValueError:
        log.warning("Current time %s doesn't fall within any charge period, skipping", at)
        return

    if at < subscription.start + subscription.charge_offset:
        log.debug("Subscription %s is still in charge offset period, skipping", subscription)
        return

    # we don't want to try charging if
    # 1) there is already ANY charge attempt (successful or not) in this charge period
    # (so if there was ERROR charge in this period, we will try again only in next period)
    # 2) there is already any PENDING charge attempt in any charge period
    previous_payment_attempts = list(
        subscription.payments.filter(
            Q(created__gte=charge_period[0], created__lt=charge_period[1])  # any attempt in this period
            | Q(
                created__gte=charge_dates[0], created__lt=charge_dates[-1], status=SubscriptionPayment.Status.PENDING
            )  # any pending attempt within charge window
        )
    )
    if previous_payment_attempts:
        log.debug(
            "Skipping this payment, because of already existing payment attempt(s): %s", previous_payment_attempts
        )

        if len(previous_payment_attempts) > 1:
            log.warning(
                "Multiple payment attempts detected (should be at most 1 attempt): %s", previous_payment_attempts
            )

        if successful_attempts := [
            attempt for attempt in previous_payment_attempts if attempt.status == SubscriptionPayment.Status.COMPLETED
        ]:
            log.warning(
                "Previous payment attempt was successful but subscription end is still approaching: %s",
                successful_attempts,
            )

        return

    try:
        log.debug("Trying to prolong subscription %s", subscription)
        payment = subscription.charge_automatically()
        log.debug("Subscription %s automatically charged: %s", subscription, payment)
        # even if background subscription succeeds, we are not sure about its status,
        # so we don't prolong the subscription here but instead let setting
        # `payment.status = COMPLETED` (by charge_automatically or webhook or whatever)
        # to auto-prolong subscription itself
    except ProlongationImpossible:
        subscription.auto_prolong = False
        subscription.save()
        log.debug("Turned off auto-prolongation of subscription %s", subscription)
        return
    except PaymentError as exc:
        log.warning("Failed to background-charge subscription", extra=exc.debug_info)

        # here we create a failed SubscriptionPayment to indicate that we tried
        # to charge but something went wrong, so that subsequent task calls
        # won't try charging again within same charge_period
        SubscriptionPayment.objects.create(
            provider_codename="auto",
            user=subscription.user,
            status=SubscriptionPayment.Status.ERROR,
            plan=subscription.plan,
            subscription=subscription,
            quantity=subscription.quantity,
            paid_since=subscription.end,
            paid_until=subscription.prolong(),
            metadata=exc.debug_info,
        )

    if dry_run:
        transaction.set_rollback(True)


def notify_stuck_pending_payments(older_than: timedelta = DEFAULT_NOTIFY_PENDING_PAYMENTS_AFTER):
    stuck_payments = SubscriptionPayment.objects.filter(
        created__lte=now() - older_than,
        status=SubscriptionPayment.Status.PENDING,
        subscription__isnull=False,  # ignore initial payments (abandoned carts)
    )
    for payment in stuck_payments:
        log.error("Payment stuck in pending state: %s", payment)


def charge_recurring_subscriptions(
    subscriptions: SubscriptionQuerySet | None = None,
    schedule: Iterable[timedelta] = DEFAULT_CHARGE_ATTEMPTS_SCHEDULE,
    num_threads: int | None = None,
    dry_run: bool = False,
):
    log.debug("Background charging according to schedule %s", schedule)
    schedule = sorted(schedule)
    if not schedule:
        return

    # charging all the subscriptions may take time, so we freeze the time at which we initiated charging process
    now_ = now()

    subscriptions = Subscription.objects.all() if subscriptions is None else subscriptions
    expiring_subscriptions = subscriptions.filter(
        auto_prolong=True,
    ).expiring(
        since=now_ - schedule[-1],
        within=schedule[-1] - schedule[0],
    )

    if not expiring_subscriptions.exists():
        log.debug("No subscriptions to charge")
        return

    charge = partial(charge_recurring_subscription, schedule=schedule, at=now_, dry_run=dry_run)

    if num_threads is not None and num_threads < 2:
        for subscription in expiring_subscriptions.values_list("uid", flat=True):
            try:
                charge(subscription)
            except Exception:
                log.exception("Failed to charge subscription %s", subscription)
    else:
        with ThreadPoolExecutor(max_workers=num_threads) as pool:
            wait(pool.submit(charge, subscription) for subscription in expiring_subscriptions)


def check_unfinished_payments(within: timedelta = timedelta(hours=12)):
    """
    Reverse-check payment status: if payment webhook didn't pass through
    for some reason, ask payment provider about payment status, and
    update SubscriptionPayment status if needed.
    """

    log.debug("Fetching status of unfinished payments")
    now_ = now()
    unfinished_payments = SubscriptionPayment.objects.filter(
        created__gte=now_ - within,
        status=SubscriptionPayment.Status.PENDING,
    )

    codenames = set(unfinished_payments.order_by("provider_codename").values_list("provider_codename", flat=True))

    for codename in codenames:
        get_provider_by_codename(codename).check_payments(unfinished_payments.filter(provider_codename=codename))


def check_duplicated_payments() -> dict[tuple[str, str], list[SubscriptionPayment]]:
    # This is rather massive as it's checking all operations.
    all_entries = SubscriptionPayment.objects.prefetch_related("subscription").all()

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

        log.info(
            "Found transaction ID: %s provider: %s with %s duplicates.",
            transaction_id,
            provider_codename,
            len(transaction_id_entries),
        )

        for idx, entry in enumerate(transaction_id_entries):
            assert entry.subscription
            log.info("\t%s: Subscription UID: %s, payment UID: %s", (idx + 1), entry.subscription.uid, entry.uid)

        result[(provider_codename, transaction_id)] = transaction_id_entries

    return result
