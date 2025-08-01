from unittest.mock import Mock

import pytest
from django.db.models.signals import post_save
from django.dispatch import receiver

from subscriptions.v0.models import SubscriptionPayment


@pytest.mark.django_db(databases=["actual_db"])
def test__signal__payment_status_changed(payment):
    assert payment.uid is not None
    assert payment.status == SubscriptionPayment.Status.COMPLETED
    target_fn = Mock()

    # set up a signal receiver to call the target function with the expected arguments
    @receiver(post_save, sender=SubscriptionPayment)
    def handler(sender, instance, **kwargs):
        if instance.tracker.has_changed("status"):
            target_fn(
                instance=instance,
                old_status=instance.tracker.previous("status"),
                new_status=instance.status,
            )

    payment.status = payment.Status.COMPLETED
    payment.save()
    assert not target_fn.called

    payment.status = payment.Status.PENDING
    payment.save()
    assert target_fn.called
    assert target_fn.call_args[1] == {
        "instance": payment,
        "old_status": SubscriptionPayment.Status.COMPLETED,
        "new_status": SubscriptionPayment.Status.PENDING,
    }
