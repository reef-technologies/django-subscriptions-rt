# Generated by Django 4.0.3 on 2022-11-09 15:25
from collections import (
    Counter,
    defaultdict,
)

from django.db import migrations
from logging import getLogger


log = getLogger(__name__)


def remove_apple_in_app_subscription_duplicates(apps, scheme_editor):
    subscription_payment_model = apps.get_model('subscriptions', 'SubscriptionPayment')

    # We need to find all the entries that share the same provider_transaction_id for provider_codename `apple_in_app`.
    # Also, we need to remove duplicated subscriptions objects.
    # I assume that we can fit all the Apple subscription entries in the memory.
    # Sadly, it is not sure which subscription item is the "right" one and there could be renewals already available.
    # Thus, we need to gather all subscription payment objects and determine how many attached payments has each
    # subscription.

    all_entries = subscription_payment_model.objects \
        .filter(provider_codename='apple_in_app') \
        .select_related('subscription', 'plan')

    subscription_attached_payments_counter = defaultdict(int)
    transaction_id_entries = defaultdict(list)

    for entry in all_entries:
        transaction_id_entries[entry.provider_transaction_id].append(entry)
        subscription_attached_payments_counter[entry.subscription.uid] += 1

    for transaction_id, subscription_payment_list in transaction_id_entries.items():
        # Exactly one transaction. Nothing to fix here.
        if len(subscription_payment_list) == 1:
            continue

        # Ensure that all the subscription payments with the same ID are assigned to the same plan.
        if len({entry.plan.name for entry in subscription_payment_list}) > 1:
            log.error(f'Multiple plans assigned to {transaction_id=} %s')
            continue

        # Map subscriptions to their counter, pick max value.
        payment_to_subscription_count_mapping = {
            entry.uid: subscription_attached_payments_counter[entry.subscription.uid]
            for entry in subscription_payment_list
        }
        # Count how many times each counter appears.
        counter_count = Counter(payment_to_subscription_count_mapping.values())
        # Remove entries with only one instance. This leaves us with information about subscriptions
        # that are assigned to two or more payments at the same time.
        del counter_count[1]

        # This is triggered if we had e.g. two different renewals and each of them was started off a different
        # instance of subscription.
        if sum(counter_count.values()) > 1:
            log.error(f'Transaction {transaction_id} has more than one subscription used in more than one payment.')
            continue

        # Otherwise, we can pick any element to stay and the rest will be removed.
        # We pick the one with the highest count or any with count 1.
        entry_id, _count = sorted(payment_to_subscription_count_mapping.items(), key=lambda x: x[1], reverse=True)[0]

        for entry in subscription_payment_list:
            # This one is to stay.
            if entry.uid == entry_id:
                continue

            subscription = entry.subscription
            entry.delete()
            subscription.delete()


def no_op(apps, scheme_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('subscriptions', '0026_alter_subscriptionpayment_status_and_more'),
    ]

    operations = [
        migrations.RunPython(code=remove_apple_in_app_subscription_duplicates, reverse_code=no_op),
    ]
