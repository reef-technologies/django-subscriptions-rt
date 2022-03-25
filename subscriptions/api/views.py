from django.conf import settings
from drf_braces.serializers.form_serializer import FormSerializer
from rest_framework.exceptions import NotFound
from rest_framework.generics import GenericAPIView, ListAPIView, ListCreateAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.schemas.openapi import AutoSchema

from ..exceptions import ProviderNotFound
from ..models import Plan, Subscription
from ..providers import get_provider
from .serializers import PlanSerializer, SubscriptionSerializer, PaymentProviderListSerializer


class PlanListView(ListAPIView):
    permission_classes = AllowAny,
    queryset = Plan.objects.filter(is_enabled=True)
    serializer_class = PlanSerializer
    schema = AutoSchema()
    ordering = '-id',


class PaymentProviderListView(GenericAPIView):
    permission_classes = AllowAny,
    exposed_info_keys = 'description',
    serializer_class = PaymentProviderListSerializer

    def get(self, request, *args, **kwargs) -> Response:
        serializer = self.serializer_class({
            'providers': [
                {'name': provider_name}
                for provider_name in settings.PAYMENT_PROVIDERS.keys()
            ]
        })

        return Response(serializer.data)


class SubscriptionListView(ListCreateAPIView):
    permission_classes = IsAuthenticated,
    queryset = Subscription.objects.active().select_related('plan')
    serializer_class = SubscriptionSerializer
    schema = AutoSchema()
    ordering = '-end', '-id',


class PaymentView(GenericAPIView):
    permission_classes = IsAuthenticated,
    schema = AutoSchema()

    def post(self, request, *args, **kwargs) -> Response:
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return self.provider.process_payment(
            request=request,
            serializer=serializer,
        )


def build_payment_view(provider_name: str) -> GenericAPIView:
    class _PaymentView(PaymentView):
        schema = AutoSchema(operation_id_base=f'_{provider_name}')
        serializer_class = get_provider(provider_name).payment_serializer_class
        provider = get_provider(provider_name)

    return _PaymentView


class PaymentWebhookView(GenericAPIView):
    permission_classes = AllowAny,
    schema = AutoSchema()

    def post(self, request, *args, **kwargs) -> Response:
        return self.provider.handle_webhook(request=request)

    get = post


def build_payment_webhook_view(provider_name: str) -> GenericAPIView:
    class _PaymentWebhookView(PaymentWebhookView):
        schema = AutoSchema(operation_id_base=f'_{provider_name}_webhook')
        provider = get_provider(provider_name)
        serializer_class = provider.webhook_serializer_class

    return _PaymentWebhookView
