from contextlib import suppress
from datetime import datetime
from decimal import Decimal

from django.utils.timezone import now
from rest_framework.fields import ChoiceField
from rest_framework.serializers import (
    CharField,
    DateTimeField,
    IntegerField,
    ModelSerializer,
    PrimaryKeyRelatedField,
    Serializer,
    SerializerMethodField,
)

from ..fields import relativedelta_to_dict
from ..models import Plan, Subscription, SubscriptionPayment
from .validators import validate_provider


class PlanSerializer(ModelSerializer):
    charge_amount = SerializerMethodField()
    charge_period = SerializerMethodField()
    max_duration = SerializerMethodField()

    class Meta:
        model = Plan
        fields = (
            "id",
            "name",
            "charge_amount",
            "charge_amount_currency",
            "charge_period",
            "max_duration",
            "is_recurring",
            "metadata",
        )

    def get_charge_amount(self, obj) -> Decimal | None:
        if obj.charge_amount is not None:
            return obj.charge_amount.amount

    def get_charge_period(self, obj) -> dict:
        return relativedelta_to_dict(obj.charge_period)

    def get_max_duration(self, obj) -> dict:
        return relativedelta_to_dict(obj.max_duration)


class SubscriptionSerializer(ModelSerializer):
    plan = PlanSerializer()
    next_charge_date = SerializerMethodField()
    provider = SerializerMethodField(read_only=True)

    class Meta:
        model = Subscription
        fields = (
            "id",
            "plan",
            "quantity",
            "start",
            "end",
            "next_charge_date",
            "provider",
        )

    def get_next_charge_date(self, subscription: Subscription) -> datetime | None:
        if not subscription.auto_prolong:
            return None

        with suppress(StopIteration):
            return next(subscription.iter_charge_dates(since=now()))

    def get_provider(self, subscription: Subscription) -> str | None:
        with suppress(SubscriptionPayment.DoesNotExist):
            reference_payment = SubscriptionPayment.get_reference_payment(
                user=subscription.user,
                provider_codename=None,
            )
            return reference_payment.provider_codename


class PaymentProviderSerializer(Serializer):
    name = CharField(read_only=True)


class PaymentProviderListSerializer(Serializer):
    providers = PaymentProviderSerializer(read_only=True, many=True)


class SubscriptionSelectSerializer(Serializer):
    plan = PrimaryKeyRelatedField(queryset=Plan.objects.all())
    quantity = IntegerField(default=1, min_value=1)
    provider = CharField(validators=[validate_provider])
    redirect_url = CharField(read_only=True)
    status = ChoiceField(choices=[[name.lower()] * 2 for _, name in SubscriptionPayment.Status.choices], read_only=True)
    payment_id = CharField(read_only=True)


class WebhookSerializer(Serializer):
    pass


class ResourcesEntrySerializer(Serializer):
    codename = CharField()
    amount = IntegerField()


class ResourcesSerializer(Serializer):
    resources = ResourcesEntrySerializer(many=True)


class SubscriptionPaymentSerializer(ModelSerializer):
    status = SerializerMethodField()
    amount = SerializerMethodField()
    currency = SerializerMethodField()
    total = SerializerMethodField()
    subscription = SubscriptionSerializer()
    paid_since = DateTimeField()
    paid_until = DateTimeField()

    class Meta:
        model = SubscriptionPayment
        fields = (
            "id",
            "status",
            "subscription",
            "quantity",
            "amount",
            "currency",
            "total",
            "paid_since",
            "paid_until",
            "created",
        )

    def get_status(self, obj) -> str:
        return obj.get_status_display().lower()

    def get_amount(self, obj) -> Decimal | None:
        if obj.amount is not None:
            return obj.amount.amount

    def get_currency(self, obj) -> str | None:
        if obj.amount is not None:
            return str(obj.amount.currency)

    def get_total(self, obj) -> Decimal | None:
        if obj.amount is not None:
            return obj.amount.amount * obj.quantity
