from concurrent.futures import ThreadPoolExecutor, wait
from datetime import timedelta
from functools import partial
from logging import getLogger
from typing import Iterable, Optional

from django.conf import settings
from django.db.models import Prefetch, QuerySet
from django.utils.timezone import now
from more_itertools import first, pairwise

from .defaults import DEFAULT_SUBSCRIPTIONS_OFFLINE_CHARGE_ATTEMPTS_SCHEDULE
from .exceptions import PaymentError, ProlongationImpossible
from .models import Subscription, SubscriptionPayment
from .providers import get_provider

log = getLogger(__name__)

LAST_CHARGE_TIMEFRAME = timedelta(hours=3)
DEFAULT_CHARGE_ATTEMPTS_SCHEDULE = getattr(
    settings,
    'SUBSCRIPTIONS_OFFLINE_CHARGE_ATTEMPTS_SCHEDULE',
    DEFAULT_SUBSCRIPTIONS_OFFLINE_CHARGE_ATTEMPTS_SCHEDULE,
)


def _charge_recurring_subscription(
    subscription: Subscription,
    charge_attempts_schedule: Iterable[timedelta],
):
    log.debug('Processing subscription %s' % subscription)

    # expiration_date = next(subscription.iter_charge_dates(since=now_))
    # TODO: what if `subscription.end_date` expiration_date doesn't match `subscription.iter_charge_dates()`?
    expiration_date = subscription.end_date

    charge_dates = [expiration_date + delta for delta in charge_attempts_schedule]
    charge_periods = pairwise(charge_dates + [
        charge_dates[-1] + LAST_CHARGE_TIMEFRAME if charge_dates[-1] >= expiration_date else expiration_date
    ])

    try:
        now_ = now()
        charge_period = first(
            period for period in charge_periods
            if period[0] <= now_ < period[1]
        )
    except ValueError:
        log.error('Current datetime %s doesn\'t belong to any of charge dates %s, probably it took to long to execute the task?' % (now_, charge_dates))
        raise

    log.debug('Current time falls withing period %s' % charge_period)

    previous_payment_attempts = subscription.payments.filter(
        created__gte=charge_period[0],
        created__lt=charge_period[1],
    )
    if previous_payment_attempts.exists():
        log.debug('Skipping this payment, because there already exists payment attempt within specified charge period: %s' % previous_payment_attempts)
        return  # don't try to charge one more time in this period

    try:
        subscription.prolong()  # try extending end date of subscription
        log.debug('Prolongation of subscription is possible')
    except ProlongationImpossible as exc:
        # cannot prolong anymore, disable auto_prolong for this subscription
        log.debug('Prolongation of subscription is impossible: %s' % exc)
        subscription.auto_prolong = False
        subscription.save()
        log.debug('Turned off auto-prolongation of subscription %s' % subscription)
        # TODO: send email to user
        return

    try:
        log.debug('Offline-charging subscription %s' % subscription)
        subscription.charge_offline()
    except PaymentError as exc:
        log.debug('Failed to offline-charge subscription %s: %s' % (subscription, exc))

        # here we create a failed SubscriptionPayment to indicate that we tried
        # to charge but something went wrong, so that subsequent task calls
        # won't try charging and sending email again withing same charge_period
        SubscriptionPayment.objects.create(
            provider_codename='',
            status=SubscriptionPayment.Status.ERROR,
            plan=subscription.plan,
            subscription=subscription,
            quantity=subscription.quantity,
        )
        return

    log.debug('Offline charge of subscription %s successfully created' % subscription)
    # even if offline subscription succeeds, we are not sure about its status,
    # so we don't prolong the subscription here but instead let setting
    # `subscription.status = COMPLETED` (by charge_offline or webhook or whatever)
    # to auto-prolong subscription itself


def charge_recurring_subscriptions(
    subscriptions: Optional[QuerySet] = None,
    charge_attempts_schedule: Iterable[timedelta] = DEFAULT_CHARGE_ATTEMPTS_SCHEDULE,
    num_threads: Optional[int] = None,
):

    log.debug('Background charging according to schedule %s' % charge_attempts_schedule)
    charge_attempts_schedule = sorted(charge_attempts_schedule)
    if not charge_attempts_schedule:
        return

    subscriptions = subscriptions or Subscription.objects.all()
    expiring_subscriptions = subscriptions\
    .filter(  # noqa
        auto_prolong=True,
    ).expiring(
        from_=now() - charge_attempts_schedule[0],
        within=charge_attempts_schedule[-1] - charge_attempts_schedule[0] + LAST_CHARGE_TIMEFRAME,
    ).select_related(
        'user', 'plan',
    ).select_for_update(
        of=('self', ),
    ).prefetch_related(
        Prefetch('payments', queryset=SubscriptionPayment.objects.order_by('created')),
    )

    with ThreadPoolExecutor(max_workers=num_threads) as pool:
        charge = partial(
            _charge_recurring_subscription,
            charge_attempts_schedule=charge_attempts_schedule,
        )
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
