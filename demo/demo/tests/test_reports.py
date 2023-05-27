from collections import Counter
from more_itertools import partition
from freezegun import freeze_time
from subscriptions.models import SubscriptionPayment
from subscriptions.reports import SubscriptionsReport, TransactionsReport, WEEKLY, MONTHLY
from datetime import timedelta


def test__reports__subscriptions__iter_periods(now, days):
    assert list(SubscriptionsReport.iter_periods(WEEKLY, since=now, until=now+days(22))) == [
        SubscriptionsReport(since=now, until=now+days(7)),
        SubscriptionsReport(since=now+days(7), until=now+days(14)),
        SubscriptionsReport(since=now+days(14), until=now+days(21)),
        SubscriptionsReport(since=now+days(21), until=now+days(22)),
    ]

    assert list(SubscriptionsReport.iter_periods(MONTHLY, since=now, until=now+days(22))) == [
        SubscriptionsReport(since=now, until=now+days(22)),
    ]


def test__reports__subscriptions__overlapping(reports_subscriptions, now, days):
    subs = reports_subscriptions

    assert set(SubscriptionsReport(now-days(1), now-timedelta(seconds=1)).overlapping) == set()
    assert set(SubscriptionsReport(now-days(1), now).overlapping) == {subs[0], subs[2]}
    assert set(SubscriptionsReport(now-days(1), now+days(2)).overlapping) == {subs[0], subs[2]}
    assert set(SubscriptionsReport(now-days(1), now+days(3)).overlapping) == {subs[0], subs[2], subs[3]}
    assert set(SubscriptionsReport(now-days(1), now+days(4)).overlapping) == {subs[0], subs[2], subs[3]}
    assert set(SubscriptionsReport(now-days(1), now+days(7)).overlapping) == {subs[0], subs[1], subs[2], subs[3]}

    assert set(SubscriptionsReport(now+days(3), now+days(7)).overlapping) == {subs[0], subs[1], subs[2], subs[3]}
    assert set(SubscriptionsReport(now+days(17), now+days(30)).overlapping) == {subs[0], subs[1], subs[2], subs[3]}
    assert set(SubscriptionsReport(now+days(17)+timedelta(seconds=1), now+days(30)).overlapping) == {subs[0], subs[1], subs[2]}

    assert set(SubscriptionsReport(now+days(30)+timedelta(seconds=1), now+days(40)).overlapping) == {subs[1]}
    assert set(SubscriptionsReport(now+days(38), now+days(40)).overlapping) == set()


def test__reports__subscriptions__new(reports_subscriptions, now, days):
    subs = reports_subscriptions

    assert set(SubscriptionsReport(now-days(1), now-timedelta(seconds=1)).new) == set()
    assert set(SubscriptionsReport(now-days(1), now).new) == {subs[0], subs[2]}
    assert set(SubscriptionsReport(now, now+days(7)).new) == {subs[0], subs[1], subs[2], subs[3]}
    assert set(SubscriptionsReport(now+days(1), now+days(2)).new) == set()
    assert set(SubscriptionsReport(now+days(1), now+days(3)).new) == {subs[3]}
    assert set(SubscriptionsReport(now+days(8), now+days(16)).new) == set()
    assert set(SubscriptionsReport(now+days(17), now+days(40)).new) == set()


def test__reports__subscriptions__new__count(reports_subscriptions, now, days):

    assert SubscriptionsReport(now-days(1), now-timedelta(seconds=1)).get_new_count() == 0
    assert SubscriptionsReport(now, now+days(2)).get_new_count() == 2
    assert SubscriptionsReport(now, now+days(3)).get_new_count() == 3
    assert SubscriptionsReport(now+days(2), now+days(3)).get_new_count() == 1
    assert SubscriptionsReport(now+days(3), now+days(6)).get_new_count() == 1
    assert SubscriptionsReport(now+days(3), now+days(7)).get_new_count() == 2
    assert SubscriptionsReport(now+days(7), now+days(40)).get_new_count() == 1
    assert SubscriptionsReport(now+days(8), now+days(40)).get_new_count() == 0


def test__reports__subscriptions__new__datetimes(reports_subscriptions, now, days):

    assert set(SubscriptionsReport(now-days(2), now-days(1)).get_new_datetimes()) == set()
    assert set(SubscriptionsReport(now+days(1), now+days(3)).get_new_datetimes()) == {now+days(3)}
    assert list(sorted(SubscriptionsReport(now, now+days(7)).get_new_datetimes())) == [now, now, now+days(3), now+days(7)]


