from django.db import models

from ...models import SubscriptionPayment, AbstractTransaction


class StripeSubscriptionPayment(SubscriptionPayment):
    payment_intent_id = models.CharField(max_length=255)


class StripeSubscriptionPaymentRefund(AbstractTransaction):
    original_payment = models.ForeignKey(StripeSubscriptionPayment, on_delete=models.PROTECT, related_name='refunds')
