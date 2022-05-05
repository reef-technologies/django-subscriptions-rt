from rest_framework import serializers

from ...api.serializers import PaymentSerializer


class DummySerializer(PaymentSerializer):
    agreed = serializers.BooleanField(required=True)