def test__reports__subscriptions__ended_or_ending__query(reports_subscriptions, now, days):
    subs = reports_subscriptions

    assert set(SubscriptionsReport(now-days(2), now-days(1)).ended_or_ending) == set()
    assert set(SubscriptionsReport(now-days(2), now-days(1)).ended_or_ending) == set()
    assert set(SubscriptionsReport(now, now+days(18)).ended_or_ending) == {subs[3]}

    with freeze_time(now):  # if look into future - expect subs[2] to be prolongated
        assert set(SubscriptionsReport(now, now+days(32)).ended_or_ending) == {subs[0], subs[3]}

    with freeze_time(now+days(32)):  # if look into past - subs[2] was not prolongated
        assert set(SubscriptionsReport(now, now+days(32)).ended_or_ending) == {subs[0], subs[2], subs[3]}


def test__reports__subscriptions__ended_or_ending__count(reports_subscriptions, now, days):

    assert SubscriptionsReport(now-days(2), now-days(1)).get_ended_count() == 0
    assert SubscriptionsReport(now-days(2), now-days(1)).get_ended_count() == 0
    assert SubscriptionsReport(now, now+days(18)).get_ended_count() == 1

    with freeze_time(now):  # if look into future - expect subs[2] to be prolongated
        assert SubscriptionsReport(now, now+days(32)).get_ended_count() == 2

    with freeze_time(now+days(32)):  # if look into past - subs[2] was not prolongated
        assert SubscriptionsReport(now, now+days(32)).get_ended_count() == 3


def test__reports__subscriptions__ended_or_ending__datetimes(reports_subscriptions, now, days):
    subs = reports_subscriptions

    with freeze_time(now+days(32)):
        ended_datetimes = SubscriptionsReport(now, now+days(32)).get_ended_datetimes()
        assert sorted(dt.timestamp() for dt in ended_datetimes) == sorted(map(lambda dt: dt.timestamp(), [subs[0].end, subs[2].end, subs[3].end]))


def test__reports__subscriptions__ended_or_ending__ages(reports_subscriptions, now, days):
    with freeze_time(now):
        assert set(SubscriptionsReport(now, now+days(32)).get_ended_or_ending_ages()) == {timedelta(days=30), timedelta(days=14)}


def test__reports__subscriptions__active__query(reports_subscriptions, now, days):
    subs = reports_subscriptions

    assert set(SubscriptionsReport(now-days(1), now-timedelta(seconds=1)).active) == set()
    assert set(SubscriptionsReport(now, now+days(2)).active) == {subs[0], subs[2]}
    assert set(SubscriptionsReport(now, now+days(17)).active) == {subs[0], subs[1], subs[2]}
    assert set(SubscriptionsReport(now+days(7), now+days(17)).active) == {subs[0], subs[1], subs[2]}

    with freeze_time(now):
        assert set(SubscriptionsReport(now+days(7), now+days(32)).active) == {subs[1], subs[2]}
    with freeze_time(now+days(32)):
        assert set(SubscriptionsReport(now+days(7), now+days(32)).active) == {subs[1]}


def test__reports__subscriptions__active__count(reports_subscriptions, now, days):
    assert SubscriptionsReport(now, now+days(17)).get_active_count() == 3


def test__reports__subscriptions__active__users_count(reports_subscriptions, now, days):
    assert SubscriptionsReport(now-days(1), now-timedelta(seconds=1)).get_active_users_count() == 0
    assert SubscriptionsReport(now, now+days(2)).get_active_users_count() == 2
    assert SubscriptionsReport(now+days(7), now+days(32)).get_active_users_count() == 1


def test__reports__subscriptions__active__ages(reports_subscriptions, now, days):
    assert sorted(SubscriptionsReport(now+days(7), now+days(17)).get_active_ages()) == sorted([timedelta(days=17), timedelta(days=10), timedelta(days=17)])
    assert sorted(SubscriptionsReport(now+days(31), now+days(36)).get_active_ages()) == [timedelta(days=29)]
    with freeze_time(now):
        assert sorted(SubscriptionsReport(now+days(31), now+days(40)).get_active_ages()) == [timedelta(days=30)]


def test__reports__subscriptions__active__plans__quantities(reports_subscriptions, now, days, plan, bigger_plan, recharge_plan):
    assert set(SubscriptionsReport(now+days(7), now+days(17)).get_active_plans_and_quantities()) == {(plan, 1), (bigger_plan, 1), (plan, 2)}


