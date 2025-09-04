from collections import Counter
from datetime import timedelta

import pytest
from django.utils.timezone import now
from freezegun import freeze_time
from more_itertools import partition

from subscriptions.v0.models import SubscriptionPayment
from subscriptions.v0.reports import (
    MONTHLY,
    WEEKLY,
    SubscriptionsReport,
    TransactionsReport,
)
from subscriptions.v0.utils import NO_MONEY

from ..helpers import days, months, usd


def test__reports__subscriptions__iter_periods__microseconds():
    now_ = now().replace(microsecond=123456)
    with pytest.raises(AssertionError):
        list(SubscriptionsReport.iter_periods(WEEKLY, since=now_, until=now_ + days(10)))


def test__reports__subscriptions__iter_periods():
    now_ = now().replace(microsecond=0)
    assert list(SubscriptionsReport.iter_periods(WEEKLY, since=now_, until=now_ + days(22))) == [
        SubscriptionsReport(since=now_, until=now_ + days(7)),
        SubscriptionsReport(since=now_ + days(7), until=now_ + days(14)),
        SubscriptionsReport(since=now_ + days(14), until=now_ + days(21)),
        SubscriptionsReport(since=now_ + days(21), until=now_ + days(22)),
    ]

    assert list(SubscriptionsReport.iter_periods(MONTHLY, since=now_, until=now_ + days(22))) == [
        SubscriptionsReport(since=now_, until=now_ + days(22)),
    ]


@pytest.mark.django_db(databases=["actual_db"])
def test__reports__subscriptions__overlapping(reports_subscriptions, eps):
    now_ = reports_subscriptions[0].start
    subs = reports_subscriptions

    assert set(SubscriptionsReport(now_ - days(1), now_ - timedelta(seconds=1)).overlapping) == set()
    assert set(SubscriptionsReport(now_ - days(1), now_).overlapping) == set()
    assert set(SubscriptionsReport(now_ - days(1), now_ + eps).overlapping) == {subs[0], subs[2]}
    assert set(SubscriptionsReport(now_ - days(1), now_ + days(2)).overlapping) == {subs[0], subs[2]}
    assert set(SubscriptionsReport(now_ - days(1), now_ + days(3) + eps).overlapping) == {subs[0], subs[2], subs[3]}
    assert set(SubscriptionsReport(now_ - days(1), now_ + days(4)).overlapping) == {subs[0], subs[2], subs[3]}
    assert set(SubscriptionsReport(now_ - days(1), now_ + days(7) + eps).overlapping) == {
        subs[0],
        subs[1],
        subs[2],
        subs[3],
    }

    assert set(SubscriptionsReport(now_ + days(3), now_ + days(7) + eps).overlapping) == {
        subs[0],
        subs[1],
        subs[2],
        subs[3],
    }
    assert set(SubscriptionsReport(now_ + days(17), now_ + months(1) + eps).overlapping) == {
        subs[0],
        subs[1],
        subs[2],
        subs[3],
    }
    assert set(SubscriptionsReport(now_ + days(17) + timedelta(seconds=1), now_ + months(1) + eps).overlapping) == {
        subs[0],
        subs[1],
        subs[2],
    }

    assert set(
        SubscriptionsReport(now_ + months(1) + timedelta(seconds=1), now_ + months(1) + days(2)).overlapping
    ) == {subs[1]}
    assert set(SubscriptionsReport(now_ + months(1) + days(8), now_ + months(1) + days(10)).overlapping) == set()


@pytest.mark.django_db(databases=["actual_db"])
def test__reports__subscriptions__new(reports_subscriptions, eps):
    now_ = reports_subscriptions[0].start
    subs = reports_subscriptions

    assert set(SubscriptionsReport(now_ - days(1), now_ - timedelta(seconds=1)).new) == set()
    assert set(SubscriptionsReport(now_ - days(1), now_).new) == set()
    assert set(SubscriptionsReport(now_ - days(1), now_ + eps).new) == {subs[0], subs[2]}
    assert set(SubscriptionsReport(now_, now_ + days(7) + eps).new) == {subs[0], subs[1], subs[2], subs[3]}
    assert set(SubscriptionsReport(now_ + days(1), now_ + days(2)).new) == set()
    assert set(SubscriptionsReport(now_ + days(1), now_ + days(3) + eps).new) == {subs[3]}
    assert set(SubscriptionsReport(now_ + days(8), now_ + days(16)).new) == set()
    assert set(SubscriptionsReport(now_ + days(17), now_ + days(40)).new) == set()


