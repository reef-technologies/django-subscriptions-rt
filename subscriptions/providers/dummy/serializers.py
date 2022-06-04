from rest_framework import serializers

from ...api.serializers import WebhookSerializer


class DummyWebhookSerializer(WebhookSerializer):
    transaction_id = serializers.CharField(required=True)