def test__reports__subscriptions__active__plans__total(reports_subscriptions, now, days, plan, bigger_plan, recharge_plan):
    assert SubscriptionsReport(now+days(7), now+days(17)).get_active_plans_total() == Counter({plan: 3, bigger_plan: 1})


def test__reports__transactions__payments__query(reports_payments, paddle, now, days):
    pmts = reports_payments

    assert list(TransactionsReport(provider_codename=paddle.codename, since=now-days(1), until=now-timedelta(seconds=1)).payments) == []
    assert list(TransactionsReport(provider_codename=paddle.codename, since=now, until=now+days(9)).payments) == [pmts[0]]
    assert list(TransactionsReport(provider_codename=paddle.codename, since=now+days(1), until=now+days(10)).payments) == [pmts[1], pmts[4]]

    assert list(TransactionsReport(provider_codename='nonexistent', since=now, until=now+days(40)).payments) == []


def test__reports__transactions__payments__count_by_status(reports_payments, paddle, now, days):
    assert dict(TransactionsReport(provider_codename=paddle.codename, since=now, until=now+days(30)).get_payments_count_by_status()) == {
        SubscriptionPayment.Status.COMPLETED: 5,
        SubscriptionPayment.Status.PENDING: 1,
        SubscriptionPayment.Status.CANCELLED: 1,
    }


def test__reports__transactions__payments__completed__query(reports_payments, paddle, now, days):
    pmts = reports_payments
    assert list(TransactionsReport(provider_codename=paddle.codename, since=now, until=now+days(20)).completed_payments) == [pmts[0], pmts[1], pmts[2], pmts[6]]


def test__reports__transactions__payments__completed__amounts(reports_payments, paddle, now, days, usd):
    amounts = TransactionsReport(provider_codename=paddle.codename, since=now, until=now+days(30)).get_completed_payments_amounts()
    values, nones = partition(lambda x: x is None, amounts)
    assert len(list(nones)) == 1
    assert sorted(values) == sorted([usd(200), usd(180), usd(160), usd(140)])


def test__reports__transactions__payments__completed__average(reports_payments, paddle, now, days, usd):
    assert TransactionsReport(provider_codename=paddle.codename, since=now, until=now+days(30)).get_completed_payments_average() == usd(170)
    assert TransactionsReport(provider_codename=paddle.codename, since=now-days(2), until=now-days(1)).get_completed_payments_average() is None


def test__reports__transactions__payments__completed__total(reports_payments, paddle, now, days, usd):
    assert TransactionsReport(provider_codename=paddle.codename, since=now, until=now+days(30)).get_completed_payments_total() == usd(680)


def test__reports__transactions__payments__incompleted__amounts(reports_payments, paddle, now, days, usd):
    amounts = TransactionsReport(provider_codename=paddle.codename, since=now, until=now+days(30)).get_incompleted_payments_amounts()
    assert amounts == [usd(400), usd(400)]


def test__reports__transactions__payments__incompleted__total(reports_payments, paddle, now, days, usd):
    TransactionsReport(provider_codename=paddle.codename, since=now, until=now+days(30)).get_incompleted_payments_total() == usd(800)


def test__reports__transactions__refunds__query(reports_payments, paddle, now, days):
    pmts = reports_payments
    assert list(TransactionsReport(provider_codename=paddle.codename, since=now, until=now+days(20)).refunds) == [pmts[-2]]


def test__reports__transactions__refunds__count(reports_payments, paddle, now, days):
    assert TransactionsReport(provider_codename=paddle.codename, since=now, until=now+days(20)).get_refunds_count() == 1


def test__reports__transactions__refunds__amounts(reports_payments, paddle, now, days, usd):
    assert TransactionsReport(provider_codename=paddle.codename, since=now, until=now+days(20)).get_refunds_amounts() == [usd(250)]


def test__reports__transactions__refunds__average(reports_payments, paddle, now, days, usd):
    assert TransactionsReport(provider_codename=paddle.codename, since=now, until=now+days(1)).get_refunds_average() is None
    assert TransactionsReport(provider_codename=paddle.codename, since=now, until=now+days(20)).get_refunds_average() == usd(250)


def test__reports__transactions__refunds__total(reports_payments, paddle, now, days, usd):
    assert TransactionsReport(provider_codename=paddle.codename, since=now, until=now+days(1)).get_refunds_total() is None
    assert TransactionsReport(provider_codename=paddle.codename, since=now, until=now+days(20)).get_refunds_total() == usd(250)