@pytest.mark.django_db(databases=["actual_db"])
def test__reports__subscriptions__new__count(reports_subscriptions, eps):
    now_ = reports_subscriptions[0].start

    assert SubscriptionsReport(now_ - days(1), now_ - timedelta(seconds=1)).get_new_count() == 0
    assert SubscriptionsReport(now_, now_ + days(2)).get_new_count() == 2
    assert SubscriptionsReport(now_, now_ + days(3) + eps).get_new_count() == 3
    assert SubscriptionsReport(now_ + days(2), now_ + days(3) + eps).get_new_count() == 1
    assert SubscriptionsReport(now_ + days(3), now_ + days(6)).get_new_count() == 1
    assert SubscriptionsReport(now_ + days(3), now_ + days(7) + eps).get_new_count() == 2
    assert SubscriptionsReport(now_ + days(7), now_ + days(40)).get_new_count() == 1
    assert SubscriptionsReport(now_ + days(8), now_ + days(40)).get_new_count() == 0


@pytest.mark.django_db(databases=["actual_db"])
def test__reports__subscriptions__excluding_end(reports_subscriptions, eps):
    now_ = reports_subscriptions[0].start

    assert SubscriptionsReport(now_ + days(3), now_ + days(7)).get_new_count() == 1
    assert SubscriptionsReport(now_ + days(3), now_ + days(7) + eps).get_new_count() == 2


@pytest.mark.django_db(databases=["actual_db"])
def test__reports__subscriptions__new__datetimes(reports_subscriptions, eps):
    now_ = reports_subscriptions[0].start

    assert set(SubscriptionsReport(now_ - days(2), now_ - days(1)).get_new_datetimes()) == set()
    assert set(SubscriptionsReport(now_ + days(1), now_ + days(3) + eps).get_new_datetimes()) == {now_ + days(3)}
    assert sorted(SubscriptionsReport(now_, now_ + days(7) + eps).get_new_datetimes()) == [
        now_,
        now_,
        now_ + days(3),
        now_ + days(7),
    ]


@pytest.mark.django_db(databases=["actual_db"])
def test__reports__subscriptions__ended_or_ending__query(reports_subscriptions):
    now_ = reports_subscriptions[0].start
    subs = reports_subscriptions

    assert set(SubscriptionsReport(now_ - days(2), now_ - days(1)).ended_or_ending) == set()
    assert set(SubscriptionsReport(now_ - days(2), now_ - days(1)).ended_or_ending) == set()
    assert set(SubscriptionsReport(now_, now_ + days(18)).ended_or_ending) == {subs[3]}

    with freeze_time(now_):  # if look into future - expect subs[2] to be prolongated
        assert set(SubscriptionsReport(now_, now_ + days(32)).ended_or_ending) == {subs[0], subs[3]}

    with freeze_time(now_ + days(32)):  # if look into past - subs[2] was not prolongated
        assert set(SubscriptionsReport(now_, now_ + days(32)).ended_or_ending) == {subs[0], subs[2], subs[3]}


@pytest.mark.django_db(databases=["actual_db"])
def test__reports__subscriptions__ended_or_ending__count(reports_subscriptions):
    now_ = reports_subscriptions[0].start

    assert SubscriptionsReport(now_ - days(2), now_ - days(1)).get_ended_count() == 0
    assert SubscriptionsReport(now_ - days(2), now_ - days(1)).get_ended_count() == 0
    assert SubscriptionsReport(now_, now_ + days(18)).get_ended_count() == 1

    with freeze_time(now_):  # if look into future - expect subs[2] to be prolongated
        assert SubscriptionsReport(now_, now_ + days(32)).get_ended_count() == 2

    with freeze_time(now_ + days(32)):  # if look into past - subs[2] was not prolongated
        assert SubscriptionsReport(now_, now_ + days(32)).get_ended_count() == 3


