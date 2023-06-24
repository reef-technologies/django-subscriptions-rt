from subscriptions.api.views import SubscriptionSelectView
from subscriptions.models import INFINITY, SubscriptionPayment, Subscription
from dateutil.relativedelta import relativedelta


def test__get_trial_period__disabled(db, plan, user):
    assert SubscriptionSelectView.get_trial_period(plan, user) == relativedelta()


def test__get_trial_period__no_charge_amount(db, trial_period, plan, user):
    plan.charge_amount *= 0
    plan.save()

    assert SubscriptionSelectView.get_trial_period(plan, user) == relativedelta()


def test__get_trial_period__not_recurring(db, trial_period, plan, user):
    plan.charge_period = INFINITY
    plan.save()

    assert SubscriptionSelectView.get_trial_period(plan, user) == relativedelta()


def test__get_trial_period__already_paid(db, trial_period, plan, user):
    payment = SubscriptionPayment.objects.create(
        user=user,
        plan=plan,
        provider_codename='some',
    )
    assert SubscriptionSelectView.get_trial_period(plan, user) == trial_period

    payment.status = SubscriptionPayment.Status.COMPLETED
    payment.save()
    assert SubscriptionSelectView.get_trial_period(plan, user) == relativedelta()


def test__get_trial_period__had_no_recurring(db, trial_period, plan, user):
    assert SubscriptionSelectView.get_trial_period(plan, user) == trial_period

    Subscription.objects.create(plan=plan, user=user)
    assert SubscriptionSelectView.get_trial_period(plan, user) == relativedelta()
