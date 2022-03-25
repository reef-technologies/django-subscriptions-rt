from decimal import Decimal
from typing import Optional

from rest_framework.serializers import CharField, ModelSerializer, Serializer, SerializerMethodField, \
                                       URLField, PrimaryKeyRelatedField

from ..fields import relativedelta_to_dict
from ..models import Plan, Subscription


class PlanSerializer(ModelSerializer):
    charge_amount = SerializerMethodField()
    charge_period = SerializerMethodField()
    max_duration = SerializerMethodField()

    class Meta:
        model = Plan
        fields = 'id', 'codename', 'name', 'charge_amount', 'charge_amount_currency', 'charge_period', 'max_duration',

    def get_charge_amount(self, obj) -> Optional[Decimal]:
        return obj.charge_amount and obj.charge_amount.amount

    def get_charge_period(self, obj) -> dict:
        return relativedelta_to_dict(obj.charge_period)

    def get_max_duration(self, obj) -> dict:
        return relativedelta_to_dict(obj.max_duration)


class SubscriptionSerializer(ModelSerializer):
    plan = PlanSerializer()

    class Meta:
        model = Subscription
        fields = 'id', 'plan', 'start', 'end',


class PaymentProviderSerializer(Serializer):
    name = CharField(read_only=True)


class PaymentProviderListSerializer(Serializer):
    providers = PaymentProviderSerializer(read_only=True, many=True)


class PaymentSerializer(Serializer):
    redirect_url = URLField(read_only=True)
    plan = PrimaryKeyRelatedField(queryset=Plan.objects.filter(is_enabled=True))


class WebhookSerializer(Serializer):
    pass