@pytest.mark.django_db(databases=["actual_db"])
def test__reports__subscriptions__ended_or_ending__datetimes(reports_subscriptions):
    now_ = reports_subscriptions[0].start
    subs = reports_subscriptions

    with freeze_time(now_ + days(32)):
        ended_datetimes = SubscriptionsReport(now_, now_ + days(32)).get_ended_datetimes()
        assert sorted(dt.timestamp() for dt in ended_datetimes) == sorted(
            map(lambda dt: dt.timestamp(), [subs[0].end, subs[2].end, subs[3].end])
        )


@pytest.mark.django_db(databases=["actual_db"])
def test__reports__subscriptions__ended_or_ending__ages(reports_subscriptions):
    now_ = reports_subscriptions[0].start
    with freeze_time(now_):
        assert set(SubscriptionsReport(now_, now_ + months(1) + days(2)).get_ended_or_ending_ages()) == {
            timedelta(days=31),
            timedelta(days=14),
        }


@pytest.mark.django_db(databases=["actual_db"])
def test__reports__subscriptions__active__query(reports_subscriptions):
    now_ = reports_subscriptions[0].start
    subs = reports_subscriptions

    assert set(SubscriptionsReport(now_ - days(1), now_ - timedelta(seconds=1)).active) == set()
    assert set(SubscriptionsReport(now_, now_ + days(2)).active) == {subs[0], subs[2]}
    assert set(SubscriptionsReport(now_, now_ + days(17)).active) == {subs[0], subs[1], subs[2]}
    assert set(SubscriptionsReport(now_ + days(7), now_ + days(17)).active) == {subs[0], subs[1], subs[2]}

    with freeze_time(now_):
        assert set(SubscriptionsReport(now_ + days(7), now_ + days(32)).active) == {subs[1], subs[2]}
    with freeze_time(now_ + days(32)):
        assert set(SubscriptionsReport(now_ + days(7), now_ + days(32)).active) == {subs[1]}


@pytest.mark.django_db(databases=["actual_db"])
def test__reports__subscriptions__active__count(reports_subscriptions, eps):
    now_ = reports_subscriptions[0].start
    assert SubscriptionsReport(now_, now_ + days(17)).get_active_count() == 3


@pytest.mark.django_db(databases=["actual_db"])
def test__reports__subscriptions__active__users_count(reports_subscriptions):
    now_ = reports_subscriptions[0].start

    assert SubscriptionsReport(now_ - days(1), now_ - timedelta(seconds=1)).get_active_users_count() == 0
    assert SubscriptionsReport(now_, now_ + days(2)).get_active_users_count() == 2

    with freeze_time(now_):
        # we expect one of the subscriptions to be recharged
        assert SubscriptionsReport(now_ + days(7), now_ + days(32)).get_active_users_count() == 2

    with freeze_time(now_ + days(32)):
        # now we already know that one of the subscriptions was not recharged
        assert SubscriptionsReport(now_ + days(7), now_ + days(32)).get_active_users_count() == 1


@pytest.mark.django_db(databases=["actual_db"])
def test__reports__subscriptions__active__ages(reports_subscriptions):
    now_ = reports_subscriptions[0].start

    assert sorted(SubscriptionsReport(now_ + days(7), now_ + days(17)).get_active_ages()) == sorted(
        [timedelta(days=17), timedelta(days=10), timedelta(days=17)]
    )
    assert sorted(SubscriptionsReport(now_ + months(1) + days(1), now_ + months(1) + days(6)).get_active_ages()) == [
        timedelta(days=30)
    ]
    with freeze_time(now_):
        assert sorted(
            SubscriptionsReport(now_ + months(1) + days(1), now_ + months(1) + days(10)).get_active_ages()
        ) == [timedelta(days=31)]


@pytest.mark.django_db(databases=["actual_db"])
def test__reports__subscriptions__active__plans__quantities(reports_subscriptions, plan, bigger_plan, recharge_plan):
    now_ = reports_subscriptions[0].start

    assert set(SubscriptionsReport(now_ + days(7), now_ + days(17)).get_active_plans_and_quantities()) == {
        (plan, 1),
        (bigger_plan, 1),
        (plan, 2),
    }


@pytest.mark.django_db(databases=["actual_db"])
def test__reports__subscriptions__active__plans__total(reports_subscriptions, plan, bigger_plan, recharge_plan):
    now_ = reports_subscriptions[0].start

    assert SubscriptionsReport(now_ + days(7), now_ + days(17)).get_active_plans_total() == Counter(
        {plan: 3, bigger_plan: 1}
    )


