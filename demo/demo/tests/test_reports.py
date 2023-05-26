from collections import Counter
from freezegun import freeze_time
from subscriptions.reports import SubscriptionsReport, TransactionsReport
from datetime import timedelta


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
