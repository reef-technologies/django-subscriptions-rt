from rest_framework.generics import GenericAPIView, ListAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.schemas.openapi import AutoSchema
from subscriptions.functions import get_remaining_amount

from ..models import Plan, Subscription
from ..providers import Provider, get_provider, get_providers
from .serializers import PaymentProviderListSerializer, PlanSerializer, ResourcesSerializer, SubscriptionSerializer


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
                {'name': provider.codename}
                for provider in get_providers()
                if provider.is_enabled
            ],
        })

        return Response(serializer.data)


class SubscriptionListView(ListAPIView):
    permission_classes = IsAuthenticated,
    serializer_class = SubscriptionSerializer
    schema = AutoSchema()
    ordering = '-end', '-id',

    def get_queryset(self):
        return Subscription.objects.active().select_related('plan').filter(user=self.request.user)


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


def build_payment_view(provider: Provider) -> GenericAPIView:
    codename = provider.codename

    class _PaymentView(PaymentView):
        schema = AutoSchema(operation_id_base=f'_{codename}')
        serializer_class = get_provider(codename).payment_serializer_class
        provider = get_provider(codename)

    return _PaymentView


class PaymentWebhookView(GenericAPIView):
    permission_classes = AllowAny,
    schema = AutoSchema()

    def post(self, request, *args, **kwargs) -> Response:
        return self.provider.handle_webhook(request=request)

    get = post


def build_payment_webhook_view(provider: Provider) -> GenericAPIView:
    codename = provider.codename

    class _PaymentWebhookView(PaymentWebhookView):
        schema = AutoSchema(operation_id_base=f'_{codename}_webhook')
        provider = get_provider(codename)
        serializer_class = get_provider(codename).webhook_serializer_class

    return _PaymentWebhookView


class ResourcesView(GenericAPIView):
    permission_classes = IsAuthenticated,
    serializer_class = ResourcesSerializer
    schema = AutoSchema()

    def get(self, request, *args, **kwargs) -> Response:
        serializer = self.serializer_class({
            'resources': {resource.codename: amount for resource, amount in get_remaining_amount(request.user).items()},
        })
        return Response(serializer.data)