@pytest.mark.django_db(databases=["actual_db"])
def test__reports__transactions__payments__query(reports_payments, paddle):
    now_ = reports_payments[0].created
    pmts = reports_payments

    assert (
        list(
            TransactionsReport(
                provider_codename=paddle.codename, since=now_ - days(1), until=now_ - timedelta(seconds=1)
            ).payments
        )
        == []
    )
    assert list(TransactionsReport(provider_codename=paddle.codename, since=now_, until=now_ + days(9)).payments) == [
        pmts[0]
    ]
    assert list(
        TransactionsReport(provider_codename=paddle.codename, since=now_ + days(1), until=now_ + days(10)).payments
    ) == [pmts[1], pmts[4]]

    assert list(TransactionsReport(provider_codename="nonexistent", since=now_, until=now_ + days(40)).payments) == []


@pytest.mark.django_db(databases=["actual_db"])
def test__reports__transactions__payments__count_by_status(reports_payments, paddle):
    now_ = reports_payments[0].created

    assert dict(
        TransactionsReport(
            provider_codename=paddle.codename, since=now_, until=now_ + days(30)
        ).get_payments_count_by_status()
    ) == {
        SubscriptionPayment.Status.COMPLETED: 5,
        SubscriptionPayment.Status.PENDING: 1,
        SubscriptionPayment.Status.CANCELLED: 1,
    }


@pytest.mark.django_db(databases=["actual_db"])
def test__reports__transactions__payments__completed__query(reports_payments, paddle):
    now_ = reports_payments[0].created
    pmts = reports_payments

    assert list(
        TransactionsReport(provider_codename=paddle.codename, since=now_, until=now_ + days(20)).completed_payments
    ) == [pmts[0], pmts[1], pmts[2], pmts[6]]


@pytest.mark.django_db(databases=["actual_db"])
def test__reports__transactions__payments__completed__amounts(reports_payments, paddle):
    now_ = reports_payments[0].created

    amounts = TransactionsReport(
        provider_codename=paddle.codename, since=now_, until=now_ + days(30)
    ).get_completed_payments_amounts()
    values, nones = partition(lambda x: x is None, amounts)
    assert len(list(nones)) == 1
    assert sorted(values) == sorted([usd(200), usd(180), usd(160), usd(140)])


@pytest.mark.django_db(databases=["actual_db"])
def test__reports__transactions__payments__completed__average(reports_payments, paddle):
    now_ = reports_payments[0].created

    assert TransactionsReport(
        provider_codename=paddle.codename, since=now_, until=now_ + days(30)
    ).get_completed_payments_average() == usd(170)
    assert (
        TransactionsReport(
            provider_codename=paddle.codename, since=now_ - days(2), until=now_ - days(1)
        ).get_completed_payments_average()
        is None
    )


@pytest.mark.django_db(databases=["actual_db"])
def test__reports__transactions__payments__completed__total(reports_payments, paddle):
    now_ = reports_payments[0].created

    assert TransactionsReport(
        provider_codename=paddle.codename, since=now_, until=now_ + days(30)
    ).get_completed_payments_total() == usd(680)


@pytest.mark.django_db(databases=["actual_db"])
def test__reports__transactions__payments__incompleted__amounts(reports_payments, paddle):
    now_ = reports_payments[0].created

    amounts = TransactionsReport(
        provider_codename=paddle.codename, since=now_, until=now_ + days(30)
    ).get_incompleted_payments_amounts()
    assert amounts == [usd(400), usd(400)]


@pytest.mark.django_db(databases=["actual_db"])
def test__reports__transactions__payments__incompleted__total(reports_payments, paddle):
    now_ = reports_payments[0].created

    TransactionsReport(
        provider_codename=paddle.codename, since=now_, until=now_ + days(30)
    ).get_incompleted_payments_total() == usd(800)


@pytest.mark.django_db(databases=["actual_db"])
def test__reports__transactions__refunds__query(reports_payments, paddle):
    now_ = reports_payments[0].created
    pmts = reports_payments
    assert list(TransactionsReport(provider_codename=paddle.codename, since=now_, until=now_ + days(20)).refunds) == [
        pmts[-2]
    ]


