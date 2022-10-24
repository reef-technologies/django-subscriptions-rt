from decimal import Decimal
from typing import Optional

from rest_framework.serializers import BooleanField, CharField, DateTimeField, IntegerField, ModelSerializer, PrimaryKeyRelatedField, Serializer, SerializerMethodField

from ..fields import relativedelta_to_dict
from ..models import Plan, Subscription, SubscriptionPayment


class PlanSerializer(ModelSerializer):
    charge_amount = SerializerMethodField()
    charge_period = SerializerMethodField()
    max_duration = SerializerMethodField()

    class Meta:
        model = Plan
        fields = 'id', 'codename', 'name', 'charge_amount', 'charge_amount_currency', 'charge_period', 'max_duration', 'is_recurring', 'metadata',

    def get_charge_amount(self, obj) -> Optional[Decimal]:
        if obj.charge_amount is not None:
            return obj.charge_amount.amount

    def get_charge_period(self, obj) -> dict:
        return relativedelta_to_dict(obj.charge_period)

    def get_max_duration(self, obj) -> dict:
        return relativedelta_to_dict(obj.max_duration)


class SubscriptionSerializer(ModelSerializer):
    plan = PlanSerializer()

    class Meta:
        model = Subscription
        fields = 'id', 'plan', 'quantity', 'start', 'end',


class PaymentProviderSerializer(Serializer):
    name = CharField(read_only=True)


class PaymentProviderListSerializer(Serializer):
    providers = PaymentProviderSerializer(read_only=True, many=True)


class SubscriptionSelectSerializer(Serializer):
    plan = PrimaryKeyRelatedField(queryset=Plan.objects.all())
    quantity = IntegerField(default=1)
    redirect_url = CharField(read_only=True)
    background_charge_succeeded = BooleanField(default=False)
    payment_id = CharField(read_only=True)


class WebhookSerializer(Serializer):
    pass


class ResourcesSerializer(Serializer):
    pass  # TODO


class SubscriptionPaymentSerializer(ModelSerializer):
    status = SerializerMethodField()
    amount = SerializerMethodField()
    currency = SerializerMethodField()
    total = SerializerMethodField()
    subscription = SubscriptionSerializer()
    paid_from = DateTimeField(source='subscription_start')
    paid_to = DateTimeField(source='subscription_end')

    class Meta:
        model = SubscriptionPayment
        fields = 'id', 'status', 'subscription', 'quantity', 'amount', 'currency', 'total', 'paid_from', 'paid_to', 'created',

    def get_status(self, obj) -> str:
        return obj.get_status_display().lower()

    def get_amount(self, obj) -> Optional[Decimal]:
        if obj.amount is not None:
            return obj.amount.amount

    def get_currency(self, obj) -> Optional[str]:
        if obj.amount is not None:
            return str(obj.amount.currency)

    def get_total(self, obj) -> Optional[Decimal]:
        if obj.amount is not None:
            return obj.amount.amount * obj.quantity