@pytest.mark.django_db(databases=["actual_db"])
def test__reports__transactions__refunds__count(reports_payments, paddle):
    now_ = reports_payments[0].created

    assert (
        TransactionsReport(provider_codename=paddle.codename, since=now_, until=now_ + days(20)).get_refunds_count()
        == 1
    )


@pytest.mark.django_db(databases=["actual_db"])
def test__reports__transactions__refunds__amounts(reports_payments, paddle):
    now_ = reports_payments[0].created

    assert TransactionsReport(
        provider_codename=paddle.codename, since=now_, until=now_ + days(20)
    ).get_refunds_amounts() == [usd(250)]


@pytest.mark.django_db(databases=["actual_db"])
def test__reports__transactions__refunds__average(reports_payments, paddle):
    now_ = reports_payments[0].created

    assert (
        TransactionsReport(provider_codename=paddle.codename, since=now_, until=now_ + days(1)).get_refunds_average()
        is None
    )
    assert TransactionsReport(
        provider_codename=paddle.codename, since=now_, until=now_ + days(20)
    ).get_refunds_average() == usd(250)


@pytest.mark.django_db(databases=["actual_db"])
def test__reports__transactions__refunds__total(reports_payments, paddle):
    now_ = reports_payments[0].created

    assert (
        TransactionsReport(provider_codename=paddle.codename, since=now_, until=now_ + days(1)).get_refunds_total()
        == NO_MONEY
    )
    assert TransactionsReport(
        provider_codename=paddle.codename, since=now_, until=now_ + days(20)
    ).get_refunds_total() == usd(250)


@pytest.mark.django_db(databases=["actual_db"])
def test__reports__transactions__estimated_recurring_charge__by_time(reports_subscriptions, paddle, eps):
    now_ = reports_subscriptions[0].start

    assert (
        TransactionsReport(
            provider_codename=paddle.codename, since=now_ - days(2), until=now_ - days(1)
        ).get_estimated_recurring_charge_amounts_by_time()
        == {}
    )
    assert TransactionsReport(
        provider_codename=paddle.codename, since=now_ - days(1), until=now_ + days(2)
    ).get_estimated_recurring_charge_amounts_by_time() == {
        now_: usd(100) * 3,
    }
    assert TransactionsReport(
        provider_codename=paddle.codename, since=now_, until=now_ + days(7) + eps
    ).get_estimated_recurring_charge_amounts_by_time() == {
        now_: usd(100) * 3,
        now_ + days(7): usd(200),
    }
    assert TransactionsReport(
        provider_codename=paddle.codename, since=now_ + days(3), until=now_ + days(7) + eps
    ).get_estimated_recurring_charge_amounts_by_time() == {
        now_ + days(7): usd(200),
    }
    assert TransactionsReport(
        provider_codename=paddle.codename, since=now_ - days(1), until=now_ + days(40)
    ).get_estimated_recurring_charge_amounts_by_time() == {
        now_: usd(100) * 3,
        now_ + days(7): usd(200),
        now_ + months(1): usd(100) * 3,
        now_ + months(1) + days(7): usd(200),
    }


@pytest.mark.django_db(databases=["actual_db"])
def test__reports__transactions__estimated_recurring_charge__total(reports_subscriptions, paddle, eps):
    now_ = reports_subscriptions[0].start

    assert (
        TransactionsReport(
            provider_codename=paddle.codename, since=now_ - days(2), until=now_ - days(1)
        ).get_estimated_recurring_charge_total()
        == NO_MONEY
    )
    assert TransactionsReport(
        provider_codename=paddle.codename, since=now_ - days(1), until=now_ + days(2)
    ).get_estimated_recurring_charge_total() == usd(300)
    assert TransactionsReport(
        provider_codename=paddle.codename, since=now_, until=now_ + days(7) + eps
    ).get_estimated_recurring_charge_total() == usd(500)
    assert TransactionsReport(
        provider_codename=paddle.codename, since=now_ + days(3), until=now_ + days(7) + eps
    ).get_estimated_recurring_charge_total() == usd(200)
    assert TransactionsReport(
        provider_codename=paddle.codename, since=now_ - days(1), until=now_ + days(40)
    ).get_estimated_recurring_charge_total() == usd(1000)
